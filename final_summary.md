# Current Submission Candidate Summary

## Candidate

The current packaged candidate is `submit.zip`, built from:

- `xlm-roberta-base`
- `current_v1` serializer
- `max_length=192`
- 5 epochs, learning rate `2e-5`, batch size 16
- `class_weight_power=0.5`, `label_smoothing=0.02`
- `replay_last1 cap10000 weight=0.5`
- OOF-tuned transformer class bias
- 12 OOF-tuned rule boosts
- final sparse SVC ensemble, sparse weight `4.0`

The transformer was final-refit on all 70,000 labeled rows plus replay examples and saved as fp16 weights. The sparse SVC was final-fit on all labeled rows.

## Validation Basis

- Canonical XLM-R 5ep no-replay handoff baseline:
  - fixed raw Macro-F1: `0.7354`
  - fixed 2-stage bias-tuned Macro-F1: `0.7389`
  - expected Public: roughly `0.726-0.728`
- XLM-R 5ep replay fixed-session:
  - raw Macro-F1: `0.739664`
  - old bias-tuned Macro-F1: `0.743909`
  - 2-stage bias-tuned Macro-F1: `0.744625`
- XLM-R 5ep replay 3-fold session OOF:
  - raw Macro-F1: `0.723739`
  - old bias-tuned Macro-F1: `0.728371`
  - 2-stage bias-tuned Macro-F1: `0.728569`
- XLM-R replay + OOF rule boosts + sparse SVC weight 4.0:
  - combined raw OOF Macro-F1: `0.739852`
  - old bias-tuned OOF Macro-F1: `0.741570`
  - 2-stage tuned OOF Macro-F1: `0.741881`

Use the OOF `0.741881` score as the main promotion signal. The fixed-session cross-check reached `0.751733`, but the fixed split has been optimistic.

## Package Evidence

- `submit.zip` size: 529MB
- `model/` size: 611MB
- Archive root contains only `script.py`, `requirements.txt`, and `model/`
- `model/hf_model/model.safetensors` is fp16, about 556MB decimal
- `model/sparse_svc.pkl` is included
- `requirements.txt` pins:
  - `transformers==4.46.3`
  - `safetensors==0.8.0`
  - `scikit-learn==1.8.0`
  - `joblib==1.5.3`

## Smoke Checks

- Zip-extracted offline smoke passed with `TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1`.
- CPU-only smoke passed with `CUDA_VISIBLE_DEVICES=''`.
- `script.py` creates `output/submission.csv`.
- Output columns are exactly `id,action`.
- Row count and id order match `sample_submission.csv` on the local format-check data.
- All predictions are in the 14 valid labels.
- Missing `model/` fails clearly.
- `python3 -m py_compile train.py train_transformer.py aggregate_oof.py tune_sparse_svc_oof.py train_sparse_svc_final.py script.py` passes.

## Public Result

`submit.zip` was submitted and reached reported Public Macro-F1 `0.743`, clearing the `0.74` Public target.

Dacon pages:

- Competition page: `https://dacon.io/competitions/official/236694/overview/description`
- Submission page: `https://dacon.io/competitions/official/236694/mysubmission`
- Public leaderboard: `https://dacon.io/competitions/official/236694/leaderboard`

Calibration note: OOF 2-stage `0.741881` mapped closely to Public `0.743`, while the fixed cross-check `0.751733` remained optimistic. Use the OOF/rules/sparse validation path as the main baseline for future improvements.

Short Korean team-share summary: `team_share_ko_0702.md`.
