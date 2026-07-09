#!/bin/bash
# Teacher venv for Qwen3.5 on 4070 WSL: mirrors A100 venv52 combo (torch 2.5.1+cu121,
# transformers 5.13, peft, FLA 0.5.1). Kept separate from venv/venv211 (eval-matched).
set -e
PYBIN=python3.11
command -v python3.11 >/dev/null 2>&1 || PYBIN=python3.10
$PYBIN -m venv /root/venv52
P=/root/venv52/bin/pip
$P install -U pip
$P install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121
$P install "transformers>=5.13,<5.14" peft accelerate safetensors sentencepiece protobuf
$P install bitsandbytes
$P install flash-linear-attention==0.5.1
/root/venv52/bin/python - <<'EOF'
import torch, transformers
print("torch", torch.__version__, "cuda_avail", torch.cuda.is_available())
print("tf", transformers.__version__)
from transformers import Qwen3_5TextForSequenceClassification
print("qwen35 seqcls class OK")
EOF
echo "[venv52 setup ALL_DONE]"
