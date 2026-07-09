#!/bin/bash
# Continuous-occupancy chain: coder-7B teacher -> (when done + A.X downloaded) A.X teacher.
# Keeps OUR job on the GPU back-to-back; babysits each with an OOM ladder.
# Also logs foreign GPU processes every 5min to /home/token1234/occupy.log.
LOG=/home/token1234/occupy.log
echo "[occupy daemon start] $(date +%H:%M:%S)" > $LOG

babysit() {  # $1=tag $2=script $3=pgrep-pattern
  local TAG=$1 SCRIPT=$2 PAT=$3
  local LADDER="16,1,ckpt 8,2,ckpt"
  while true; do
    sleep 180
    if grep -q "teacher $TAG ALL_DONE" /home/token1234/teacher_$TAG.log 2>/dev/null; then
      echo "[$TAG ALL_DONE] $(date +%H:%M:%S)" >> $LOG; return 0
    fi
    if pgrep -f "$PAT" > /dev/null; then continue; fi
    if grep -qiE "out of memory" /home/token1234/teacher_$TAG.log 2>/dev/null; then
      local NEXT=${LADDER%% *}
      if [ -z "$NEXT" ]; then echo "[$TAG ladder exhausted]" >> $LOG; return 1; fi
      local REST=${LADDER#* }; [ "$REST" = "$LADDER" ] && REST=""
      LADDER=$REST
      local B=$(echo "$NEXT" | cut -d, -f1) A=$(echo "$NEXT" | cut -d, -f2) C=$(echo "$NEXT" | cut -d, -f3)
      mv /home/token1234/teacher_$TAG.log /home/token1234/teacher_$TAG.oom.$(date +%s).log
      echo "[$TAG oom retry -> $B $A $C] $(date +%H:%M:%S)" >> $LOG
      nohup bash "$SCRIPT" "$B" "$A" "$C" > /dev/null 2>&1 &
    else
      echo "[$TAG died non-OOM] $(date +%H:%M:%S)" >> $LOG
      tail -15 /home/token1234/teacher_$TAG.log >> $LOG 2>/dev/null; return 1
    fi
  done
}

# foreign-process logger (background sidecar)
( while true; do
    F=$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits 2>/dev/null | wc -l)
    echo "[watch] $(date +%H:%M:%S) apps=$F" >> /home/token1234/gpu_monitor.log
    sleep 300
  done ) &

# stage 1: coder
echo "[launch coder 16 1 nockpt] $(date +%H:%M:%S)" >> $LOG
nohup bash /home/token1234/teacher_coder_a100.sh 16 1 nockpt > /dev/null 2>&1 &
babysit coder /home/token1234/teacher_coder_a100.sh "Qwen2.5-Coder-7B" || exit 1

# stage 2: A.X (wait for download if needed)
while ! grep -q "OK ax" /home/token1234/teacher_dl7.log 2>/dev/null; do sleep 120; done
echo "[launch ax 16 1 nockpt] $(date +%H:%M:%S)" >> $LOG
nohup bash /home/token1234/teacher_ax_a100.sh 16 1 nockpt > /dev/null 2>&1 &
babysit ax /home/token1234/teacher_ax_a100.sh "A.X-4.0-Light"
echo "[occupy chain complete] $(date +%H:%M:%S)" >> $LOG
