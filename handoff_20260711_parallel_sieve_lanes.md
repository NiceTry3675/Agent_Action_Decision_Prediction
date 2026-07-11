# Handoff — 시브 병렬 2레인 (KD 감쇠 × soft 재배정), 런칭 직전 상태

- **작성:** 2026-07-11 (이 문서가 이전 세션의 전체 맥락을 승계한다)
- **선행 문서:** `archive/handoff_20260710_final_two_theories.md` (07-11 애드덤 포함 — T1은
  시브로 소비되어 Public 양성, T2 특권잔차는 frozen gate REJECT로 전면 종료)
- **읽기 순서:** 이 문서 → `final_summary.md` → `leaderboard_calibration.md` →
  `research_log.md` 2026-07-11 항목들

---

## 0. 현재 상태 한 장 요약

- **챔피언:** `kd_sieve_ca_s42.zip` Public **0.7938** (sieve×condalpha 레시피 스택,
  런타임 5:58). 리더 0.797, **잔여 갭 ~0.0032.**
- **마감:** 2026-07-15 (수) 10:00 KST. **마지막 날에도 슬롯 10개** — 잔여
  07-12/13/14 각 10장 + 07-15 오전 10장.
- **사용자 방침:** 시드 추첨보다 새 돌파구/최적화 우선. 신규 리핏의 베이스
  레시피는 sieve×condalpha (`--distill-alpha-weak 0.7` 로컬 구현 완료).
- **오늘 확인된 핵심 신호:** 백본-그래디언트 시브(+0.0026)와 조건부 KD α(+0.0021)
  가 같은 "노이즈 행 supervision 완화" 축에서 **가산**됐다. 이 축은 아직 포화가
  아니며, 남은 두 손잡이가 아래 두 레인이다.
- **두 레인 모두 코드·payload·검증·번들 push까지 완료. A100 러너 부착과 launch
  명령 실행만 남았다.**

## 1. 레인 A — c=0 KD 브랜치 감쇠 (`--consensus-kd-weights`)

### 가설
M8 full-refit teacher는 자기 학습행을 암기했으므로 c=0 행(12,776행, full M8도
89% 오답)의 KD 타깃 ≈ 노이즈 라벨의 원핫. 시브가 hard CE에서 차단한 독이 KD
브랜치로 그대로 흐른다. 챔피언 시브와 정확히 대칭인 메커니즘(KD head gradient
유지, **KD backbone gradient만 c-bin별 [0, 0.25, 0.75, 1]**, 클래스 정규화 동일)
으로 차단한다.

### 구현 (완료, push됨)
- `train_transformer.py`: `--consensus-kd-weights` 플래그.
  `forward_with_consensus_sieve`가 kd_backbone_scales를 받으면 3-튜플
  (ordinary, hard, kd) 반환; KD KL은 kd-gated logits로 계산(수치 동일,
  gradient만 상이). **미설정 시 기존과 비트 동일.**
- 유닛테스트 `tests/test_consensus_kd_sieve.py` 4개 + 기존 회귀
  (`test_consensus_sieve`, `test_distill_alpha_weak`) 통과 확인됨.

### 런칭 (lane A = `AADP_exchange`, `colab_runner.ipynb`)
[mount]→[bootstrap]→[agent] 후:

```bash
python colab/cloud_sync.py launch train_transformer.py -- \
  --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B --lr 2e-5 --device cuda \
  --split session --serializer current_v1 --max-length 384 --epochs 3 --batch-size 16 \
  --grad-accum-steps 1 --eval-batch-size 64 --gradient-checkpointing \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --distill-logits /content/drive/MyDrive/AADP_exchange/teacher/m8_qwen35_refit_train70k_fp16.pt \
  --distill-alpha 0.5 --distill-alpha-weak 0.7 --distill-temp 3.0 \
  --consensus-reliability /content/drive/MyDrive/AADP_exchange/anchors/20260710_m7_m8_v6_oof_consensus.pt \
  --consensus-backbone-weights 0,0.25,0.75,1 \
  --consensus-kd-weights 0,0.25,0.75,1 \
  --tokenize-batch-size 1024 --seed 42 --no-research-log --save-fp16 \
  --epoch-checkpoint-dir /content/drive/MyDrive/AADP_exchange/models/kd_sieve_ca_kdsv_refit_ckpt \
  --output-dir /content/drive/MyDrive/AADP_exchange/models/kd_sieve_ca_kdsv_refit \
  --experiment-suffix kd_sieve_ca_kdsv_refit_s42 \
  --notes 'lane A single variable: champion kd_sieve_ca_s42 + consensus KD-branch backbone sieve 0,0.25,0.75,1 (KD head full, class-normalized); zero bias/rules; final-only Public candidate' \
  --final-model --final-only
```

