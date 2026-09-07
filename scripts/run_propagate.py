"""대상 위성의 궤적을 계산해 출력한다.

config/mission.yaml의 모든 위성에 대해 최신 캐시 TLE를 찾아 지정 구간의
지표 투영점을 계산한다. FDS가 실제로 도는지 눈으로 확인하기 위한 스크립트다.

실행:
    python scripts/run_propagate.py [--hours 24] [--step 300]
"""
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

from groundseg.fds.propagator import load_tle, subpoints_over

CONFIG = Path("config/mission.yaml")


def latest_tle_path(cache_dir, norad_id):
    """해당 위성의 캐시 중 epoch가 가장 늦은 파일을 고른다.

    파일명이 `{norad_id}_{epoch}.tle` 형식이고 epoch가 ISO 기본형이라
    사전순 정렬이 곧 시간순 정렬이 된다.

    Args:
        cache_dir: TLE 캐시 디렉토리.
        norad_id: 위성 카탈로그 번호.

    Returns:
        가장 최근 TLE 파일 경로.

    Raises:
        FileNotFoundError: 해당 위성의 캐시가 하나도 없을 때.
            run_fetch_tle.py를 먼저 실행해야 한다.
    """
    matches = sorted(cache_dir.glob(f"{norad_id}_*.tle"))
    if not matches:
        raise FileNotFoundError(
            f"NORAD {norad_id}의 TLE 캐시가 없다. run_fetch_tle.py를 먼저 실행하라."
        )
    return matches[-1]


def main():
    """설정된 모든 위성의 궤적을 계산해 요약과 표본을 출력한다."""
    parser = argparse.ArgumentParser(description="위성 궤적 계산")
    parser.add_argument("--hours", type=int, default=24, help="전파할 시간 (기본 24)")
    parser.add_argument("--step", type=int, default=300, help="표본 간격 초 (기본 300)")
    args = parser.parse_args()

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    cache_dir = Path(cfg["tle"]["cache_dir"])

    start = datetime.now(timezone.utc)
    end = start + timedelta(hours=args.hours)

    for sat_cfg in cfg["satellites"]:
        name, norad_id = sat_cfg["name"], sat_cfg["norad_id"]
        tle_path = latest_tle_path(cache_dir, norad_id)
        satellite = load_tle(tle_path)

        points = subpoints_over(satellite, start, end, args.step)
        alts = [p.altitude_km for p in points]

        print(f"\n{name} ({norad_id})")
        print(f"  TLE epoch: {satellite.epoch.utc_datetime():%Y-%m-%d %H:%M:%S} UTC")
        print(f"  구간: {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M} UTC")
        print(f"  표본 {len(points)}개, 간격 {args.step}초")
        print(f"  고도: 최저 {min(alts):.1f} km, 최고 {max(alts):.1f} km")
        print("  처음 3개 표본:")
        for p in points[:3]:
            print(
                f"    {p.time:%H:%M:%S}  "
                f"위도 {p.latitude_deg:+7.2f}°  "
                f"경도 {p.longitude_deg:+8.2f}°  "
                f"고도 {p.altitude_km:.1f} km"
            )


if __name__ == "__main__":
    main()