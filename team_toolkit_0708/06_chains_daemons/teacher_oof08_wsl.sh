#!/bin/bash
# Qwen3.5-0.8B (m8 arch) 2-fold session-OOF teacher. usage: teacher_oof08_wsl.sh <fold> [batch] [accum]
# No --final-model: the deliverable is the held-out fold's val logits (experiments/logits/).
set -e
FOLD=$1
BATCH=${2:-16}
ACCUM=${3:-1}
cd /root/dacon
export TRANSFORMERS_OFFLINE=1
PY=/root/venv52/bin/python
LOG=/root/teacher_oof08_f$FOLD.log

echo "[teacher oof08 f$FOLD] start batch=$BATCH accum=$ACCUM $(date +%m-%d\ %H:%M:%S)" >> $LOG
$PY -u train_transformer.py --base-model igorktech/Qwen3.5-0.8B-Base-LM --model-class qwen35text \
  --lr 2e-5 --device cuda --split session_oof --n-folds 2 --fold-id $FOLD \
  --serializer current_v1 --max-length 384 --epochs 3 \
  --batch-size $BATCH --grad-accum-steps $ACCUM --gradient-checkpointing --eval-batch-size 32 \
  --pad-to-multiple-of 64 --optim adamw8bit \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 --no-research-log --seed 42 \
  --data-dir /root/dacon/open/data \
  --epoch-checkpoint-dir /root/dacon/models/teacher_oof08_f${FOLD}_ckpt \
  --output-dir /root/dacon/models/teacher_oof08_f$FOLD --experiment-suffix teacher_oof08_f$FOLD \
  --notes "Qwen3.5-0.8B m8-arch 2-fold session-OOF teacher fold $FOLD (4070)" >> $LOG 2>&1
echo "[teacher oof08 f$FOLD ALL_DONE] $(date +%H:%M:%S)" >> $LOG
