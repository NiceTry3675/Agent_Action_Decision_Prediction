# AADP Colab lane

This file is the repository-specific overlay for Colab operation. Generic Colab
CLI behavior, authentication, session inspection, recovery, and compute-unit
safety live in the installed `colab-operator` skill:

- installed: `$CODEX_HOME/skills/colab-operator/SKILL.md`
- upstream: <https://github.com/googlecolab/google-colab-cli/blob/main/skills/colab-operator/SKILL.md>

When the skill and this file overlap, use the skill for generic `colab` behavior
and this file for AADP paths, wrappers, persistence, and promotion workflow. The
skill tracks CLI `main`; this repository currently pins
`google-colab-cli==0.6.0`, and `aadp_colab.py` always supplies the required
global auth/config flags explicitly.

## AADP control split

```text
aadp_colab.py
├── google-colab-cli       VM lifecycle, keep-alive, kernel calls, stop
├── cloud_sync.py          dirty code/data/model/result transport via Drive
└── vm_agent.py            detached training, heartbeat, auto-collect
```

Keep these boundaries:

- Colab CLI controls the VM, not large artifact transport.
- Drive/rclone remains the data and artifact plane.
- Training always runs as a detached `vm_agent.py` child, never as a long
  synchronous `colab exec` or `colab run` job.
- The existing `colab_runner*.ipynb` files are emergency fallback only.
- Evaluation-server `requirements.txt` must never contain Colab CLI packages.

## Human interaction boundary

`aadp_colab.py up` includes `colab drivemount`, which requires a real terminal
and human approval on a new VM. Following the operator skill, an agent must not
invoke that interactive step unattended.

Normal ownership is:

1. Agent: push code/data and prepare the run.
2. Human terminal: run `up` and complete Drive approval.
3. Agent: launch, monitor, collect, and stop the ready lane.

If Drive is already mounted in an existing session, the agent may use recovery
commands with `--skip-mount` instead of invoking the interactive mount again.

## Fast path

One-time for this repository:

```bash
uv tool install --force 'google-colab-cli==0.6.0'
python colab/aadp_colab.py doctor a
```

Prepare the lane:

```bash
# Agent; add --data only on the first use of this exchange folder.
python colab/aadp_colab.py push a --data

# Human terminal; complete Drive approval when prompted.
python colab/aadp_colab.py up a --gpu A100
```

Operate the ready lane:

```bash
python colab/aadp_colab.py launch a train_transformer.py -- \
  --device cuda \
  --quick-val-size 600 \
  --epochs 1 \
  --experiment-suffix cli_canary

python colab/aadp_colab.py status a
python colab/aadp_colab.py pull a
python colab/aadp_colab.py down a
```

`pull a` selects the latest collected run. `down a --pull-latest` pulls before
release. `down a --force` is only for intentionally terminating a live or
unverifiable job.

## Lane map

Tracked, non-secret configuration lives in `colab/lanes.json`:

| Lane | CLI session | Drive exchange | Default GPU | Bootstrap packages |
| --- | --- | --- | --- | --- |
| `a` | `aadp-a` | `AADP_exchange` | A100 | standard |
| `b` | `aadp-b` | `AADP_exchange_b` | L4 | standard |
| `c` | `aadp-c` | `AADP_exchange_c` | G4 | safetensors 0.8 + bitsandbytes |
| `d` | `aadp-d` | `AADP_exchange_d` | T4 | standard |

CLI token/endpoint state is separate under gitignored `.colab/`. Never commit,
copy, or include that directory in a code bundle. One exchange folder maps to
exactly one runtime; paired comparisons stay on the same lane/GPU class.

## Repository wrapper commands

```bash
python colab/aadp_colab.py doctor [lane]
python colab/aadp_colab.py push <lane> [--data]
python colab/aadp_colab.py up <lane> [--gpu GPU] [--reuse]
python colab/aadp_colab.py probe <lane>
python colab/aadp_colab.py mount <lane>                 # human terminal only
python colab/aadp_colab.py bootstrap <lane> [--force]
python colab/aadp_colab.py daemon <lane>
python colab/aadp_colab.py launch <lane> SCRIPT -- ARGS...
python colab/aadp_colab.py status <lane>
python colab/aadp_colab.py list <lane>
python colab/aadp_colab.py pull <lane> [RUN_NAME]
python colab/aadp_colab.py pull-model <lane> MODEL_NAME
python colab/aadp_colab.py down <lane> [--pull-latest] [--force]
```

