#!/bin/bash
# usage: gate_pack.sh <pack_dir_relative_to_/root/dacon> <tag>
set -e
PACK=$1
TAG=$2
cd /root/dacon
export TRANSFORMERS_OFFLINE=1
echo "[gate $TAG start] $(date +%H:%M:%S)" > /root/gate_$TAG.log
/root/venv211/bin/python -u eval_pseudo_holdout.py --pack "$PACK" --data-dir /root/dacon/open/data \
  --limit 20000 --batch-size 32 >> /root/gate_$TAG.log 2>&1
echo "[gate $TAG ALL_DONE] $(date +%H:%M:%S)" >> /root/gate_$TAG.log
