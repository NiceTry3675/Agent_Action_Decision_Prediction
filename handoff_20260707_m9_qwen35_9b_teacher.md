# Handoff 2026-07-07: M9 = Qwen3.5-9B-Base full-FT KD teacher (lane C)

전략 문맥(신선한 에이전트용): 현 Public baseline **0.7891** =
`kd_m8_refit.zip`(HCX-0.5B 학생 + M8 Qwen3.5-0.8B full-refit 교사 KD,
`final_summary.md` 참조). 마감 ~07-14, 제출 10슬롯/일(타 레인과 공유).

Goal: 교사 스케일업 단일 변수 실험 — M8(Qwen3.5-0.8B) 교사를 9B로 교체했을 때
HCX-0.5B 학생의 KD가 좋아지는가. **판정은 matched-seed42 Public 제출 1슬롯으로만**
(KD-fold-leak 규칙: 로컬 fixed/OOF 스크린으로 KD 레시피를 순위 매기지 않는다).
성공 기준: Public > 0.7891 + 0.002 (노이즈 플로어 위, 즉 ≥0.791). 승리 시
27B(bf16 LoRA) 에스컬레이션 검토, 패배/동률 시 교사 스케일 레인 폐쇄.

## 0. 코드 전달과 런칭 (커밋 없음 주의)

- `--bf16` 플래그(2026-07-07 추가)와 R-Drop 변경은 **미커밋 워킹트리** 상태다.
  `colab/cloud_sync.py push`는 tracked+untracked 워킹트리 전체를 번들하므로
  **push만 하면 레인 C에 그대로 전달**된다 — VM에서 git pull 하지 말 것.
  이 문서 자체도 untracked로 함께 실린다.
- 함께 실리는 R-Drop 코드는 이 실험과 무관 — 플래그 미사용 시 기본 경로
  비트 동일(07-07 리뷰 확인). 이 실험에서 `--rdrop-alpha`/`--dropout`을 켜지 말 것.
- 런칭은 `colab/COLAB.md` 프로토콜: `[launch]` 셀 또는 `vm_agent.py launch`로
  **백그라운드** 실행(노트북 셀 동기 실행 금지), 유휴 시 `cloud_sync.py unassign`.

## 1. 베이스 체크포인트 결정: `Qwen/Qwen3.5-9B-Base` (공식)

- M8의 실제 계보는 **Base**다: `igorktech/Qwen3.5-0.8B-Base-LM`은
  `Qwen/Qwen3.5-0.8B-Base`의 커뮤니티 text-only(LM) 추출본 (results.csv 전
  M8 행에서 확인). 9B도 Base를 써야 "교사 스케일"만 변수로 남는다 (9B
  instruct를 쓰면 변형 변수가 하나 더 낀다).
- igorktech에는 9B 추출본이 **없음** (0.8B 단일). 제3자 추출본은 미검증 변환
  변수를 추가하므로 기각 — 공식 체크포인트 사용.
- 공식 9B-Base는 멀티모달 컨피그(`Qwen3_5ForConditionalGeneration`,
  vision_config + text_config, transformers_version 4.57.0.dev0)다.
  transformers ≥4.57에서 `AutoModelForSequenceClassification`이
  `Qwen3_5TextForSequenceClassification`으로 해석되어 **비전 타워를 버리고
  텍스트 백본만 로드**한다 — 커뮤니티 추출본과 수학적으로 동일한 가중치.
  차이는 다운로드 용량(비전 타워 포함)과 auto-mapping 의존뿐.

## 2. 프리플라이트 (본 런 전, 레인 C에서 ~15분)

1. pip: **최신 transformers(≥4.57)**, bitsandbytes, (속도 옵션) fla-core +
   causal-conv1d — 기존 RED 프로브는 T4(SM75) 한정 이슈였으므로 이 GPU에서
   재시도 가치 있음. 로컬 .venv(4.51.3)는 qwen3_5 컨피그 자체를 못 읽으니
   레인 C 설치가 필수.
2. `--bf16` 플래그 첫 실전 검증 (오늘 추가됨, 기본 경로 무변경): 0.8B로 몇 분짜리
   퀵런 —
   `--base-model igorktech/Qwen3.5-0.8B-Base-LM --bf16 --optim adamw8bit
   --quick-val-size 600 --epochs 1` + 챔피언 레시피 나머지. loss가 정상 하강하면 통과.
3. 9B 로드 스모크: `AutoModelForSequenceClassification.from_pretrained(
   "Qwen/Qwen3.5-9B-Base", num_labels=14, torch_dtype=torch.bfloat16)` + forward
   1배치. **auto-mapping 실패 시 플랜 B**: `Qwen3_5ForConditionalGeneration`으로
   로드 → `.language_model` 서브모듈 + 토크나이저를 독립 디렉토리로
   save_pretrained (igorktech이 0.8B에 한 것과 동일 작업) → `--base-model`을
   로컬 경로로.
