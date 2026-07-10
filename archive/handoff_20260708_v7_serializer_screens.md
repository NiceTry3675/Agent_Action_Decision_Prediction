# Handoff 2026-07-08: current_v7 / current_v7r 직렬화기 스크린 (피처 레인)

전략 문맥: 현 Public baseline **0.7891** = `kd_m8_refit.zip`(HCX-0.5B + M8 KD,
`final_summary.md`). 마감 ~07-14, 제출 10슬롯/일(타 레인 공유).
**GPU 우선순위: 2-teacher KD(레인 B), M9 교사 스케일(레인 C)이 먼저 — 이 문서의
스크린들은 유휴 레인에서만 태운다.** 스크린 자체는 슬롯 0개.

**2026-07-08 밤 자율 실행 상태**: 레인 A(기본 `AADP_exchange`, RTX PRO 6000)
부트스트랩 완료(commit `2c587122`)·Screen 1 런칭됨(pid 6003,
`v7_hcx05b_screen`). 사용자 취침 — Screen 1→(통과 시)Screen 2/3까지 백그라운드
감시 후 자율 진행, **Dacon 실제 제출 직전(패키징+오프라인 스모크까지 완료)
에서 정지하고 보고**. 게이트는 이날 밤 전부 **+0.005/+0.003 → +0.002로 완화**
(아래 각 절에 반영, 사용자 지시).

**최종 결과 (완료, research_log.md 07-08 밤 절 상세)**:
- Screen 1(v7 논-KD): **통과** — 2stage 0.772532 (+0.002736 vs 앵커). plan_task
  -0.0112(표적 외)는 ask_user +0.0441과의 교환으로 순유리, 무효화 안 함.
- Screen 2(v7 KD): **실패** — 2stage 0.785344, KD 앵커(0.787801) 대비
  **-0.002457**. grep_search 부호 반전, plan_task -0.0411(ask_user 이득의
  6배 — 순불리로 역전). **v7 KD 체인 종료.**
- Screen 3(v7r vs v7 논-KD): **통과** — 2stage 0.776961 (+0.004429 vs v7,
  +0.007165 vs v1 앵커). plan_task가 v1 대비 **+0.0138로 완전 회복·역전**
  (레지스터 가설의 핵심 예측 적중). grep_search만 v7 대비 -0.0101(v1 대비는
  -0.0028, 노이즈권).
- v7r KD 스크린: **실패, v7-KD보다 더 나쁨** — 2stage 0.783032, 앵커 대비
  **-0.004769**. grep_search 3단 누적 악화(v1→v7→v7r), list_directory 신규
  역행. plan_task만 v7-KD 대비 +0.0126 부분 회복(레지스터 메커니즘 자체는
  KD 위에서도 일부 재현 — 단 다른 클래스 확산 손실을 상쇄 못함).

**최종 판정 (2026-07-08 낮 정정)**: v7·v7r 둘 다 **논-KD에서 유효한 검증
레버**. KD 스크린 2건의 실패는 야간에 "KD 흡수 일반화, 레인 마감"으로
판정했으나 **교란변수 미통제로 철회** — teacher 로짓의 출처
`m8_qwen35_refit`은 **current_v1으로 학습된 teacher**라, alpha=0.5의 KL 항이
v7/v7r 피처가 예측을 바꾸는 바로 그 행들에서 v1-수준 불확실성으로 되끌어당기는
역-그래디언트로 작동한다(grep_search 부호 반전, plan_task 역행 3.7배 증폭이
이 예측과 정합). 확정된 것은 "**v1-teacher KD와 비스택**"까지이고
**matched-teacher KD는 미검증**. 레인 상태: 마감 아님 — §5의 Phase 2(레지스터
설계 반복 + matched-teacher 경로)로 재개. research_log.md 07-08 밤 엔트리
2건도 같은 취지로 정정됨.

전체 증거 체인(정보 인벤토리 → referee 사망 → 어텐션 싱크/필러 프로브 → 설계):
`experiments/artifacts/oof_error_text_signals_20260707/diag_serializer_headroom_20260707.json`
(+ 동봉 프로브 스크립트). 이 문서는 실행 계획만 담는다.

## 0. 코드 전달 (커밋 없음 주의)

