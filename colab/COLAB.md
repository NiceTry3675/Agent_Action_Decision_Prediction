# Colab cloud training lane

How cloud GPU training runs through the VS Code Colab extension, and how results
flow back into this repo. The control plane is `colab/colab_runner.ipynb`; the
transport is a Google Drive folder `AADP_exchange/`.

## Drive exchange layout

```text
AADP_exchange/
├── code/   code_<utc>_<sha8>[_dirty].tar.gz   # from `cloud_sync.py push` (tracked files, working-tree versions)
├── data/   open_data.tar.gz                   # from `cloud_sync.py push --data` (one-time, 102MB)
├── cmd/    queue/ done/ heartbeat.json        # command channel (vm_agent daemon <-> cloud_sync cmd/hb)
└── runs/   <utc>_<experiment_id>/             # from vm_agent collect (or the notebook [collect] cell)
            ├── results_rows.csv               # new experiments/results.csv rows only
            ├── logits/  artifacts/            # files created after [bootstrap]
            ├── extra/                         # optional large outputs (model dirs)
            └── manifest.json
```

Drive is used instead of a GitHub PAT clone because the repo is private, it keeps a
single auth surface, and it can ship uncommitted experiment code (bundles are tagged
`_dirty` with the dirty file list recorded in `cloud_manifest.json`).

## One-time setup (human)

1. Install rclone in WSL (`sudo apt install rclone` or the official install script),
   then `rclone config` → new remote named `gdrive`, type Google Drive, default scope,
   finish the browser OAuth. Verify with `rclone lsd gdrive:`.
   (Different remote name: export `AADP_RCLONE_REMOTE`.)
2. Install the Google Colab VS Code extension and sign in. Create a GPU runtime
   (**default to L4** for base runs — user preference, ~1.5-2x faster than T4 with
   24GB VRAM; A100 for xlm-roberta-large) and attach it as the kernel of
   `colab/colab_runner.ipynb`.
3. Upload the dataset once: `python colab/cloud_sync.py push --data`.

## Per-session checklist (human)

- Open `colab/colab_runner.ipynb` with the Colab kernel attached.
- Run the `[mount]` cell and click through the Drive OAuth (once per runtime).
- Keep the notebook tab active in VS Code while the agent drives it. The agent's
  `executeCode` tool fails with `No active notebook editor` otherwise.

## Agent protocol

Every `mcp__ide__executeCode` call pops a VS Code Quick Pick the user must approve —
this cannot be disabled (official docs, "Jupyter execution always asks first"). So the
notebook is only used to boot the runtime; everything else goes through the command
channel below.

1. If local code changed since the last push: `python colab/cloud_sync.py push`.
2. Run `[probe]`; check the GPU class fits the job.
3. Run `[bootstrap]`; verify the printed commit matches local HEAD (or the intended
   dirty push) and `transformers` is 4.46.3.
4. Run `[agent]` — runs `vm_agent.py daemon` synchronously (the session's last Quick
   Pick). The cell stays busy for the whole session; that is the keepalive. Colab's
   idle timer only counts executing cells — a detached daemon leaves the kernel idle
   and the runtime gets reclaimed ~90 min after the last cell run, even with the GPU
   at full load (2026-07-03 incident).
5. From here drive everything locally, no notebook cells needed:
   - launch: `python colab/cloud_sync.py launch train_transformer.py -- --device cuda ...`
     (args go after `--` as plain argv; quoting — spaces in `--notes`, leading-dash
     values — is handled by the tool. The old `cmd "python colab/vm_agent.py launch ..."`
     form still works.)
   - multi-fold OOF chains: `python colab/chain_oof_folds.py --folds 1 2` waits for
     the in-flight run's collect, then launches each fold in turn (local process —
     it dies with the machine; safe to restart, see its docstring).
   - poll (cheap, no VM roundtrip): `python colab/cloud_sync.py hb` — the heartbeat
     carries run pid/state, a 5-line log tail, GPU util, and idle countdown
   - deeper look: `python colab/cloud_sync.py cmd "python colab/vm_agent.py status"`
   - collect: automatic — the daemon collects as soon as the training process exits
     (`collect_note` in the heartbeat names the run); manual fallback:
     `... cmd "python colab/vm_agent.py collect"`
6. Locally `python colab/cloud_sync.py pull <run_name>`. The pull merges new rows into
   `experiments/results.csv` deduped by experiment_id and places logits/artifacts.