4. GPU 메모리 게이트: bf16 + adamw8bit + grad-ckpt 기준 9B ≈ 60-65GB
   (가중치 18 + grad 18 + optim 18 + activation). **80GB 미만 GPU면 9B 불가**
   → A100 40GB급이면 `Qwen/Qwen3.5-4B-Base`로 동일 실험 축소 수행.

## 3. 교사 refit (M8 refit 커맨드 미러링, 변경분: base-model / --bf16 / adamw8bit / grad-ckpt)

```bash
train_transformer.py --base-model Qwen/Qwen3.5-9B-Base \
  --lr 2e-5 --device cuda --split session --serializer current_v1 --max-length 400 \
  --epochs 3 --batch-size 8 --grad-accum-steps 2 --eval-batch-size 64 \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 --no-research-log \
  --seed 42 --save-fp16 --bf16 --optim adamw8bit --gradient-checkpointing \
  --epoch-checkpoint-dir /content/drive/MyDrive/AADP_exchange_c/models/m9_qwen35_9b_refit_ckpt \
  --output-dir /content/drive/MyDrive/AADP_exchange_c/models/m9_qwen35_9b_refit \
  --experiment-suffix m9_qwen35_9b_refit --final-model --final-only \
  --notes 'M9 Qwen3.5-9B-Base ep3 len400 FULL-DATA refit; teacher-scale-only vs M8'
```

- 벽시계 캘리브레이션: M8(0.8B) refit은 A100 40GB에서 29,368s(8.2h) — 유효
  MFU ~3%(eager DeltaNet + fp32 마스터 추정). H100급 + bf16이면 9B ep3는
  대략 4-12h 범위로 추정되나 분산이 크다. **시작 15분에 step 시간 측정해 총시간
  프로젝션, 마감(07-14) 역산으로 계속/중단 판단.**
- lr 2e-5는 9B에서 미검증(다소 높을 수 있음) — one-variable 원칙상 그대로 간다.
  발산이 걱정되면 refit 전에 `--quick-val-size 600 --epochs 1` 스크린(~1/3
  epoch 비용)으로 확인.

## 4. 교사 로짓 export (refit 완료 후, m8 아티팩트 규격 미러)

```bash
export_teacher_logits.py --model-dir /content/drive/MyDrive/AADP_exchange_c/models/m9_qwen35_9b_refit \
  --split train --serializer current_v1 --max-length 400 --dtype fp16 \
  --output experiments/logits/m9_qwen35_9b_refit_train70k_fp16.pt \
  --also-npz experiments/logits/m9_qwen35_9b_refit_train70k_fp16.npz
```

스테이징은 기존 관례대로 rclone `AADP_exchange_c/logits/` 경유, 로드 검증
(70000×14, ids/classes/y_true) 후 사용.

## 5. 학생 KD 런 + 판정

kd_m8_refit 레시피에서 **`--distill-logits`만 m9 파일로 교체**. 전문
(= `final_summary.md`의 hcx05b_refit 커맨드 + KD 플래그, kd_m8_refit과 동일 구성):

```bash
train_transformer.py --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B \
  --lr 2e-5 --device cuda --split session --serializer current_v1 --max-length 384 \
  --epochs 3 --batch-size 16 --grad-accum-steps 1 --gradient-checkpointing \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --seed 42 --save-fp16 \
  --distill-logits experiments/logits/m9_qwen35_9b_refit_train70k_fp16.pt \
  --distill-alpha 0.5 --distill-temp 3.0 \
  --output-dir /content/drive/MyDrive/AADP_exchange_c/models/kd_hcx_m9_refit \
  --experiment-suffix kd_hcx_m9_refit --final-model --final-only \
  --notes 'HCX-0.5B student, KD from M9 Qwen3.5-9B full-refit teacher; teacher-scale-only vs kd_m8_refit'
```

- **학생 런에는 `--bf16`을 쓰지 말 것** — 0.7891 인스턴스(kd_m8_refit)와
  정밀도 경로까지 동일해야 matched 비교가 성립한다. 교사 refit만 bf16.
- 이후 `package_submission.py --no-sparse --requirements requirements_qwen3.txt`
  → 클린 추출 오프라인 스모크(AGENTS.md 절차) → Public 제출.
- 비교 대상: 0.7891 (kd_m8_refit, 동일 seed42).
- class_bias 전부 0 유지 (final refit 미튜닝 관례), rules 레이어 없음 — kd_m8_refit과
  조건 동일하게.

## 6. 알려진 리스크

- **one-hot 교사**: 9B는 70k를 0.8B보다 더 강하게 암기 → soft target 정보량이
  오히려 줄 수 있음. 이게 바로 이 실험이 측정하는 것 — 부정 결과도 레인 폐쇄
  근거로 가치 있음.
- results.csv 행은 `cloud_sync.py pull`로만 회수 (수기 복사 금지).
- `--bf16`은 fp32 마스터 없이 bf16 가중치에 직접 업데이트 — lr 2e-5/3ep
  분류 FT에서는 표준 관행이나, loss 곡선이 fp16-autocast 런들과 미세하게 다를 수 있음.
