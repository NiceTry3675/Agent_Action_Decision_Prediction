#!/bin/bash
# blend v2 chain: pack assembly (CPU) -> KD refit -> gate(fp16) -> sha
LOG=/root/blend2_chain.log
mark() { echo "[$1] $(date +%m-%d\ %H:%M:%S)" >> $LOG; }
echo "[blend2 chain start] $(date +%m-%d\ %H:%M:%S)" > $LOG

cd /root/dacon
/root/venv211/bin/python -u make_blend2_pack.py >> $LOG 2>&1 || { mark "pack assembly FAILED"; exit 1; }
mark "pack done"

bash /root/kd_blend2_refit_wsl.sh
grep -q "kd blend2 pack ALL_DONE" /root/kd_blend2_refit.log 2>/dev/null || { mark "kd refit FAILED"; tail -20 /root/kd_blend2_refit.log >> $LOG; exit 1; }
mark "kd refit done"

bash /root/gate_pack.sh models/kd_blend2_refit blend2kd
grep -E "macro" /root/gate_blend2kd.log >> $LOG
sha256sum /mnt/c/dacon/kd_hcx_blend2.zip >> $LOG 2>&1
mark "blend2 chain ALL_DONE"
