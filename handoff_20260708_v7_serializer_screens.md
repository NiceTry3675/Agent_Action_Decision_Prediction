# Handoff 2026-07-08: current_v7 / current_v7r 직렬화기 스크린 (피처 레인)

전략 문맥: 현 Public baseline **0.7891** = `kd_m8_refit.zip`(HCX-0.5B + M8 KD,
`final_summary.md`). 마감 ~07-14, 제출 10슬롯/일(타 레인 공유).
**GPU 우선순위: 2-teacher KD(레인 B), M9 교사 스케일(레인 C)이 먼저 — 이 문서의
스크린들은 유휴 레인에서만 태운다.** 스크린 자체는 슬롯 0개.

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

- **통과: fixed 2stage ≥ 0.7748 (+0.005)**. R-Drop(+0.0064)이 게이트 통과
  전례, v2(-0.0010)가 탈락 전례.
- aggregate만 보지 말 것 — 약클래스 개별 확인: list_directory / read_file /
  grep_search / glob_pattern / run_bash / run_tests. 사전등록 리스크: v2에서
  ask_user +0.05가 list/read -0.019에 상쇄된 전례, R-Drop의 read_file -0.009
  전례 — **read/list 역행 여부가 핵심 관찰 항목.**

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
- **통과: 2stage ≥ 0.7928 (+0.005)** → `--final-model --final-only` refit →
  `package_submission.py --no-sparse --requirements requirements_qwen3.txt` →
  클린 추출 오프라인 스모크(AGENTS.md) → **Public 1슬롯, 판정 vs 0.7891**
  (class_bias 0 유지, rules 없음 — kd_m8_refit과 조건 동일).
- Screen 2 실패 시: v7은 논-KD 전용 레버로 기록만(R-Drop과 같은 지위),
  refit/슬롯 없음.

### Screen 3 — (Screen 1 통과 시) v7r vs v7, 단일변수 증분

Screen 1 커맨드에서 `--serializer current_v7r`,
`--experiment-suffix v7r_hcx05b_screen`만 변경. 대조는 **Screen 1의 v7 결과**
(동일 seed/split이므로 직접 비교).

- **통과: v7 대비 +0.003 이상** (같은 방향 일관 델타). 통과 시 KD 체인도 v7r로.
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
