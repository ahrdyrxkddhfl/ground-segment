"""TLE 이력에서 궤도 유지 기동 구간을 식별한다.

전파 편차 검증은 서로 다른 세대의 TLE를 비교하는데, 그 사이에 궤도 유지
기동이 있었다면 측정값이 전파 오차가 아니라 기동으로 인한 궤도 변경을
반영하게 된다. 이 모듈은 그런 구간을 미리 골라내 검증에서 제외할 수 있게
한다.

식별 원리:
    mean motion은 하루당 공전수이며 궤도가 낮아질수록 커진다. 대기 저항은
    이 값을 서서히 증가시키고, 고도를 올리는 기동은 급격히 감소시킨다.
    따라서 이웃한 세대 간 변화량의 분포에서 크게 벗어난 지점이 기동 후보다.

    산포 기준으로 표준편차 대신 MAD(중앙값 절대편차)를 쓴다. 표준편차는
    찾으려는 이상치 자체에 끌려가 기준이 느슨해지는 반면, MAD는 중앙값
    기반이라 소수의 큰 값에 영향을 덜 받는다.

관측된 특성:
    KOMPSAT-5는 약 한 달 간격으로 mean motion이 -6e-4 ~ -9e-4만큼 감소하는
    구간이 나타난다. 규칙적 간격과 일관된 크기, 그리고 궤도를 높이는 방향이
    모두 궤도 유지 기동과 일치한다. KOMPSAT-3A는 같은 기간 감소 구간이 없고
    mean motion이 단조 증가하여, 고도 유지 없이 자연 감쇠 중인 것으로 보인다.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np


@dataclass(frozen=True)
class ManeuverWindow:
    """기동이 일어난 것으로 추정되는 두 TLE 세대 사이의 구간.

    구간의 양 끝은 관측된 TLE의 epoch이며, 실제 기동 시각은 그 사이
    어딘가다. TLE만으로는 더 좁힐 수 없다.

    Attributes:
        start: 기동 이전 마지막 TLE의 epoch.
        end: 기동 이후 첫 TLE의 epoch.
        delta_mean_motion: 두 세대 간 mean motion 변화량. 음수면 궤도가
            높아진 것이므로 고도 상승 기동에 해당한다.
    """

    start: datetime
    end: datetime
    delta_mean_motion: float

    def contains(self, t0, t1):
        """주어진 두 시각 사이에 이 기동 구간이 걸치는지 판정한다.

        Args:
            t0: 비교 구간의 시작.
            t1: 비교 구간의 끝.

        Returns:
            겹치면 True. 편차 측정에서 해당 TLE 쌍을 제외할지 판단하는 데 쓴다.
        """
        return not (t1 <= self.start or t0 >= self.end)


def parse_mean_motion(line2):
    """TLE 2행에서 mean motion(rev/day)을 읽는다.

    Args:
        line2: TLE 2행 문자열. 53-63 컬럼(0-기준 52-63)에 값이 있다.

    Returns:
        하루당 공전수.

    Raises:
        ValueError: 해당 컬럼이 숫자가 아닐 때.
    """
    return float(line2[52:63])


def detect_maneuvers(epochs, mean_motions, mad_threshold=10.0):
    """mean motion 변화 이상치로 기동 구간을 찾는다.

    Args:
        epochs: TLE epoch 리스트. 시간순 정렬되어 있어야 한다.
        mean_motions: 같은 순서의 mean motion 리스트.
        mad_threshold: 중앙값에서 MAD의 몇 배까지를 정상으로 볼지. 기본 10은
            관측 데이터에서 정상 감쇠와 기동이 명확히 갈리는 지점으로,
            KOMPSAT-5 기준 이 값에서 기동 7건이 분리되고 정상 구간은 남는다.
            낮추면 대기 밀도 변동까지 기동으로 잡히고, 높이면 작은 기동을 놓친다.

    Returns:
        ManeuverWindow 리스트. 시간순.

    Raises:
        ValueError: 두 리스트의 길이가 다르거나 표본이 3개 미만일 때.
    """
    if len(epochs) != len(mean_motions):
        raise ValueError(f"길이 불일치: epoch {len(epochs)}, mean motion {len(mean_motions)}")
    if len(epochs) < 3:
        raise ValueError(f"표본이 부족하다: {len(epochs)}개")

    mm = np.asarray(mean_motions, dtype=float)
    diff = np.diff(mm)
    median = np.median(diff)
    mad = np.median(np.abs(diff - median))

    if mad == 0:
        return []

    flagged = np.abs(diff - median) > mad_threshold * mad
    return [
        ManeuverWindow(
            start=epochs[i],
            end=epochs[i + 1],
            delta_mean_motion=float(diff[i]),
        )
        for i in np.where(flagged)[0]
    ]


def filter_uplift(windows):
    """궤도를 높이는 방향의 기동만 남긴다.

    mean motion 감소는 궤도 상승을 뜻하며 의도적 기동일 가능성이 높다.
    증가 방향의 이상치는 대기 밀도 급변이나 궤도요소 정정일 수 있어
    기동으로 단정하지 않는다.

    Args:
        windows: detect_maneuvers가 반환한 리스트.

    Returns:
        delta_mean_motion이 음수인 항목만 담은 리스트.
    """
    return [w for w in windows if w.delta_mean_motion < 0]