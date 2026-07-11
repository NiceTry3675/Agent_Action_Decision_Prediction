# Handoff — 우승권 돌파를 위한 최종 2개 이론

- **기준일:** 2026-07-10
- **대상 저장소:** `NiceTry3675/Agent_Action_Decision_Prediction`
- **목표:** Public Macro-F1 `0.78962`에서 리더 `0.797`을 확실히 넘길 수 있는 독립적 구조 신호 탐색
- **상태:** 아래 두 이론만 신규 실험 레인으로 허용한다. 둘을 동시에 합친 실험은 각 이론이 독립 게이트를 통과한 뒤에만 허용한다.

---

## 2026-07-11 상태 갱신 (이 섹션이 본문 §0–§2와 충돌하면 이 섹션이 우선)

- **T1은 변형된 형태로 실행 완료, Public 양성.** 본문 §2의 T1-HR(행 단위
  hard/KD 이중 weight) 대신 **OOF consensus gradient sieve**로 구현됐다:
  hard-label **backbone gradient만** c=[0,1,2,3] → scale [0, 0.25, 0.75, 1]
  (클래스 내 평균 1.0 정규화), head는 full hard gradient, KD 불변, replay
  미시브, Weak4 한정이 아니라 전 클래스 적용. fixed-val은 OOF 소스 중첩으로
  fail-closed 처리하고 full-refit-only Public 단발로 판정했다.
- **결과: `kdm8_sieve_s42.zip` Public `0.7917`** — `condalpha-KD 0.78962`
  대비 +0.00208, `kd_m8_refit 0.78913` 대비 +0.00257(단일 변수 비교). 0.002
  노이즈 플로어를 넘어 **새 팀/로컬 챔피언으로 승격**. 상세는
  `final_summary.md`, `leaderboard_calibration.md`, `research_log.md`
  2026-07-11 항목.
- 따라서 §1.1의 "현재 챔피언 condalpha-KD 0.78962"와 §2의 T1 실행 계획
  (gradient audit → T1-HR/ARW/PERM → matched control 제출)은 구식이다.
  PERM/paired-seed 메커니즘 확인은 마감(2026-07-15(수) 오전 10시 KST,
  마지막 날도 10슬롯) 대비 슬롯 가치가 낮아 생략됐다. 리더와의 잔여
  갭은 `0.797 − 0.7917 ≈ 0.0053`.
- **레인 배분:** T1 후속(시브 × condalpha 스택, 한 변수)은 팀원 담당.
  이 레포 레인은 **T2(반사실 미래 궤적 특권잔차 증류, §3)** 착수.
- **T2의 parent는 새 챔피언으로 교체한다:** frozen parent = 시브 챔피언
  fp16 (`experiments/incoming/models/kd_m8_consensus_sieve_refit/`).
  §1.1의 Phase 0 선행 조건(0.78962 인계)은 시브 챔피언이 로컬에 fp16으로
  존재하므로 그 형태로 충족된 것으로 본다. §3의 게이트·금지사항은 그대로
  유효하다.
- **[07-11 추가] T2 종료.** frozen 감사 parent는 leak-free
  `kd_hcx_m8_screen`(full-refit parent는 train 행 z0가 암기되어 오라클
  무효)으로 실행. F0 GO(복원 86.50%/Weak4 94.97%, donor full-K 99.75%,
  길이 AUC 0.5004) 후 F1 teacher gate **REJECT**: T1−T2 macro `-0.0393`,
  Weak4 `-0.0639`, 3/3 fold 음수, actual oracle이 parent보다 나쁨. §3.7의
  즉시 종료 조건 발동, §끝의 "grid 연명 금지" 준수 — **레인 전면 종료,
  Public 0장.** 이로써 이 문서의 두 이론은 모두 해소됐다(T1=시브로 소비·
  Public 양성 0.7917, T2=frozen gate 종료).

---

## 0. 실행 결론

이번 단계에서 실험할 이론은 정확히 두 개다.

1. **OOF 신뢰도 기반 CE/KD 이중채널 노이즈 시브**  
   Weak4 행의 `hard-label CE`와 `teacher KD`를 하나의 α로 묶지 않고 분리한다. OOF 합의가 높은 행에는 hard label을 더 신뢰하고, 모든 OOF 모델이 실패한 행에는 hard-label 압력만 낮춘다.

2. **반사실 미래 궤적 특권잔차 증류**  
   미래 event가 정답 action을 노출하는 효과를 동일 action의 다른 세션 donor로 상쇄한 뒤, 실제 미래에만 남는 **행별 잔차 정보**가 현재 hidden에서 예측 가능한지 frozen-cache 단계에서 검증한다.

다음 항목은 **독립 신규 레인으로 다시 열지 않는다.**

- 명시적 generator FSM, action suffix, event suffix, prompt template
- broad lexical rule, pair별 hard override, 전역 bias
- Weak4 4-way specialist 및 라우팅 변형
- serializer 추가 세대
- A4 hidden kNN의 직접 추론 배포
- generic cascade/blend
- R-Drop 재조합

