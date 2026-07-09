#!/bin/bash
# per-epoch adapter snapshots (~90MB each): teacher_X_ckpt is overwritten every epoch,
# so copy it to teacher_X_ckpt_epN as soon as epoch N's save has settled (>=30s old).
LOG=/home/token1234/ckpt_snapshot.log
echo "[snapshot daemon start] $(date +%H:%M:%S)" >> $LOG
while true; do
  for D in /home/token1234/aadp/models/teacher_ax_ckpt /home/token1234/aadp/models/teacher_gemma_ckpt; do
    S=$D/checkpoint_state.json
    [ -f "$S" ] || continue
    AGE=$(( $(date +%s) - $(stat -c %Y "$S") ))
    [ "$AGE" -ge 30 ] || continue
    N=$(grep -o '[0-9]\+' "$S" | head -1)
    [ -n "$N" ] || continue
    T=${D}_ep${N}
    if [ ! -d "$T" ]; then
      cp -r "$D" "$T.tmp" && mv "$T.tmp" "$T"
      echo "[snapshot $T] $(date +%H:%M:%S)" >> $LOG
    fi
  done
  sleep 120
done
