# Final Summary

## Current Public Baseline

- Public Macro-F1: `0.780`
- Package: `submissions/m7_qwen3_refit.zip`
- Model path: `script.py` + `model/` (int8 encoder-only, no sparse leg)
- Baseline stack: Qwen3-0.6B (decoder, `current_v1` serializer, len416, focal g2.0,
  replay last1 cap10000) ep3 FULL-DATA refit + 3-fold OOF class bias (2-stage) +
  12 OOF rule boosts
- Main validation signal: OOF 2-stage Macro-F1 `0.758499` -> +12 rules `0.767129`
- Ships with `requirements_qwen3.txt` (`transformers>=4.51,<4.52` override of the
  server's preinstalled 4.46.3 — installs clean, no submission-slot cost)
- ⚠ Server inference `8:50/10:00` — tightest margin measured to date; no ensemble
  legroom on this pack without further optimization

The decoder-family line (Qwen2.5-0.5B -> Qwen3-0.6B) replaced the encoder-only
XLM-R line in one day: XLM-R replay+rules+sparse `0.743` -> Qwen2.5-0.5B ep3
refit `0.770` -> Qwen3-0.6B ep3 refit (this pack, first submission with a
genuine 3-fold OOF bias/rule tune rather than val-tuning) `0.780`. This is the
first categorical (0.02+) jump of the competition.

## Model Configuration

- Base model: `Qwen/Qwen3-0.6B` (decoder, `AutoModelForSequenceClassification`
  via the 2-line pad-token patch)
- Serializer: `current_v1`
- Max length: `416` (zero truncation; Qwen tokenizer p100 = 409 tokens)
- Final transformer training: 3 epochs (decoder peak-epoch finding), lr `2e-5`,
  batch size `16`
- Regularization: `class_weight_power=0.5`, `label_smoothing=0.02`, focal loss
  gamma `2.0`
- Replay: `last1`, cap `10000`, weight `0.5`
- Inference add-ons: 3-fold OOF class bias (2-stage tuned), 12 OOF-tuned rule
  boosts; no sparse SVC leg (encoder-only package)
- Artifacts: int8-codec HF weights (`model.int8.safetensors`, 598 MB, quantized
  from a 1192 MB fp16 refit checkpoint)

## Scores

| Candidate | Fixed | OOF raw | OOF 2-stage | Public | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| XLM-R 5ep no-replay handoff | `0.738900` tuned | n/a | n/a | expected `0.726-0.728` | Superseded. |
| XLM-R replay + rules + sparse SVC w4 | `0.751733` | `0.739852` | `0.741881` | `0.743` | Superseded encoder-line baseline. |
| Qwen2.5-0.5B ep3 FULL-DATA refit (decoder) | n/a (refit) | n/a | n/a | `0.770` | Superseded; decoder line confirmed. |
| Qwen3-0.6B ep3 len416 FULL-DATA refit + 3-fold OOF bias/rules | n/a (refit) | `0.755929` | `0.758499` -> `0.767129` w/ rules | `0.780` | **Current baseline.** |

Use OOF, not fixed-session validation, for future finalist promotion.

## Package And Smoke

- `m7_qwen3_refit.zip`: 539 MB
- `model/`: int8 encoder-only, 598 MB weights
- Archive root: `script.py`, `requirements.txt`, `model/`
- Offline zip-extracted smoke passed with `TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1`
  under a `transformers>=4.51,<4.52` PYTHONPATH overlay (matches the shipped
  `requirements_qwen3.txt`).
- CPU-only smoke passed with `CUDA_VISIBLE_DEVICES=''`.
- Output file: `output/submission.csv`
- Output columns: exactly `id,action`
- Output row count and id order match `sample_submission.csv`.
- All predictions are in the 14 valid labels.

## Next Improvement Candidates

- Make any new promotion decision from OOF logits, not fixed split alone.
- Prioritize weak-class gains for `list_directory`, `read_file`, `grep_search`, `web_search`, and `glob_pattern`.
- Keep rule and sparse ensemble changes only if they improve OOF after 2-stage bias tuning.
- Inference margin (8:50/10:00) is now the binding constraint on this line — any
  further encoder/ensemble addition needs a timing check before packaging.
- Encoder-family re-screen (fair champion-recipe conditions) results:
  - `mdeberta-v3-base` (canonical lr 1e-5): raw `0.702023` / 2stage `0.721257` — closed, no signal.
  - `microsoft/deberta-v3-base` (EN): fixed 2stage `0.746753` — inside the base448 band, ensemble-diversity candidate only.
  - `kakaobank/kf-deberta-base`: prior qv600 signal not yet re-confirmed under champion recipe.
  - `bert-base-multilingual-cased`: possible ensemble-diversity candidate (sub-0.02 lever, not pursued further).

## References

- Experiment index: `experiments/results.csv`
- Detailed metrics and tuning artifacts: `experiments/artifacts/*.json`
- Dacon submission page: `https://dacon.io/competitions/official/236694/mysubmission`
