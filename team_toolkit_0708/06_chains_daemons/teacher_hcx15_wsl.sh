#!/bin/bash
# HCX-1.5B teacher: full-data refit (adamw8bit, batch8 accum2 ckpt) then export train-70k soft-labels
set -e
cd /root/dacon
export TRANSFORMERS_OFFLINE=1
PY=/root/venv211/bin/python

echo "[teacher hcx15] start $(date +%m-%d\ %H:%M:%S)" > /root/teacher_hcx15.log
$PY -u train_transformer.py --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-1.5B \
  --lr 1e-4 --device cuda --split session --serializer current_v1 --max-length 384 --epochs 3 \
  --batch-size 8 --grad-accum-steps 2 --gradient-checkpointing --eval-batch-size 32 \
  --lora-r 16 \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 --no-research-log --seed 42 \
  --save-fp16 --data-dir /root/dacon/open/data \
  --epoch-checkpoint-dir /root/dacon/models/teacher_hcx15_ckpt \
  --output-dir /root/dacon/models/teacher_hcx15 --experiment-suffix teacher_hcx15 \
  --notes "HCX-1.5B teacher LoRA r16 refit for KD soft-labels" \
  --final-model --final-only >> /root/teacher_hcx15.log 2>&1

echo "[train done, exporting logits] $(date +%H:%M:%S)" >> /root/teacher_hcx15.log
$PY -u export_teacher_logits.py --hf-model /root/dacon/models/teacher_hcx15/hf_model \
  --data-dir /root/dacon/open/data --serializer current_v1 --max-length 384 --batch-size 64 \
  --source-note "hcx15 full-refit seed42 teacher" \
  --out /root/dacon/teacher_hcx15_train70k_fp16.pt >> /root/teacher_hcx15.log 2>&1
cp /root/dacon/teacher_hcx15_train70k_fp16.pt /mnt/c/dacon/
echo "[teacher hcx15 ALL_DONE] $(date +%H:%M:%S)" >> /root/teacher_hcx15.log
