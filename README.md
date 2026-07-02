# Agent Action Decision Prediction

This repository contains the code-submission solution for the AI Agent Action Decision Prediction Challenge. The task is to predict the next agent action among 14 classes from a coding-agent session state.

## Current baseline

The current submitted baseline cleared the target band:

- Public Macro-F1: `0.743`
- Main validation signal: 3-fold session OOF 2-stage Macro-F1 `0.741881`
- Model stack: `xlm-roberta-base` + `current_v1` serialization + `replay_last1 cap10000` + OOF-tuned rule boosts + sparse SVC ensemble
- Final package: `submit.zip`, with root files `script.py`, `requirements.txt`, and `model/`

Use `final_summary.md` as the source of truth for the current submitted package, validation basis, and smoke-test status.

## Repository map

```text
.
├── script.py                      # Offline inference entrypoint used by the evaluation server
├── train_transformer.py           # Transformer training, replay augmentation, OOF, caching
├── train.py                       # Shared metrics, split, bias-tuning, and legacy utilities
├── tune_sparse_svc_oof.py          # Fold-aware sparse SVC OOF ensemble tuning
├── train_sparse_svc_final.py       # Final sparse SVC artifact training
├── aggregate_oof.py                # OOF logits aggregation and bias tuning
├── evaluate_rule_boosts.py         # Rule-boost evaluation on saved logits
├── tune_oof_rule_boosts.py         # OOF-derived rule boost selection
├── final_summary.md                # Current public baseline and package evidence
├── leaderboard_calibration.md      # Local/OOF/Public calibration notes
├── research_log.md                 # Compact decision log, not raw experiment dump
├── experiments/results.csv         # Machine-readable experiment index
└── open/                           # Competition docs and baseline assets
```

Large local artifacts are intentionally ignored by git:

```text
data/
model/
output/
submit.zip
experiments/artifacts/
experiments/logits/
experiments/cache/
*.pt, *.pkl, *.safetensors
```

## Data and labels

Expected competition data files:

```text
data/train.jsonl
data/train_labels.csv
data/test.jsonl
data/sample_submission.csv
```

Each JSONL row contains `id`, `session_meta`, `history`, and `current_prompt`. Labels are one of:

```text
read_file, grep_search, list_directory, glob_pattern,
edit_file, write_file, apply_patch,
run_bash, run_tests, lint_or_typecheck,
ask_user, plan_task, web_search, respond_only
```

## Inference

The evaluation server runs `script.py`. The script expects a trained artifact under `./model` and writes `./output/submission.csv`.

```bash
python script.py
```

The current inference path loads:

- Hugging Face model from `model/hf_model/`
- metadata from `model/hf_meta.json`
- optional sparse SVC ensemble from `model/sparse_svc.pkl` and `model/sparse_meta.json`

The script fails clearly if `./model` is missing.

## Build submission zip

```bash
rm -f submit.zip
zip -r submit.zip script.py requirements.txt model
```

The archive root must contain exactly the expected submission files:

```text
submit.zip
├── script.py
├── requirements.txt
└── model/
```

## Smoke checks

Before submitting, run from a clean extracted directory with `data/` copied in:

```bash
TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 python script.py
```

Check:

- `output/submission.csv` exists
- columns are exactly `id,action`
- row count and ID order match `sample_submission.csv` when available
- every predicted action is in the 14-class label set
- inference does not require internet access
- `submit.zip` stays under the competition size limit

## Research workflow

Use a staged validation funnel:

1. Quick screen: reject weak ideas cheaply.
2. Fixed session split: sanity check and weak-class inspection.
3. Session OOF: promote finalists and tune class bias, rules, and ensembles.
4. Public leaderboard: final calibration only after packaging and smoke checks.

Do not promote a candidate from fixed-session validation alone. The current baseline showed that OOF tracked Public much better than fixed-session validation.

## Key documents

- `final_summary.md` — current baseline, score table, package evidence, and next candidates
- `leaderboard_calibration.md` — relationship between fixed, OOF, and Public scores
- `research_log.md` — compact decision log
- `experiments/results.csv` — full experiment index