7. Done with the VM? `python colab/cloud_sync.py unassign` — do not leave it idling.
8. Continue with the normal validation funnel (`aggregate_oof.py`, OOF promotion).
   Never hand-edit `results.csv` with cloud numbers.

The notebook `[args]`/`[launch]`/`[poll]`/`[collect]` cells remain as the manual
fallback when the daemon is down (each needs a Quick Pick approval).

## Command channel and CU safety

`vm_agent.py daemon` (VM) polls `AADP_exchange/cmd/queue/` every ~15s, executes each
JSON command spec with `shell=True` in `/content/AADP`, and writes the result to
`cmd/done/<id>.json`; `cloud_sync.py cmd` (local) queues a spec via `rclone rcat` and
polls for the result. Expect 20–90s roundtrip (Drive propagation both ways). Builtins:
`@unassign` releases the runtime, `@stop` exits the daemon. On a wait timeout the local
side reports whether the daemon CLAIMED the command (executing / result propagating —
re-check `done/` later) or it is STILL QUEUED (daemon has not seen it — likely down).

If the daemon dies, nothing executes queued commands — `status`/`unassign` are inert.
With the synchronous `[agent]` cell this is at least visible (the cell ends in VS Code)
and self-limiting (idle kernel → Colab reclaims in ~90 min, capping CU burn); the
fastest manual stop is releasing the runtime from the Colab UI / VS Code extension.

CU (compute unit) safety, in the daemon:

- **Stale-command expiry**: queued commands older than `AADP_CMD_MAX_AGE_MIN` (default
  30 min) are answered with rc=125 `expired` instead of executed, so an `@unassign` or
  launch queued at a dead daemon cannot fire when the next daemon starts.
- **Auto-collect**: when the tracked training process exits, the daemon runs
  `collect` once, so results reach Drive even if nobody is watching. Collected
  experiment_ids fold into the baseline, making re-collects no-ops.
- **Idle auto-unassign**: after `AADP_IDLE_MAX_MIN` minutes (default 45) with no live
  training process and no incoming commands, the runtime is released — but only after
  the last run was collected. Idle VMs stop burning CUs on their own.
- Explicit release: `python colab/cloud_sync.py unassign` right after the final pull.
- Release mechanics: `google.colab runtime.unassign()` only works from the kernel, not
  from the daemon subprocess (`'NoneType' object has no attribute 'kernel'` — this
  failed silently until 2026-07-03). So on `@unassign` or the idle limit the daemon
  exits with code 86 and the synchronous `[agent]` cell performs the actual unassign.

Heartbeat: `cmd/heartbeat.json`, rewritten every ~15s. `cloud_sync.py hb` prints it
with an age check — age over ~3 minutes means the daemon or VM is gone (recycled VM,
Drive auth expiry, or idle unassign already fired).

## Failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Code execution cancelled by user` from executeCode | VS Code Quick Pick dismissed (Esc/focus loss) — it always asks, by design | rerun; ask the user to press Execute |
| `cloud_sync.py cmd` times out | timeout message says CLAIMED → executing/Drive lag; STILL QUEUED → daemon down or VM recycled | claimed: re-check `done/` later; queued: rerun `[bootstrap]` + `[agent]`, or new runtime if hb shows unassign fired |
| runtime reclaimed ~90 min after the last cell run, GPU still busy | kernel idle — Colab only counts executing cells as activity; background daemon/training do not | keep the synchronous `[agent]` cell running the whole session; rerun it after any kernel restart |
| `No active notebook editor` from executeCode | notebook tab not active | ask the user to focus the runner notebook tab |
| NameError on RUN_ARGS/PID | kernel reconnected, variables lost | `[poll]`/`[collect]` read state files — just rerun; rerun `[args]` before `[launch]` |
| `[bootstrap]` assert "no code bundle" | never pushed | local `cloud_sync.py push` |
| `/content/aadp_state.json` missing | VM recycled | rerun from `[mount]`/`[bootstrap]`; in-flight runs are lost |
| Drive mount errors | auth expired | rerun `[mount]` |
| run vanished mid-training | Colab runtime recycled | collect finished runs promptly; do not start multi-hour runs (xlm-r-large) until per-epoch Drive checkpointing (`--select-best-epoch` work) lands |

## Constraints

- `/content` is ephemeral; anything not collected to Drive is lost on recycle.
- Pin only `transformers==4.46.3` (tokenizer consistency with local); keep Colab's
  torch. Cross-GPU runs are not bit-reproducible anyway; OOF comparisons are fine.
- Colab session/idle limits apply; prefer collecting right after each run finishes.
