"""TLE 세대 간 전파 편차를 구간별로 측정해 보고한다.

config/mission.yaml의 모든 위성에 대해 캐시된 TLE 이력을 읽고, 궤도 유지
기동 구간을 식별한 뒤, 지정한 전파 기간마다 편차를 측정해 통계를 낸다.

측정 결과는 표로 출력하고 CSV로 저장한다. 그래프는 별도 스크립트에서
이 CSV를 읽어 그린다. 측정과 시각화를 분리하면 그림을 다시 그릴 때마다
수백 건의 전파를 반복하지 않아도 된다.

실행:
    python scripts/run_validate_propagation.py [--lags 1 3 7 14]
"""
import argparse
import csv
import statistics
from pathlib import Path

import yaml

from groundseg.fds.maneuver import detect_maneuvers, filter_uplift, parse_mean_motion
from groundseg.fds.propagator import load_tle
from groundseg.fds.validation import build_pairs, measure_pair

CONFIG = Path("config/mission.yaml")
OUT_CSV = Path("data/propagation_deviation.csv")


def load_history(cache_dir, norad_id):
    """해당 위성의 캐시된 TLE를 모두 읽어 epoch 순으로 반환한다.

    파일명의 epoch가 ISO 기본형이라 사전순 정렬이 곧 시간순 정렬이다.

    Args:
        cache_dir: TLE 캐시 디렉토리.
        norad_id: 위성 카탈로그 번호.

    Returns:
        (EarthSatellite 리스트, mean motion 리스트) 튜플. 두 리스트의
        순서는 대응한다.

    Raises:
        FileNotFoundError: 캐시가 없을 때.
    """
    paths = sorted(cache_dir.glob(f"{norad_id}_*.tle"))
    if not paths:
        raise FileNotFoundError(
            f"NORAD {norad_id}의 TLE 캐시가 없다. fetch_tle_history.py를 먼저 실행하라."
        )

    satellites, mean_motions = [], []
    for p in paths:
        sat = load_tle(p)
        satellites.append(sat)
        line2 = p.read_text(encoding="utf-8").splitlines()[2]
        mean_motions.append(parse_mean_motion(line2))
    return satellites, mean_motions


def summarize(values):
    """편차 값 목록의 대표값을 낸다.

    중앙값을 함께 내는 이유는 소수의 큰 값이 평균을 끌어올릴 수 있어서다.
    두 값이 크게 다르면 분포가 한쪽으로 치우쳤다는 신호다.

    Args:
        values: 측정값 리스트.

    Returns:
        (평균, 중앙값, 최댓값) 튜플. 빈 리스트면 모두 0.
    """
    if not values:
        return 0.0, 0.0, 0.0
    return statistics.mean(values), statistics.median(values), max(values)


def main():
    """모든 위성과 구간에 대해 편차를 측정하고 표와 CSV로 낸다."""
    parser = argparse.ArgumentParser(description="전파 편차 측정")
    parser.add_argument("--lags", type=float, nargs="+", default=[1, 3, 7, 14],
                        help="측정할 전파 기간 (일)")
    args = parser.parse_args()

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    cache_dir = Path(cfg["tle"]["cache_dir"])

    rows = []

    for sat_cfg in cfg["satellites"]:
        name, norad_id = sat_cfg["name"], sat_cfg["norad_id"]
        satellites, mean_motions = load_history(cache_dir, norad_id)
        epochs = [s.epoch.utc_datetime() for s in satellites]

        maneuvers = filter_uplift(detect_maneuvers(epochs, mean_motions))

        print(f"\n{'=' * 62}")
        print(f"{name} ({norad_id})")
        print(f"  TLE 세대 {len(satellites)}개  "
              f"{epochs[0]:%Y-%m-%d} ~ {epochs[-1]:%Y-%m-%d}")
        print(f"  궤도 상승 기동 {len(maneuvers)}건 식별, 해당 구간 제외")
        for m in maneuvers:
            print(f"    {m.start:%Y-%m-%d %H:%M} ~ {m.end:%Y-%m-%d %H:%M}"
                  f"   Δn={m.delta_mean_motion:+.3e}")

        print(f"\n  {'기간':>6} {'쌍':>5} "
              f"{'거리 평균':>10} {'중앙':>9} {'최대':>9} "
              f"{'|along|':>10} {'|cross|':>9} {'|radial|':>9}   (km)")
        print(f"  {'-' * 74}")

        for lag in args.lags:
            pairs = build_pairs(satellites, lag, maneuvers=maneuvers)
            devs = [measure_pair(old, new, new.epoch.utc_datetime())
                    for old, new in pairs]

            if not devs:
                print(f"  {lag:>5.0f}일 {0:>5}   측정 가능한 쌍 없음")
                continue

            total_mean, total_med, total_max = summarize([d.total_km for d in devs])
            along_mean, _, _ = summarize([abs(d.along_km) for d in devs])
            cross_mean, _, _ = summarize([abs(d.cross_km) for d in devs])
            radial_mean, _, _ = summarize([abs(d.radial_km) for d in devs])

            print(f"  {lag:>5.0f}일 {len(devs):>5} "
                  f"{total_mean:>10.3f} {total_med:>9.3f} {total_max:>9.3f} "
                  f"{along_mean:>10.3f} {cross_mean:>9.3f} {radial_mean:>9.3f}")

            for d in devs:
                rows.append({
                    "satellite": name,
                    "norad_id": norad_id,
                    "target_lag_days": lag,
                    "actual_lag_days": round(d.lag_days, 4),
                    "total_km": round(d.total_km, 6),
                    "along_km": round(d.along_km, 6),
                    "cross_km": round(d.cross_km, 6),
                    "radial_km": round(d.radial_km, 6),
                })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n측정 {len(rows)}건을 {OUT_CSV}에 저장했다.")


if __name__ == "__main__":
    main()