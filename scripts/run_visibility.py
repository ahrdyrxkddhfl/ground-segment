"""대상 위성의 지상국 가시성 윈도우를 계산해 출력한다.

각 위성의 최신 TLE로 지정 구간의 패스를 찾고, 서로 겹치는 패스를 경합으로
보고한다. 단일 안테나를 가정하므로 겹치는 패스는 둘 다 잡을 수 없다.

실행:
    python scripts/run_visibility.py [--hours 24]
"""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from groundseg.fds.propagator import load_tle
from groundseg.fds.visibility import find_conflicts, find_passes

MISSION = Path("config/mission.yaml")
STATION = Path("config/groundstation.yaml")


def latest_tle_path(cache_dir, norad_id):
    """해당 위성의 캐시 중 epoch가 가장 늦은 파일을 고른다.

    Args:
        cache_dir: TLE 캐시 디렉토리.
        norad_id: 위성 카탈로그 번호.

    Returns:
        가장 최근 TLE 파일 경로.

    Raises:
        FileNotFoundError: 캐시가 없을 때.
    """
    matches = sorted(cache_dir.glob(f"{norad_id}_*.tle"))
    if not matches:
        raise FileNotFoundError(
            f"NORAD {norad_id}의 TLE 캐시가 없다. run_fetch_tle.py를 먼저 실행하라."
        )
    return matches[-1]


def main():
    """모든 위성의 패스를 계산하고 경합과 함께 출력한다."""
    parser = argparse.ArgumentParser(description="가시성 윈도우 계산")
    parser.add_argument("--hours", type=int, default=24, help="탐색 기간 (기본 24)")
    args = parser.parse_args()

    mission = yaml.safe_load(MISSION.read_text(encoding="utf-8"))
    station_doc = yaml.safe_load(STATION.read_text(encoding="utf-8"))
    station = station_doc["station"]
    visibility = station_doc["visibility"]
    antenna = station_doc["antenna"]

    cache_dir = Path(mission["tle"]["cache_dir"])
    start = datetime.now(timezone.utc)
    end = start + timedelta(hours=args.hours)

    print(f"지상국: {station['name']} "
          f"({station['latitude_deg']:.4f}, {station['longitude_deg']:.4f})")
    print(f"최소 앙각: {visibility['min_elevation_deg']}°  "
          f"안테나: {antenna['count']}기")
    print(f"구간: {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M} UTC\n")

    all_passes = []

    for sat_cfg in mission["satellites"]:
        name, norad_id = sat_cfg["name"], sat_cfg["norad_id"]
        satellite = load_tle(latest_tle_path(cache_dir, norad_id))
        passes = find_passes(satellite, station, visibility, start, end)
        all_passes.extend(passes)

        total_seconds = sum(p.duration_seconds for p in passes)
        print(f"{name} ({norad_id})  패스 {len(passes)}회, "
              f"총 교신 가능 {total_seconds / 60:.1f}분")
        for p in passes:
            print(f"  {p.aos:%m-%d %H:%M:%S} ~ {p.los:%H:%M:%S}  "
                  f"{p.duration_seconds / 60:5.1f}분  "
                  f"최대앙각 {p.max_elevation_deg:5.1f}° "
                  f"({p.max_elevation_time:%H:%M:%S})")
        print()

    conflicts = find_conflicts(all_passes)
    if conflicts:
        print(f"안테나 경합 {len(conflicts)}건")
        for a, b in conflicts:
            print(f"  {a.satellite} {a.aos:%m-%d %H:%M:%S}~{a.los:%H:%M:%S}  vs  "
                  f"{b.satellite} {b.aos:%m-%d %H:%M:%S}~{b.los:%H:%M:%S}")
    else:
        print("안테나 경합 없음")


if __name__ == "__main__":
    main()