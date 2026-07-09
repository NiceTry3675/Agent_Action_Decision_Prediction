#!/bin/bash
# recovery replacement for the dead occupy ax stage: launch A.X (hold-gated) + OOM ladder babysit
LOG=/home/token1234/occupy.log
echo "[ax daemon (recovery) start] $(date +%H:%M:%S)" >> $LOG
nohup bash /home/token1234/teacher_ax_a100.sh 16 1 nockpt > /dev/null 2>&1 &
LADDER="16,1,ckpt 8,2,ckpt"
while true; do
  sleep 180
  if grep -q "teacher ax ALL_DONE" /home/token1234/teacher_ax.log 2>/dev/null; then
    echo "[ax ALL_DONE] $(date +%H:%M:%S)" >> $LOG; break
  fi
  pgrep -f "A.X-4.0-Light" > /dev/null && continue
  pgrep -f "teacher_ax_a100.sh" > /dev/null && continue
  if grep -qiE "out of memory" /home/token1234/teacher_ax.log 2>/dev/null; then
    NEXT=${LADDER%% *}
    if [ -z "$NEXT" ]; then echo "[ax ladder exhausted]" >> $LOG; break; fi
    REST=${LADDER#* }; [ "$REST" = "$LADDER" ] && REST=""
    LADDER=$REST
    B=$(echo "$NEXT" | cut -d, -f1); A=$(echo "$NEXT" | cut -d, -f2); C=$(echo "$NEXT" | cut -d, -f3)
    mv /home/token1234/teacher_ax.log /home/token1234/teacher_ax.oom.$(date +%s).log
    echo "[ax oom retry -> $B $A $C] $(date +%H:%M:%S)" >> $LOG
    nohup bash /home/token1234/teacher_ax_a100.sh "$B" "$A" "$C" > /dev/null 2>&1 &
  else
    echo "[ax died non-OOM - STOP] $(date +%H:%M:%S)" >> $LOG
    tail -15 /home/token1234/teacher_ax.log >> $LOG 2>/dev/null
    break
  fi
done