`current_v7`/`current_v7r`은 **미커밋 워킹트리** 상태(script.py +
train_transformer.py 등록). `colab/cloud_sync.py push`가 워킹트리 전체를
번들하므로 push만 하면 레인에 전달된다 — VM에서 git pull 금지. 이 문서도
untracked로 함께 실린다. results.csv 회수는 `cloud_sync.py pull`로만.

## 1. 후보 정의

### current_v7 (= v1 보존 + 조기 state 라인 + tail echo)

```
current: <프롬프트>                                        ← v1 그대로
state: last=<액션>:<버킷> prev=<액션>:<버킷> ptype=<클래스>[:<앵커>] rerun=<y|n>
meta/workspace/actions/last_user/args/results               ← v1 바이트 동일
state2: last=<액션>:<버킷> ptype=<클래스>[:<앵커>] rerun=<y|n>   ← 최종 라인
```

- 근거 요약: v1 전 라인 보존(압축 3연패의 메커니즘 후보 = 어텐션 평준화 회피),
  조기 배치(causal 노출 + 절단 안전), tail echo(분류 토큰의 비-싱크 어텐션
  0.263이 마지막 40토큰에 집중 — 실측), 앵커(부하-지탱 프롬프트 토큰의 중복
  착지점). 버킷/ptype/rerun 규칙과 커버리지 감사는 위 JSON 참조.
- 토큰(HCX, 8k 샘플): mean 241.3 (+40.6 vs v1), p99 358, **max 402 — len384
  초과 0.075%는 잉여 echo만 잘림**(조기 사본 생존, 허용).
- 추론 비용 전망: +20% 토큰 → 단순 비례로 ~7:52/10:00 (kd_m8_refit 6:32 기준).

### current_v7r (= v7 + 조기 레지스터 라인, 단일변수 증분)

v7의 state 라인 다음에 상수 라인 `reg: $$$...$` ("$"×64 = HCX에서 `$$$$` 동일
토큰 16개) 삽입. 나머지는 v7과 동일.

- 근거: 상수 K/V 레지스터 — 자연 잡음은 행마다 다른 value를 어텐션 합에
  주입하지만 상수 벽은 학습 가능한 상수 바이어스로 대체(ViT register 유사).
  배포 모델 프로브에서 $ 벽은 자연 meta 잡음과 동급 질량(0.034 vs 0.033)을
  흡수하며 구조 교란 없음. **미지수는 3에폭 파인튜닝이 레지스터 용법을
  학습하는가** — 이 스크린이 측정하는 것.
- 벽 토큰 선정 감사(2026-07-08): `#/~/%/*/-/_/.`는 연속열이 1토큰으로 붕괴
  (슬롯 1개 — 부적합), `|`는 v1 필드 구분자와 충돌, `@`는 코퍼스 1.17% 충돌.
  결선 `$`/`^`/`&`/`░` 어텐션 프로브에서 **$가 흡수 최대(0.034), 충돌 0.02%,
  사전학습 충분한 중립 토큰**으로 우승. ░(충돌 0%)는 근-초기화 임베딩 리스크로 차점.
- 토큰: mean 261.3, p99 378, max 422 — **len384 초과 0.625%, 역시 echo만
  잘림**. 추론 전망 ~+31% → ~8:35/10:00 (단일 모델 팩 기준 여유, 단 이 위에
  앙상블 레그 추가는 불가 수준의 마진임을 인지).

## 2. 실험 순서와 게이트

판정 원칙: 동일 seed42·동일 fixed split 대조(v2/R-Drop 스크린과 같은 프로토콜).
KD 레시피의 절대 판정은 Public 전용(KD-fold-leak 룰) — 스크린끼리의 델타만 신뢰.

### Screen 1 — v7 논-KD vs v1 앵커 `0.769796`

```bash
train_transformer.py --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B \
  --lr 2e-5 --device cuda --split session --serializer current_v7 --max-length 384 --epochs 3 \
  --batch-size 16 --grad-accum-steps 1 --gradient-checkpointing \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 --no-research-log \
  --seed 42 --save-fp16 --save-val-model \
  --output-dir <lane>/models/v7_hcx05b_screen --experiment-suffix v7_hcx05b_screen \
  --notes 'current_v7 (state+anchor+echo) non-KD screen vs v1 anchor 0.769796'
```

