#!/bin/bash
# coder logits relay chain v2: stats -> auto epoch-fallback (acc>=0.85 -> ep2 ckpt logits) -> KD -> gate(fp16) -> sha
LOG=/root/coder_kd_chain.log
mark() { echo "[$1] $(date +%m-%d\ %H:%M:%S)" >> $LOG; }
# atomic once-only lock: a re-fired watcher must not launch a second KD run
mkdir /root/coder_kd.lock 2>/dev/null || exit 0
echo "[coder kd chain start] $(date +%m-%d\ %H:%M:%S)" > $LOG

# ssh.exe interop can hang on session close after the remote command already ran --
# every call gets a timeout wrapper and side effects are verified by file checks, not rc
SSHA="timeout 60 /mnt/c/Windows/System32/OpenSSH/ssh.exe -o BatchMode=yes -o ConnectTimeout=10 -p 8822 token1234@203.252.22.61"
SCPA="timeout 300 /mnt/c/Windows/System32/OpenSSH/scp.exe -o BatchMode=yes -P 8822"

stats_line() {
  /root/venv211/bin/python - "$1" <<'PYEOF'
import sys, torch
p = torch.load(sys.argv[1], map_location="cpu", weights_only=False)
lo = p["logits"].float(); y = torch.tensor(p["y_true"])
acc = (lo.argmax(1) == y).float().mean().item()
pr = torch.softmax(lo, 1)
ent = (-pr * pr.clamp_min(1e-9).log()).sum(1).mean().item()
print(f"train_acc={acc:.4f} wrong={1-acc:.4f} entropy={ent:.3f} top1={pr.max(1).values.mean().item():.3f}")
PYEOF
}

cp /mnt/c/dacon/teacher_coder_train70k_fp16.pt /root/dacon/ || { mark "pt copy FAILED"; exit 1; }
S3=$(stats_line /root/dacon/teacher_coder_train70k_fp16.pt)
echo "CODER_TEACHER_STATS ep3 $S3" >> $LOG
ACC=$(echo "$S3" | sed 's/.*train_acc=\([0-9.]*\).*/\1/')
PACK=/root/dacon/teacher_coder_train70k_fp16.pt
TAGNOTE="ep3-final"

HI=$(/root/venv211/bin/python -c "print(1 if float('$ACC' or 0) >= 0.85 else 0)")
if [ "$HI" = "1" ]; then
  mark "acc $ACC >= 0.85 -> ep2 ckpt fallback export on A100"
  $SSHA "nohup bash /home/token1234/export_coder_ep2.sh > /dev/null 2>&1 &" </dev/null
  mark "ep2 export launch attempted"
  N=0; OK=0
  while [ $N -lt 22 ]; do
    sleep 300; N=$((N+1))
    if $SSHA "grep -q 'ep2 export ALL_DONE' /home/token1234/coder_ckpt_export.log" </dev/null 2>/dev/null; then OK=1; break; fi
    if $SSHA "grep -qiE 'Traceback' /home/token1234/coder_ckpt_export.log" </dev/null 2>/dev/null; then mark "ep2 export ERROR"; break; fi
  done
  if [ "$OK" = "1" ]; then
    rm -f /mnt/c/dacon/teacher_coder_ep2_train70k_fp16.pt
    $SCPA token1234@203.252.22.61:/home/token1234/teacher_coder_ep2_train70k_fp16.pt /mnt/c/dacon/ </dev/null
    [ -f /mnt/c/dacon/teacher_coder_ep2_train70k_fp16.pt ] && cp /mnt/c/dacon/teacher_coder_ep2_train70k_fp16.pt /root/dacon/ || OK=0
  fi
  if [ "$OK" = "1" ]; then
    S2=$(stats_line /root/dacon/teacher_coder_ep2_train70k_fp16.pt)
    echo "CODER_TEACHER_STATS ep2 $S2" >> $LOG
    ACC2=$(echo "$S2" | sed 's/.*train_acc=\([0-9.]*\).*/\1/')
    PICK=$(/root/venv211/bin/python -c "print('ep2' if abs(float('$ACC2')-0.81) < abs(float('$ACC')-0.81) else 'ep3')")
    if [ "$PICK" = "ep2" ]; then
      PACK=/root/dacon/teacher_coder_ep2_train70k_fp16.pt
      TAGNOTE="ep2-ckpt"
    fi
  else
    mark "ep2 export timeout/fail - keeping ep3"
  fi
fi
$SSHA "rm -f /home/token1234/hold_ax" </dev/null 2>/dev/null
$SSHA "rm -f /home/token1234/hold_ax" </dev/null 2>/dev/null || mark "hold release uncertain (ax 3h backstop will clear)"
mark "teacher pick: $TAGNOTE"

bash /root/kd_coder_refit_wsl.sh "$PACK" "$TAGNOTE"
grep -q "kd coder pack ALL_DONE" /root/kd_coder_refit.log 2>/dev/null || { mark "kd refit FAILED"; tail -20 /root/kd_coder_refit.log >> $LOG; exit 1; }
mark "kd refit done ($TAGNOTE)"

bash /root/gate_pack.sh models/kd_coder_refit coderkd
grep -E "macro" /root/gate_coderkd.log >> $LOG
sha256sum /mnt/c/dacon/kd_hcx_coder.zip >> $LOG 2>&1
mark "coder kd chain ALL_DONE"
