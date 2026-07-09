# Gemma-4-12B 교사 학습 핸드오프 (Colab A100용) — 07-08

## 목적
Gemma-4-12B(제3계열)로 KD 교사 로짓(train 70k soft-label)을 뽑는다.
우리 A100에서도 A.X 다음 순번으로 돌 예정(모레 새벽 완료 예상)이라, Colab에서 병렬로 돌리면
하루를 앞당기거나 상호 백업이 된다. **학생 학습 아님 — 교사 전용** (tf 5.13에서 학생 저장 금지, RoPE 사고 참조).

## 동봉 파일
- `train_transformer.py` — 마스터 (--model-class gemma4custom, --lora-r, 캐시 시드/fold 픽스 포함)
- `gemma4_seqcls.py` — Gemma-4 unified 백본용 자체 seq-cls 헤드 (공식 클래스 부재, PR #45294 미머지 대응)
- `script.py`, `train.py` — 직렬화(current_v1)·라벨 의존성
- `export_teacher_logits.py` — 최종 모델 로짓 추출 (--model-class gemma4custom 분기 포함)
- `export_ckpt_logits.py` — 에폭 체크포인트(어댑터)에서 로짓 추출 (참고: gemma4custom은 미지원 —
  필요 시 최종만 써도 됨. 에폭별 추출하려면 이 파일의 모델 로드부를 gemma4_seqcls로 교체)
- `teacher_gemma_a100.sh` — 우리 쪽 실행 커맨드 원본 (경로만 Colab에 맞게 수정)

## 환경 (Colab A100 40GB 필수 — 12B fp16 동결 ~24GB)
```bash
pip install "transformers>=5.13,<5.14" peft==0.19.1 accelerate safetensors sentencepiece protobuf bitsandbytes
export HF_HUB_DISABLE_XET=1   # Xet "hex hash value" 오류 방지
# google/gemma-4-12B 라이선스 동의 필요 (HF 계정) + HF_TOKEN 설정
```
데이터: `open/data/train.jsonl` + `train_labels.csv` (기존 보유분 그대로).

## 실행 (핵심 하이퍼 — seed42 매치드 유지 필수)
```bash
python -u train_transformer.py --base-model google/gemma-4-12B --model-class gemma4custom \
  --lr 1e-4 --device cuda --split session --serializer current_v1 --max-length 384 --epochs 3 \
  --batch-size 16 --grad-accum-steps 1 --gradient-checkpointing --eval-batch-size 32 \
  --pad-to-multiple-of 64 --lora-r 16 \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 --no-research-log --seed 42 \
  --save-fp16 --data-dir ./open/data \
  --epoch-checkpoint-dir /content/drive/MyDrive/gemma_ckpt \
  --output-dir ./models/teacher_gemma --experiment-suffix teacher_gemma_colab \
  --final-model --final-only
```
- OOM 시 래더: `--batch-size 8 --grad-accum-steps 2` → `4/4`
- **에폭 ckpt는 Drive에** (--epoch-checkpoint-dir) — Colab 끊김 대비 + 재개(--resume-from) + 에폭 선택용
- **주의: ckpt 디렉토리는 에폭마다 덮어써짐** — 에폭 끝날 때마다 `cp -r gemma_ckpt gemma_ckpt_epN`으로 스냅샷 떠둘 것 (우리는 이걸 늦게 해서 coder ep1을 잃음)

## 로짓 추출 + 스윗스팟 규칙 (오늘 실측으로 확립)
```bash
python -u export_teacher_logits.py --hf-model ./models/teacher_gemma/hf_model --model-class gemma4custom \
  --data-dir ./open/data --serializer current_v1 --max-length 384 --batch-size 64 \
  --source-note "gemma-4-12b lora-r16 seed42 (colab)" \
  --out teacher_gemma_train70k_fp16.pt
```
출력 마지막 줄의 **train argmax acc가 판정 기준**:
- **0.80~0.82 = 스윗스팟** (m8 0.811 → +0.0039 / coder ep2 0.7997 채택)
- **≥0.85면 한 에폭 전 ckpt에서 재추출** (ep3 0.8686은 이견이 암기로 소실 — coder에서 ep2로 후퇴함)
- 0.9+ 는 KD 가치 없음 (m9 0.943 → +0.0001 실측)

## 결과물
`teacher_gemma_train70k_fp16.pt` (~6.5MB) — {ids, logits fp16 70k×14, classes, labels, y_true, metadata}.
Slack으로 공유해주면 우리 쪽 HCX 학생 KD(α0.5/T3 seed42)에 바로 투입.

## 예상 소요 (Colab A100 기준)
12B LoRA ckpt: ~15,000스텝 × ~15-18 step/분 ≈ **14~17시간** + export ~1시간.
Colab 세션 한도에 걸리면 Drive ckpt에서 --resume-from으로 이어붙이면 됨.