A4 hidden kNN은 독립 이론이 아니라, **이론 1이 통과한 뒤 `c=0` 내부를 세분화하는 학습용 reliability 자산**으로만 보존한다.

---

# 1. 공통 맥락과 고정 조건

## 1.1 현재 챔피언

현재 팀 Public 챔피언은 `condalpha-KD`, Macro-F1 **0.78962**다.

- 학생: `naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B`
- 교사: M8 `Qwen3.5-0.8B` full-refit train70k logits
- serializer: `current_v1`
- max length: `384`
- epoch: `3`
- learning rate: `2e-5`
- focal gamma: `2.0`
- class-weight power: `0.5`
- label smoothing: `0.02`
- replay: `last1`, cap `10000`, replay weight `0.5`
- KD temperature: `3.0`
- 비Weak4 matched original: `α=0.5`
- Weak4 matched original: `α=0.7`
- replay/unmatched: KD 제외
- inference bias/rules/sparse: 없음

**선행 조건:** 정확한 `0.78962` 체크포인트, 패키지, 커맨드, teacher-logit hash, conditional-alpha 구현을 저장소에 인계하고 smoke 검증하기 전에는 새 레시피의 Public 비교를 시작하지 않는다.

## 1.2 문제의 재해석

Weak4는 다음 네 클래스다.

```text
0 read_file
1 grep_search
2 list_directory
3 glob_pattern
```

휴먼 재라벨 감사에서는 오류 집중 표본의 데이터셋 라벨이 사람의 의미적 선택과 자주 불일치했지만, 복원된 실제 합성 에이전트 다음 행동과는 일치했다. 따라서 최적화 대상은 “사람에게 합리적인 도구”가 아니라 **합성 에이전트의 실제 정책 기록**이다.

또한 conditional-alpha의 이득은 Weak4 자체를 더 잘 맞힌 효과라기보다, noisy Weak4 hard-label 압력이 완화되며 다음 mid 클래스가 좋아진 효과로 해석되고 있다.

```text
ask_user
plan_task
lint_or_typecheck
```

따라서 모든 평가에는 Weak4뿐 아니라 위 mid 클래스의 F1과 confusion을 포함한다.

## 1.3 사전검증에서 닫힌 가설

생성기 FSM 구조 자체는 존재한다. 70,000행에서 73,181 trajectory node가 충돌 없이 복원됐다. 그러나 현재 KD main 위의 추가 잔차는 거의 없었다.

| 카드 | Macro Δ | Weak4 Δ | 양수 폴드 |
|---|---:|---:|---:|
| A0 coarse transition | -0.000002 | -0.000007 | 1/5 |
| A1 variable action suffix | -0.000001 | -0.000003 | 2/5 |
| A2 args/result event suffix | -0.000157 | -0.000548 | 1/5 |
| A3 prompt template | +0.000040 | +0.000140 | 1/5 |
| A0–A3 combined | -0.000117 | -0.000408 | 1/5 |
| A4 exact 1024d hidden kNN | +0.001062 | +0.003717 | 5/5 |

A4 confirm은 `Macro +0.000574 / Weak4 +0.002010`, rescue/harm `71/50=1.42`였다. 독립 제출 게이트에는 미달했다.

**결론:** 정책을 규칙으로 다시 표현하는 레인은 종료한다. 남은 신호는 hidden 안의 작은 비선형 잔차와 noisy supervision 처리 쪽에 있다.

## 1.4 공통 검증 규칙

1. 모든 split은 session group을 보존한다.
2. fixed outer-validation의 정답이나 예측이 train-row weight/target 생성에 우회 유입되면 안 된다.
3. full-data refit에는 각 행이 자신을 학습하지 않은 모델에서 얻은 OOF 신호만 사용한다.
4. 한 Public 제출에는 한 변수만 변경한다.
5. primary local metric은 **raw Macro-F1**이다. 2-stage bias는 진단용이며 챔피언 패키지에는 주입하지 않는다.
6. Public delta `<0.002`는 recipe 증거로 보지 않는다.
7. replay pseudo-row와 teacher-unmatched row는 현재와 동일하게 pure hard-label loss를 유지한다.
8. 실험 결과에는 반드시 다음을 저장한다.

```text
전체 Macro-F1
Weak4 macro 및 4개 클래스 F1
ask_user / plan_task / lint_or_typecheck F1
상위 confusion pair
bin별 행 수·loss·정확도·gradient 통계
rescue / harm / unchanged
prediction distribution
사용한 payload 및 코드 SHA256
```

---

# 2. 이론 1 — OOF 신뢰도 기반 CE/KD 이중채널 노이즈 시브

## 2.1 한 문장 가설

