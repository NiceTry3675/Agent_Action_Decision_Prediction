"""Chain Track B xlm-r-large OOF fold launches through the Colab command channel.

Waits for the run currently in the heartbeat to be collected, then launches the
given folds sequentially (each launch waits for its collect before the next).
Recipe is pinned to the Track B fixed run (len192 5ep batch8 lr2e-5 replay_last1).

Usage (e.g. resuming after a local reboot killed a previous chain):
    .venv/bin/python colab/chain_oof_folds.py --folds 1 2

Check what already ran first: `rclone lsf gdrive:AADP_exchange/runs/` for
collected folds, `cloud_sync.py hb` for the in-flight one. --floor overrides the
wait stamp (defaults to the heartbeat run's launch stamp); if the heartbeat has
no live run and there is nothing to wait for, launching starts immediately.

Exit codes: 0 = all folds collected, 2 = heartbeat stale (daemon/VM gone),
3 = deadline, 4 = collect problem, 5 = launch failed.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = str(REPO / ".venv/bin/python")
REMOTE = "gdrive:AADP_exchange/cmd/heartbeat.json"

BASE_ARGS = [
    "--device", "cuda", "--split", "session_oof", "--n-folds", "3",
    "--base-model", "xlm-roberta-large", "--serializer", "current_v1",
    "--max-length", "192", "--epochs", "5", "--batch-size", "8",
    "--eval-batch-size", "64", "--lr", "2e-5", "--class-weight-power", "0.5",
    "--label-smoothing", "0.02", "--replay-mode", "last1",
    "--max-replay-samples", "10000", "--replay-sample-weight", "0.5",
    "--keep-threshold", "0.0", "--tokenize-batch-size", "1024",
    "--no-research-log",
]


def read_hb():
    out = subprocess.run(["rclone", "cat", REMOTE], capture_output=True, text=True, timeout=90)
    return json.loads(out.stdout)


def wait_collect(floor, deadline):
    while time.time() < deadline:
        time.sleep(240)
        try:
            hb = read_hb()
        except Exception as exc:
            print("hb read failed:", exc, flush=True)
            continue
        age = time.time() - hb["ts"]
        note = hb.get("collect_note", "")
        run = hb.get("run") or {}
        tail = (run.get("tail") or [""])[-1]
        print(f"age={age:.0f}s alive={run.get('alive')} note={note[:55]} tail={tail.strip()[:70]}",
              flush=True)
        if note.startswith("collected:") and note[10:25] > floor:
            print("COLLECTED:", note[10:], flush=True)
            return
        if note.startswith(("collect_error", "collect_skipped")):
            sys.exit(4)
        if age > 400:
            print(f"HEARTBEAT STALE ({age:.0f}s)", flush=True)
            sys.exit(2)
    print("deadline reached", flush=True)
    sys.exit(3)


def launch(fold):
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    args = ([PY, "colab/cloud_sync.py", "launch", "train_transformer.py", "--"]
            + BASE_ARGS + [
        "--fold-id", str(fold),
        "--experiment-suffix", f"oof_xlmr_large_len192_ep5_replay_last1_fold{fold}",
        "--notes", f"Track B OOF fold {fold}/3 xlm-r-large, recipe as fixed run (batch8 lr2e-5)",
    ])
    r = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=400)
    print(f"launch fold{fold}: rc={r.returncode}\n{r.stdout.strip()}", flush=True)
    if r.returncode != 0:
        print(r.stderr, flush=True)
        sys.exit(5)
    return stamp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folds", type=int, nargs="+", required=True)
    parser.add_argument("--floor", help="wait for a collect stamped after this UTC stamp "
                                        "(default: launch stamp of the heartbeat's live run)")
    parser.add_argument("--max-minutes", type=int, default=340)
    args = parser.parse_args()
    deadline = time.time() + args.max_minutes * 60

    floor = args.floor
    if floor is None:
        run = read_hb().get("run") or {}
        match = re.match(r"run_(\d{8}_\d{6})\.log$", run.get("log") or "")
        if run.get("alive") and match:
            floor = match.group(1)
            print(f"waiting on live run {run['log']}", flush=True)
    if floor:
        wait_collect(floor, deadline)
    for fold in args.folds:
        wait_collect(launch(fold), deadline)
    print("ALL FOLDS COLLECTED", flush=True)


if __name__ == "__main__":
    main()