- **통과: fixed 2stage ≥ 0.771796 (+0.002, 2026-07-08 사용자 지시로 +0.005→
  +0.002 완화 — 이 repo의 표준 노이즈 플로어와 일치시킴)**. R-Drop(+0.0064)이
  게이트 통과 전례, v2(-0.0010)가 탈락 전례.
- aggregate만 보지 말 것 — 약클래스 개별 확인: list_directory / read_file /
  grep_search / glob_pattern / run_bash / run_tests. 사전등록 리스크: v2에서
  ask_user +0.05가 list/read -0.019에 상쇄된 전례, R-Drop의 read_file -0.009
  전례 — **read/list 역행 여부가 핵심 관찰 항목. 게이트가 완화된 만큼, 통과해도
  약클래스 역행이 크면(개별 -0.01 이상) 통과 무효로 취급하고 다음 단계 보류.**

### Screen 2 — (Screen 1 통과 시) v7 KD vs KD 앵커 `0.787801`

kd_m8_refit 레시피에서 serializer만 교체. teacher 로짓은 id-매칭이라
`m8_qwen35_refit_train70k_fp16.pt` 그대로 재사용(재export 불필요).

```bash
train_transformer.py --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B \
  --lr 2e-5 --device cuda --split session --serializer current_v7 --max-length 384 --epochs 3 \
  --batch-size 16 --grad-accum-steps 1 --gradient-checkpointing \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 --no-research-log \
  --seed 42 --save-fp16 --save-val-model \
  --distill-logits experiments/logits/m8_qwen35_refit_train70k_fp16.pt \
  --distill-alpha 0.5 --distill-temp 3.0 \
  --output-dir <lane>/models/kd_v7_hcx05b_screen --experiment-suffix kd_v7_hcx05b_screen \
  --notes 'current_v7 KD screen (M8 teacher) vs kd anchor 0.787801'
```

- 앵커 `20260707_100521_..._kd_hcx_m8_screen_s42` (fixed 2stage 0.787801).
  양쪽 다 teacher-낙관을 공유하므로 스크린간 델타는 유효한 상대 대조.
- **통과: 2stage ≥ 0.789801 (+0.002, 07-08 완화)** → `--final-model
  --final-only` refit → `package_submission.py --no-sparse --requirements
  requirements_qwen3.txt` → 클린 추출 오프라인 스모크(AGENTS.md) 까지는
  **자율 진행**(사용자 부재 시에도 로컬/레인 산출물 생성은 안전 — 되돌릴 수
  있고 팀 공유 상태에 영향 없음). **Dacon 실제 제출(슬롯 소모, 팀 공유 예산,
  Claude에게 제출 도구 없음)은 여기서 멈추고 사용자 확인 대기** — 패키징된
  zip과 스모크 결과만 보고.
- Screen 2 실패 시: v7은 논-KD 전용 레버로 기록만(R-Drop과 같은 지위),
  refit/슬롯 없음.

### Screen 3 — (Screen 1 통과 시) v7r vs v7, 단일변수 증분

Screen 1 커맨드에서 `--serializer current_v7r`,
`--experiment-suffix v7r_hcx05b_screen`만 변경. 대조는 **Screen 1의 v7 결과**
(동일 seed/split이므로 직접 비교).

- **통과: v7 대비 +0.002 이상** (07-08 완화, 원래 +0.003; 같은 방향 일관 델타).
  통과 시 KD 체인도 v7r로 — 단 Screen 2가 이미 v7로 Public 슬롯까지 간
  상태라면 v7r의 KD/refit/슬롯은 별도 판단 대상으로 보류하고 사용자에게
  보고(같은 밤에 두 번째 슬롯 자동 소모는 하지 않음).
- Screen 1(v7) 실패 시: v7r은 독립 메커니즘(레지스터)이라 즉사는 아니지만
  우선순위 하락 — 다른 레인 전부 소진된 유휴 시간에만 v1 + reg 라인 단독
  변형(state 없이)으로 재구성해 태울 것. 추격 금지.

## 3. 판정 기록

- 스크린 결과 행: `experiments/results.csv` (`cloud_sync.py pull`).
- 레인 판정(통과/폐쇄): `research_log.md`에 결정만.
- 제출 시: `leaderboard_calibration.md` 원장 + `final_summary.md`는 Public
  개선 시에만.

## 4. 사전등록 리스크 요약

