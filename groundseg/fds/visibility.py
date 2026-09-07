"""지상국 기준 위성 가시성 윈도우를 계산한다.

위성이 지상국에서 보이는 구간(패스)을 찾고, 각 패스의 시작과 끝, 최대
앙각, 지속시간을 낸다. 지상 관제는 이 구간에만 교신할 수 있으므로 모든
임무 계획의 출발점이 된다.

용어:
    AOS(Acquisition of Signal)는 위성이 최소 앙각 위로 올라와 교신이
    시작되는 시각이고, LOS(Loss of Signal)는 다시 내려가 끊기는 시각이다.
    최대 앙각은 그 사이 위성이 가장 높이 뜬 각도이며, 클수록 거리가 가깝고
    교신 여건이 좋다.

최소 앙각에 관하여:
    지평선(0도)이 아니라 5도 안팎을 기준으로 삼는 것이 통례다. 낮은 앙각은
    전파가 통과하는 대기 경로가 길어 감쇠가 크고, 지형과 건물에 가려질
    가능성도 높다. 값은 config로 뺀다.

탐색 방법:
    앙각은 시간에 대해 연속이지만 해석적으로 풀기 번거로우므로 두 단계로
    훑는다. 먼저 성긴 간격으로 앙각의 부호가 바뀌는 구간을 찾고, 그 구간
    안에서만 촘촘한 간격으로 경계를 좁힌다. 전 구간을 1초 간격으로 훑으면
    24시간에 86400회 계산이 필요하지만, 2단계 방식은 그 수십 분의 일로
    같은 정밀도를 얻는다.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from skyfield.api import load, wgs84

_TIMESCALE = None


def _timescale():
    """Skyfield 타임스케일을 지연 로딩해 재사용한다."""
    global _TIMESCALE
    if _TIMESCALE is None:
        _TIMESCALE = load.timescale()
    return _TIMESCALE


@dataclass(frozen=True)
class Pass:
    """위성이 지상국에서 보이는 한 구간.

    Attributes:
        satellite: 위성 이름.
        aos: 최소 앙각을 넘어서는 시각. UTC.
        los: 최소 앙각 아래로 내려가는 시각. UTC.
        max_elevation_deg: 구간 내 최대 앙각.
        max_elevation_time: 최대 앙각에 도달한 시각. UTC.

    Notes:
        duration_seconds는 저장하지 않고 필요할 때 계산한다. aos와 los에서
        유도되는 값을 따로 들고 있으면 어긋날 여지가 생긴다.
    """

    satellite: str
    aos: datetime
    los: datetime
    max_elevation_deg: float
    max_elevation_time: datetime

    @property
    def duration_seconds(self):
        """패스 지속시간(초)."""
        return (self.los - self.aos).total_seconds()

    def overlaps(self, other):
        """다른 패스와 시간이 겹치는지 판정한다.

        안테나가 하나뿐인 지상국에서 겹치는 두 패스는 동시에 잡을 수 없다.
        MPS가 배정 충돌을 판단할 때 쓴다.

        Args:
            other: 비교할 Pass.

        Returns:
            겹치면 True.
        """
        return not (self.los <= other.aos or self.aos >= other.los)


def _make_observer(station_cfg):
    """설정에서 지상국 관측자 객체를 만든다.

    Args:
        station_cfg: latitude_deg, longitude_deg, elevation_m 키를 가진 딕셔너리.

    Returns:
        Skyfield의 지오이드 위치 객체.
    """
    return wgs84.latlon(
        latitude_degrees=station_cfg["latitude_deg"],
        longitude_degrees=station_cfg["longitude_deg"],
        elevation_m=station_cfg.get("elevation_m", 0.0),
    )


def _elevations(satellite, observer, times):
    """여러 시각의 앙각을 한 번에 계산한다.

    Skyfield는 시각 배열을 받아 벡터로 처리하므로 시각마다 호출하는 것보다
    훨씬 빠르다. 패스 탐색은 수천 회의 앙각 계산을 요구하므로 이 차이가 크다.

    Args:
        satellite: EarthSatellite.
        observer: 지상국 위치.
        times: Skyfield Time 배열.

    Returns:
        앙각(도) 배열.
    """
    difference = satellite - observer
    topocentric = difference.at(times)
    altitude, _, _ = topocentric.altaz()
    return altitude.degrees


def _refine_crossing(satellite, observer, before, after, min_elevation, step_seconds):
    """앙각이 기준선을 넘는 정확한 시각을 좁힌다.

    성긴 탐색에서 부호가 바뀐 두 시각 사이를 촘촘한 간격으로 다시 훑어
    경계에 가장 가까운 시각을 찾는다.

    Args:
        satellite: EarthSatellite.
        observer: 지상국 위치.
        before: 기준선 반대편의 시각.
        after: 기준선을 넘은 뒤의 시각.
        min_elevation: 기준 앙각(도).
        step_seconds: 세밀 탐색 간격.

    Returns:
        기준선을 넘는 시각. UTC.
    """
    ts = _timescale()
    total = int((after - before).total_seconds())
    if total <= 0:
        return before

    candidates = [before + timedelta(seconds=s) for s in range(0, total + 1, step_seconds)]
    times = ts.from_datetimes(candidates)
    elevations = _elevations(satellite, observer, times)

    rising = elevations[-1] > elevations[0]
    above = elevations >= min_elevation
    indices = np.where(above)[0] if rising else np.where(~above)[0]

    if len(indices) == 0:
        return after
    return candidates[int(indices[0])]


def find_passes(satellite, station_cfg, visibility_cfg, start, end):
    """구간 내 모든 가시성 윈도우를 찾는다.

    Args:
        satellite: EarthSatellite. `.name`이 Pass에 기록된다.
        station_cfg: 지상국 위치 설정.
        visibility_cfg: min_elevation_deg, coarse_step_seconds,
            fine_step_seconds 키를 가진 딕셔너리.
        start: 탐색 시작. tzinfo 필수.
        end: 탐색 끝. tzinfo 필수.

    Returns:
        Pass 리스트. AOS 기준 시간순 정렬.

    Raises:
        ValueError: 시간대 정보가 없거나 end가 start보다 앞설 때.

    Notes:
        구간 경계에 걸친 패스는 잘린 채로 반환된다. 탐색 시작 시점에 이미
        위성이 떠 있으면 start가 AOS가 되고, 끝날 때 아직 떠 있으면 end가
        LOS가 된다. 이 경우 최대 앙각은 관측된 범위 안에서의 값이다.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("시간대 정보가 없는 시각은 받지 않는다")
    if end <= start:
        raise ValueError(f"end가 start보다 앞선다: {start} ~ {end}")

    min_elev = visibility_cfg["min_elevation_deg"]
    coarse = visibility_cfg["coarse_step_seconds"]
    fine = visibility_cfg["fine_step_seconds"]

    observer = _make_observer(station_cfg)
    ts = _timescale()

    start_utc = start.astimezone(timezone.utc)
    total = int((end - start).total_seconds())
    samples = [start_utc + timedelta(seconds=s) for s in range(0, total + 1, coarse)]
    times = ts.from_datetimes(samples)
    elevations = _elevations(satellite, observer, times)
    above = elevations >= min_elev

    passes = []
    i = 0
    n = len(samples)

    while i < n:
        if not above[i]:
            i += 1
            continue

        # 성긴 표본에서 가시 구간의 시작과 끝 인덱스를 잡는다
        first = i
        while i < n and above[i]:
            i += 1
        last = i - 1

        # 앞뒤 경계를 세밀하게 좁힌다. 구간 끝에 걸리면 그대로 둔다
        if first == 0:
            aos = samples[0]
        else:
            aos = _refine_crossing(
                satellite, observer, samples[first - 1], samples[first], min_elev, fine
            )

        if last == n - 1:
            los = samples[-1]
        else:
            los = _refine_crossing(
                satellite, observer, samples[last], samples[last + 1], min_elev, fine
            )

        # 최대 앙각은 확정된 구간 안에서 다시 훑어 구한다
        span = int((los - aos).total_seconds())
        peak_samples = [aos + timedelta(seconds=s) for s in range(0, span + 1, fine)]
        peak_elevations = _elevations(satellite, observer, ts.from_datetimes(peak_samples))
        peak_index = int(np.argmax(peak_elevations))

        passes.append(
            Pass(
                satellite=satellite.name,
                aos=aos,
                los=los,
                max_elevation_deg=float(peak_elevations[peak_index]),
                max_elevation_time=peak_samples[peak_index],
            )
        )

    return passes


def find_conflicts(passes):
    """서로 겹치는 패스 쌍을 찾는다.

    단일 안테나 지상국에서 두 위성의 패스가 겹치면 하나만 잡을 수 있다.
    MPS가 해결해야 할 경합 목록이 된다.

    Args:
        passes: 여러 위성의 Pass가 섞인 리스트.

    Returns:
        겹치는 (Pass, Pass) 튜플 리스트. AOS 기준 시간순.
    """
    ordered = sorted(passes, key=lambda p: p.aos)
    conflicts = []
    for i, first in enumerate(ordered):
        for second in ordered[i + 1:]:
            if second.aos >= first.los:
                break
            if first.satellite != second.satellite and first.overlaps(second):
                conflicts.append((first, second))
    return conflicts