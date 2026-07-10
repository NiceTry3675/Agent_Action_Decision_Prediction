# Weak4 Routed Specialist — 최종 구현 스펙 (개정 2)

> 구현 담당 에이전트용 최종 사양 (2026-07-10 확정). 사양 협상은 완료됨.
> 구현 중 사양 변경이 필요해지면 임의 수정하지 말고 사용자에게 보고할 것.

개정 2 요지 (교차 검토 피드백 반영, 전부 코드 검증 완료): ① weak-only 학습
loss를 14-way가 아닌 **조건부 4-way 슬라이스**로, ② 블렌드를 mass_renorm이
아닌 **family-lock conditional4**로, ③ 라우팅 캡을 전역 마진이 아닌 **Weak4
내부 마진**으로, ④ generic cascade 확장이 아닌 **전용 weak4_specialist
경로**로 (cascade는 main 모델을 미리 로드하지 않는 독립 2-로드 구조라
in-place merge와 양립 불가, script.py:2472-2479 확인). 추가로 val-ID assert,
직렬화기 multiline 버그 수정, LoRA parity 프로토콜, tune/confirm 분리.

## Context

- 07-10 휴먼 리레이블(`experiments/artifacts/20260710_human_relabel/`) 결론:
  Weak4(read_file/grep_search/list_directory/glob_pattern) 라벨은 의미적 정답이
  아니라 합성 에이전트 정책 기록. 의미 이해 레인 종료(research_log 07-10 항목).
- 남은 헤드룸 = 클러스터 내부 오분류 ~2,200행/14k val. 신호는 최근-액션
  전이·결과 상태. 강한 전이 슬라이스는 이미 풀려 있음(glob 정답 82%가
  두-단계-전 grep, 슬라이스 정확도 98.4%) → 기대 가치는 attribution 명시화와
  모호 행 분별.
- 설계 계약: **main이 family(weak vs non-weak)를 고르고, specialist는 Weak4
  내부 순서만 교정한다.** 이 계약을 코드로 보장하는 것이 개정 2의 핵심.
- 마감 ~07-14, 제출 10/day. 판정: **fixed-val 튜닝(tune/confirm 분리) + 사전
  등록 게이트 + Public 1슬롯** (HCX 라인 OOF 부재, 신축 안 함 — 사용자 확정).
  레인 전부 가용, 런타임 직접 선택 가능(T4 포함) → 병렬 실행.

## 검증된 핵심 수치·사실

| 항목 | 값 |
|---|---|
| Weak4 canonical 인덱스 | 0=read_file, 1=grep_search, 2=list_directory, 3=glob_pattern (`script.py:13-28`) — **연속 0-3이라 4-way 슬라이스 시 라벨 리매핑 불필요** |
| main 앵커 val logits | `experiments/logits/20260707_100521_gpu_transformer_session_current_v1_len384_replay-last1_kd_hcx_m8_screen_s42_val_logits.pt` (14001행, `ids` 포함, raw macro 0.783852) |
| val에서 argmax-weak 비율 | 41.03% (5745/14001) |
| 챔피언 팩 후처리 | **전부 비어 있음** — class_bias 전부 0, rule_boosts/sparse 없음 (조립 시 assert) |
| 배포 zip | int8 512MB (`kd_m8_refit_int8`) → adapter +20MB 여유 충분 |
| weak-true 학습 행 | train 70k 중 28,782 (80% split ≈ 23k) |
| 웜스타트 소스 | 스크린: `AADP_exchange_c/models/kd_hcx_m8_screen` (**Drive에만 존재, val-held-out**). 최종: `experiments/incoming/models/kd_m8_refit/hf_model` (로컬 fp16 1.13GB; **val을 봤으므로 스크린 웜스타트 사용 금지 — 누수**) |
| `safe_text` | 개행 보존 (script.py:33-38) → **multiline prompt에서 `split("\n")` 삽입은 버그** |
| loss 슬라이스 전례 | explorer4 보조 loss가 정확히 같은 패턴 (train_transformer.py:564-575) |

## 1. 직렬화기 사양

### weak_nav_v1

current_v1(`serialize_transformer_sample_current`, script.py:139-181) 출력을
바이트 단위 보존, `current:` 다음에 한 줄 삽입:

```
nav: prev=<action|none>:<bucket> last=<action|none>:<bucket> repeat=<0|1|2|3+> turn=<exact/phase> arg=<kind>
```

- `last`/`prev` = assistant_action `action_names[-1]`/`[-2]` (v10 규약,
  script.py:1186-1187). 없으면 `none`.
