# AGENTS.md

Current score, target, and in-flight state live in the docs below, not here.

## Read first

`final_summary.md` (submitted package — source of truth) →
`leaderboard_calibration.md` (local↔Public mapping) → `research_log.md`
(decision log) → `experiments/results.csv` (all runs; the `train_command` column
holds the exact command per run). Cloud GPU work: `colab/COLAB.md`.

## Task and evaluation environment

Dacon 236694: predict the next coding-agent action (14 classes) from
`current_prompt`/`history`/`session_meta`; metric Macro-F1. The server runs
`python script.py` offline on T4 16GB / 3 vCPU / 12GB RAM: inference ≤ 10 min, pip install
≤ 10 min, submission zip ≤ 1 GB.

## Submission contract

- `script.py`: reads `./data/test.jsonl` (local fallback `./open/data`), loads
  everything from `./model/` — fail loudly if missing, no silent fallback to
  stale artifacts — writes `./output/submission.csv` with columns exactly
  `id,action`, only the 14 valid labels, no network calls.
- Submission zip root: exactly `script.py`, `requirements.txt`, `model/`.
  Zips live in `submissions/` (gitignored); filenames ≤ 30 chars, no `submit`
  prefix (e.g. `m3_len448_s42.zip`) — `package_submission.py` enforces both.
- `requirements.txt` is the eval-server install list (torch preinstalled there);
  training deps live in `.venv` only.
- Label names and class order must not change unless all artifacts are
  regenerated together.

## Validation and promotion (2026-07-04 protocol: Public-gated)

1. Quick screen (`--quick-val-size 600`, 1 epoch) — optional cheap rejection.
2. Fixed session split (`--split session`) full run, launched with
   `--save-val-model --save-fp16 --output-dir <run-specific dir>` so every
   screen leaves submittable weights — sanity + weak-class check. Fixed is
   optimistic vs Public by ~0.009-0.016 and single-seed deltas inside ±0.005
   are seed noise; don't decide from this step alone.
3. **Public — the decision metric.** `package_submission.py` (new encoders:
   `--no-sparse`) → offline smoke → submit. Budget 10/day; log every
   submission in `leaderboard_calibration.md`. One variable per submission;
   Public deltas < 0.002 are measurement noise, and a single Public delta
   under ~0.02 is still not directional evidence (instance draws swing that
   much). 2026-07-05 amendment (standings shifted — see research_log):
   sub-0.02 levers are promoted from consolidation garnish to first-class
   lanes; judge recipes on OOF (a single Public delta can't separate recipe
   effect from instance draw) and stack validated levers into consolidated
   packs. Public = the final score, 100% — no private holdout (user-
   confirmed; not in rule.md). Public-best instance selection (best-of-N
   seed refits) is therefore legitimate and directly rewarded; the binding
   constraint is the 10/day slot budget, not overfitting.
4. Winner consolidation: `--final-model` full refit → repackage → resubmit.
   Session OOF (`--split session_oof` + `aggregate_oof.py`) is demoted to a
   tool — ensemble construction, bias/rule tuning, near-tie calls — not a
   promotion gate.

One variable per experiment. The `final_summary.md` baseline changes only on a
Public improvement. Report the weak classes, not just the aggregate:
`list_directory`, `read_file`, `grep_search`, `glob_pattern`, `web_search`,
`lint_or_typecheck`, `run_tests` vs `run_bash`.

## Replay and leakage

Validation-session examples must never enter training via replay; use only the
fold-aware replay in `train_transformer.py`. Proven setting: `--replay-mode
last1 --max-replay-samples 10000 --replay-sample-weight 0.5`. Do not expand
unless Public or weak-class F1 improves.

## Where results go

- Experiment rows → `experiments/results.csv`; cloud rows only via
  `colab/cloud_sync.py pull` (dedupes by experiment_id), never hand-copied.
- Detailed metrics, bias vectors, rules, ensembles →
  `experiments/artifacts/*.json`; logits → `experiments/logits/`; decisions
  only → `research_log.md` (no confusion tables or per-class dumps in prose docs).
- Baseline change → update `final_summary.md`, `leaderboard_calibration.md`,
  `research_log.md` minimally.

## Environment

`.venv/bin/python` (3.12). Local data is `open/data/` (training default
`--data-dir open/data`); `./data/` exists only on the eval server. Tuning chain:
`aggregate_oof.py`, `tune_oof_rule_boosts.py`, `tune_sparse_svc_oof.py`,
`train_sparse_svc_final.py`.

## Cloud lane (Colab)

Protocol: `colab/COLAB.md`. Sync code/data/results only via `cloud_sync.py
push`/`pull`; launch training in the background (`[launch]` cell or
`vm_agent.py launch`), never synchronously in a notebook cell; release idle
runtimes with `cloud_sync.py unassign`.

## Alternative encoders

Screened: `xlm-align-base` and `infoxlm-base` collapsed under the current
recipe; `mdeberta-v3-base` slow long-shot; klue/koelectra-class Korean vocabs
rejected at the tokenizer gate (+35-38% tokens on ~8%-Korean inputs). qv600
logit-ensemble probe (`experiments/artifacts/20260703_qv600_encoder_diversity_probe.json`):
`bert-base-multilingual-cased` (0.6976) is the diversity candidate — best
pairing with xlm-r-large; `kakaobank/kf-deberta-base` (0.6945) demoted, helps
no ensemble (ask_user-rule long-shot only). No long runs without a concrete
OOF ensemble-diversity hypothesis.

## Pre-submission smoke

From a clean extraction of the rebuilt zip with `data/` added:

```bash
TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 python script.py
CUDA_VISIBLE_DEVICES='' python script.py   # CPU-only, when feasible
```

Verify: columns exactly `id,action`; ID order matches `sample_submission.csv`;
all labels valid; zip under 1 GB; archive root has only the three required entries.