> OOF 모델들이 모두 실패한 Weak4 행의 주된 피해는 “교사를 덜 따라야 한다”가 아니라 **설명 불가능한 hard label이 공유 backbone에 강한 gradient를 주는 것**이다. 따라서 hard-label CE와 teacher KD를 분리해 CE 압력만 재배치하면, 전체 KD량을 늘리지 않고 mid 클래스와 Macro-F1을 개선할 수 있다.

## 2.2 왜 α 재배치만으로는 부족한가

현재 loss는 사실상 다음 형태다.

\[
L_i=(1-\alpha_i)L_{hard,i}+\alpha_i L_{KD,i}
\]

사전검증에서 기존 M7/M8/v6 session-OOF가 정답을 맞힌 수 `c`에 따라 Weak4가 다음처럼 분리됐다.

| bin | 행 수 | 진단용 학생 정확도 | full M8 정확도 |
|---|---:|---:|---:|
| `c=3` | 13,964 | 97.4% | 98.6% |
| `c=1~2` | 5,568 | 61.9% | 69.7% |
| `c=0` | 9,250 | 6.0% | 10.7% |

기존 제안인 `α=.60/.70/.85`는 Weak4 평균 α가 약 `0.6997`이라 총량 통제는 좋다. 하지만 `c=0`에서 full M8도 89.3%를 틀리므로 다음 두 효과가 섞인다.

```text
hard CE: 0.30 → 0.15  # 노이즈 억제 가능성
KD:      0.70 → 0.85  # 오답 teacher 강화 가능성
```

이론 1은 두 채널을 분리해 이 혼동을 제거한다.

## 2.3 reliability bin 정의

### Canonical bin

Weak4 원본 train row마다 다음 값을 만든다.

```text
c = M7 OOF 정답 여부 + M8 OOF 정답 여부 + v6 OOF 정답 여부
```

```text
stable:     c=3
ambiguous:  c=1 또는 c=2
outlier:    c=0
```

### 필수 provenance

- 각 OOF 예측은 해당 session을 학습하지 않은 fold model에서 나와야 한다.
- 한 모델이라도 OOF row가 누락되면 그 행은 `unknown`으로 두고 control weight를 사용한다.
- full-refit teacher의 train prediction은 bin 생성에 사용하지 않는다.
- fixed screen의 outer-train row bin은 **outer-train 내부 nested session-OOF**로 다시 생성한다.
- outer-validation row의 label/prediction을 train weight 생성에 사용하지 않는다.

## 2.4 loss 정의

구현 목표는 α가 아니라 두 독립 weight다.

\[
L_i=w^{hard}_iL_{hard,i}+w^{KD}_i m_i L_{KD,i}
\]

- `m_i`: teacher matched original row이면 1, replay/unmatched이면 0
- replay/unmatched: `w_hard=1.0`, `w_kd=0.0`
- 비Weak4 matched original: `w_hard=0.5`, `w_kd=0.5`

### Primary control: `T1-C0`

현재 conditional-alpha의 정확한 loss를 재현한다.

| Weak4 bin | hard | KD |
|---|---:|---:|
| 모두 | 0.30 | 0.70 |

### Primary candidate: `T1-HR`

Hard-label pressure만 재배치한다. KD는 모든 Weak4 bin에서 고정한다.

| Weak4 bin | hard | KD | row total |
|---|---:|---:|---:|
| `c=3` | **0.40** | **0.70** | 1.10 |
| `c=1~2` | **0.30** | **0.70** | 1.00 |
| `c=0` | **0.15** | **0.70** | 0.85 |

현재 bin count를 적용하면 Weak4 평균 hard weight는 약 `0.30031`, 평균 KD는 `0.70`이다. 즉 전체 Weak4 loss 질량은 거의 그대로 유지하면서 hard-label 압력만 행 사이에 이동한다.

### Required negative control: `T1-PERM`

`T1-HR`과 weight 개수는 같지만 실제 `c` bin을 무작위로 섞는다.

Permutation 층화 키:

```text
true Weak4 class
× source(sim/au)
× turn bin
× token-length bin
```

AU sibling/primary-scenario group이 확인되는 경우 같은 group을 하나의 block으로 취급한다. permutation seed는 최소 3개를 로컬에서 확인하고, 평균과 최댓값을 모두 보고한다.

### Conditional fallback: `T1-ARW`

값싼 gradient 감사에서 `c=0`의 hard gradient와 KD gradient가 둘 다 해로운 경우에만 사용한다. 전체 supervision 자체를 줄인다.

| Weak4 bin | hard | KD | row total |
|---|---:|---:|---:|
| `c=3` | 0.36 | 0.84 | 1.20 |
| `c=1~2` | 0.30 | 0.70 | 1.00 |
| `c=0` | 0.21 | 0.49 | 0.70 |

현재 count 기준 평균 hard/KD는 약 `0.30019 / 0.70043`이다.

### 첫 wave에서 하지 않을 것

```text
α-only .60/.70/.85를 primary candidate로 제출하지 않는다.
```

