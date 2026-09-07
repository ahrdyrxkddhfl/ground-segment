# 트러블슈팅

막혔던 지점과 해결 과정을 기록한다. 여러 선택지 중 하나를 고른 판단은
[decisions.md](decisions.md)에 따로 둔다.

---

## groundseg 모듈을 찾지 못함

### 증상

    $ python scripts/run_validate_propagation.py
    Traceback (most recent call last):
      File "scripts/run_validate_propagation.py", line 20, in <module>
        from groundseg.fds.maneuver import detect_maneuvers, ...
    ModuleNotFoundError: No module named 'groundseg'

`groundseg/` 디렉토리는 프로젝트 루트에 있고 `__init__.py`도 존재했다.

### 원인

파이썬은 실행한 스크립트가 위치한 디렉토리를 모듈 검색 경로에 넣는다.
`scripts/run_validate_propagation.py`를 실행하면 `scripts/` 안에서
`groundseg`를 찾으므로 루트에 있는 패키지가 보이지 않는다.

`run_fetch_tle.py`가 문제없이 돌았던 것은 그 스크립트가 표준 라이브러리와
`yaml`만 쓰고 `groundseg`를 참조하지 않았기 때문이다. 첫 내부 모듈 참조에서
드러난 문제다.

### 해결

`pyproject.toml`을 추가해 `groundseg`를 설치 가능한 패키지로 만들고 편집
모드로 설치했다.

    pip install -e .

`sys.path` 조작이나 `PYTHONPATH` 지정으로도 우회할 수 있으나 패키지화를
택했다. 이 프로젝트의 핵심 주장이 "kpi-sentinel을 패키지로 이식한다"인데
정작 자신은 패키지가 아니면 일관성이 없다. 또한 `tests/`에서 같은 문제가
반복될 것이고 pytest도 설치된 패키지를 기준으로 import한다.

### 부수 사고

처음 설치할 때 작업 디렉토리가 `docs/`였다. `pip install -e .`의 `.`이
현재 디렉토리를 뜻하므로 `docs/ground_segment.egg-info/`가 생성되었다.
디렉토리를 지우고 프로젝트 루트에서 다시 설치했다.

`.gitignore`에 `*.egg-info/`와 `__pycache__/`를 추가해 빌드 산출물이
추적되지 않도록 했다. kpi-sentinel 쪽에는 이미 넣어두었으나 이 저장소에는
빠져 있었다.

---

## Space-Track 이력 수집에서 조회 건수와 저장 건수가 불일치

### 증상

    KOMPSAT-5 (39227)
      조회 754건, 저장 723건, 기존 31건
    KOMPSAT-3A (40536)
      조회 759건, 저장 711건, 기존 48건

직전에 `run_fetch_tle.py`로 받은 TLE는 위성당 1건뿐인데 "기존"이 수십 건으로
집계되었다.

### 추정 원인

캐시 파일명이 `{norad_id}_{epoch}.tle` 형식이고 epoch를 초 단위까지 표현한다.
같은 시각에 발행된 복수 레코드가 하나의 파일명으로 뭉쳐 뒤에 온 것이
`out.exists()`에서 걸러진 것으로 보인다.

### 처리

편차 측정에 영향이 없다고 보고 진행했다. 남은 표본이 위성당 700세대 이상이며
구간별로 수백 쌍을 확보할 수 있다.

다만 이는 확인이 아니라 추정이다. 실제 파일 수를 세어 조회 건수와 대조하면
검증할 수 있으며, 원인이 다르다면 다른 곳에서 문제가 될 수 있다.

---

## 탐색용 스크립트를 모듈로 승격

### 상황

성분 분해를 할지 판단하려고 TLE 이력의 mean motion 변화를 훑는 스크립트를
만들었다. 일회성 탐색 목적이었다.

### 판단

결과를 보니 기동 식별이 편차 측정의 필수 전처리였다. 기동 구간을 포함한 TLE
쌍은 전파 오차가 아니라 궤도 변경을 재게 되므로, 측정할 때마다 제외해야 한다.
일회성 스크립트로 두면 매번 눈으로 확인해 수동으로 걸러야 한다.

### 처리

`groundseg/fds/maneuver.py`로 옮기고 `ManeuverWindow` 타입과 구간 겹침 판정을
추가했다. `validation.build_pairs`가 이를 받아 오염된 쌍을 자동으로 제외한다.

탐색용 스크립트가 그대로 제품 코드가 되는 경우가 있다. 결과를 보고 결정하는
것이 먼저이고, 승격 여부는 그 다음이다.
