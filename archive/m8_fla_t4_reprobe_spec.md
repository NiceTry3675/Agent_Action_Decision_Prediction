# M8 fla 커널 T4 재프로브 스펙 v2 — 서버 복제 스택 (rule.md 반영)

실행 에이전트용. 배경 플랜: `qwen-gleaming-waterfall.md`.
v1(2026-07-05, fla-core 단독/콜랩 기본 스택) 결과는 RED_timing_failed였으나,
그 결론의 층위 1(휠 부재)·2(fla 커널 T4 적대성) 부분은 **콜랩 py3.12/torch2.11
스택 한정**이었음이 rule.md 공개로 확인됨. 이 v2는 서버와 동일한 스택에서
fast path를 실제로 켜고 진짜 ratio를 실측한다.

## 전제 변화 (v1 이후)

1. **서버 환경 확정** (rule.md): T4, **python 3.11, torch 2.7.1+cu128**,
   transformers 4.46.3 프리인스톨(pip 오버라이드 허용 — M7 검증),
   build-essential/cmake/ninja 있음, **nvcc 목록에 없음**(소스 빌드 불가 가정).
2. **causal-conv1d 프리빌트 휠 존재 확인** (GitHub API, v1.6.2.post1,
   2026-05-09 릴리스):
   - 서버용: `causal_conv1d-1.6.2.post1+cu12torch2.7cxx11abiTRUE-cp311-cp311-linux_x86_64.whl`
   - 콜랩 복제용(py3.12): 동일 태그의 `...-cp312-cp312-linux_x86_64.whl`
   - URL 베이스: `https://github.com/Dao-AILab/causal-conv1d/releases/download/v1.6.2.post1/`
   - torch 2.7 공식 휠은 cxx11abi TRUE — 일치.
3. **품질 게이트 GREEN**: G4 스크린 ep3 fixed 2stage `0.780427`
   (0.6B 스크린 0.770875 대비 +0.0096), 이득이 약클래스에 집중
   (list_directory +0.042, web_search +0.028, grep_search +0.018).
   E=3 확정, 3-fold OOF 진행 중 — 이 프로브는 그와 병렬로 무료 T4에서 수행.
4. v1의 fla-core 단독 실패 원인: transformers qwen3.5의 fast path는
   `fla`와 `causal_conv1d` **둘 다** 있어야 켜짐(`all(...)` 게이트).
   v2는 둘 다 설치한다.

## 환경 — 콜랩 T4에서 서버 복제

```
pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cu128
pip install "transformers>=5.13,<5.14"
pip install https://github.com/Dao-AILab/causal-conv1d/releases/download/v1.6.2.post1/causal_conv1d-1.6.2.post1+cu12torch2.7cxx11abiTRUE-cp312-cp312-linux_x86_64.whl
pip install fla-core   # 아래 버전 규칙 참조
```

- 콜랩 py3.12 + cp312 휠로 측정해도 무방 — 커널 타이밍은 GPU(SM75)·torch·
  triton이 결정하고 python 마이너 버전은 무관. **서버 제출용 핀은 cp311 URL.**
- torch 2.7.1은 triton 3.3.x를 동반. fla-core 최신(0.5.1)이 torch>=2.7.0
  하한이라 통과하지만 **triton 3.3과의 호환은 미검증** — import 실패 시
  fla-core 버전을 한 단계씩 내려 import가 되는 최신 버전을 채택, 기록.
- torch 재설치 후 세션 재시작 필요할 수 있음(콜랩 프리로드 torch와 충돌 주의).
- 각 단계에서 `pip freeze` 관련 라인과 버전 조합을 아티팩트에 기록.

## Step 1 — fast path 활성 확인

- 모델 로드(`igorktech/Qwen3.5-0.8B-Base-LM`, SeqCls num_labels=14, fp16)
  시 warnings/logging 캡처.
- **판정: "fast path is not available ... falling back" 경고가 사라져야 진행.**
  둘 다 깔았는데도 남으면 어떤 심볼이 None인지(transformers qwen3_5 modeling의
  가용성 플래그) 확인해 기록 후 중단(RED_fastpath).

## Step 2 — 정합성 게이트 (타이밍보다 먼저, 필수)