이 카드는 `T1-HR`이 양성일 때 메커니즘 확인용 ablation으로만 허용한다.

---

## 2.5 값싼 감사

### Audit 1 — payload/provenance 감사

GPU 학습 전에 CPU에서 완료한다.

필수 출력:

```text
전체/클래스별 bin count
source별 bin count
turn별 bin count
token length별 bin count
OOF row coverage
중복 id / 누락 id
각 OOF artifact의 model/split/seed/hash
Weak4 평균 hard/KD/total weight
PERM의 층화별 count 보존 여부
```

즉시 중단 조건:

- 한 session이 OOF train과 prediction fold에 동시에 존재
- outer-validation 정보를 사용한 bin
- bin payload id와 train id 불일치
- 클래스 순서 불일치
- Weak4 평균 loss 질량이 의도값에서 `±0.005` 이상 벗어남

### Audit 2 — bin별 gradient alignment

목표는 `c=0`에서 줄여야 할 것이 hard CE인지, KD까지 포함한 전체 supervision인지 값싸게 구분하는 것이다.

#### 데이터

- 공식 outer-validation은 사용하지 않는다.
- outer-train session 안에서 inner train/confirm split을 만든다.
- 각 bin·클래스·source를 층화해 여러 minibatch를 샘플링한다.

#### 파라미터 범위

첫 감사에서는 다음만 gradient 대상으로 삼는다.

```text
classification score head
마지막 transformer block
마지막 layer norm
```

#### gradient

```text
g_hard[c=3], g_kd[c=3]
g_hard[c=1~2], g_kd[c=1~2]
g_hard[c=0], g_kd[c=0]
g_confirm_balanced
g_confirm_mid
g_confirm_weak4
```

`g_confirm_balanced`는 class-balanced CE surrogate로 계산한다. 파라미터 update가 `θ ← θ - ηg_train`이므로, `g_confirm · g_train > 0`이면 1차 근사상 confirm loss를 낮추는 방향이다.

필수 보고:

```text
cosine(g_confirm_balanced, g_hard/bin)
cosine(g_confirm_balanced, g_kd/bin)
cosine(g_confirm_mid, g_hard/bin)
cosine(g_confirm_mid, g_kd/bin)
각 bin gradient norm
T1-HR / T1-ARW / T1-PERM의 합성 gradient 예상치
5개 session fold의 부호
```

판정:

- `c=0 hard`만 지속적으로 충돌하고 `c=0 KD`는 중립/양수 → `T1-HR`
- `c=0 hard`와 `c=0 KD`가 모두 충돌 → `T1-ARW`
- 실제 bin과 PERM 차이가 없거나 fold 부호가 불안정 → 이론 1 종료

### Audit 3 — 짧은 warm-start micro-screen

Gradient 감사 통과 시에만 수행한다.

- 동일 leak-free checkpoint에서 `T1-C0`, 선택 후보, `T1-PERM`을 동일 RNG/data order로 fork한다.
- head/마지막 block만 먼저 업데이트하는 짧은 screen을 사용한다.
- 이 screen은 **거절용**이다. 여기서 좋아도 승격 증거로 사용하지 않는다.

계속 조건:

```text
후보 > C0
후보 > PERM 평균
mid 3종 F1 합이 양수
특정 한 클래스의 prior 폭주가 없음
```

---

## 2.6 구현 유의사항

### 권장 payload

```python
{
    "format": "row-loss-weights-v1",
    "ids": [...],
    "classes": [...],
    "reliability_bin": [...],
    "hard_weight": float32[N],
    "kd_weight": float32[N],
    "source_oof_artifacts": [...],
    "split": "...",
    "seed": 42,
    "code_sha256": {...},
    "data_sha256": {...}
}
```

### train loss 변경

현재 scalar alpha 로직을 다음처럼 확장한다.

```python
hard = classification_loss_values(...)
kd = kl_div_per_row(...)

if original_teacher_matched:
    loss = hard_weight[row] * hard + kd_weight[row] * kd
else:
    # replay 또는 teacher-unmatched
    loss = hard

loss = loss * replay_or_sample_weight
```

주의:

1. `sample_weight`와 새 `hard/kd weight`를 혼동하지 않는다.
2. replay weight `0.5`는 최종 row loss 바깥에서 기존대로 적용한다.
3. unmatched original은 기존 동작과 동일하게 pure hard loss를 유지한다.
4. 평균 loss를 batch size로 단순 나누지 말고 기존 sample-weight 정규화를 보존한다.
5. `hard+KD` 합이 bin마다 달라도 optimizer LR을 자동 보정하지 않는다. 평균 총량이 통제돼 있다.
6. AMP 이전에 weight tensor dtype/device를 명시한다.
7. payload id join은 fail-closed로 구현한다. positional join 금지.
8. final refit에서만 70k canonical OOF payload를 사용한다.
9. fixed screen에는 nested payload를 별도 파일로 만든다.
10. 현재 conditional-alpha 체크포인트를 resume할 경우 optimizer state resume와 fresh training을 혼동하지 않는다.