- `<bucket>` = `route_count_bucket_from_result`(script.py:1073-1092)를 **해당
  이벤트 자신의** result_summary에 적용 (prev는 `action_events[-2]`의 result).
- `repeat` = 꼬리 연속 동일 액션 길이. **액션 없으면 `0`** (no-history와
  one-action 구분).
- `turn` = `turn_v6_token` (script.py:208-209). `arg` = `route_arg_kind`
  (script.py:1095-1114).
- **삽입 방식 (필수)**: `split("\n")` 금지 — prompt 개행 시 nav가 prompt
  내부에 박힘. `serialize_transformer_sample_current`를 **parts 리스트를
  반환하는 내부 헬퍼로 리팩터**하고(기존 함수는 `"\n".join(parts)` 유지 —
  바이트 동일), weak_nav_v1은 같은 parts에 nav를 index 1로 삽입. rfind 마커
  방식도 금지 (args/results 라인이 마커 문자열을 포함하면 오삽입).
- **회귀 테스트**: 70k 전행에서 nav 라인 제거 == current_v1 출력 바이트 일치.
- **금지**: hint regex(V11S_HINT_*), candidate_pool/route/op/scope/multiplicity,
  meta/workspace 변경, 재배열. 토큰 +25-30 → len384 유지.

### weak_nav_paths_v1 (rung 3 — 보류)

weak_nav_v1 + `last_paths:` 한 줄 (최신순 경로형 arg 값, dedupe, 최대 4개,
60자 절단, 원문만). **마감 상황상 기본 보류 — spec_v1/spec_nav에 집중.**
시도하게 되면 원문 버전 실패 시 정규화 변형(`last_path_types: basename/dir/
glob/ext:py` 등)을 대안으로 기록해 둠.

## 2. 코드 변경 목록

### train_transformer.py

1. `--serializer` choices(1335)에 `weak_nav_v1`, `weak_nav_paths_v1` 추가.
2. 신규 `--train-label-filter {none,weak4}` (기본 none): `add_replay_examples`
   직후(1114) train_idx를 weak-true로 필터. final-refit 두 지점(984, 1211)에도
   동일 헬퍼. 파서 가드: filter≠none이면 replay none·distill 미사용 강제.
3. **조건부 4-way loss (필수 — zero-weight 방식으로 대체 금지)**:
   `per_row_loss`(550-584)에서 filter 활성 시
   `loss_logits = logits[:, [0,1,2,3]]`, 라벨은 그대로(0-3 범위 assert),
   weight는 **weak4 4클래스 기준으로 계산한 4-way 가중치**
   (class_weight_power 0.5 유지). CE(551-557)와 focal `pt`(562) **모두**
   `loss_logits` 위에서 계산. 구현 패턴은 explorer4 보조 loss(564-575)와 동일.
   효과: smoothing·focal·softmax 분모가 배포 시의 conditional4와 일치, score
   4-13행은 gradient 0으로 원본 보존.
   `class_weights()`(53-60)의 zero-count 회귀 수정(부재 클래스 weight 0,
   존재 클래스만 평균 정규화)은 일반 위생으로 별도 적용하되, specialist
   정확성은 슬라이스가 담보.
4. **val-ID 동일성 assert (첫 optimizer step 전 실패해야 함)**: 신규
   `--assert-val-ids <anchor.pt>` — anchor payload(1.4MB, 레인에 push)를 로드해
   (a) 계산된 val id 집합 == anchor `ids` 집합 (14001행),
   (b) train id 집합과 anchor val id 집합 disjoint,
   (c) `classes == ALL_CLASSES`, `seed`/`split` 일치.
   추가: `--resume-from` 디렉토리에 `checkpoint_state.json` **부재** assert
   (있으면 epoch-resume로 오해석, 587-601).
5. 필터 활성 시 val 진단 출력(1142 이후): val true∈Weak4 행에서 컬럼 [0-3]
   argmax로 4-way macro-F1 + 4×4 confusion. (14-way macro는 무시.)
6. 변경 불필요(검증됨): `--tune-bias` 미전달 → raw logits 저장;
   `--resume-from` full-weights 로드 + fresh LoRA(417, 471-503); PeftModel
   `save_pretrained` = adapter+score만 저장(692-699, gemma4 레인 전례).
   **val은 전체 14,001행 유지, 정렬은 항상 id-join** (평가 순서 길이-버킷).

### script.py

