# Final Submission Summary

## Best Local Validation

- Baseline-compatible random split:
  - Baseline TF-IDF + LogisticRegression: Macro-F1 `0.438757`
  - Final candidate: Macro-F1 `0.662137`
- Session-aware split using id prefix before `-step_`:
  - Baseline TF-IDF + LogisticRegression: Macro-F1 `0.432977`
  - Final candidate: Macro-F1 `0.656725`

## Chosen Model

The final submission uses `prompt_svc`:

- `LinearSVC`
- `class_weight="balanced"`
- `C=0.05`
- TF-IDF word n-grams over `current_prompt`
- TF-IDF character n-grams over `current_prompt`
- Compact intent/action/workspace metadata tokens
- Validation-tuned additive class bias for Macro-F1

The final artifact was refit on all 70,000 training rows and saved as `model/model.joblib`.

## Discarded Approaches

- Baseline current-prompt LogisticRegression was much weaker on both random and session-aware validation.
- Full raw history channels slightly hurt session-aware Macro-F1, likely because old user turns and result text diluted the current decision signal.
- No-class-weight SVC reduced Macro-F1 versus balanced weighting.
- Very low `C=0.025` underfit compared with `C=0.05`.

## Risks

- The hardest classes remain `list_directory`, `read_file`, `glob_pattern`, `web_search`, and `grep_search`.
- Validation uses one fixed session-aware split; true private distribution may differ.
- Class-bias tuning is validation-derived and may overfit slightly, but it improved both conservative and baseline-compatible local checks.

## Build Command

```bash
rm -f submit.zip && zip -r submit.zip script.py requirements.txt model
```

## Smoke Checks

- `script.py` runs from repository root.
- Extracted `submit.zip` runs from a clean temporary directory with `data/` added.
- `output/submission.csv` is created.
- Output columns are exactly `id,action`.
- Row count and id order match `sample_submission.csv` on the local 5-row format-check data.
- All predictions are in the 14 valid labels.
- `submit.zip` contains only `script.py`, `requirements.txt`, and `model/` at the root.