챔피언 `kd_sieve_ca_s42` 커맨드 대비 **추가된 것은 `--consensus-kd-weights` 한
줄과 출력 경로/suffix뿐**이다 (단일 변수).

### 런 로그 검증 포인트
`consensus sieve:` 라인에 `kd_weights=[0.0, 0.25, 0.75, 1.0]` 표기,
histogram `c0 12,776 / c1 3,806 / c2 4,811 / c3 48,607`, distill matched
70000/80000, alpha_weak=0.7 weak_rows=28782.

## 2. 레인 B — soft/source-aware 시브 v2 (행 재배정 payload 스왑)

### 가설 (팀 제안, 로컬 검증 완료)
count `c`는 정보 압축 손실이 크다. fixed-val에서 같은 c=1이라도 학생 정확도가
M8-only 0.682 / M7-only 0.336 / v6-only 0.361, 같은 c=2에서도 M7+M8 0.846 vs
M7+v6 0.585로 갈린다(이 세션에서 재검증한 수치). soft score
`r = pM7 + 2·pM8 + pv6 + pXbase + pXlarge` (session-OOF T=1 gold-label 확률;
**Xlarge = xlmr_large len192** — 팀 진단 AUC 0.9747을 이 변형이 정확히 재현,
p2_large384는 0.9741)로 true class 내 순위를 매겨, **클래스별 티어 히스토그램을
비트 동일하게 유지한 채 어느 행이 어느 weight를 받는지만 재배정**한다.

### 산출물 (완료, 스테이징됨)
- 빌더: `build_sieve_v2_payload.py` (5소스 커버리지/중복/라벨 fail-closed 감사,
  히스토그램 동일성 assert, (r desc, id asc) 안정 정렬, 전이 행렬·AUC 진단 내장)
- payload: `experiments/artifacts/20260711_sieve_v2_soft_reassign.pt`
  (sha256 `8fcd4bb9...5719`), **Drive `AADP_exchange_b/anchors/`에 업로드 완료.**
  기존 `oof_correct_consensus_reliability` 스키마의 pseudo-count 인코딩이라
  **train 코드 변경 0** — artifact 경로 스왑만으로 동작.
- 재배정 규모: **9,315행(13.3%)** 티어 변경 (c2→3 1,822 / c1→0 1,147 등 양방향).
  진단 AUC(fixed-val 학생 정오 예측, 읽기 전용): count 0.9549 → soft r 0.9747 →
  재배정 4-티어 0.9595.
- report: `experiments/artifacts/20260711_sieve_v2_soft_reassign.report.json`

### 런칭 (lane B = `AADP_exchange_b`, `colab_runner_b.ipynb`)
[mount]→[bootstrap]→[agent] 후 (모든 cloud_sync 호출에
`AADP_EXCHANGE_DIR=AADP_exchange_b` 접두 필수):

```bash
AADP_EXCHANGE_DIR=AADP_exchange_b python colab/cloud_sync.py launch train_transformer.py -- \
  --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B --lr 2e-5 --device cuda \
  --split session --serializer current_v1 --max-length 384 --epochs 3 --batch-size 16 \
  --grad-accum-steps 1 --eval-batch-size 64 --gradient-checkpointing \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --distill-logits /content/drive/MyDrive/AADP_exchange/teacher/m8_qwen35_refit_train70k_fp16.pt \
  --distill-alpha 0.5 --distill-alpha-weak 0.7 --distill-temp 3.0 \
  --consensus-reliability /content/drive/MyDrive/AADP_exchange_b/anchors/20260711_sieve_v2_soft_reassign.pt \
  --consensus-backbone-weights 0,0.25,0.75,1 \
  --tokenize-batch-size 1024 --seed 42 --no-research-log --save-fp16 \
  --epoch-checkpoint-dir /content/drive/MyDrive/AADP_exchange_b/models/kd_sieve_ca_v2rows_refit_ckpt \
  --output-dir /content/drive/MyDrive/AADP_exchange_b/models/kd_sieve_ca_v2rows_refit \
  --experiment-suffix kd_sieve_ca_v2rows_refit_s42 \
  --notes 'lane B single variable: champion kd_sieve_ca_s42 with sieve v2 soft/source-aware row reassignment payload (histogram-preserving, formula pM7+2pM8+pv6+pXbase+pXlarge, Xlarge=xlmr_large192); zero bias/rules; final-only Public candidate' \
  --final-model --final-only
```