1. 직렬화기 2종 + parts-헬퍼 리팩터 (§1).
2. **전용 `weak4_specialist` 경로 (generic cascade 확장 금지)**. 근거:
   cascade 존재 시 main 모델 미로드(2472-2479) + `cascade_scores`는
   `encoder_probs`로 base/secondary 독립 로드 — in-place merge·family-lock과
   구조적으로 안 맞음. 휴면 cascade는 손대지 않음(회귀 0).

```json
"weak4_specialist": {
  "enabled": true,
  "classes": [0, 1, 2, 3],
  "alpha": <튜너 출력>,
  "route_fraction": <T4 실측으로 결정, 미실측 시 0.30>,
  "serializer_name": "weak_nav_v1",
  "max_length": 384,
  "batch_size": 64,
  "lora_dir": "lora_weak"
}
```

   single-model 경로(2472- )에 삽입되는 흐름:

```
model 로드 → main logits 전체 (model_logits_sorted)
→ (기존 class_bias/rule/sparse 체인은 main logits에만 — 챔피언은 전부 no-op,
   조립 시 empty-stack assert로 고정)
→ main preds
→ 라우트 선택: main raw argmax ∈ classes
   캡: main4 = softmax(main_logits[:, 0:4]); weak-내부 top1-top2 마진 낮은
   순으로 route_fraction·N 상한; 동률은 행 인덱스 tie-break (결정론)
→ merge_lora_inplace(model, lora_dir) + score 교체  # 재로드 없음
→ routed 행만 weak_nav_v1로 재직렬화 → 2차 pass
→ family-lock 블렌드:
   spec4 = softmax(spec_logits[:, 0:4])
   mix4 = (1-α)·main4 + α·spec4
   routed 행 최종 라벨 = classes[argmax(mix4)]      # 항상 Weak4에 남음
   비라우팅 행 = main 예측 그대로 (identity — 구조 보장)
```

   - α=0 ⇔ main과 완전 동일. blend_mode 그리드 없음 — **conditional4 고정**
     (raw는 weak-only 학습 모델의 14-way 스케일이라 구조적으로 부적합,
     mass_renorm은 family flip 반례 존재).
3. **LoRA merge 로더** (peft 없이 torch+safetensors, 서버 요건):
   - expected 모듈 집합을 **하드코딩하지 말고**
     `adapter_config["target_modules"] × model.named_modules()`에서 유도;
     adapter의 A/B 쌍 집합과 정확 일치 assert; 잔여 미매칭 텐서 raise.
   - `scale = lora_alpha/r`; `p.data += (scale·B@A)` fp32 계산 후 캐스트;
     score는 **교체**(delta 아님), shape는 `model.score.weight.shape`와 비교.
   - merged 마커 속성으로 이중 적용 방지.
   - peft 키 문법은 버전 의존 — 첫 spec_v1 아티팩트의 실제 키 덤프로 확정.
   - **peft 버전 pin** (레인 `pip install peft==<pinned>`); 아티팩트에
     peft/transformers/torch/safetensors/python 버전 + git SHA 기록.

### tune_weak4_router.py (신규, CPU-로컬)

- 자유도 축소(fixed-val 최대화 편향 방지): blend=conditional4 고정,
  cap=T4 실측으로 사전 고정 → **튜닝 변수는 α 하나** (0:1:0.05).
- **tune/confirm 분리**: 14001 val 행을 결정론적 session-hash로 60/40 분할.
  tune에서 α 선택(및 rung 비교), confirm에서 사전등록 게이트 판정, 전체
  set은 최종 리포트만.
- id-join (classes·y_true·seed·split assert). script.py의 블렌드/라우트
  함수를 import (재구현 금지).
- **sanity gate: α=0 == payload raw_metrics 0.783852 정확 재현, 아니면 abort.**
- 리포트: 전체 macro-F1, Weak4 per-class F1, routed rescue/harm/net,
  토큰 분포(전체 vs **routed subset 별도** mean/p95/p99), α 플래토.
- α 선택: 플래토 가장자리(날카로운 피크 금지; fixed-val 레이어 Public 전이
  실패 전례 kdm8_fvb_sp 0.786 vs 0.7891).
- `--relabels` 248행 이중 리포팅(dataset 일치 vs human 일치) — 진단 전용.
- `--verify --pack-dir` 모드: 실제 script.py 경로 실행 결과와 예측 동일성
  assert.

### package_submission.py + build_weak4_pack.py

