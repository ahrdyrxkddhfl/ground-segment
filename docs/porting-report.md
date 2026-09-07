# kpi-sentinel 이식 기록

선행 프로젝트 [kpi-sentinel](https://github.com/ahrdyrxkddhfl/kpi-sentinel)의
탐지 코어를 위성 텔레메트리 도메인으로 옮기는 과정을 기록한다.

원래 주장은 `sentinel/`이 도메인 용어를 포함하지 않으며 `config/`의 YAML
교체만으로 다른 도메인에 적용된다는 것이었다. 그 주장이 실제로 성립하는지,
어디까지 성립하는지를 확인한다.

검증 대상: kpi-sentinel v0.1.0 (54ac9bd)

---

## 1. 실행 컨텍스트 결합 여부

### 확인할 것

도메인 용어가 없다는 것과 다른 프로젝트에서 쓸 수 있다는 것은 다르다. 코어가
설정 파일 경로를 직접 알고 있으면 패키지로 설치했을 때 작업 디렉토리가
달라지며 깨진다. 도메인 용어와 별개로 실행 컨텍스트 결합을 확인했다.

### 결과

    $ grep -rn "config/" sentinel/
    (결과 없음)

    $ grep -rn "open(\|safe_load\|Path(" sentinel/
    sentinel/stream.py:26:    with Path(path).open(newline="") as f:
    sentinel/storage.py:28:        Path(path).parent.mkdir(parents=True, exist_ok=True)

두 건 모두 경로를 인자로 받는 형태이며 코어가 위치를 아는 곳은 없다. 설정
로딩 책임은 이미 `scripts/`에 있었으므로 리팩터링 없이 이식할 수 있었다.

원래 주장보다 나은 결과다. 주장은 도메인 용어의 부재였는데 실행 컨텍스트
결합도 없었다.

---

## 2. 패키지 설치 검증

### 방법

코드 복사가 아니라 패키지 의존성 설치로 이식한다. 복사하면 재사용 가능성이
주장으로만 남지만, 설치하면 성립 여부가 빌드에서 드러난다.

kpi-sentinel에 `pyproject.toml`을 추가하고 `sentinel` 패키지만 배포 대상으로
지정했다. 코어 의존성은 numpy, pandas, pyyaml로 한정하고 storage / plot /
stream / report는 optional로 분리했다. ground-segment는 탐지 코어만 쓰므로
Kafka 클라이언트나 LLM API 클라이언트가 필요 없다.

### 결과

    $ pip install "git+https://github.com/ahrdyrxkddhfl/kpi-sentinel.git@v0.1.0"
    Resolved ... to commit 54ac9bdd45cdb9db4f200187be58e5810e1a4fdc
    Successfully installed kpi-sentinel-0.1.0 numpy-2.4.6 pandas-3.0.5
      python-dateutil-2.9.0.post0 pyyaml-6.0.3 six-1.17.0

    $ python -c "import sentinel; print(sentinel.__file__)"
    .../ground-segment/.venv/lib/python3.11/site-packages/sentinel/__init__.py

설치 경로가 `site-packages` 아래이므로 옆 디렉토리를 참조한 것이 아니라
원격에서 받아 설치한 독립 패키지다.

confluent-kafka, anthropic, duckdb가 설치되지 않아 optional 분리도 의도대로
동작했다. 버전은 태그로 고정했으므로 kpi-sentinel을 이후에 수정해도
ground-segment는 영향받지 않는다.

---

## 3. 위성 텔레메트리 적용

*(진행 예정)*

1·2절은 "옮길 수 있는가"를 확인했다. 이 절은 "옮겨서 쓸모가 있는가"를 다룬다.

예상되는 쟁점은 데이터 특성 차이다. kpi-sentinel의 탐지기는 요일×시간대
버킷으로 계절성을 학습하는데, 이는 이동통신망 트래픽이 사람의 생활 주기를
따른다는 전제에 기댄다. 위성 텔레메트리에는 그런 주기가 없고 대신 궤도
주기와 일조 조건에 따른 주기가 있다. 또한 실제 텔레메트리는 데이터 결손이
잦고 샘플링 주기가 중간에 바뀐다.

성립하지 않는 가정과 그 대응을 여기에 기록한다.