1. 사후 선형 상한은 +0.0003(referee 실측) — v7의 베팅은 학습 중 비선형
   활용/어텐션 재배치이며, 실패 확률이 낮지 않다(인접 셀 전례). 실패도
   "표현-정규화 셀 폐쇄"라는 정보값이 있다.
2. 탐색 클래스 재배치 리스크(read/list 역행) — 약클래스 개별 판정 필수.
3. v7r 레지스터는 문헌-근거(ViT register)이며 파인튜닝-only 채택은 미검증.
4. len384 초과 행(v7 0.075% / v7r 0.625%)은 설계상 잉여 echo만 잘림 — 별도
   조치 불요, 단 v7r에서 echo 손실률이 8배임을 스크린 해석 시 참고.

## 5. Phase 2: 레지스터 설계 반복 (07-08 오후 — §6 v8 복합에 우선순위 양보, 폴백으로 강등)

**상태 변경(사용자 지시)**: 시간 제약상 one-variable 스윕 대신 §6의 복합
재설계(current_v8)를 직행 스크린한다. 아래 5a 변형 3종(v7rl/v7rm/v7rd)은
구현·검증 완료 상태로 유지하되, **v8이 v7r을 못 이길 때의 폴백**으로만 태운다.
5b(matched-teacher KD 경로)는 승자가 무엇이든 그대로 유효.

Phase 1이 확정한 것: 레지스터 메커니즘 실재(v7r = v7 +0.004429, v1 +0.007165;
plan_task 완전 회복). 미해결: (a) grep_search -0.0101(v7 대비) 역행, (b) 설계
자유도(용량 16슬롯·단일 토큰종·조기 단일 배치)가 전부 1차 추정값, (c) 챔피언
티어 진입 경로(KD)가 v1-teacher 교란으로 미검증.

### 5a. 레지스터 설계 스크린 3종 (논-KD, 각 ~35-37분, 유휴 레인)

구현·등록 완료(`script.py`/`train_transformer.py`, 미커밋 — push로 전달).
70k 전행 구조 불변식 검증 통과. 셋 다 **v7r 앵커 `0.776961`과 동일 seed42/
fixed 대조**, 서로 독립(순서 무관, 앞 결과 대기 불필요 — 유휴 시간에 연속
소진 가능). 커맨드는 Screen 1과 동일하고 `--serializer`/`--experiment-suffix`
만 교체.

| 변형 | 설계 변수 (v7r 대비 단일) | 레지스터 구성 | 토큰(70k 실측, 동일 프로토콜) |
|---|---|---|---|
| `current_v7rl` | 용량 16→4슬롯 | `$`×16 | mean 249.1, p99 366, >384: 0.207% |
| `current_v7rm` | 슬롯 다양화(동일 용량 16) | `$$$$`·`^^^^`·`&&`·`░` 각 4슬롯 | mean 261.1, p99 378, >384: 0.594% |
| `current_v7rd` | 배치 분할(조기 8 + echo 직전 8) | `$`×32 조기 + `reg2: $`×32 후미 | mean 266.1, p99 383, >384: 0.914% |

(동일 프로토콜 재측정 기준선: v7 mean 241.1/>384 0.100%, v7r mean 261.1/>384
0.594%. §1의 구수치는 special-token 처리 다른 구프로토콜 — 이 표가 기준.)

- 가설 매핑: v7rl = grep 역행이 과흡수라면 회복(ViT 문헌은 4슬롯 충분);
  v7rm = 동일-토큰 슬롯은 RoPE 위상만 다른 중복 — 임베딩 구분이 헤드별
  슬롯 지정을 가능케 함(ViT 레지스터는 서로 다른 학습 벡터); v7rd = 분류
  토큰의 꼬리-국소 창(마지막 40토큰 0.263)에 학습된 집계 버퍼 제공. v7rd의
  후미 레지스터는 echo **앞** 배치 — 오버플로 시 echo부터 잘리고 분류 위치는
  항상 신호 토큰 위에 있도록(무학습 junk의 꼬리 하이재킹 0.250 실측이 근거).
- **게이트: v7r 대비 +0.002 이상이면 승자 교체, ±0.002 이내면 v7r 유지**(단
  v7rl은 동률이어도 12토큰 절감 메모로 기록). 약클래스 개별 확인 필수 —
  **grep_search 회복 여부와 plan_task 이득(v1 대비 +0.0138) 유지가 핵심 관찰
  항목**; plan_task가 v1 아래로 떨어지면 aggregate 통과라도 무효.

