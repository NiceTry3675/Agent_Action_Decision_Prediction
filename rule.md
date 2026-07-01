# 코드 제출 대회 가이드

본 대회는 submit.zip 파일을 제출하는 방식의 '코드 제출 대회'로 진행됩니다.

참가자는 아래와 같은 구조로 submit.zip을 구성하여 제출해야 합니다.

## 📁 제출 파일 구조 (submit.zip)

```text
submit.zip
├── model/        # 모델 가중치 파일을 저장하는 디렉토리
│      └── (예: model.pt 등)
├── script.py       # 실제 추론이 수행되는 실행 코드
└── requirements.txt   # 필요한 패키지 및 버전 명시
```

script.py는 submit.zip을 제출 시 평가 서버에서 자동으로 실행됩니다.

requirements.txt는 pip install -r requirements.txt 명령어로 설치 가능한 형태여야 하며, 추론 시 필요한 모든 패키지를 포함해야 합니다.

submit.zip 내 구조는 반드시 일치해야하며 구조가 불일치하는 경우 설치 오류가 발생합니다.

## ⚙️ 평가 서버에서 추가되는 항목

제출 시, 평가 서버에서 참가자가 제출한 submit.zip 파일에는 아래 항목이 자동으로 추가됩니다.

```text
submit.zip
├── model/        # 참가자 구성
├── script.py       # 참가자 구성
├── requirements.txt   # 참가자 구성
├── data/         # 평가에 사용될 테스트 데이터 (디렉토리 자동 생성)
└── output/submission.csv        # 참가자 추론 결과가 저장되는 경로 (디렉토리 자동 생성)
```

data/ 디렉토리는 실제 평가 데이터를 포함한 경진대회 데이터가 포함되며, 읽기전용으로 쓰기 및 수정이 불가능한 디렉토리입니다.

output/ 디렉토리는 참가자의 script.py 실행 결과로 생성된 예측 결과 파일이 저장되는 디렉토리이며, 해당 디렉토리 내에 반드시 submission.csv으로 생성될 수 있어야합니다.

## ⚙️ 평가 서버 사양

- OS : Ubuntu 22.04.5 LTS
- GPU : NVIDIA T4 (VRAM 16GB)
- CPU: 3 vCPU
- CPU RAM: 12GB
- Python : 3.11.15
- 인터넷 접속: ❌ 비활성화 (패키지 설치 외 외부 서버 연결 및 다운로드 불가)
- CUDA : 12.8

## 💾 평가 서버 기본 설치 패키지(라이브러리) 목록

아래의 패키지(라이브러리)는 평가 서버에 기본적으로 설치되어 있으며, 버전이 명시된 아래의 패키지(라이브러리)에 한해서는 다른 버전을 사용할 때 설치 에러가 발생할 수 있으므로 가급적 평가 서버에 기본 설치된 패키지(라이브러리)를 활용하고 제출하는 requirements.txt에는 포함하지 않는 것을 권장드립니다.

라이브러리 설치 에러가 발생하면 설치 오류에 해당하며, 일일 제출 횟수에는 반영되지 않습니다.

### 1) 주요 설치 패키지(라이브러리)

```text
torch==2.7.1+cu128
pandas==2.0.3
numpy==1.26.4
scipy==1.15.3
scikit-learn==1.8.0
joblib==1.5.3
threadpoolctl==3.6.0
narwhals==2.21.2
transformers==4.46.3
accelerate==1.9.0
sentencepiece==0.1.99
regex==2023.12.25
tqdm==4.66.4
loguru==0.7.2
pyyaml==6.0.1
rich==13.7.1
```

### 2) 주요 설치 시스템 패키지

```text
git
build-essential
python3.11
python3.11-dev
python3.11-venv
python3-pip
libffi-dev
libblas3
liblapack3
libomp-dev
tzdata
unzip
p7zip-full
gfortran
libatlas-base-dev
default-jre-headless
cmake
pkg-config
ninja-build
libgl1
libglib2.0-0
```

## 유의 사항

- 1일 최대 제출 횟수: 10회
- 2026.07.01 ~ 2026.07.15
