# Final Submission Summary

## Best Local Validation

- Baseline-compatible random split:
  - Baseline TF-IDF + LogisticRegression: Macro-F1 `0.438757`
  - Previous CPU sparse candidate: Macro-F1 `0.662137`
  - Final GPU candidate: Macro-F1 `0.722073`
- Session-aware split using id prefix before `-step_`:
  - Baseline TF-IDF + LogisticRegression: Macro-F1 `0.432977`
  - Previous CPU sparse candidate: Macro-F1 `0.656725`
  - Final GPU candidate: Macro-F1 `0.710721`

## Chosen Model

The final submission uses a GPU transformer:

- `distilbert-base-multilingual-cased`
- Fine-tuned on CUDA for 3 epochs
- Serialized prompt/action/workspace text input
- Class-weighted cross entropy with label smoothing
- Validation-tuned additive class bias for Macro-F1

The final artifact was refit on all 70,000 training rows and saved under `model/hf_model/`, with metadata in `model/hf_meta.json`.

## Discarded Approaches

- Baseline current-prompt LogisticRegression was much weaker on both random and session-aware validation.
- Full raw history channels slightly hurt session-aware Macro-F1, likely because old user turns and result text diluted the current decision signal.
- No-class-weight SVC reduced Macro-F1 versus balanced weighting.
- Very low `C=0.025` underfit compared with `C=0.05`.
- GPU hashed/vocab sparse Torch classifiers underperformed the CPU sparse SVC, topping out near Macro-F1 `0.574929`.
- GPU transformer 1 epoch was close to the old CPU sparse model, while 2-3 epochs clearly improved validation Macro-F1.

## Risks

- The hardest classes remain `list_directory`, `read_file`, `web_search`, `grep_search`, and `lint_or_typecheck`.
- Validation uses one fixed session-aware split; true private distribution may differ.
- Class-bias tuning is validation-derived and may overfit slightly, but it improved both conservative and baseline-compatible local checks.
- The final package is much larger than the sparse model but remains under the 1 GB limit.

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
- Local smoke test used CUDA and printed `device=cuda`.