### 단위 테스트

최소 테스트:

```text
T1-C0가 기존 α=.7 loss와 수치적으로 일치
비Weak4가 기존 α=.5와 일치
replay/unmatched가 pure hard loss와 일치
payload id 순서가 달라도 id join 결과 동일
누락/중복/unknown class에서 fail
PERM이 층화별 weight count 보존
nested payload에 outer-val id가 없음
```

---

## 2.7 full screen 및 승격 게이트

### Matched full screen

- 동일 base checkpoint
- 동일 seed
- 동일 lane/GPU 종류
- 동일 tokenizer cache
- 동일 batch order
- 동일 optimizer/scheduler/scaler 초기 상태
- 변경 변수는 row hard/KD weight 하나

### 로컬 게이트

후보는 `T1-C0`와 `T1-PERM`을 모두 이겨야 한다.

```text
primary raw Macro: 후보 - C0 ≥ +0.0020
confirm raw Macro: ≥ +0.0010
5개 nested fold 중 4개 이상 양수
후보 - PERM 평균 ≥ +0.0010
후보 - PERM 최고 seed > 0
ask/plan/lint F1 합 > C0
Weak4 macro 급락 없음(권장 하한 -0.003)
rescue > harm
```

`2-stage`만 좋아지고 raw가 좋아지지 않으면 반려한다.

### Public 게이트

1. 정확한 `T1-C0` matched control 제출
2. 선택 후보 제출
3. 후보가 C0를 Public `+0.002` 이상 이기면 두 번째 paired seed에서 C0/후보 반복
4. 두 seed가 같은 방향이면 recipe 승격

Public이 `+0.002` 미만이면 최고 instance로 보존할 수는 있으나 이론 증거로 승격하지 않는다.

### 즉시 종료 조건

- gradient audit에서 실제 bin과 PERM의 차이가 없음
- local full screen raw Macro가 음수
- 개선이 fixed 2-stage bias에만 존재
- `c=0` teacher 오답 강화로 Weak4 confusion이 악화
- mid 클래스 개선이 재현되지 않음

---

# 3. 이론 2 — 반사실 미래 궤적 특권잔차 증류

## 3.1 한 문장 가설

> 다음 event와 이후 user turn에는 현재 observable state만으로는 직접 보이지 않는 생성기 latent state가 포함되어 있다. 단, 미래 텍스트가 action 이름을 사실상 노출하는 효과를 동일 action donor로 제거한 뒤에도 남는 **행별 실제 미래 잔차**가 있고, 그 잔차의 일부가 현재 hidden에서 예측 가능하다면 inference 시 미래 없이도 개선할 수 있다.

## 3.2 데이터 상태

사전검증의 복원 coverage:

| target | 전체 | Weak4 |
|---|---:|---:|
| target event | 86.50% | 94.97% |
| next user | 83.32% | 91.33% |
| next-next event | 70.81% | 80.23% |

충돌, prompt mismatch, malformed history는 모두 0이었다.

그러나 target result summary는 거의 정답지다.

```text
args key schema only:
  fixed-val accuracy 78.24%
  Weak4 Macro-F1 70.11%

result summary first two words only:
  accuracy 99.55%
  Weak4 Macro-F1 1.000

args schema + result prefix:
  accuracy 99.96%
```

따라서 `P1 actual future > P0 no future`는 아무 의미가 없다. 반드시 같은 action 조건을 가진 반사실 donor `P2`와 비교한다.

## 3.3 첫 privileged channel

첫 frozen 감사에서는 **next-user only**를 사용한다.

이유:

- `ok read`, `found occurrences`, `files matched` 같은 result prefix의 직접 label leakage를 피한다.
- next-user는 성공 결과와 세션 진행 상태를 간접적으로 담지만 action name을 직접 노출하지 않는다.
- next-user-only가 실패하면 raw target result를 추가해도 실제 deployable 신호일 가능성이 낮다.

첫 감사에서 금지:

```text
target action name
raw result prefix
raw full target event를 current text 뒤에 단순 append
```

현재 text 뒤에 append하면 max-length truncation으로 현재 입력이 손상될 수 있으므로, current와 future는 **같은 frozen backbone weight를 쓰되 별도 sequence로 인코딩**한다.

## 3.4 P0/P1/P2 정의

### P0 — parent

```text
h0 = frozen parent hidden(current_v1 input)
z0 = frozen parent logits(current_v1 input)
```

### P1 — actual future

```text
hf_actual = frozen backbone hidden("future_user: <actual next user>")
```

### P2 — matched counterfactual future

다른 session의 next-user donor를 사용한다.

Primary donor match:

```text
same true action
× same source(sim/au)
× same turn bin
× same language_pref
```

필수 제외:

