#!/bin/bash
# Gemma-3-12B-pt (multimodal base, text-only class) LoRA r16 teacher -- third-family axis
set -e
BATCH=${1:-16}
ACCUM=${2:-1}
CKPT_FLAG="--gradient-checkpointing"
if [ "${3:-ckpt}" = "nockpt" ]; then CKPT_FLAG=""; fi
cd /home/token1234/aadp
export HF_HOME=/home/token1234/aadp/hf_home
export TRANSFORMERS_OFFLINE=1
PY=/home/token1234/venv52/bin/python

echo "[teacher gemma] start batch=$BATCH accum=$ACCUM ckpt=${3:-ckpt} $(date +%m-%d\ %H:%M:%S)" >> /home/token1234/teacher_gemma.log
$PY -u train_transformer.py --base-model google/gemma-4-12B --model-class gemma4custom \
  --lr 1e-4 --device cuda --split session --serializer current_v1 --max-length 384 --epochs 3 \
  --batch-size $BATCH --grad-accum-steps $ACCUM $CKPT_FLAG --eval-batch-size 32 \
  --pad-to-multiple-of 64 --lora-r 16 \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 --no-research-log --seed 42 \
  --save-fp16 --data-dir ./open/data \
  --epoch-checkpoint-dir ./models/teacher_gemma_ckpt \
  --output-dir ./models/teacher_gemma --experiment-suffix teacher_gemma \
  --notes "Gemma-4-12B custom-seqcls LoRA r16 teacher (A100)" \
  --final-model --final-only >> /home/token1234/teacher_gemma.log 2>&1

echo "[train done, exporting logits] $(date +%H:%M:%S)" >> /home/token1234/teacher_gemma.log
$PY -u export_teacher_logits.py --hf-model ./models/teacher_gemma/hf_model --model-class gemma4custom \
  --data-dir ./open/data --serializer current_v1 --max-length 384 --batch-size 64 \
  --source-note "gemma-4-12b lora-r16 refit seed42 teacher (A100)" \
  --out /home/token1234/teacher_gemma_train70k_fp16.pt >> /home/token1234/teacher_gemma.log 2>&1
echo "[teacher gemma ALL_DONE] $(date +%H:%M:%S)" >> /home/token1234/teacher_gemma.log
