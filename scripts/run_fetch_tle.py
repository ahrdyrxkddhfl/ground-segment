"""Celestrak에서 대상 위성의 TLE를 받아 캐시하고 신선도를 보고한다.

config/mission.yaml에 정의된 모든 위성에 대해 최신 TLE를 조회하고,
epoch 기준 경과일이 staleness_warn_days를 넘으면 경고한다.

TLE는 미 우주군이 궤도상 물체를 추적해 발행하므로 위성의 운용 종료 여부와
무관하게 계속 나온다. 다만 갱신 주기는 대상에 따라 달라지며, 갱신이 멈춘
위성은 FDS의 전파 편차 검증(과거 TLE로 전파한 결과를 최신 TLE와 비교)에
쓸 수 없다. 이 스크립트가 신선도를 보고하는 이유다.

받은 TLE는 epoch를 파일명에 넣어 캐시한다. 같은 위성의 서로 다른 시점
TLE를 누적해 두면 전파 편차 검증에서 그대로 입력으로 쓸 수 있다.

실행:
    python scripts/run_fetch_tle.py

종료 코드:
    0  모든 위성의 TLE가 신선함
    1  하나 이상이 오래되었거나 조회에 실패함
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode

import yaml

CONFIG = Path("config/mission.yaml")


def load_config(path=CONFIG):
    """임무 설정을 읽어 딕셔너리로 반환한다.

    Args:
        path: mission.yaml 경로. 기본값은 프로젝트 루트 기준 상대 경로이므로
            프로젝트 루트에서 실행해야 한다.

    Returns:
        satellites 리스트와 tle 설정을 담은 딕셔너리.

    Raises:
        FileNotFoundError: 설정 파일이 없을 때.
        yaml.YAMLError: 설정 파일 형식이 잘못되었을 때.
    """
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_tle(base_url, norad_id):
    """Celestrak GP API에서 단일 위성의 TLE 3행을 받아온다.

    Celestrak은 CATNR 질의에 대해 카탈로그명 1행과 궤도요소 2행,
    총 3행을 평문으로 응답한다. 존재하지 않는 카탈로그 번호에 대해서는
    오류 코드 대신 "No GP data found" 같은 안내 문구를 200으로 돌려주므로,
    응답이 TLE 형식인지 직접 확인해야 한다.

    Args:
        base_url: Celestrak GP API 엔드포인트.
        norad_id: 조회할 위성의 NORAD 카탈로그 번호.

    Returns:
        (카탈로그명, 1행, 2행) 튜플. 각 행은 개행이 제거된 상태다.

    Raises:
        ValueError: 응답이 TLE 형식이 아닐 때. 카탈로그 번호가 틀렸거나
            해당 물체가 카탈로그에서 제외된 경우다.
        urllib.error.URLError: 네트워크 오류나 타임아웃.
    """
    query = urlencode({"CATNR": norad_id, "FORMAT": "TLE"})
    with urlopen(f"{base_url}?{query}", timeout=30) as resp:
        text = resp.read().decode("utf-8")

    lines = [ln.rstrip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 3 or not lines[1].startswith("1 "):
        raise ValueError(f"TLE 형식이 아님 (NORAD {norad_id}): {text[:120]}")
    return lines[0], lines[1], lines[2]


def parse_epoch(line1):
    """TLE 1행에서 epoch를 읽어 UTC datetime으로 변환한다.

    TLE의 epoch는 1행 19-32 컬럼(0-기준 18-32)에 YYDDD.DDDDDDDD 형식으로
    들어 있다. YY는 두 자리 연도, DDD.DDDDDDDD는 그 해의 통일 일수로
    1월 1일이 1.0이다. 따라서 1월 1일 자정을 기준으로 (일수 - 1)을 더해야
    실제 시각이 된다.

    두 자리 연도는 57을 기준으로 세기를 나눈다. 57 이상이면 1900년대,
    미만이면 2000년대다. 최초의 인공위성이 1957년에 발사되었기 때문에
    NORAD가 정한 관례이며, 2057년까지 유효하다.

    Args:
        line1: TLE 1행 문자열. 최소 32자 이상이어야 한다.

    Returns:
        UTC 시간대가 붙은 datetime.

    Raises:
        ValueError: 해당 컬럼이 숫자가 아닐 때.
        IndexError: 문자열이 32자보다 짧을 때.
    """
    field = line1[18:32]
    year = int(field[:2])
    year += 2000 if year < 57 else 1900
    day_of_year = float(field[2:])
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_of_year - 1)


def main():
    """설정에 정의된 모든 위성의 TLE를 받아 캐시하고 신선도를 출력한다.

    위성 하나의 조회가 실패해도 나머지는 계속 처리한다. 부분 실패를 전체
    실패로 만들지 않기 위한 것이며, 실패한 위성은 신선도 미달과 함께
    묶어 마지막에 보고한다.

    캐시 파일명은 `{norad_id}_{epoch}.tle` 형식이라 같은 위성의 여러 시점
    TLE가 덮어쓰이지 않고 누적된다.
    """
    cfg = load_config()
    base_url = cfg["tle"]["source_url"]
    cache_dir = Path(cfg["tle"]["cache_dir"])
    warn_days = cfg["tle"].get("staleness_warn_days", 7)
    cache_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    stale = []

    for sat in cfg["satellites"]:
        name, norad_id = sat["name"], sat["norad_id"]
        try:
            l0, l1, l2 = fetch_tle(base_url, norad_id)
        except Exception as exc:
            print(f"[FAIL] {name} ({norad_id}): {exc}")
            stale.append(name)
            continue

        epoch = parse_epoch(l1)
        age = (now - epoch).total_seconds() / 86400

        out = cache_dir / f"{norad_id}_{epoch:%Y%m%dT%H%M%S}.tle"
        out.write_text("\n".join([l0, l1, l2]) + "\n", encoding="utf-8")

        flag = "STALE" if age > warn_days else "OK"
        print(f"[{flag}] {name} ({norad_id})")
        print(f"       카탈로그명: {l0}")
        print(f"       epoch: {epoch:%Y-%m-%d %H:%M:%S} UTC  ({age:.1f}일 전)")
        print(f"       저장: {out}")

        if age > warn_days:
            stale.append(name)

    if stale:
        print(f"\n갱신이 지연된 위성: {', '.join(stale)}")
        sys.exit(1)
    print("\n모든 위성의 TLE가 최신 상태다.")


if __name__ == "__main__":
    main()