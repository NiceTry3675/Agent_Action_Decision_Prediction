# XLM-R Handoff 2026-07-02

## Canonical Fixed-Session Baseline

- Model: `xlm-roberta-base`
- Serializer: `current_v1`-style
- Max length: 192
- Epochs: 5
- Learning rate: 2e-5
- Batch size: 16
- Seed: 42
- Class weight power: 0.5
- Label smoothing: 0.02
- Replay: none
- Validation rows: 14,001
- Raw fixed-session Macro-F1: 0.7354
- 2-stage class-bias tuned fixed-session Macro-F1: 0.7389
- Validation logits: not saved in the teammate run
- Artifact: fp16 XLM-R weights, about 556MB

## Weak Classes

- `list_directory`: 0.474
- `read_file`: 0.561
- `grep_search`: 0.596
- `web_search`: 0.610
- `glob_pattern`: 0.626

## Top Confusions

- `grep_search -> read_file`: 564
- `read_file -> list_directory`: 360
- `grep_search -> list_directory`: 283

## Decision

Promote XLM-R 5ep no-replay to the current fixed-session baseline. The earlier XLM-R 3ep public submission remains a public calibration anchor only.

Expected Public from fixed 0.7389 is roughly 0.726-0.728, so this does not yet justify treating the 0.74 Public target as solved.

## Next Experiment

Run `xlm-roberta-base`, `current_v1`, max_length 192, epochs 5, lr 2e-5, batch 16, replay_last1 cap10000 weight 0.5, 2-stage class-bias tuning, and saved validation logits. Compare directly against the no-replay 0.7389 fixed-session baseline.

## Replay Follow-Up

Repo run `20260701_183208_gpu_transformer_session_current_v1_len192_replay-last1_stage2_xlmr_base_len192_ep5_replay_last1_cap1000` finished with:

- Raw fixed-session Macro-F1: 0.739664
- Old bias-tuned Macro-F1: 0.743909
- 2-stage bias-tuned Macro-F1: 0.744625
- Validation logits: `experiments/logits/20260701_183208_gpu_transformer_session_current_v1_len192_replay-last1_stage2_xlmr_base_len192_ep5_replay_last1_cap1000_val_logits.pt`
- Decision: promote this replay candidate to 3-fold session OOF before final refit or submission.

## OOF Follow-Up

3-fold session OOF for the replay candidate finished with:

- Raw OOF Macro-F1: 0.723739
- Old bias-tuned OOF Macro-F1: 0.728371
- 2-stage bias-tuned OOF Macro-F1: 0.728569
- Weakest classes: `list_directory` 0.454, `read_file` 0.566, `grep_search` 0.589, `web_search` 0.600, `lint_or_typecheck` 0.610
- Decision: fixed-session replay was optimistic by about 0.016. Do not final refit or submit yet; next low-cost checks are OOF rule boosts and sparse SVC ensemble on the saved logits.

## Ensemble Follow-Up

OOF rule boosts improved the XLM-R replay aggregate to 0.735673. Adding fold-aware sparse SVC scores and retuning the sparse weight from saved logits found the current best candidate:

- Stack: XLM-R replay logits + OOF rule boosts + sparse SVC scores
- Sparse weight: 4.0
- Combined raw OOF Macro-F1: 0.739852
- Old bias-tuned OOF Macro-F1: 0.741570
- 2-stage tuned OOF Macro-F1: 0.741881
- Artifact: `experiments/artifacts/20260702_oof_sparse_svc_plus_rules_xlmr_base_len192_ep5_replay_last1_cap10000_w4_retune_sparse_weight_retune.json`
- Decision: best OOF result so far and above 0.74, but submission requires implementing and smoke-testing sparse SVC inference packaging plus rule boosts.

Fixed-session cross-check for the same stack is directionally positive:

- Base fixed Macro-F1: 0.744625
- Rule-boosted fixed Macro-F1: 0.747755
- Sparse-only fixed Macro-F1: 0.550580
- Combined fixed Macro-F1 at sparse weight 4.0: 0.751733

Use the OOF 0.741881 score as the primary decision metric; fixed split remains optimistic.

## Final Package

Built `submit.zip` for the XLM-R replay + rules + sparse SVC weight 4.0 stack.

- Final transformer refit: all 70k labeled rows plus replay_last1 cap10000
- Transformer artifact: fp16 XLM-R, OOF transformer class bias, 12 OOF rule boosts
- Sparse artifact: final sparse SVC trained on all labeled rows, sparse weight 4.0, OOF ensemble class bias
- Requirements include `scikit-learn==1.8.0` and `joblib==1.5.3`, matching the sparse artifact training environment and baseline package pins.
- `model/` size: 611MB
- `submit.zip` size: 529MB
- Zip contents: `script.py`, `requirements.txt`, `model/`
- Zip-extracted offline smoke passed with `TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1`
- Output checks passed: columns `id,action`, id order matches sample submission, all labels valid

## Public Result

Submitted `submit.zip` reached reported Public Macro-F1 0.743.

- Target status: met, because Public is above 0.74
- Calibration: OOF 2-stage 0.741881 mapped closely to Public 0.743
- Fixed split remained optimistic: fixed cross-check 0.751733 versus Public 0.743

Decision: keep this package as the achieved submission baseline. Future work should be treated as leaderboard improvement, not target recovery.
