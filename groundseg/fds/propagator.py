"""SGP4 기반 궤도 전파.

TLE를 입력받아 임의 시각의 위성 위치를 계산한다. 계산은 Skyfield에
위임하며, 이 모듈은 프로젝트 공용 타입과의 변환과 좌표계 선택만 담당한다.

좌표계에 관하여:
    SGP4는 TEME(True Equator Mean Equinox) 좌표계로 결과를 낸다. 지상국
    가시성처럼 지구에 붙은 관측자를 다루려면 지구와 함께 회전하는 좌표계가
    필요하므로, Skyfield가 제공하는 지리좌표(위도·경도·고도) 변환을 쓴다.
    좌표계를 직접 변환하지 않는 이유는 세차·장동 보정이 관측 목적에 비해
    과하고 오히려 오류 여지를 늘리기 때문이다.

정확도에 관하여:
    SGP4는 TLE와 짝을 이루도록 설계된 해석적 모델이며 J2 섭동과 대기 저항을
    포함한다. 다만 TLE 자체가 관측값에서 역산된 추정치이고 epoch에서 멀어질수록
    편차가 커진다. 이 편차의 크기는 validation 모듈에서 실측한다.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from skyfield.api import EarthSatellite, load, wgs84

_TIMESCALE = None


def _timescale():
    """Skyfield 타임스케일을 지연 로딩해 재사용한다.

    Skyfield의 timescale()은 최초 호출 시 윤초 데이터를 확인하고 필요하면
    내려받는다. 모듈 임포트 시점에 부작용이 생기지 않도록 첫 사용까지
    미루고, 이후에는 같은 객체를 재사용한다.

    Returns:
        skyfield.timelib.Timescale 인스턴스.
    """
    global _TIMESCALE
    if _TIMESCALE is None:
        _TIMESCALE = load.timescale()
    return _TIMESCALE


@dataclass(frozen=True)
class SubPoint:
    """특정 시각 위성의 지표 투영점과 고도.

    위성 바로 아래 지표면의 좌표를 뜻하며, 궤적을 지도에 그리거나 위성이
    지금 어느 지역 상공에 있는지 판단할 때 쓴다. 관측자 기준 방위각·앙각은
    지상국이 있어야 정해지므로 visibility 모듈에서 따로 다룬다.

    Attributes:
        time: 계산 시각. 항상 UTC.
        latitude_deg: 위도. 북위가 양수.
        longitude_deg: 경도. 동경이 양수.
        altitude_km: 지표면 기준 고도.
    """

    time: datetime
    latitude_deg: float
    longitude_deg: float
    altitude_km: float


def load_tle(path):
    """캐시된 TLE 파일을 읽어 Skyfield 위성 객체로 만든다.

    run_fetch_tle.py가 저장한 3행 형식을 전제한다. 첫 행은 카탈로그명이고
    나머지 두 행이 궤도요소다.

    Args:
        path: TLE 파일 경로.

    Returns:
        EarthSatellite 인스턴스. `.name`에 카탈로그명이, `.epoch`에 TLE
        기준 시각이 들어 있다.

    Raises:
        ValueError: 파일이 3행 형식이 아닐 때.
        FileNotFoundError: 파일이 없을 때.
    """
    lines = [ln.rstrip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) != 3:
        raise ValueError(f"TLE 파일이 3행이 아님: {path} ({len(lines)}행)")
    name, line1, line2 = lines
    return EarthSatellite(line1, line2, name, _timescale())


def subpoint_at(satellite, when):
    """단일 시각의 지표 투영점을 계산한다.

    Args:
        satellite: load_tle이 만든 EarthSatellite.
        when: 계산 시각. tzinfo가 있어야 하며 UTC로 변환해 사용한다.

    Returns:
        SubPoint 인스턴스.

    Raises:
        ValueError: when에 시간대 정보가 없을 때. 시간대 없는 시각을
            임의로 UTC로 간주하면 조용히 어긋나므로 명시적으로 거부한다.
    """
    if when.tzinfo is None:
        raise ValueError("시간대 정보가 없는 시각은 받지 않는다")

    when_utc = when.astimezone(timezone.utc)
    t = _timescale().from_datetime(when_utc)
    geocentric = satellite.at(t)
    sub = wgs84.subpoint(geocentric)

    return SubPoint(
        time=when_utc,
        latitude_deg=sub.latitude.degrees,
        longitude_deg=sub.longitude.degrees,
        altitude_km=sub.elevation.km,
    )


def subpoints_over(satellite, start, end, step_seconds=60):
    """구간을 일정 간격으로 나누어 지표 투영점을 계산한다.

    Args:
        satellite: load_tle이 만든 EarthSatellite.
        start: 구간 시작. tzinfo 필수.
        end: 구간 끝. tzinfo 필수. start보다 뒤여야 한다.
        step_seconds: 표본 간격. 기본 60초는 저궤도 위성이 약 7.5km를
            이동하는 거리에 해당하며, 궤적을 그리기에 충분하고 가시성
            윈도우의 시작과 끝을 대략 잡기에도 무리가 없다. 윈도우 경계를
            정밀하게 구할 때는 visibility 모듈에서 더 좁은 간격으로 다시
            훑는다.

    Returns:
        SubPoint 리스트. 시간순으로 정렬되어 있다.

    Raises:
        ValueError: 시간대 정보가 없거나 end가 start보다 앞설 때.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("시간대 정보가 없는 시각은 받지 않는다")
    if end <= start:
        raise ValueError(f"end가 start보다 앞선다: {start} ~ {end}")

    ts = _timescale()
    total = int((end - start).total_seconds())
    offsets = range(0, total + 1, step_seconds)

    start_utc = start.astimezone(timezone.utc)
    times = ts.utc(
        start_utc.year, start_utc.month, start_utc.day,
        start_utc.hour, start_utc.minute,
        [start_utc.second + o for o in offsets],
    )

    subs = wgs84.subpoint(satellite.at(times))
    lats = subs.latitude.degrees
    lons = subs.longitude.degrees
    alts = subs.elevation.km

    return [
        SubPoint(
            time=t.utc_datetime(),
            latitude_deg=float(lat),
            longitude_deg=float(lon),
            altitude_km=float(alt),
        )
        for t, lat, lon, alt in zip(times, lats, lons, alts)
    ]