```text
same session
same AU primary scenario / known sibling scenario
동일 원문 next-user
```

- 행마다 deterministic하게 최대 `K=4` donor를 선택한다.
- donor seed는 sample id hash로 고정한다.
- donor가 부족한 행은 primary 분석에서 제외하고 coverage를 보고한다.
- matching을 느슨하게 backoff해 coverage를 억지로 올리지 않는다.

확장 arm에서 target args/result를 사용할 경우 P2는 추가로 다음을 맞춘다.

```text
args-key schema
result-prefix template
```

또는 해당 직접 누출 토큰을 P1/P2 양쪽에서 마스킹한다.

## 3.5 privileged residual 정의

### Frozen privileged head

current와 future를 별도 인코딩한 후 작은 head를 학습한다.

권장 입력:

```text
[h0, hf, h0 * hf, abs(h0 - hf)]
```

첫 모델은 L2 linear/ridge 또는 low-rank linear로 제한한다. 복잡한 MLP는 linear가 양성일 때만 연다.

동일 head에 actual future와 donor future를 각각 넣는다.

```text
z1 = privileged_head(h0, hf_actual)
z2_k = privileged_head(h0, hf_donor_k)
```

logit scale 영향을 제거한다.

```python
q1 = log_softmax(z1)
q2 = mean(log_softmax(z2_k), dim=donor)
delta = center(q1 - q2)
center(v) = v - mean(v, dim=class)
```

- `delta` norm은 train fold의 p99에서 clip한다.
- class order는 canonical 14 classes와 assert한다.
- raw logit subtraction은 사용하지 않는다.

### Teacher oracle 평가

held-out fold에서 실제 future를 사용하는 oracle은 다음을 평가한다.

```text
z_oracle = z0 + λ * delta
```

`λ`는 held-out fold에서 튜닝하지 않는다. inner tune에서 결정한 λ를 그대로 적용한다.

### Current-only student

학생은 미래를 보지 않는다.

```text
delta_hat = student_residual(h0)
z_student = z0 + λs * delta_hat
```

첫 student:

```text
L2 ridge / linear 1024→14
```

두 번째 student는 linear가 유망하지만 회수율이 낮을 때만 허용한다.

```text
1024→64→14, zero-init output
```

학습 target은 `delta`이며 row-centered MSE 또는 KL/cosine 혼합을 사용한다. 첫 감사에서는 MSE 하나로 시작한다.

## 3.6 필수 control

| 이름 | 내용 |
|---|---|
| `T2-P0` | parent logits only |
| `T2-T1` | actual next-user privileged oracle |
| `T2-T2` | same-action matched donor privileged oracle |
| `T2-S1` | current-only student trained on actual-minus-donor residual |
| `T2-SPERM` | action/source/turn 안에서 residual target을 permutation한 student |

Teacher signal은 `T2-T1 - T2-T2`로 판단한다. Student는 `T2-S1 - T2-SPERM`과 `T2-S1 - T2-P0`를 모두 본다.

---

## 3.7 값싼 frozen-cache 감사

### Fold 구성

- 3-fold session group CV
- known AU sibling/primary scenario는 같은 fold로 묶는다.
- privileged head와 student는 fold train에서만 학습한다.
- held-out fold의 actual future는 teacher oracle 평가에만 사용한다.
- held-out future 또는 residual target이 student train에 들어가면 안 된다.

### Stage F0 — leakage 및 donor 감사

필수 출력:

```text
recovery coverage
Weak4 recovery coverage
donor K별 coverage
same-session donor 0건
same-scenario sibling donor 0건
P1/P2 token-length 분포
future text duplicate 비율
action-only classifier 성능
result-prefix leakage 재현
```

중단 조건:

- donor coverage가 전체/Weak4 중 하나라도 80% 미만
- same session/scenario donor가 1건 이상
- P1/P2 길이 분포가 현저히 달라 길이만으로 구분 가능
- current 입력 truncation이 발생

### Stage F1 — teacher oracle gate

각 held-out fold에서 inner-tuned λ를 적용한다.

통과 조건:

```text
T2-T1 - T2-T2 Macro ≥ +0.0020
T2-T1 - T2-T2 Weak4 macro ≥ +0.0060
3개 fold 중 2개 이상 Macro 양수
특정 한 donor seed에만 의존하지 않음
```

실제 future가 donor future보다 좋지 않으면 즉시 종료한다. P0 대비 향상은 승격 근거가 아니다.

### Stage F2 — current-only student gate

Teacher gate를 통과한 경우에만 실행한다.

정의:

```text
teacher_excess = Macro(T2-T1) - Macro(T2-T2)
student_excess = Macro(T2-S1) - Macro(T2-SPERM)
recovery_ratio = student_excess / teacher_excess
```

통과 조건:

```text
recovery_ratio ≥ 0.30
T2-S1 - T2-P0 > 0
T2-S1 - T2-SPERM ≥ +0.0010
3개 fold 중 2개 이상 양수
Weak4 방향 양수
```