- v1과 동일: 고정 seed 256행, 저장해 둔 동일 랜덤 헤드 state_dict로
  폴백 경로(패키지 제거 또는 강제 폴백) vs fast path 로짓 비교.
- 통과: argmax 일치율 ≥ 99.5%, max|Δ| 분포가 fp16 오차 수준(요약 기록).
- 실패 시 fla-core 버전 교체 1회 재시도(#792류 버그 가능성), 그래도
  실패면 RED_correctness.

## Step 3 — 타이밍 실측

- v1 프로토콜 유지: train.jsonl seed42 4,096행, current_v1, script.py
  길이정렬 배치 재현, batch64 len400, **웜업 포함이 공식 수치**
  (서버는 콜드 스타트, Triton JIT 포함). steady-state(첫 4배치 제외) 별도 기록.
- **분모(Qwen3-0.6B len416 batch64)도 같은 세션·같은 torch 2.7.1 스택에서
  재측정** — 이 값이 서버 530s에 대응하는 앵커. v1 대비 분모 변화도 기록.
- fla Triton 커널은 shape별 재컴파일 우려(v1에서 배치당 19s의 유력 원인)가
  있으므로 **두 가지 배치 모드 모두 측정**:
  a. 길이정렬 가변 shape (현행 script.py 방식)
  b. 고정 패딩 len400 (shape 1개 → 컴파일 1회; 패딩 비용과 재컴파일 절감의
     트레이드오프 실측)
- 산출: ratio = qwen35 / qwen3 (tokenize+infer, 모드별), projected = ratio × 530.

## Step 4 — 조건부 그리드 (YELLOW 밴드에서만)

- projected 510~600s일 때만: batch {64, 96, 128} × len {400, 336} ×
  배치 모드 {정렬, 고정, 버킷 2-3개} 중 유망 조합 4~6개.

## 판정 및 후속

- **GREEN** (projected ≤ 510s + 정합성 통과): `requirements_qwen35.txt` 구성 —
  ```
  transformers>=5.13,<5.14
  fla-core==<검증 버전>
  causal-conv1d @ https://github.com/Dao-AILab/causal-conv1d/releases/download/v1.6.2.post1/causal_conv1d-1.6.2.post1+cu12torch2.7cxx11abiTRUE-cp311-cp311-linux_x86_64.whl
  safetensors==0.8.0
  scikit-learn==1.8.0
  joblib==1.5.3
  ```
  주의: 서버 pip이 github.com에 접근 가능한지는 미확인(설치 실패는 슬롯
  무소모이므로 감수 가능한 프로브). script.py가 고정/버킷 패딩 모드를 요구하면
  그 변경은 별도 승인 후 진행. 이후 Phase 2(OOF 완료 → refit → 패키징) 자격.
- **YELLOW** (510 < projected ≤ 600s): 그리드 결과 첨부, 사용자 콜.
  이 밴드에서는 torch.compile + 인덕터/트리톤 캐시 동봉(서버 스택 확정으로
  가능해진 트릭)이 추가 레버 후보 — 착수는 사용자 승인 후.
- **RED**: fast path 미활성/정합성 실패/projected > 600s → 직접 배포는 차단.
  잔여 경로는 증류(0.8B teacher → 0.6B student) 검토로 이관.

## 산출물

- `experiments/artifacts/m8_qwen35_t4_replica_probe.json` — v1 스키마 +
  {stack: {python, torch, triton, transformers, fla_core, causal_conv1d},
  fastpath_active, 배치 모드별 타이밍, verdict}.
- `research_log.md` 결정 1건 (결정만).
- GREEN 전에는 `requirements_qwen35.txt`·플랜 파일 수정 금지.

## 주의 / 제약

- 전부 콜랩 T4에서 (WSL 무거운 작업 금지).
- G4 레인의 OOF 진행과 독립 — G4 쪽 중단·변경 금지.
- torch 덮어쓰기는 콜랩 복제 환경에서만. 서버 requirements에 torch를 넣지
  말 것(2.7.1 프리인스톨로 충분, cu128 휠 2~3GB는 pip 10분 한도 리스크).
- 커밋 금지 (요청 시에만).