### 5b. 챔피언 티어 진입 (KD) — 승자 확정 후

1. **싼 메커니즘 확인(~35분, 선택)**: v1-teacher 그대로, 승자 serializer로
   `--distill-alpha 0.3` 재스크린. 역-그래디언트 가설이 맞다면 alpha0.5의
   0.783032보다 개선되고 논-KD와의 갭이 좁아져야 함. 8시간 투입 전 확증용.
2. **본 실험(~9-10h GPU + 스크린 35분)**: M8 teacher(Qwen3.5-0.8B)를 승자
   serializer로 재학습(전례 29,368s ≈ 8.2h) → train70k 로짓 재export →
   KD 스크린 vs 앵커 0.787801. **통과(+0.002) 시에만** refit → 패키징 →
   오프라인 스모크 → 사용자 제출(1슬롯, vs 0.7891).
   - 레인 C(M9 9B teacher) 결과가 먼저 나오면 "어느 teacher를 재학습할지"를
     그 시점에 재판단 — 더 강한 teacher의 matched 재학습이 상한이 높다.
   - 기대치 사전등록: matched teacher라도 논-KD 델타(+0.007) 전량 스택 보장
     없음 — KD 자체가 plan_task↔ask_user류 안정화를 일부 중복 제공.

## 6. Phase 3 (2026-07-08 오후, 사용자 승인): 복합 재설계 current_v8 + 위성 v8t

시간 제약으로 one-variable 원칙을 이 셀에 한해 유예 — 검증된 조각을 한 번에
합성한 "직관적 최선" 구조를 직행 스크린하고, 이기면 새 논-KD 베이스로 삼는다
(사용자 결정). 구현·등록·70k 검증 완료, push됨(양 레인, 03:01/03:01 UTC 번들).

### current_v8 구조

```
current: <프롬프트>                                   ← v1 그대로
state: last/prev:버킷 ptype[:앵커] rerun turn=05/mid lang=python!ts   ← v7 state + v6 구간화
$$$…$ ($×64 = 16슬롯)                                 ← 구 meta 라인 자리 통째 (키워드 포함 치환)
workspace: dirty=.. ci=.. ^^^…^ (^×48≈13슬롯) open=..  ← loc=/langs= 자리만 벽
actions / last_user / args / results                  ← v1 바이트 동일
state2: last:버킷 ptype rerun turn=05/mid              ← 얇은 echo + 국면 비트
```

설계 결정 요약(전체 논거는 대화·research_log 07-08):
- 벽은 in-place(C1 검증 유일 배치), 사이트별 문자 분리($/^), 전용 reg 라인
  삭제(총 ~29슬롯 = 자연잡음+v7r 검증량, 과흡수 회피). dirty/ci는 유지.
- 구간화 착지점은 state(조기)+echo(꼬리). echo는 얇게 — 분류 창 40토큰을
  echo와 최신 result_summary가 나눠 쓰므로 echo 비대화는 최고 신호를 창
  밖으로 밀어낸다.
- 버리는 내용: tier/lang_pref/budget/elapsed/loc 원시값+키워드 (xmeta
  +0.0002 실측). turn은 05/mid로, 언어구성은 python!ts로 생존.

### current_v8t (위성, 꼬리 질문 실측)

v8 + echo 직전 꼬리 벽 `&`×16(8슬롯). "분류 창 내부의 학습된 집계 버퍼"
가설 vs "무학습 junk 꼬리 하이재킹 0.250" 리스크를 직접 판정. ④(echo에 뭘
실을지)와 "벽 3개째" 질문을 하나의 셀로 답한다.

### 토큰 실측(70k 전행, special-token 포함 프로토콜)

| | mean | p99 | max | >384 |
|---|---:|---:|---:|---:|
| v8 | 242.4 | 359 | 432 | 0.111% |
| v8t | 251.4 | 368 | 441 | 0.251% |
| (참고 v7r) | 261.1 | 378 | 451 | 0.594% |

v7r보다 -19토큰 — 추론 마진 오히려 개선.

### 실행과 게이트

