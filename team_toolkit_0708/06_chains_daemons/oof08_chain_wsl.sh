#!/bin/bash
# OOF experiment chain: venv52 -> model dl -> fold0 -> fold1 -> merge -> KD refit -> gate
LOG=/root/oof08_chain.log
mark() { echo "[$1] $(date +%m-%d\ %H:%M:%S)" >> $LOG; }
echo "[oof08 chain start] $(date +%m-%d\ %H:%M:%S)" > $LOG

# stage 0: teacher venv (tf 5.13 + FLA)
if [ ! -x /root/venv52/bin/python ]; then
  mark "stage0 venv52 setup"
  bash /root/setup_venv52_4070.sh > /root/setup_venv52.log 2>&1 || { mark "venv52 setup FAILED"; tail -15 /root/setup_venv52.log >> $LOG; exit 1; }
fi
/root/venv52/bin/python -c "from transformers import Qwen3_5TextForSequenceClassification; import torch; assert torch.cuda.is_available()" >> $LOG 2>&1 || { mark "venv52 sanity FAILED"; exit 1; }
mark "stage0 venv52 ready"

# stage 1: model download (idempotent)
export HF_HUB_DISABLE_XET=1
/root/venv52/bin/python /root/dl_qwen08.py > /root/teacher_dl_oof08.log 2>&1 || { mark "model dl FAILED"; tail -10 /root/teacher_dl_oof08.log >> $LOG; exit 1; }
mark "stage1 dl done"

# stage 2: fold teachers with one OOM-ladder retry each
for F in 0 1; do
  mark "fold$F launch 16/1"
  bash /root/teacher_oof08_wsl.sh $F 16 1
  if ! grep -q "teacher oof08 f$F ALL_DONE" /root/teacher_oof08_f$F.log 2>/dev/null; then
    if grep -qiE "out of memory" /root/teacher_oof08_f$F.log 2>/dev/null; then
      mark "fold$F oom retry 8/2"
      sleep 20
      bash /root/teacher_oof08_wsl.sh $F 8 2
    fi
  fi
  grep -q "teacher oof08 f$F ALL_DONE" /root/teacher_oof08_f$F.log 2>/dev/null || { mark "fold$F FAILED - stop"; tail -20 /root/teacher_oof08_f$F.log >> $LOG; exit 1; }
  mark "fold$F done"
done

# stage 3: merge OOF pack (stats printed into chain log)
cd /root/dacon
F0=$(ls -t experiments/logits/*teacher_oof08_f0*_val_logits.pt 2>/dev/null | head -1)
F1=$(ls -t experiments/logits/*teacher_oof08_f1*_val_logits.pt 2>/dev/null | head -1)
if [ -z "$F0" ] || [ -z "$F1" ]; then mark "val logits missing - stop"; exit 1; fi
/root/venv211/bin/python -u merge_oof_pack.py --fold-logits "$F0" "$F1" \
  --data-dir /root/dacon/open/data \
  --source-note "qwen3.5-0.8B m8-arch 2fold session-OOF seed42 lr2e-5 fullFT adamw8bit (4070)" \
  --out /root/dacon/teacher_oof08_train70k_fp16.pt >> $LOG 2>&1 || { mark "merge FAILED"; exit 1; }
cp /root/dacon/teacher_oof08_train70k_fp16.pt /mnt/c/dacon/
mark "stage3 merge done"

# stage 4: KD student refit + int8 pack
bash /root/kd_oof08_refit_wsl.sh
grep -q "kd oof08 pack ALL_DONE" /root/kd_oof08_refit.log 2>/dev/null || { mark "kd refit FAILED"; tail -20 /root/kd_oof08_refit.log >> $LOG; exit 1; }
mark "stage4 kd refit done"

# stage 5: pseudo-holdout gate
bash /root/gate_pack.sh models/kd_oof08_pack oof08kd
mark "oof08 chain ALL_DONE"