- `stage()`(77-79) glob에 `lora_*` 디렉토리 복사 추가.
- 신규 `build_weak4_pack.py`: 튜너 JSON → `weak4_specialist` 블록 생성 →
  `experiments/incoming/models/kd_m8_weak4/` 조립 (`hf_model/`=
  kd_m8_refit_int8 복사, `lora_weak/`, meta+provenance).
  **empty post-processing stack assert** (class_bias 전부 0, rule/sparse 없음
  — 위반 시 fail; 라우팅·블렌드가 raw main 확률 기준이라는 전제 고정).
- zip: ~532MB, 이름 `kd_m8_weak4.zip`.

## 3. 실험 사다리 (serializer만 변수; LoRA r16·lr 1e-4·ep2·focal γ2.0
(4-way 슬라이스 위에서)·seed42·split session·len384·replay none·KD 없음·
tune-bias 없음 — rung 간 완전 고정)

| Rung | 실험 | serializer | 조건 |
|---|---|---|---|
| 1 | `spec_v1` (컨트롤) | current_v1 | 무조건 (lane C) |
| 2 | `spec_nav` | weak_nav_v1 | 무조건 (lane B와 병렬 가능) |
| 3 | `spec_paths` | weak_nav_paths_v1 | **기본 보류** |

Rung 1 커맨드 (rung 2는 serializer/suffix/output-dir만 교체):

```
AADP_EXCHANGE_DIR=AADP_exchange_c python colab/cloud_sync.py launch train_transformer.py -- \
  --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B \
  --resume-from /content/drive/MyDrive/AADP_exchange_c/models/kd_hcx_m8_screen/hf_model \
  --lora-r 16 --train-label-filter weak4 --replay-mode none \
  --assert-val-ids <레인 경로>/anchor_val_logits.pt \
  --serializer current_v1 --split session --seed 42 --max-length 384 \
  --epochs 2 --lr 1e-4 --batch-size 16 --grad-accum-steps 1 --eval-batch-size 64 \
  --gradient-checkpointing --class-weight-power 0.5 --label-smoothing 0.02 \
  --loss focal --focal-gamma 2.0 --keep-threshold 0.0 --tokenize-batch-size 1024 \
  --save-val-model --save-fp16 \
  --output-dir /content/drive/MyDrive/AADP_exchange_c/models/spec_v1_screen \
  --experiment-suffix spec_v1_weak4_screen_s42 --no-research-log
```

- 사전 준비: 런타임 `pip install peft==<pinned>` 1회; 웜스타트 체크포인트
  존재 확인(`rclone lsd gdrive:AADP_exchange_c/models`); anchor .pt push;
  **fp16 가능 GPU 확인** — G4/Blackwell이면 재롤, 전 rung 같은 GPU 클래스·
  같은 dtype (기본 A100 fp16, bf16 혼용 금지).
- 각 rung 후: `pull` → `pull-model` → adapter 키 덤프(로더 확정) →
  tune_weak4_router.py. **rung 판정은 tune set의 블렌드 게인** (같은 main
  앵커 대비); rung 간 델타 < ~0.002는 판단 근거 아님.
- 최종(승자만): `kd_m8_refit/hf_model` 레인 업로드 → 같은 커맨드에
  `--serializer <승자> --final-model --final-only
  --resume-from .../models/kd_m8_refit/hf_model` → α/cap은 **스크린 단계
  튜너 값 유지** (refit은 val이 없고, val 재튜닝은 누수).

## 4. 병렬 실행 플랜

- **GPU 트랙 1 (lane C)**: rung 1. **GPU 트랙 2 (lane B)**: rung 2 — 사전에
  `rclone copy gdrive:AADP_exchange_c/models/kd_hcx_m8_screen
  gdrive:AADP_exchange_b/models/kd_hcx_m8_screen` (서버사이드) + lane B에
  code/data push. 두 레인 A100 fp16 통일.
- **CPU 트랙 (로컬, 동시)**: 코드 변경 전부(§2), 튜너, 토큰 감사(전체+routed
  subset), 직렬화기 바이트-보존 회귀, 로컬 .venv에 peft 설치 후 **LoRA parity
  테스트 준비**, kd_m8_refit 레인 업로드.
- **T4 트랙 (스크린 완료 후)**: 레인에 T4 런타임 배정 → §6.6 타이밍 리허설.
- (선택 가속) rung 1/2 완료 직후 두 후보 final refit을 투기적 병렬 런칭,
  패자 폐기.

## 5. 승격 게이트 (사전 등록 — 변경 금지)

