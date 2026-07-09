#!/bin/bash
# Stage 3 of occupancy chain: after A.X ALL_DONE and gemma download done -> Gemma-3-12B teacher.
LOG=/home/token1234/occupy.log
echo "[gemma stage armed] $(date +%H:%M:%S)" >> $LOG
while true; do
  if grep -q "teacher ax ALL_DONE" /home/token1234/teacher_ax.log 2>/dev/null \
     && grep -q "OK google/gemma-4-12B" /home/token1234/teacher_dl9.log 2>/dev/null; then
    break
  fi
  sleep 300
done
echo "[launch gemma 16 1 ckpt] $(date +%H:%M:%S)" >> $LOG
nohup bash /home/token1234/teacher_gemma_a100.sh 16 1 ckpt > /dev/null 2>&1 &

LADDER="8,2,ckpt 4,4,ckpt"
while true; do
  sleep 180
  if grep -q "teacher gemma ALL_DONE" /home/token1234/teacher_gemma.log 2>/dev/null; then
    echo "[gemma ALL_DONE] $(date +%H:%M:%S)" >> $LOG; break
  fi
  if pgrep -f "gemma-4-12B" > /dev/null; then continue; fi
  if grep -qiE "out of memory" /home/token1234/teacher_gemma.log 2>/dev/null; then
    NEXT=${LADDER%% *}
    if [ -z "$NEXT" ]; then echo "[gemma ladder exhausted]" >> $LOG; break; fi
    REST=${LADDER#* }; [ "$REST" = "$LADDER" ] && REST=""
    LADDER=$REST
    B=$(echo "$NEXT" | cut -d, -f1); A=$(echo "$NEXT" | cut -d, -f2); C=$(echo "$NEXT" | cut -d, -f3)
    mv /home/token1234/teacher_gemma.log /home/token1234/teacher_gemma.oom.$(date +%s).log
    echo "[gemma oom retry -> $B $A $C] $(date +%H:%M:%S)" >> $LOG
    nohup bash /home/token1234/teacher_gemma_a100.sh "$B" "$A" "$C" > /dev/null 2>&1 &
  else
    echo "[gemma died non-OOM - STOP] $(date +%H:%M:%S)" >> $LOG
    tail -15 /home/token1234/teacher_gemma.log >> $LOG 2>/dev/null; break
  fi
done