챔피언 대비 변수는 **`--consensus-reliability` 경로 하나** (KD weights 없음 —
레인 A와 독립).

### 사전 등록 제약
- soft 공식·가중(M8 ×2)·티어 매핑은 **고정** — fixed-val AUC로 공식을 더 격자
  탐색하지 않는다 (설계 선택에 이미 val 정보가 약하게 들어갔으므로 추가 탐색 금지).
- 히스토그램 보존이 총 질량·클래스 정규화 계수를 보존하므로 이것은 "어느 행"
  단일 변수다. 레인 A는 "어느 브랜치" 단일 변수. 두 축은 직교.

## 3. 공통 프로토콜 — 런 완료 후

1. hb 폴링: `python colab/cloud_sync.py hb` (lane B는 env 접두). 데몬이 종료 시
   자동 collect. `python colab/cloud_sync.py pull <run_name>`으로 results.csv 병합.
2. 모델 로컬 회수: `python colab/cloud_sync.py pull-model kd_sieve_ca_kdsv_refit`
   (lane B: env 접두 + `kd_sieve_ca_v2rows_refit`).
3. 패키징: `python package_submission.py --hf-dir experiments/incoming/models/<name> --no-sparse`.
   zip 이름 30자 이하·`submit` 접두 금지 — 예: `kdsv_s42.zip`, `v2rows_s42.zip`.
4. 오프라인 스모크 (재빌드 zip 클린 추출 + data/ 추가):
   `TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 python script.py`,
   가능하면 `CUDA_VISIBLE_DEVICES='' python script.py`.
5. 제출. **비교 기준은 각각 `kd_sieve_ca_s42` Public 0.7938, 매칭 seed42 단일
   변수. 노이즈 플로어 0.002.** 제출마다 `leaderboard_calibration.md`에 기록,
   결정은 `research_log.md`.
6. 승격 시 `final_summary.md`/`leaderboard_calibration.md`/`research_log.md` 최소
   갱신 (현행 챔피언 섹션 패턴 그대로).

### 이후 카드
- **둘 다 +0.002 이상 승리 시:** 스택 리핏(v2 payload + `--consensus-kd-weights`
  동시 적용) 한 장 — 두 축이 직교라 가산 기대. 이때 KD weights의
  reliability도 v2 payload를 그대로 공유한다(코드상 같은 artifact에서 읽음).
- **한쪽만 승리:** 승자만 새 챔피언·베이스로. 패자 축은 grid 연명 금지.
- **둘 다 실패:** 시브 축 포화로 판정. 잔여 후보는 챔피언 second seed(레시피
  확인+인스턴스 추첨 겸용), 교사축 전파(팀원 레인).
- kNN 애드온(하단 §5)은 v2 결과가 양성일 때만 검토.

## 4. 닫힌 레인 — 재개 금지

- **T2 반사실 미래잔차**: frozen teacher gate REJECT (T1−T2 macro −0.0393,
  3/3 fold 음수, actual oracle이 parent보다 나쁨). 미래 관측 계열 3번째 독립
  음성. artifacts: `20260711_future_p1_p2_teacher_gate.json` 외 2종.
- **weight soup**: 클린표면 연결성 실패(동일-레시피 리롤 페어에서도 중간점이
  끝점 아래). `soup_merge_eval.py`는 도구로만 잔존.
- FSM/suffix/템플릿 규칙, A4 kNN 독립 배포, v7r, R-Drop 스택, 학생 스케일업,
  Weak4 specialist/라우팅, 2-stage bias 주입: 전부 기존 문서대로 닫힘.
