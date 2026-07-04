# Final Summary

## Current Public Baseline

- Public Macro-F1: `0.743`
- Package: `submissions/baseline_0702.zip` (formerly `submit.zip`)
- Model path: `script.py` + `model/`
- Baseline stack: XLM-R 5ep replay_last1 cap10000 + OOF rule boosts + sparse SVC weight `4.0`
- Main validation signal: OOF 2-stage Macro-F1 `0.741881`

XLM-R 5ep no-replay started as the canonical fixed-session baseline at raw
`0.7354` / 2-stage tuned `0.7389`, then replay_last1 + OOF rules + sparse SVC
improved the final package to Public `0.743`.

## Model Configuration

- Base model: `xlm-roberta-base`
- Serializer: `current_v1`
- Max length: `192`
- Final transformer training: 5 epochs, lr `2e-5`, batch size `16`
- Regularization: `class_weight_power=0.5`, `label_smoothing=0.02`
- Replay: `last1`, cap `10000`, weight `0.5`
- Inference add-ons: OOF-tuned class bias, 12 OOF-tuned rule boosts, sparse SVC ensemble weight `4.0`
- Artifacts: fp16 HF weights plus `model/sparse_svc.pkl`

## Scores

| Candidate | Fixed | OOF raw | OOF 2-stage | Public | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| XLM-R 5ep no-replay handoff | `0.738900` tuned | n/a | n/a | expected `0.726-0.728` | Superseded baseline. |
| XLM-R 5ep replay_last1 | `0.744625` tuned | `0.723739` | `0.728569` | not submitted | Fixed split was optimistic. |
| XLM-R replay + rules + sparse SVC w4 | `0.751733` | `0.739852` | `0.741881` | `0.743` | Current baseline. |

Use OOF, not fixed-session validation, for future finalist promotion.

## Package And Smoke

- `submit.zip`: about 529 MB
- `model/`: about 611 MB
- Archive root: `script.py`, `requirements.txt`, `model/`
- Offline zip-extracted smoke passed with `TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1`.
- CPU-only smoke passed with `CUDA_VISIBLE_DEVICES=''`.
- Output file: `output/submission.csv`
- Output columns: exactly `id,action`
- Output row count and id order match `sample_submission.csv`.
- All predictions are in the 14 valid labels.

## Next Improvement Candidates

- Make any new promotion decision from OOF logits, not fixed split alone.
- Prioritize weak-class gains for `list_directory`, `read_file`, `grep_search`, `web_search`, and `glob_pattern`.
- Keep rule and sparse ensemble changes only if they improve OOF after 2-stage bias tuning.
- Alternative encoders screened under quick-val only:
  - `xlm-align-base`: collapsed under the current recipe; not active.
  - `infoxlm-base`: collapsed to `read_file`; not active.
  - `mdeberta-v3-base`: not collapsed but slow; long-shot.
  - `bert-base-multilingual-cased`: possible ensemble-diversity candidate.

## References

- Experiment index: `experiments/results.csv`
- Detailed metrics and tuning artifacts: `experiments/artifacts/*.json`
- Dacon submission page: `https://dacon.io/competitions/official/236694/mysubmission`
