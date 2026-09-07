"""TLE 세대 간 전파 편차를 측정한다.

같은 위성의 과거 TLE로 특정 시각을 전파한 결과와, 그 시각에 가까운 TLE가
말하는 위치를 비교한다. TLE 자체가 관측값에서 역산된 추정치이므로 이는
절대 정확도가 아니라 전파 기간에 따른 상대적 편차다.

라이브러리 간 대조를 쓰지 않는 이유:
    Skyfield는 내부적으로 sgp4 패키지를 호출하므로 두 결과는 항상 일치한다.
    같은 엔진을 두 이름으로 부르는 것이라 아무것도 검증하지 못한다.

성분 분해:
    편차 벡터를 궤도 진행 방향(along-track), 궤도면 수직(cross-track),
    고도 방향(radial)으로 나눈다. 기저는 기준 시각의 위치·속도 벡터에서
    만든다. radial은 위치 벡터 방향, cross-track은 위치와 속도의 외적 방향,
    along-track은 나머지 하나다. 세 축은 정규직교이므로 성분 제곱합이
    전체 거리의 제곱과 같다.

기동 구간 제외:
    비교하는 두 TLE 사이에 궤도 유지 기동이 있으면 측정값이 전파 오차가
    아니라 궤도 변경을 반영한다. maneuver 모듈이 식별한 구간에 걸치는 쌍은
    측정에서 제외한다.
"""
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
from skyfield.api import load

_TIMESCALE = None


def _timescale():
    """Skyfield 타임스케일을 지연 로딩해 재사용한다."""
    global _TIMESCALE
    if _TIMESCALE is None:
        _TIMESCALE = load.timescale()
    return _TIMESCALE


@dataclass(frozen=True)
class Deviation:
    """한 쌍의 TLE로 측정한 전파 편차.

    Attributes:
        lag_days: 두 TLE의 epoch 간격. 전파 기간에 해당한다.
        total_km: 두 위치의 3차원 거리.
        along_km: 궤도 진행 방향 성분. 부호는 앞서거나 뒤처짐을 뜻한다.
        cross_km: 궤도면 수직 성분.
        radial_km: 고도 방향 성분.
    """

    lag_days: float
    total_km: float
    along_km: float
    cross_km: float
    radial_km: float


def _rtn_basis(position_km, velocity_km_s):
    """위치·속도 벡터에서 radial/cross/along 정규직교 기저를 만든다.

    Args:
        position_km: 지구 중심 기준 위치 벡터 (3,).
        velocity_km_s: 같은 시각의 속도 벡터 (3,).

    Returns:
        (radial, cross, along) 단위벡터 튜플.

    Raises:
        ValueError: 위치와 속도가 평행해 궤도면을 정의할 수 없을 때.
            정상 궤도에서는 발생하지 않는다.
    """
    radial = position_km / np.linalg.norm(position_km)
    cross = np.cross(position_km, velocity_km_s)
    norm = np.linalg.norm(cross)
    if norm == 0:
        raise ValueError("위치와 속도가 평행하다. 궤도면을 정의할 수 없다.")
    cross = cross / norm
    along = np.cross(cross, radial)
    return radial, cross, along


def measure_pair(old_sat, new_sat, at_epoch):
    """TLE 두 세대로 같은 시각을 계산해 편차를 잰다.

    Args:
        old_sat: 이전 세대 EarthSatellite. 이것으로 at_epoch를 전파한다.
        new_sat: 이후 세대 EarthSatellite. 기준값 역할을 한다.
        at_epoch: 비교 시각. 통상 new_sat의 epoch를 쓴다.

    Returns:
        Deviation 인스턴스.
    """
    t = _timescale().from_datetime(at_epoch)

    old_state = old_sat.at(t)
    new_state = new_sat.at(t)

    old_pos = old_state.position.km
    new_pos = new_state.position.km
    new_vel = new_state.velocity.km_per_s

    delta = old_pos - new_pos
    radial, cross, along = _rtn_basis(new_pos, new_vel)

    lag = (at_epoch - old_sat.epoch.utc_datetime()).total_seconds() / 86400

    return Deviation(
        lag_days=lag,
        total_km=float(np.linalg.norm(delta)),
        along_km=float(np.dot(delta, along)),
        cross_km=float(np.dot(delta, cross)),
        radial_km=float(np.dot(delta, radial)),
    )


def build_pairs(satellites, target_lag_days, tolerance_days=0.3, maneuvers=()):
    """지정한 전파 기간에 해당하는 TLE 쌍을 고른다.

    Args:
        satellites: EarthSatellite 리스트. epoch 오름차순으로 정렬되어야 한다.
        target_lag_days: 원하는 전파 기간.
        tolerance_days: 허용 오차. TLE 발행 간격이 균일하지 않아 정확히
            N일 떨어진 쌍이 없을 수 있으므로 범위로 찾는다.
        maneuvers: ManeuverWindow 리스트. 구간에 걸치는 쌍은 제외한다.

    Returns:
        (이전 세대, 이후 세대) 튜플 리스트.
    """
    pairs = []
    epochs = [s.epoch.utc_datetime() for s in satellites]

    for j, new_epoch in enumerate(epochs):
        want = new_epoch - timedelta(days=target_lag_days)
        lo = want - timedelta(days=tolerance_days)
        hi = want + timedelta(days=tolerance_days)

        for i in range(j - 1, -1, -1):
            if epochs[i] < lo:
                break
            if epochs[i] > hi:
                continue
            if any(m.contains(epochs[i], new_epoch) for m in maneuvers):
                break
            pairs.append((satellites[i], satellites[j]))
            break

    return pairs