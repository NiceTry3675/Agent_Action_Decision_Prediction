# FE `current_v5` 스펙 — Qwen 라인 meta/workspace 디노이즈 (2026-07-05)

> 상태: **구현·스모크 완료, GPU A/B는 보류** (Colab 레인 여유 없음 — 레인이 비면 실행).
> 팀원 FE 레인(FE_Teammate_handoff/)은 중단·인수됨. 이 문서가 FE 레인의 현행 스펙.
> 측정 원자료: `experiments/artifacts/20260705_fe_v5_design_measurements.json`.

## 설계 원칙

- current_v1 레이아웃·필드명 **보존**, 삭제+치환만 (state_v2 재구조화 −0.007, v3 추가 −0.013의 실증 회피).
- 채택 기준은 marginal MI가 아니라 **conditional MI** — "모델이 이미 보는 것(히스토리·원문) 조건부 순증"으로 심사. v3 실패 원인(마진 lift는 크지만 raw 프롬프트와 중복)을 사전 차단하는 표준 스크린.
- 전제 파기 주의: 팀원의 "len384에서 10.6% 절단"은 **xlm-r 토크나이저 기준**. Qwen3.5 토크나이저로는 current_v1이 len400에서 **절단 0%** — 절단 회복 메커니즘은 이 라인에 존재하지 않음. v5의 이득 가설은 (a) 죽은 토큰 제거로 attention 희석 감소(중립~소폭+), (b) **토큰 -21.2% = 추론 예산 단축**(Qwen 팩 타이밍 병목에 직결), (c) 모델 불문 이식성(Qwen3-0.6B 0.780 라인 포함).

## 스펙 (v1 대비 변경 라인만)

```
meta: turn=<start|early|mid|late|long|na>
workspace: dirty=<..> ci=<..> lang=<top1[+top2]|na> open=<v1과 동일>
```

- **제거**: `tier` `lang`(=language_pref) `budget` `elapsed`(meta), `loc` `langs=<float 나열>`(workspace).
  근거: tier/lang_pref MI 0.0%H, budget 0.2%, loc 0.2%; elapsed는 marginal 1.7%지만 turn 조건부 순증 +0.008 nats(r=0.71)로 중복.
- **turn 구간화**: 경계 `(1,2,4,6)`(`turn > edge` 카운트), 이름 `start/early/mid/late/long` — 전부 Qwen 단일 토큰.
  분위수가 아니라 **레짐 경계**(턴별 라벨분포 전수탐색 최적): {1}=착수(list/read/plan, 히스토리 0%), {2}=파악(read), {3,4}=본작업(edit 지배), {5,6}=마무리 전환(respond 등장), {7+}=평평한 정상상태(인접턴 JS~0.001). MI 0.1349 = exact turn의 97%, 분위수 5구간(0.118) 대비 +14%.
  turn 자체는 가시 액션 수와 조건부 중복(+0.0002)이나, 0.8B 모델의 "개수 세기"를 대신하는 1토큰 보험으로 유지.
- **lang top-2**: `lang=py+ts` (2위 없으면 `lang=py`, language_mix 없으면 `na`). top-1 MI 0.0272 → top-2 0.0389 (+43%, 편향 보정 후 ~+0.007 실재). 비중 게이트(≥0.25만 표시)는 오히려 MI 하락(0.0305) → **무게이트 top-2**. v1의 top-5 float 나열을 줄이는 것이므로 v3-류 추가 노이즈 아님.
- 그 외 라인(current/actions/last_user/args/results) **바이트 동일**.

## 측정 결과 (train 70k, Qwen3.5 토크나이저)

| | mean | p95 | p99 | max | trunc@400 |
|---|---|---|---|---|---|
| current_v1 | 216.0 | 312 | 334 | 400 | 0 |
| **current_v5** | **170.3** | 265 | 287 | **353** | 0 |

샘플당 **-45.8토큰(-21.2%)**. v5 max=353이라 승격 후 len 400→384/352 축소(추가 추론 단축)는 별도 변수로 검토 가능.

## 구현

- `script.py`: `TURN_BIN_EDGES/TURN_BIN_NAMES`, `turn_bin_token()`, `top_language_pair()`, `serialize_transformer_sample_current_v5()`, 디스패치 `current_v5` 등록.
- `train_transformer.py`: `--serializer` choices에 `current_v5` 추가.
- 추론 정합성: 모델 meta의 `serializer_name`이 `script.py:972`에서 자동 디스패치 — v5 팩이 v1 직렬화를 타는 사고는 구조상 불가. 직렬화 캐시도 serializer 이름으로 키 분리.
- 스모크: 구간 경계 단위검사, train 3샘플 + 로컬 test.jsonl 스텁(5행) 직렬화 눈검사, 70k 토큰 재측정 — 전부 통과 (2026-07-05).

## 체인 결합 (중요)

serializer는 **학습 단계부터** 적용 — v1 학습 모델에 v5 추론은 OOD로 무조건 손해. v5로 가면 체인 전체 재생산:
학습(`--serializer current_v5`) → OOF 로짓 → **rules/bias 재튜닝**(기존 M8 rules 0.7740은 v1 로짓 전용, 이식 불가) → refit → 패키징.
비용은 "GPU 폴드 3개"가 아니라 "체인 한 벌"로 산정할 것.

## 알려진 근사 (수용)

replay 샘플(`train_transformer.py:188`)은 session_meta를 원본 그대로 복사 — 프리픽스인데 turn_index는 세션 끝 값이라 turn 토큰이 최대 1턴(경계에서 빈 1개) 어긋날 수 있음. last1 replay라 영향 미미하고 **v1 베이스라인도 동일 어긋남으로 학습**되어 A/B 비교성 보존. 수정하면 두 번째 변수가 되므로 이번 라운드는 그대로 둠.

## 실행 계획 (Colab 레인 확보 시)

1. Quick screen: Qwen3.5-0.8B, `--serializer current_v5`, 1ep, `--quick-val-size 600` — 참사 기각 게이트만.
2. 본판정: 3-fold session OOF, len400 ep3, 챔피언 레시피 그대로(변수는 serializer 하나), vs 베이스라인 `m8_qwen35_oof_len400_ep3` (raw **0.7678** / +rules **0.7740**). 판정은 07-05 독트린대로 OOF 집계 + 탐색 약클래스(list 0.512/read 0.610/grep 0.619/glob 0.665) per-class.
3. 승격 시: rules 재튜닝 → refit → (배포 경로가 열린 라인에) 패키징. Qwen3-0.6B 라인 이식 제안 포함.
4. 기대치(정직): F1 sub-0.01, 판정가치의 절반은 **추론 -21% + 이식성**.

## 2차 레인 (GPU-free, 지금도 가능)

`tune_oof_rule_boosts.py` = 이미 OOF conditional bias search (조건×클래스×강도 greedy). 어휘 업그레이드 후보:
- turn 조건 빈을 `[1,2,4,8,12]` → 레짐 경계 `[1,2,4,6]`으로 (7+ 분할은 저-support 노이즈 조건).
- `last_pair`(bigram)에 **trigram 조건 추가** — 팀원 실증 trigram MI 0.444 > bigram 0.360.
- lang top-2 pair 조건 추가.
기존 M8 OOF 로짓(`experiments/logits/`) 위에서 즉시 재튜닝 가능. 기대치 sub-0.005 Public. 단 v5 채택 시 rules는 어차피 재튜닝이므로 **코드(어휘)는 이월, 튜닝값은 소모품**.