위 조건 중 하나라도 실패하면 full backbone screen, refit, Public을 진행하지 않는다.

### Stage F3 — target-event 확장

next-user-only가 F1/F2를 통과한 경우에만 검토한다.

추가 channel 후보:

```text
canonicalized args schema
path/scope role
result count/status bucket
action-bearing result prefix를 제거한 summary
next-next user/action trajectory
```

확장 channel은 next-user-only 대비 독립 이득 `Macro +0.001` 이상이 없으면 채택하지 않는다.

---

## 3.8 구현 유의사항

### 복원

기존 `privileged_event_modes.py`의 strict positional + exact prompt witness 방식을 유지한다.

- prompt-only fallback 금지
- ID-free fallback 금지
- witness conflict는 fail-closed
- target action name은 privileged text에서 제거
- terminal/no-witness row는 mask 처리

### future 인코딩

- current와 future를 별도 batch로 인코딩한다.
- 같은 tokenizer/backbone/checkpoint를 사용한다.
- future max length는 별도 작은 상한을 둔다.
- future encoding이 current token cache를 덮어쓰지 않게 cache key를 분리한다.
- P1/P2는 동일 max length, 동일 padding/bucketing을 사용한다.

### donor 결정론

```python
seed = sha256(f"{sample_id}|future-donor-v1")
```

- donor index와 donor sample id를 artifact에 저장한다.
- donor pool을 fold train/held-out에 맞게 별도로 만든다.
- 같은 held-out row의 donor가 student train target 생성에 쓰이는 것은 허용되지만, donor의 target label이나 residual이 fold 경계를 넘으면 안 된다.
- primary scenario sibling group이 있으면 group 단위 제외를 우선한다.

### target residual

- `log_softmax` 후 차이를 사용한다.
- row mean을 제거한다.
- norm clip threshold는 train fold에서만 추정한다.
- λ와 clip threshold를 held-out fold에서 튜닝하지 않는다.
- target residual permutation은 action/source/turn strata 안에서 수행한다.

### 단위 테스트

```text
P1/P2에 target action name이 없음
same-session/same-scenario donor 0건
donor 선택 결정론
held-out fold target이 train에 없음
row-centered residual의 class mean≈0
P1=P2일 때 delta=0
PERM이 strata count를 보존
current/future cache key 충돌 없음
future append로 current truncation하지 않음
```

---

## 3.9 frozen gate 통과 후의 본학습 형태

Frozen gate 통과 전에는 아래 구현을 하지 않는다.

### OOF privileged target 생성

각 70k row의 `delta`는 해당 row/session을 학습하지 않은 fold-specific parent/privileged head에서 생성한다.

저장 payload:

```python
{
    "format": "counterfactual-future-residual-v1",
    "ids": [...],
    "delta": float16[N, 14],
    "mask": bool[N],
    "donor_ids": [...],
    "classes": [...],
    "fold": [...],
    "teacher_metrics": {...},
    "hashes": {...}
}
```

### Current-only residual adapter

표준 HCX 학습에 zero-init residual head를 추가한다.

```text
parent logits z
current hidden h
residual r(h)
final logits = z + βr(h)
```

보조 loss:

```text
base champion loss
+ γ * MSE(center(r(h)), privileged delta)
```

첫 본학습은 linear residual head로 제한한다. linear라면 최종적으로 parent `score` weight/bias에 합쳐 inference 코드 변경 없이 배포할 수 있다.

```text
score_final.weight = score_parent.weight + β * residual.weight
score_final.bias   = score_parent.bias   + β * residual.bias
```

MLP가 필요하면 별도 artifact와 `script.py` 경로가 필요하므로 linear gate 통과 후에만 검토한다.

### 본학습 승격 게이트

```text
matched fixed raw Macro ≥ parent +0.003
Weak4 macro ≥ parent +0.006
student residual norm 폭주 없음
PERM target arm보다 ≥ +0.0015
2개 seed 같은 방향
```

통과 후에만 full refit → package → Public 한 장을 연다.

### 즉시 종료 조건

- teacher actual-vs-donor 차이가 게이트 미달
- student 회수율 30% 미만
- 개선이 result-prefix leakage arm에서만 존재
- next-user-only가 실패하고 raw result만 성공
- 특정 donor seed/fold에만 양수
- frozen hidden에서만 양수이고 matched full screen에서 사라짐

---

# 4. 실행 순서

## Phase 0 — 재현 기반 고정

1. 정확한 `condalpha-KD 0.78962` artifact/code/command 인계
2. clean GPU/CPU smoke
3. exact control 재학습 또는 체크포인트 provenance 확인
4. teacher logits 및 OOF artifacts hash 고정

## Phase 1 — 값싼 감사 병렬 실행

### Lane T1

```text
payload/provenance audit
→ gradient alignment
→ 짧은 C0/candidate/PERM micro-screen
```