- **α_weak 격자(0.6/0.8)와 시브 c-weights 격자는 기대 제한적** — 두 레인이 모두
  실패한 뒤에만 고려.

## 5. 보류 자산 (필요 시)

- **soft consensus asset**: `experiments/artifacts/20260711_soft_consensus_asset.pt`
  (+`.report.json`) — 행별 3모델 p_true. c=0 내부 p_true_mean 스프레드 p10
  0.076 ~ p90 0.328. v2 payload가 이 신호를 흡수했으므로 독립 용도는 없음.
- **M8 OOF fold hidden kNN (애드온 진단)**: `run_m8_fold_hidden_exports.py` 1회
  런칭으로 Drive의 `AADP_exchange/models/m8_qwen35_oof_len400_ep3_fold{0,1,2}_ckpt`
  에서 held-out hidden 3개 export (fold 로짓과 argmax ≥99% 게이트 내장; fold
  로짓 3개는 `AADP_exchange_b/anchors/`에 스테이징 완료). 이후
  `build_reliability_v2.py --stage knn --hidden-caches <3 files>`로 합의율 계산.
  **채택 조건: r 위에 학생 정오 AUC를 추가로 올릴 때만** (v2보다 후순위).
- T2 인프라(`audit_future_donors.py`, `export_future_hidden_cache.py`,
  `probe_future_residual_gate.py`, `experiments/cache/*` 2개)는 종료된 레인의
  재현용으로만 보존.

## 6. 인프라 주의사항 (이번 세션에서 밟은 지뢰 포함)

- 두 레인 코드 번들은 `code_20260711_080658/080743_0bb32384_dirty` (commit
  0bb32384 + dirty 6: 이 세션의 신규 스크립트들). **로컬 코드를 더 고치면 해당
  레인에 재push 필수** — 레인별 번들은 공유되지 않는다.
- `cloud_sync.py push-anchor`는 파일명을 `anchor_val_logits.pt`로 **강제
  개명**한다 — 원명 유지가 필요하면 `rclone copy <file> gdrive:<lane>/anchors/`.
- 시브 계열 artifact는 전부 `usage_scope=full_data_refit_only` — fixed-val과
  함께 쓰면 train 스크립트가 fail-closed로 거부한다(정상 동작). 두 레인 모두
  **로컬 스크린 불가, Public 단발 판정**이 사전 등록된 프로토콜이다.
- `[agent]` 셀은 세션 내내 실행 상태여야 한다(키커널 idle 90분 후 회수). 런
  종료 후 `cloud_sync.py unassign`으로 즉시 반납.
- WSL에서 무거운 학습/대형 검증 금지(OOM으로 WSL 전체 사망). 로컬 3070 Ti는
  다른 작업 점유 중이었음.
- 커밋은 사용자 요청 시에만. 이 핸드오프 문서와 팀원 문서는 untracked 유지.

## 7. 이 세션의 untracked 작업 파일 목록

```text
build_sieve_v2_payload.py            # v2 payload 빌더 (레인 B 핵심)
build_reliability_v2.py              # soft consensus + kNN 스테이지
run_m8_fold_hidden_exports.py        # kNN용 fold hidden 드라이버 (보류)
export_oof_fold_hidden.py            # fold hidden export (보류)
audit_future_donors.py               # T2 (종료)
export_future_hidden_cache.py        # T2 (종료)
probe_future_residual_gate.py        # T2 (종료)
tests/test_consensus_kd_sieve.py     # 레인 A 테스트 (통과)
tests/test_future_donor_audit.py     # T2 테스트 (통과)
tests/test_future_residual_gate.py   # T2 테스트 (통과)
archive/handoff_20260710_final_two_theories.md  # 07-11 애드덤 2개 반영됨
handoff_20260711_parallel_sieve_lanes.md # 이 문서
```

`train_transformer.py`는 tracked 파일에 KD-시브 diff가 실려 있다(커밋 전 상태,
번들에는 포함됨). 회귀 확인 커맨드:
`.venv/bin/python -m unittest tests.test_consensus_kd_sieve tests.test_consensus_sieve tests.test_distill_alpha_weak`
