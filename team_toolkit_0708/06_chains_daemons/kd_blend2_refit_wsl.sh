#!/bin/bash
# KD FULL refit: HCX student x blend-v2 teacher (0.5*m8+0.5*q35) a0.5 T3 seed42 + int8 pack
set -e
cd /root/dacon
export TRANSFORMERS_OFFLINE=1
PY=/root/venv211/bin/python

echo "[kd blend2 refit] start $(date +%m-%d\ %H:%M:%S)" > /root/kd_blend2_refit.log
$PY -u train_transformer.py --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B \
  --lr 2e-5 --device cuda --split session --serializer current_v1 --max-length 384 --epochs 3 \
  --batch-size 16 --grad-accum-steps 1 --gradient-checkpointing --eval-batch-size 64 \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 --no-research-log --seed 42 \
  --distill-logits /root/dacon/teacher_blend2_train70k_fp16.pt --distill-alpha 0.5 --distill-temp 3.0 \
  --save-fp16 --data-dir /root/dacon/open/data \
  --epoch-checkpoint-dir /root/dacon/models/kd_blend2_refit_ckpt \
  --output-dir /root/dacon/models/kd_blend2_refit --experiment-suffix kd_blend2_refit_a05t3 \
  --notes "KD FULL refit: HCX x blend v2 (m8+q35 prob-avg) a0.5 T3 seed42" \
  --final-model --final-only >> /root/kd_blend2_refit.log 2>&1

echo "[refit done, packing] $(date +%H:%M:%S)" >> /root/kd_blend2_refit.log
mkdir -p models/kd_blend2_pack/hf_model
cp models/kd_blend2_refit/hf_meta.json models/kd_blend2_pack/
for f in models/kd_blend2_refit/hf_model/*; do
  if [ "$(basename "$f")" != "model.safetensors" ]; then cp "$f" models/kd_blend2_pack/hf_model/; fi
done
$PY quantize_checkpoint.py quantize --input models/kd_blend2_refit/hf_model/model.safetensors \
  --output models/kd_blend2_pack/hf_model/model.int8.safetensors >> /root/kd_blend2_refit.log 2>&1
$PY package_submission.py --hf-dir models/kd_blend2_pack --no-sparse \
  --requirements requirements_qwen3.txt --python $PY --out kd_hcx_blend2.zip >> /root/kd_blend2_refit.log 2>&1
cp submissions/kd_hcx_blend2.zip /mnt/c/dacon/
echo "[kd blend2 pack ALL_DONE] $(date +%H:%M:%S)" >> /root/kd_blend2_refit.log