Public 슬롯 소모 조건 (전부 충족):
1. **confirm set(40%)에서**: 블렌드 델타 > 0 AND rescue > harm.
2. **전체 val에서**: macro-F1 게인 ≥ +0.005 (α는 tune set 플래토 가장자리).
3. **Weak4 클래스별**: F1 하락 0.005 이상 금지, 특히 `list_directory` 하락
   금지 (2-stage bias의 "grep 상승, read/list 희생" 전례 방지).
4. non-weak 예측은 게이트가 아니라 **identity assert** (family-lock 구조
   보장의 검증).

게이트 실패 시 Public 미제출, research_log에 결과 기록 후 레인 종료 보고.

## 6. Verification

1. **토큰 감사**: weak_nav_v1, HCX 토크나이저 70k — 전체 + routed subset
   (main argmax∈Weak4 행) 별도 mean/p95/p99.
2. **직렬화기 회귀**: 70k 전행 nav 제거 == current_v1 바이트 일치.
3. **split-ID assert**: §2 train 4번 (학습 시작 전 자동).
4. **LoRA parity (int8 진행의 전제조건)**: 같은 fp16 base + 같은 adapter로
   PEFT `merge_and_unload()` vs 수동 merge, ~512행 로짓 비교 —
   max/mean abs diff, argmax 일치, weak4 conditional argmax 일치. fp16끼리
   사실상 완전 일치 요구. **불일치 시 int8 검증 진행 금지.**
5. **3단 등가성 분리**:
   (i) 알고리즘 parity — 같은 fp16 아티팩트에서 튜너 예측 == script.py 예측
   정확 일치 (`--verify`);
   (ii) int8 구현 parity — 같은 int8 아티팩트에서 오프라인 참조 == script.py
   정확 일치;
   (iii) fp16→int8 변화 — 별도 **안정성 지표**로만 (예측 일치율, route set
   변화, rescue/harm 이동). **최종 팩의 val 재평가를 품질 게이트로 쓰지 말 것**
   (최종 어댑터는 val을 본 refit 기반 = 누수). 품질 재확인이 필요하면
   quantized **screen** 체크포인트 표면에서 게이트 유지 확인 (선택, 시간 시).
6. **T4 full-volume 타이밍 리허설 (Public 전 필수)**: T4 런타임에서 ~30k
   합성 입력으로 전체 경로(main 추론 → 라우트 → merge → 2차 토크나이즈+추론
   → 출력) 실측. cap 0.30/0.35별 wall time + LoRA merge 단독 시간 기록.
   챔피언 앵커: 서버 6:32/10:00. **실측 결과로 route_fraction 확정; 실측
   불가 시 0.30.** (cap은 meta-only 변경이라 재학습 불필요.)
7. **오프라인 스모크**: 재빌드 zip 클린 추출 + data/ →
   `TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 python script.py` +
   `CUDA_VISIBLE_DEVICES='' python script.py`. 컬럼 `id,action`, ID 순서 =
   sample_submission, 라벨 유효, zip <1GB, 루트 3항목. (로컬 test.jsonl은
   5행 스텁 — 타이밍은 §6.6.)

## 7. 주요 함정 (확인 순서대로)

1. 조건부 4-way loss 누락 → zero-weight만으로는 smoothing/focal/분모가
   14-way로 남아 objective-배포 불일치 (§2 train 3).
2. multiline prompt에 nav 오삽입 → parts-헬퍼 리팩터만이 견고 (§1).
3. peft 키 문법 버전 의존 → 실제 키 덤프 + 유도된 expected 집합 + parity
   테스트 (§2 script 3, §6.4).
4. family flip → conditional4 family-lock으로 구조 차단 (§2 script 2).
5. 캡 우선순위 → weak4 내부 마진, 행 인덱스 tie-break (§2 script 2).
6. fixed-val 낙관 편향 → tune/confirm 분리 + α 단일 변수 + 플래토 가장자리
   + 사전등록 게이트 (§5).
7. `stage()`가 lora_weak/ 누락 → glob 확장 필수 (§2 packaging).
8. 레인 dtype 통일 (fp16 앵커 비교 가능성), 웜스타트 소스 혼동 금지
   (스크린에 kd_m8_refit 사용 = 누수).
9. 행 정렬 — 항상 id-join, positional 금지.

## 8. 기록

- 실험 행 results.csv 자동 append (+ cloud_sync pull dedup). 튜너/팩 산출물
  experiments/artifacts/. **결정만** research_log에 (사다리 요약 1항목 +
  게이트 판정/Public 항목). 커밋·push는 사용자 요청 시에만.
