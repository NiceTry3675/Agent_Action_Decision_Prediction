# Agent Action Decision Prediction

Solution for the Dacon [AI Agent Action Decision Prediction Challenge](https://dacon.io/competitions/official/236694/):
given the recorded state of an AI coding-agent session, predict the agent's next
action among 14 classes. The metric is Macro-F1. This is a code-submission
competition — you submit a zip containing inference code plus trained model, and
the server runs it offline.

Current result: **Public Macro-F1 0.780** (as of 2026-07-05), from a
Qwen3-0.6B decoder classifier with `current_v1`, replay augmentation, OOF-tuned
class bias, and OOF-tuned rule boosts. `final_summary.md` is the source of truth
for the submitted package.

## Competition constraints

The evaluation server runs `python script.py` on a T4 (16 GB) / 3 vCPU / 12 GB
RAM box, fully offline (network only during pip install):

- inference ≤ 10 minutes, package install ≤ 10 minutes
- submission zip ≤ 1 GB, archive root exactly `script.py`, `requirements.txt`, `model/`
- zip filenames live under `submissions/`, must be ≤ 30 chars, and must not start
  with `submit`

## Task and data

Each JSONL sample has `current_prompt` (latest user message), `history` (prior
conversation and actions), and `session_meta`. The label is one of:

```text
read_file  grep_search  list_directory  glob_pattern
edit_file  write_file   apply_patch
run_bash   run_tests    lint_or_typecheck
ask_user   plan_task    web_search      respond_only
```

Local data lives in `open/data/` (`train.jsonl`, `train_labels.csv`,
`test.jsonl`, `sample_submission.csv`); the training scripts default to
`--data-dir open/data`. The evaluation server instead provides `./data/`, which
is what `script.py` reads (with a local fallback to `./open/data`). Details:
`outline.md` and `open/Data_specifications.md`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install torch scikit-learn safetensors joblib pandas
.venv/bin/pip install "transformers>=4.51,<4.52"  # Qwen3 line
```

Note: `requirements.txt` is not the dev environment — it is the minimal install
list for the evaluation server, where torch is preinstalled.

## Pipeline

Every experiment appends a row to `experiments/results.csv`; its `train_command`
column holds the exact reproduction command for each run. The current workflow is
Public-gated, with OOF used for recipe, ensemble, bias, and rule decisions (full
policy in `AGENTS.md`):

```bash
# 1. Quick screen (1 epoch, small validation) — cheap rejection of weak ideas
.venv/bin/python train_transformer.py --base-model Qwen/Qwen3-0.6B \
  --serializer current_v1 --epochs 1 --quick-val-size 600 ...

# 2. Fixed session split — sanity + weak-class check, with submittable weights
.venv/bin/python train_transformer.py ... --split session --epochs 3 \
  --save-val-model --save-fp16 --output-dir <run-specific-dir> --tune-bias

# 3. Session-grouped 3-fold OOF — bias/rule/ensemble construction
.venv/bin/python train_transformer.py ... --split session_oof --fold-id 0  # then 1, 2
.venv/bin/python aggregate_oof.py ...

# 4. OOF-tuned add-ons: rule boosts, optional sparse/ensemble legs
.venv/bin/python tune_oof_rule_boosts.py ...
.venv/bin/python tune_sparse_svc_oof.py ...
.venv/bin/python train_sparse_svc_final.py ...

# 5. Final artifacts into model/
.venv/bin/python train_transformer.py ... --final-model --save-fp16
```

Long GPU runs go through the Colab lane (`colab/COLAB.md`): code and data sync
over a Google Drive exchange folder via `colab/cloud_sync.py`, background
launches on the VM, and results merged back with `cloud_sync.py pull`.

## Inference and packaging

```bash
python script.py    # reads ./data (or ./open/data) + ./model, writes ./output/submission.csv
.venv/bin/python package_submission.py --hf-dir model --no-sparse \
  --requirements requirements_qwen3.txt --out m7_qwen3_refit.zip
```

Before submitting, run the pre-submission checklist in `AGENTS.md` (offline and
CPU-only smoke tests from a clean zip extraction). `package_submission.py`
performs the standard clean-extraction smoke and validates columns, ID order,
labels, zip root entries, filename policy, and size.

## Repository map

```text
script.py                  Offline inference entrypoint (what the server runs)
train_transformer.py       Transformer training: serializers, replay, splits/OOF, caching
train.py                   Shared metrics, splits, bias tuning
aggregate_oof.py           Merge OOF fold logits, tune class bias
tune_oof_rule_boosts.py    Select rule boosts on OOF logits
tune_sparse_svc_oof.py     Fold-aware sparse-SVC ensemble tuning
train_sparse_svc_final.py  Final sparse-SVC artifact
evaluate_*.py              One-off evaluations on saved logits
package_submission.py      Build contract-compliant zips and run smoke validation
colab/                     Cloud training lane (COLAB.md, cloud_sync.py, vm_agent.py, runner notebook)
experiments/results.csv    Experiment index (cloud rows merged only via cloud_sync.py pull)
archive/                   Archived prose logs retained for historical lookup
open/                      Competition handouts: data, spec, baseline
```

Git-ignored local artifacts: `data/`, `model/`, `output/`, `submissions/`,
`experiments/{artifacts,logits,cache,incoming}/`, and checkpoint files.

## Documents

| File | Role |
| --- | --- |
| `AGENTS.md` | Operating manual for coding agents: invariants, validation policy, leakage rules |
| `final_summary.md` | Source of truth for the submitted package and its scores |
| `leaderboard_calibration.md` | How fixed/OOF validation maps to Public score |
| `research_log.md` | Compact decision log |
| `colab/COLAB.md` | Cloud GPU lane protocol |
| `handoff_20260706_gpu_plan.md` | Current GPU execution handoff when active |
