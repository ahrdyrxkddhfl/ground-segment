"""Space-Track에서 대상 위성의 과거 TLE 이력을 일괄 수집한다.

FDS의 전파 편차 검증은 서로 다른 epoch의 TLE 두 개를 필요로 한다. 과거
TLE로 특정 시각을 전파한 결과와, 그 시각 근처에 발행된 TLE가 말하는 위치를
비교하는 방식이기 때문이다. Celestrak은 최신 TLE만 제공하므로 이력은
Space-Track에서 받는다.

Space-Track 이용에 관하여:
    계정 로그인이 필요하며 세션 쿠키를 유지해야 한다. API 호출 빈도에 제한이
    있으므로(분당·시간당 상한) 위성별로 한 번씩만 질의하고 결과를 파일로
    남긴다. 같은 데이터를 반복해서 받지 않도록 이미 캐시된 epoch는 건너뛴다.

실행:
    python scripts/fetch_tle_history.py [--days 180]
"""
import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

CONFIG = Path("config/mission.yaml")
BASE = "https://www.space-track.org"
LOGIN_URL = f"{BASE}/ajaxauth/login"


def login(session, user, password):
    """Space-Track에 로그인해 세션에 인증 쿠키를 심는다.

    Space-Track은 토큰이 아니라 세션 쿠키로 인증한다. 반환된 세션을
    이후 모든 질의에 재사용해야 하며, 질의마다 로그인하면 호출 제한에
    금방 걸린다.

    Args:
        session: requests.Session 인스턴스.
        user: 계정 이메일.
        password: 계정 비밀번호.

    Raises:
        RuntimeError: 인증에 실패했을 때. Space-Track은 실패 시에도
            200을 돌려주고 본문에 오류 문구를 넣는 경우가 있어
            상태 코드만으로 판단하지 않는다.
    """
    resp = session.post(
        LOGIN_URL,
        data={"identity": user, "password": password},
        timeout=30,
    )
    if resp.status_code != 200 or "error" in resp.text.lower():
        raise RuntimeError(f"Space-Track 로그인 실패: {resp.status_code} {resp.text[:200]}")


def fetch_history(session, norad_id, since):
    """특정 위성의 지정 시점 이후 TLE 이력을 모두 받아온다.

    gp_history 클래스를 epoch 범위로 질의한다. 저궤도 위성은 하루에
    한두 세대씩 발행되므로 180일이면 200~400건 정도가 돌아온다.

    Args:
        session: 로그인된 requests.Session.
        norad_id: 위성 카탈로그 번호.
        since: 이 시각 이후에 발행된 TLE만 받는다. UTC.

    Returns:
        TLE 레코드 리스트. 각 항목은 TLE_LINE0/1/2와 EPOCH 키를 갖는다.
        epoch 오름차순으로 정렬되어 있다.

    Raises:
        RuntimeError: 질의가 실패했을 때.
    """
    url = (
        f"{BASE}/basicspacedata/query/class/gp_history"
        f"/NORAD_CAT_ID/{norad_id}"
        f"/EPOCH/%3E{since:%Y-%m-%d}"
        f"/orderby/EPOCH%20asc"
        f"/format/json"
    )
    resp = session.get(url, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"질의 실패 (NORAD {norad_id}): {resp.status_code} {resp.text[:200]}")
    return resp.json()


def save_records(records, cache_dir, norad_id):
    """받아온 TLE 레코드를 epoch별 파일로 저장한다.

    파일명 형식과 3행 구조를 run_fetch_tle.py와 동일하게 맞춘다. 두 경로로
    수집한 TLE를 하나의 캐시에서 구분 없이 쓰기 위해서다.

    Args:
        records: fetch_history가 반환한 레코드 리스트.
        cache_dir: 캐시 디렉토리.
        norad_id: 위성 카탈로그 번호.

    Returns:
        (새로 저장한 건수, 이미 있어 건너뛴 건수) 튜플.
    """
    saved = skipped = 0
    for rec in records:
        epoch = datetime.fromisoformat(rec["EPOCH"]).replace(tzinfo=timezone.utc)
        out = cache_dir / f"{norad_id}_{epoch:%Y%m%dT%H%M%S}.tle"
        if out.exists():
            skipped += 1
            continue
        body = "\n".join([
            rec.get("TLE_LINE0", f"0 {norad_id}"),
            rec["TLE_LINE1"],
            rec["TLE_LINE2"],
        ])
        out.write_text(body + "\n", encoding="utf-8")
        saved += 1
    return saved, skipped


def main():
    """설정된 모든 위성의 TLE 이력을 받아 캐시에 채운다."""
    parser = argparse.ArgumentParser(description="Space-Track TLE 이력 수집")
    parser.add_argument("--days", type=int, default=180, help="며칠 전까지 (기본 180)")
    args = parser.parse_args()

    load_dotenv()
    user = os.getenv("SPACETRACK_USER")
    password = os.getenv("SPACETRACK_PASS")
    if not user or not password:
        print("SPACETRACK_USER / SPACETRACK_PASS를 .env에 설정하라.")
        sys.exit(1)

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    cache_dir = Path(cfg["tle"]["cache_dir"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)

    with requests.Session() as session:
        login(session, user, password)
        print(f"로그인 성공. {since:%Y-%m-%d} 이후 이력을 수집한다.\n")

        for sat in cfg["satellites"]:
            name, norad_id = sat["name"], sat["norad_id"]
            records = fetch_history(session, norad_id, since)
            saved, skipped = save_records(records, cache_dir, norad_id)

            print(f"{name} ({norad_id})")
            print(f"  조회 {len(records)}건, 저장 {saved}건, 기존 {skipped}건")
            if records:
                first = datetime.fromisoformat(records[0]["EPOCH"])
                last = datetime.fromisoformat(records[-1]["EPOCH"])
                print(f"  범위: {first:%Y-%m-%d} ~ {last:%Y-%m-%d}")


if __name__ == "__main__":
    main()