- 레인 A=v8, 레인 C=v8t (둘 다 RTX PRO 6000 — 앵커 v7/v7r과 동일 GPU 클래스,
  동일 seed42/fixed 레시피). 데몬 사망 상태라 하트비트 fresh 감지 시 자동
  런칭하는 로컬 워처 2개 가동 중 — **사용자가 colab_runner.ipynb /
  colab_runner_c.ipynb에서 [mount]→[bootstrap]→[agent]만 실행하면 됨.**
- **v8 게이트: vs v7r 0.776961 +0.002 이상 → 새 논-KD 승자·동결 후보** (vs v1
  0.769796도 병기 보고). 관찰 필수: grep_search 회복 여부(v7r의 -0.0101이
  reg 라인 삭제로 고쳐지는가), plan_task 유지(v1 대비 +0.0138 방어).
- **v8t 게이트: vs 형제 v8 +0.002 이상 → 꼬리 벽 채택.**
- 복합 셀의 해석 한계(사전등록): v8이 지면 원인 귀속 불가(내용 손실/벽 배치/
  reg 삭제 중 어느 것인지) — 그 경우 §5a 폴백으로 분해 재검.
- 승자 동결 목표 ~07-10 → §5b matched-teacher 경로(teacher 재학습 ~8.2h +
  로짓 재export + KD 재스크린 + refit + Public 1슬롯).

## 7. Phase 3 결과: v8/v8t 대실패 — v7r 챔피언 유지 (§5a 폴백 또는 §5b 직행 대기)

**결과(둘 다 게이트 미달을 훨씬 넘어 방향 반전)**:
- v8: 2stage **0.764886** vs v7r 0.776961 = **-0.012075**. v1 앵커(0.769796)보다도
  낮음(-0.004910). 표적 클래스 거의 전부 동시 역행(web_search -0.0307,
  list_directory -0.0303, read_file -0.0283, plan_task -0.0176, lint -0.0174,
  grep_search -0.0108, glob_pattern -0.0105, ask_user -0.0079).
- v8t: 2stage **0.757931**, v8 대비 추가 -0.006955(ask_user -0.0297,
  run_bash -0.0236, run_tests -0.0153 추가 붕괴) — **집계 버퍼 가설 기각**,
  무학습 꼬리 junk 하이재킹 경고가 학습 후에도 재현.

**판정: v8/v8t 폐기. current_v7r(0.776961)이 논-KD 챔피언으로 유지.**

**원인 가설(복합 셀이라 확정 불가, §6 사전등록대로)**: v7r의 레지스터는 독립
`reg:` 라인(구문적으로 분리)이었는데 v8은 `^` 벽을 workspace 라인 내부
`ci=...`↔`open=<경로>` 사이에 인라인 삽입 — 가장 무너진 클래스가 정확히
`open=` 파일경로 의존 탐색 클래스라 "위치 규칙성 파괴"가 유력. 교훈: 레지스터는
**독립 라인**이어야 하고, 기존 필드 내부에 끼워 넣으면 안 됨.

**남은 경로(사용자 판단 대기)**:
1. §5a 폴백 실행 — v7rl/v7rm/v7rd(전부 독립 reg 라인 유지, 용량/다양성/배치만
   변경, 구현·검증 완료, 각 ~35분, 레인 유휴 시 바로 태울 수 있음).
2. v7r을 그대로 §5b matched-teacher KD 경로로 직행(가장 빠른 챔피언 진입로,
   ~8.2h + 스크린 35분).

### §6 결과 (2026-07-08): v8/v8t 대실패 — 폐기

- v8 2stage **0.764886** (v7r -0.012075, v1보다도 -0.004910), v8t **0.757931**
  (v8 대비 -0.006955). 전 표적클래스 동시 역행; v8t는 ask_user/run_bash/
  run_tests 추가 붕괴(꼬리 하이재킹 리스크 실현).
- 채택 법칙: **벽은 추가만, 치환 금지** — 자연 저정보 토큰의 삭제(v5/v6)도
  질량-보존 치환(v8)도 죽고, 추가(v7r)만 산다. C1 프로브의 기계적 동등성은
  고정 모델 측정이라 학습 동역학으로 전이 안 됨(사전등록 한계 실현).
- 파생 판정: §5a **v7rd 폐기**(v8t와 동일 메커니즘), v7rl/v7rm 유휴 백로그.
- 잔존 경로: v7r 동결 게이트 = **s777 시드 복제 페어(v1 vs v7r 논-KD)** —
  hcx05_s777 캘리브레이션(-0.0202 순수 시드)상 v7r의 +0.0072도 단일-시드
  미확정 등급이므로, 통과 시에만 §5b matched-teacher(8h) 투입.

