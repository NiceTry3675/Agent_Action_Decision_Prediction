#!/bin/bash
# KD FULL refit: HCX student x coder-7B teacher a0.5 T3 seed42 (matched vs m8 0.7891) + int8 pack
# $1 = teacher logits pack (default ep3 final), $2 = teacher tag note (ep3-final / ep2-ckpt)
set -e
TEACHER=${1:-/root/dacon/teacher_coder_train70k_fp16.pt}
TNOTE=${2:-ep3-final}
cd /root/dacon
export TRANSFORMERS_OFFLINE=1
PY=/root/venv211/bin/python

echo "[kd coder refit] start teacher=$TNOTE $(date +%m-%d\ %H:%M:%S)" > /root/kd_coder_refit.log
$PY -u train_transformer.py --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B \
  --lr 2e-5 --device cuda --split session --serializer current_v1 --max-length 384 --epochs 3 \
  --batch-size 16 --grad-accum-steps 1 --gradient-checkpointing --eval-batch-size 64 \
  --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 --no-research-log --seed 42 \
  --distill-logits "$TEACHER" --distill-alpha 0.5 --distill-temp 3.0 \
  --save-fp16 --data-dir /root/dacon/open/data \
  --epoch-checkpoint-dir /root/dacon/models/kd_coder_refit_ckpt \
  --output-dir /root/dacon/models/kd_coder_refit --experiment-suffix kd_coder_refit_a05t3 \
  --notes "KD FULL refit: HCX x Qwen2.5-Coder-7B ($TNOTE) teacher a0.5 T3 seed42" \
  --final-model --final-only >> /root/kd_coder_refit.log 2>&1

echo "[refit done, packing] $(date +%H:%M:%S)" >> /root/kd_coder_refit.log
mkdir -p models/kd_coder_pack/hf_model
cp models/kd_coder_refit/hf_meta.json models/kd_coder_pack/
for f in models/kd_coder_refit/hf_model/*; do
  if [ "$(basename "$f")" != "model.safetensors" ]; then cp "$f" models/kd_coder_pack/hf_model/; fi
done
$PY quantize_checkpoint.py quantize --input models/kd_coder_refit/hf_model/model.safetensors \
  --output models/kd_coder_pack/hf_model/model.int8.safetensors >> /root/kd_coder_refit.log 2>&1
$PY package_submission.py --hf-dir models/kd_coder_pack --no-sparse \
  --requirements requirements_qwen3.txt --python $PY --out kd_hcx_coder.zip >> /root/kd_coder_refit.log 2>&1
cp submissions/kd_hcx_coder.zip /mnt/c/dacon/
echo "[kd coder pack ALL_DONE] $(date +%H:%M:%S)" >> /root/kd_coder_refit.log