### Lane T2

```text
future recovery/donor audit
→ next-user-only P1/P2 frozen teacher gate
→ current-only residual recovery gate
```

두 lane 모두 이 단계에서는 Public 0장이다.

## Phase 2 — 첫 full screen

우선순위는 T1이다.

```text
T1-C0 matched control
T1-HR 또는 gradient 감사가 선택한 T1-ARW
T1-PERM
```

T2는 frozen gate를 모두 통과했을 때만 full current-only residual screen을 연다.

## Phase 3 — Public

1. 독립 local gate를 통과한 이론만 한 변수 제출
2. `+0.002` 이상이면 paired second seed
3. 각 이론이 독립 Public 양성일 때만 결합 실험
4. 결합 시에도 `T1 winner + T2 winner` 한 번에 바로 넣지 말고, 더 강한 단독 winner를 base로 다른 하나만 추가한다.

---

# 5. 에이전트용 금지사항

- `c=0`을 “teacher가 고칠 수 있는 행”으로 해석하지 않는다.
- full M8 train prediction으로 reliability bin을 만들지 않는다.
- fixed outer-validation 정보를 train weight 생성에 쓰지 않는다.
- α-only 결과를 CE 억제와 teacher 강화 중 어느 메커니즘인지 구분 없이 해석하지 않는다.
- actual future가 P0보다 좋다는 이유로 이론 2를 승격하지 않는다.
- result summary prefix를 그대로 privileged 입력으로 넣고 성과를 주장하지 않는다.
- future를 current 뒤에 append해 current context를 truncate하지 않는다.
- same-session 또는 AU sibling donor를 허용하지 않는다.
- A4 kNN을 독립 inference package로 다시 열지 않는다.
- local 2-stage bias 개선만으로 Public을 열지 않는다.
- 두 이론을 첫 실험부터 결합하지 않는다.

---

# 6. 산출물 계약

## 이론 1

```text
experiments/artifacts/<date>_row_reliability_provenance.json
experiments/artifacts/<date>_dual_weight_gradient_audit.json
experiments/artifacts/<date>_t1_c0_metrics.json
experiments/artifacts/<date>_t1_candidate_metrics.json
experiments/artifacts/<date>_t1_perm_metrics.json
experiments/logits/<...>_val_logits.pt
experiments/row_weights/<...>.pt
```

## 이론 2

```text
experiments/artifacts/<date>_future_recovery_donor_audit.json
experiments/artifacts/<date>_future_p1_p2_teacher_gate.json
experiments/artifacts/<date>_future_student_recovery_gate.json
experiments/cache/<...>_future_hidden.pt
experiments/privileged_targets/<...>_future_residual.pt
```

모든 report에 다음을 포함한다.

```text
repo commit
uncommitted diff hash
train/data/artifact SHA256
Python/torch/transformers 버전
GPU 종류
split/seed/fold
정확한 command
결정: GO / HOLD / REJECT
사전등록 gate 대비 결과
```

---

# 7. 최종 의사결정표

| 이론 | 값싼 감사 통과 | full screen 조건 | Public 조건 | 실패 시 |
|---|---|---|---|---|
| T1 CE/KD 이중채널 | 실제 bin gradient가 PERM보다 일관되게 우수 | raw `+0.002`, confirm `+0.001`, 4/5 folds | matched control 대비 `+0.002`, second seed 같은 방향 | reliability 재가공 포함 전면 종료 |
| T2 미래 특권잔차 | P1−P2 `Macro +0.002`, Weak4 `+0.006`; student 30% 회수 | parent 대비 raw `+0.003`, PERM 대비 `+0.0015` | 독립 한 변수 `+0.002` | future trajectory 레인 전면 종료 |

---

# 8. 참고할 저장소 파일

```text
final_summary.md
leaderboard_calibration.md
research_log.md
experiments/results.csv
train_transformer.py
script.py
privileged_event_modes.py
probe_privileged_mode_residual.py
audit_generator_policy_mixture.py
experiments/artifacts/20260710_m7_m8_v6_oof_consensus.json
experiments/artifacts/20260710_human_relabel/
experiments/artifacts/20260710_generator_policy_mixture_audit.json
experiments/artifacts/20260710_selective_mode_probe_report.json
```

---

## 최종 요약

- **T1은 가장 현실적인 제출 후보**다. 핵심은 `c=0`에서 teacher를 더 세게 따르는 것이 아니라, hard-label backbone gradient를 줄이는 효과를 독립적으로 검증하는 것이다.
- **T2는 가장 큰 구조적 상방 후보**지만, 미래 템플릿의 정답 누출이 극심하다. actual future와 same-action donor의 차이만 사용하고, 그 차이를 current-only student가 회수할 때만 본학습을 허용한다.
- 둘 중 하나가 게이트를 못 넘으면 파라미터 grid로 연명하지 말고 해당 이론을 닫는다.