Recovery of an existing session should run only the required stage. `up
--reuse` never allocates another session with the same name. Bootstrap refuses
to replace a live or uncollected workspace; use `bootstrap --force` only after
manually confirming the VM is idle.

## AADP persistence contract

`cloud_sync.py push` deliberately sends the current tracked and non-ignored
untracked working tree, including dirty provenance and a content fingerprint.
The Drive layout remains:

```text
<exchange>/
├── code/      code_<utc>_<sha8>[_dirty].tar.gz
├── data/      open_data.tar.gz
├── cmd/       queue/ done/ heartbeat.json
├── models/    saved fp16 checkpoints
└── runs/      <utc>_<experiment_id>/
               ├── results_rows.csv
               ├── logits/ artifacts/ extra/
               └── manifest.json
```

Screens must write submittable weights directly to Drive:

```text
--save-val-model --save-fp16
--output-dir /content/drive/MyDrive/<exchange>/models/<experiment-suffix>
```

Fetch them with `pull-model`, then use the normal
`package_submission.py --no-sparse` path. Cloud experiment rows enter the local
ledger only through `cloud_sync.py pull` or the wrapper; never hand-copy them.

Multi-run plans continue through `chain_runs.py`; its default heartbeat poll is
60 seconds and is configurable with `--poll-seconds`.

## Project-specific safety invariants

- In a CLI lane, release only with `aadp_colab.py down <lane>`. The legacy
  `cloud_sync.py unassign` route cannot release a detached CLI session and is
  rejected in both local and VM-side code.
- `heartbeat.run.alive`, not CLI `status` IDLE/BUSY, is authoritative for the
  detached trainer.
- The VM-side launch guard is the atomic boundary preventing two trainers from
  overwriting `last_run.json`.
- Missing/stale heartbeat, a live PID, an uncollected run, or collect failure
  blocks normal `down`; only explicit `--force` bypasses those checks.
- `down` verifies both local state removal and account-wide endpoint removal.
- `up` audits untracked account assignments before provisioning and cleans up a
  partially created session on failure or interruption.
- A CLI-mode daemon marks `idle_expired_cli_stop_required` after the project
  idle limit. It stays observable; run `down` promptly.
- `/content` is ephemeral. Models, checkpoints, and irreplaceable artifacts must
  be written to Drive before release.

## Minimal live canary

Last verified on 2026-07-11:

- `doctor` passed through CLI `whoami` plus account-wide `sessions`.
- A lifecycle-only `aadp-a` T4 session was created without Drive/bootstrap,
  and the remote probe reported Python 3.12.13, CUDA available, and a Tesla T4
  with 15,360 MiB.
- `status` and structured CLI log worked; `down --force` was used because the
  lifecycle canary intentionally skipped the project daemon/heartbeat.
- Stop verification passed: no server assignment, no keep-alive PID, and empty
  local session state remained.
- Drive mount, bootstrap, auto-collect, and a real quick screen still require
  the next live canary.

After CLI/auth changes, keep validation short:

1. `doctor a`.
2. Human runs `up a --gpu T4`.
3. Check `status a`, then `down a`.
4. Re-open and run one real `--quick-val-size 600 --epochs 1` screen.
5. Confirm heartbeat PID/GPU, auto-collect, pull, and verified release.

Local unit tests mock Colab/rclone and do not allocate a VM.

## Notebook fallback

If the CLI path is blocked, use the existing lane notebook unchanged:

1. Attach `colab_runner.ipynb` (or `_b`, `_c`, `_d`) to a Colab GPU runtime.
2. Run `[probe] -> [mount] -> [bootstrap] -> [agent]`.
3. Keep `[agent]` executing synchronously as the legacy keep-alive.
4. Use `cloud_sync.py` for launch, heartbeat, and pull.
5. Only in this synchronous-notebook mode, `cloud_sync.py unassign` exits with
   86 so the kernel cell can call `runtime.unassign()`.

Weak4 specialist and isolated teacher-export dependency procedures remain
unchanged. Continue with the Public-gated promotion rules in `AGENTS.md` after
collection.
