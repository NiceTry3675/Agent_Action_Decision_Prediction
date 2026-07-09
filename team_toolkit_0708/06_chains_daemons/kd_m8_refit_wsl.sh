#!/bin/bash
# KD FULL refit #2: HCX student x m8 raw-logit teacher a0.5 T3 seed42 (matched triple vs 0.7852/0.7827) + int8 pack
set -e
cd /root/dacon
export TRANSFORMERS_OFFLINE=1
PY=/root/venv211/bin/python

echo "[kd m8 refit] start $(date +%m-%d\ %H:%M:%S)" > /root/kd_m8_refit.log
cp /mnt/c/dacon/m8_qwen35_refit_train70k_fp16.pt /root/dacon/
$PY -u train_transformer.py --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B \
  --lr 2e-5 --device cuda --split session --serializer current_v1 --max-length 384 --epochs 3 \
  --batch-size 16 --grad-accum-steps 1 --gradient-checkpointing --eval-batch-size 64 \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 --no-research-log --seed 42 \
  --distill-logits /root/dacon/m8_qwen35_refit_train70k_fp16.pt --distill-alpha 0.5 --distill-temp 3.0 \
  --save-fp16 --data-dir /root/dacon/open/data \
  --epoch-checkpoint-dir /root/dacon/models/kd_m8_refit_ckpt \
  --output-dir /root/dacon/models/kd_m8_refit --experiment-suffix kd_m8_refit_a05t3 \
  --notes "KD FULL refit 2: HCX x m8 raw teacher a0.5 T3 seed42" \
  --final-model --final-only >> /root/kd_m8_refit.log 2>&1

echo "[refit done, packing] $(date +%H:%M:%S)" >> /root/kd_m8_refit.log
mkdir -p models/kd_m8_pack/hf_model
cp models/kd_m8_refit/hf_meta.json models/kd_m8_pack/
for f in models/kd_m8_refit/hf_model/*; do
  if [ "$(basename "$f")" != "model.safetensors" ]; then cp "$f" models/kd_m8_pack/hf_model/; fi
done
$PY quantize_checkpoint.py quantize --input models/kd_m8_refit/hf_model/model.safetensors \
  --output models/kd_m8_pack/hf_model/model.int8.safetensors >> /root/kd_m8_refit.log 2>&1
$PY package_submission.py --hf-dir models/kd_m8_pack --no-sparse \
  --requirements requirements_qwen3.txt --python $PY --out kd_hcx_m8.zip >> /root/kd_m8_refit.log 2>&1
cp submissions/kd_hcx_m8.zip /mnt/c/dacon/
echo "[kd m8 pack ALL_DONE] $(date +%H:%M:%S)" >> /root/kd_m8_refit.log