## 7. Phase 4 (2026-07-08, 사용자 카드): 태그 스키마 current_v9o / current_v9f

마지막 직렬화기 세대. v7r에서 **우리가 발명한 마커 층만** 태그로 교체(자연
토큰·내용·배치 전부 바이트 동일 — v8 법칙 준수). 벽 표면형 카드(`---`,
`<empty>`)는 토크나이저 실측으로 기각(`---` 연속 1토큰, `<empty>` 슬롯 효율
절반+의미 오염).

- `current_v9o`: `field: ` → `<field> ` (여는 태그만). mean 269.7(+8.6),
  >384 1.28%.
- `current_v9f`: + `</field>` 닫는 태그. mean 292.8(+31.7), **>384 6.46% —
  절단 교란 사전등록**(echo 손실 행 증가; v9f<v9o면 원인 분리 불가).
- 근거: HCX 사전학습이 태그-구획(StarCoder 계열 added_tokens), `<`/`</` 단일
  토큰. 게이트: vs v7r 0.776961 +0.002(+형제간 비교), 단일-시드 미확정 등급
  주의(hcx05_s777).
- 실행: 레인 A 단독 순차(v9o → v9f), 워처 자동 런칭. 판정 후 직렬화기 동결
  → §5b matched-teacher 경로(teacher 선택은 M9-KD Public 결과 참조).

### §7 결과 (2026-07-08): v9o 대실패(-0.0172, 13/14클래스 동시 하락) → v9f 반전 회복(-0.0027, 사실상 동률)

- v9o(여는 태그만) 2stage **0.759719** — v7r 대비 -0.017242, v1보다도 낮음.
  전 14클래스 중 13개 동시 하락(web_search -0.0526, lint -0.0450, plan_task
  -0.0441 최대). **폐기.**
- v9f(여닫이 완결) 2stage **0.774215** — v9o 대비 **+0.014496 회복**, v7r
  대비 -0.002746(노이즈 플로어 안, 게이트 미달). ask_user만 뚜렷 손실
  (-0.0276), 나머지 대부분 회복·일부 역전(grep_search/run_bash/list_directory
  +).
- 정정: "닫는 태그=오버플로 손실만 추가" 예측 틀림 — **미완성 마커(짝 없는
  여는 태그)가 사전학습 이탈이라 더 해롭고, 완결 태그 쌍이 오버플로 비용을
  상쇄**. 태그 레인은 죽은 카드는 아니나 이 시점 추가 우위 없음 — **v7r
  챔피언 유지, 직렬화기 동결**.
- 다음: s777 시드 복제 게이트(v1 vs v7r 논-KD) → 통과 시 §5b matched-teacher
  착수.

## 8. Phase 5 (2026-07-08, 최종 큐): 공리 기반 단일변수 3셀 + 동결

§7 결과: v9o 0.759719(-0.0172 — 반쪽 마크업 유해 확증), v9f 0.774215(-0.0027,
6.46% 절단 핸디캡 하). 설계 공리계는 research_log 07-08 참조.

| 셀 | 레인 | 내용 | 게이트 |
|---|---|---|---|
| v1_s777 / v7r_s777 | A (완료/진행) | 동결 게이트: 두 번째 시드에서 v7r>v1 방향 확인 | 방향 유지 |
| v9f@416 | A (자동 체인) | 절단 핸디캡 제거 재대결 | vs v7r **+0.004** (배포 ~9:32 프리미엄) |
| v7rb | C (부팅 대기) | v7r + state에 turn/lang 빈 사본 (+10.1tok, 기능적 치환; budget/elapsed 제외) | vs v7r +0.002 |

- 백로그(동결 후 여유 시에만): loc 빈, open_hit(CPU 감사 선행), 벽 2호기.
- 이후: 승자 동결 → §5b matched-teacher(teacher 재학습 ~8.2h + 로짓 export +
  KD 스크린 + refit + Public 1슬롯, teacher 선택은 M9-KD Public 참조).
- v9f와 v7rb는 직교 층(마커/내용) — 둘 다 통과 시 합성은 기계적(한 줄), 재실험
  불요.
