# AGENTS.md

Guidance for coding agents working in this repository.

## Start here

Before changing code or running new experiments, read these files in order:

1. `final_summary.md` — current Public baseline and submitted package details
2. `leaderboard_calibration.md` — how local validation maps to Public score
3. `research_log.md` — compact decision log
4. `experiments/results.csv` — experiment index
5. `script.py` — current offline inference path

The current achieved baseline is:

```text
Public Macro-F1: 0.743
Stack: XLM-R 5ep + replay_last1 cap10000 + OOF rule boosts + sparse SVC weight 4.0
Primary validation signal: OOF 2-stage Macro-F1 0.741881
```

Future work is leaderboard improvement, not target recovery.

## Do not break the submission path

The evaluation server runs:

```bash
python script.py
```

`script.py` must:

- read `./data/test.jsonl`
- optionally read `./data/sample_submission.csv`
- load all artifacts from `./model/`
- write `./output/submission.csv`
- preserve `id,action` columns
- produce only the 14 valid labels
- run offline

Do not add network calls to inference. Do not make `script.py` silently fall back to stale baseline artifacts. Missing `./model` should fail clearly.

## Submission package contract

The final zip must have this root structure:

```text
submit.zip
├── script.py
├── requirements.txt
└── model/
```

Do not commit or require local-only paths. Large artifacts are ignored by git: `data/`, `model/`, `output/`, `submit.zip`, `experiments/artifacts/`, `experiments/logits/`, and model checkpoint formats.

## Validation policy

Use this promotion funnel:

1. Quick screen: reject weak ideas cheaply.
2. Fixed session split: sanity check and weak-class inspection.
3. Session OOF: promote finalists and tune class bias, rules, and ensembles.
4. Public: final calibration after packaging and smoke checks.

Fixed-session validation is optimistic. Do not promote a package from fixed-session score alone. OOF is the main finalist metric.

## Replay and leakage rules

Replay examples are allowed only when fold/session safety is preserved.

- Replay examples from validation sessions must never enter training.
- Use the existing fold-aware replay implementation in `train_transformer.py`.
- `replay_last1 cap10000 weight=0.5` is the current proven setting.
- Do not expand replay depth or size unless OOF or weak-class F1 improves.

## Logging policy

Keep human-facing docs compact.

- Put experiment rows in `experiments/results.csv`.
- Put detailed metrics, bias vectors, rules, and ensemble artifacts in `experiments/artifacts/*.json`.
- Put logits in `experiments/logits/`.
- Put only decisions in `research_log.md`.

Do not paste full confusion tables, prediction distributions, or per-class dumps into `research_log.md` unless they are central to a decision.

## Current model stack

The submitted inference stack combines:

1. `xlm-roberta-base` transformer logits
2. OOF-tuned transformer class bias
3. OOF-derived rule boosts
4. sparse SVC decision scores
5. sparse ensemble class bias

The sparse SVC component is an ensemble component, not a strong standalone model. Keep it only when it improves OOF after bias tuning.

## Weak classes

Known recurring weak/confused classes:

```text
list_directory
read_file
grep_search
glob_pattern
web_search
lint_or_typecheck
run_tests vs run_bash
```

Any proposed improvement should report whether these classes improve or only shift errors around.

## Alternative encoder policy

Alternative encoders are secondary. Existing quick screens showed:

- `xlm-align-base`: collapsed under current recipe
- `infoxlm-base`: collapsed to `read_file`
- `mdeberta-v3-base`: not collapsed but slow; long-shot
- `bert-base-multilingual-cased`: possible ensemble-diversity candidate

Do not spend long runs on alternative encoders unless there is a clear OOF ensemble-diversity hypothesis.

## Code style and safety

- Keep files compact and operational.
- Prefer small, testable changes over broad rewrites.
- Do not split the repository into many extra modules unless it clearly reduces risk.
- Preserve exact label names and class order unless all artifacts are regenerated.
- Keep `requirements.txt` minimal and compatible with the evaluation server.
- Do not modify ignored large artifacts through git.

## Required checks before a leaderboard submission

Run smoke checks from a clean extracted zip with `data/` added:

```bash
TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 python script.py
```

Verify:

- `output/submission.csv` exists
- columns are exactly `id,action`
- ID order matches `sample_submission.csv` when available
- all labels are valid
- CPU-only smoke is acceptable when feasible: `CUDA_VISIBLE_DEVICES='' python script.py`
- `submit.zip` size is under the competition limit
- archive root contains only `script.py`, `requirements.txt`, and `model/`

## When in doubt

If a change only improves the fixed split, do not package it. If it improves OOF and passes smoke checks, update `final_summary.md`, `leaderboard_calibration.md`, `research_log.md`, and `experiments/results.csv` with the minimum necessary information.
