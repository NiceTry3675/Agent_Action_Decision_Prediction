# Final Summary

## Current Public Baseline

- Public Macro-F1: `0.7891`
- Package: `submissions/kd_m8_refit.zip` (512 MB) — HCX-0.5B student, KD from
  M8 (Qwen3.5-0.8B) alone as teacher. Base training recipe is identical to
  `hcx05b_refit_s42` (see "Previous baseline" below: `current_v1`, len384,
  ep3, lr 2e-5, batch16, grad-accum1, gradient-checkpointing,
  class-weight-power 0.5, label-smoothing 0.02, focal g2.0, replay last1
  cap10000, seed42), with KD from M8 added on top; no rule-boosts layer; KD
  alpha=0.5, temperature=3. Teacher logits are
  `experiments/logits/m8_qwen35_refit_train70k_fp16.pt`/`.npz` — verified
  locally: M8's **full-refit** model's own forward pass over all 70000 train
  rows (fp16), not a 3-fold OOF stitch. **Weights absorbed 2026-07-07**
  (teammate handoff): fp16 original at
  `experiments/incoming/models/kd_m8_refit/`, deployed int8 at
  `experiments/incoming/models/kd_m8_refit_int8/`; verified the deployed int8
  is bit-exactly derived from the fp16 checkpoint (all 219 tensors,
  int8-rowwise-v1 re-quantization match; fp16 sha256 `9e684faa...b1842`).
  Repackaged through this repo's own `package_submission.py`, offline CPU
  smoke passed — this pack is now the repo's directly-reproducible best.
  `class_bias` is all zeros (final refit, never tuned) — deployed as-is; do
  not inject a bias without a separate Public validation.
- Server inference `6:32/10:00`, essentially unchanged from the non-KD HCX
  pack's `6:29` (KD only changes the training loss, not architecture/length).
- +0.0038 vs the non-KD HCX-0.5B baseline (`0.7852`) — above the 0.002 noise
  floor, the largest single jump since the M7 breakthrough (`0.780`).
- Contrasts with the earlier m7+m8+v6-blend KD attempt on HCX, which
  *reversed* on Public (see KD-fold-leak finding below) — single-teacher KD
  from M8 alone transferred positively instead. Structural difference now
  confirmed: this teacher is one full-refit model's train-set predictions,
  not a per-fold OOF ensemble, so there's no cross-fold path for a student's
  held-out val rows to have leaked into the teacher's own training data the
  way the m7+m8+v6 blend had. M8's predictions being near one-hot (memorized
  on its own training rows) still limits the "dark knowledge" per row, but
  the KD signal apparently still helped net.
- **Seed-variance calibration point:** a same-recipe non-KD HCX-0.5B refit
  with only the seed changed (42 -> 777, `hcx05_s777`) scored Public `0.765`
  — `-0.0202` vs the seed42 instance, roughly **2x this repo's previously
  calibrated seed-noise band (±0.007-0.011)**. HCX-0.5B's seed variance on
  this recipe may genuinely be wider than prior lines — see
  `leaderboard_calibration.md` 2026-07-07.

### Previous baseline: HCX-0.5B refit, non-KD (`hcx05b_refit.zip`, `0.7852`)

- Baseline stack: **champion recipe unchanged** (`current_v1` serializer,
  focal g2.0, ep3, replay last1 cap10000, len384) — only the base model
  changed, from `Qwen/Qwen3-0.6B` to `naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B`
  ("HCX-0.5B", 0.5B, smaller). This is a pure base-model swap, not an FE or
  recipe change.
- Main validation signal: fixed-session (seed42, 14001 val rows) raw
  `0.765997` -> old-bias `0.768743` -> 2-stage `0.769796`. No rule-boosts
  layer on this pack. Refit delta to Public: `+0.0154`, consistent with the
  calibration pattern seen on every decoder pack so far.
- Server inference `6:29/10:00` — **-2:26 vs the prior Qwen3-0.6B baseline
  (8:55)**, driven mostly by the base model itself: HCX's own tokenizer
  produces ~11% fewer tokens than Qwen's on the same `current_v1` text at
  len384 (measured mean ~200 vs Qwen's ~226, max 386 at len384 — near-zero
  truncation), and per-token cost is also lower. This opens real
  ensemble/cascade legroom the Qwen3-0.6B line didn't have.
- +0.0045 vs the prior `0.780` Qwen3-0.6B baseline — above the 0.002 noise
  floor, a genuine (if modest) gain from architecture alone.
- Ships with `requirements_qwen3.txt` (`transformers>=4.51,<4.52` override —
  HCX is a Llama-family architecture and loads fine even on the server's
  stock 4.46.3, so the override is harmless rather than required).
- Cross-architecture diversity is real but not yet deployable: a fixed-val
  comparison (same 14001-row split, same champion recipe) found HCX and
  Qwen3-0.6B tied on aggregate but with **complementary per-class
  strengths** — HCX ahead on `plan_task` (+0.037), `read_file` (+0.028),
  `list_directory` (+0.025), `web_search` (+0.010); Qwen ahead on `ask_user`
  (+0.041, HCX recall only 0.56 there) and `lint_or_typecheck` (small). Error
  overlap 82%, mutual-rescue 608/531 rows. A softmax logit blend of the two
  reached fixed 2stage `0.7725` (+0.0027 over the better single model), but a
  2-model package currently exceeds the 10-minute server budget — parked as a
  future ensemble candidate once one leg's inference cost drops further.

**Methodological finding (KD-fold-leak — read before any future KD
attempt):** a matched-seed42 experiment ran KD with HCX-0.5B as the student
against the exported OOF-blend teacher (M7+M8+v6), compared directly to the
non-KD HCX baseline above. Local screen showed KD winning big (`0.7697` ->
`0.7820` raw, `ask_user` +0.075), but the **same-seed Public submission
scored `0.7827` — 0.0025 *below* the non-KD baseline (`0.7852`)**, a clean
matched-pair result (not seed noise). Root cause: the teacher blend's logits
are per-fold **OOF** predictions, but each fold's teacher model was trained
on the other ~2/3 of the data — so a KD student's local validation rows are
never actually held out from the teacher's training data in aggregate, and
the student absorbs some of that leaked signal through the KD target itself.
This inflates local fixed/OOF screens for KD students specifically **without
inflating Public**. This matches and sharpens the caveat already registered
for `kd_m8blend_qwen3_refit.zip` (rules-layer OOF->Public transfer was
already negative there, `-0.005`; see `leaderboard_calibration.md`
2026-07-07). **Rule going forward: do not trust local fixed/OOF screens to
rank KD/stacking recipes — decide only via matched-seed Public submissions.**
The teacher export itself remains useful; only the *promotion method*
changes.

The decoder-family line (Qwen2.5-0.5B -> Qwen3-0.6B -> HCX-0.5B) replaced the
encoder-only XLM-R line in four days: XLM-R replay+rules+sparse `0.743` ->
Qwen2.5-0.5B ep3 refit `0.770` -> Qwen3-0.6B ep3 refit (OOF bias/rules, not
val-tuning) `0.780` -> Qwen3-0.6B **distilled** from an OOF-diversity teacher
blend `0.782` -> HCX-0.5B refit, same champion recipe, base-model swap only
`0.7852` -> **HCX-0.5B distilled from the M8 full-refit teacher**
`0.7891` (current best, `kd_m8_refit.zip`). The non-KD HCX pack and both
Qwen3-0.6B packs remain valid fallbacks (`submissions/hcx05b_refit.zip`,
`submissions/m7_qwen3_refit.zip`, `submissions/kd_m8blend_qwen3_refit.zip`).

## Model Configuration

### Current best: HCX-0.5B KD refit (`kd_m8_refit.zip`, `0.7891`)

- Identical to the `hcx05b_refit` configuration below (base model, serializer,
  length, epochs, regularization, replay) — the only change is the added KD
  term in the training loss:
  `--distill-logits m8_qwen35_refit_train70k_fp16.pt --distill-alpha 0.5
  --distill-temp 3.0` (teacher = M8 Qwen3.5-0.8B full-refit forward pass over
  all 70000 train rows; replay rows keep pure hard-label loss by construction)
- Inference add-ons: none — `class_bias` all zeros (final refit, untuned), no
  rule boosts. This exact configuration scored Public `0.7891` / `6:32`.
- Artifacts: int8-codec HF weights (512 MB package); fp16 original preserved
  at `experiments/incoming/models/kd_m8_refit/` for weight-space work
  (soup/SWA/best-of-N need fp16)

### Prior pack: HCX-0.5B non-KD refit (`hcx05b_refit.zip`, `0.7852`, fallback)

- Base model: `naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B` (Llama-
  family decoder, `AutoModelForSequenceClassification`, same generic pad-token
  patch as the Qwen lines — no HCX-specific code needed)
- Serializer: `current_v1`
- Max length: `384` (near-zero truncation; HCX tokenizer mean 200 tokens on
  this text, -11% vs Qwen3-0.6B's 226)
- Final transformer training: 3 epochs, lr `2e-5`, batch size `16`,
  grad-accum 1, gradient checkpointing
- Regularization: `class_weight_power=0.5`, `label_smoothing=0.02`, focal loss
  gamma `2.0`
- Replay: `last1`, cap `10000`, weight `0.5`
- Inference add-ons: fixed-screen 2-stage class bias only, injected into the
  final-refit meta at packaging time (final-refit runs have no val split of
  their own — same convention as every other pack in this repo); no rule
  boosts layer
- Artifacts: int8-codec HF weights (`model.int8.safetensors`, 512 MB total
  package)
- Train command:
  ```
  train_transformer.py --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B \
    --lr 2e-5 --split session --serializer current_v1 --max-length 384 --epochs 3 \
    --batch-size 16 --grad-accum-steps 1 --gradient-checkpointing \
    --class-weight-power 0.5 --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
    --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
    --tune-bias --keep-threshold 0.0 --seed 42 --save-fp16 [--final-model --final-only]
  ```

### Prior pack: Qwen3-0.6B KD (`kd_m8blend_qwen3_refit.zip`, `0.782`, fallback)

- Base model: `Qwen/Qwen3-0.6B` (decoder, `AutoModelForSequenceClassification`
  via the 2-line pad-token patch)
- Serializer: `current_v1`
- Max length: `416` (zero truncation; Qwen tokenizer p100 = 409 tokens)
- Final transformer training: 3 epochs (decoder peak-epoch finding), lr `2e-5`,
  batch size `16`
- Regularization: `class_weight_power=0.5`, `label_smoothing=0.02`, focal loss
  gamma `2.0`
- Replay: `last1`, cap `10000`, weight `0.5`
- Knowledge distillation: `--distill-logits` teacher = softmax-averaged OOF
  logits from M7 (Qwen3-0.6B, current_v1), M8 (Qwen3.5-0.8B, current_v1), and
  current_v6 (Qwen3-0.6B, compressed serializer) fold models; `--distill-alpha
  0.5 --distill-temp 2.0`; teacher rows matched by id, replay rows excluded by
  construction (mask 0, pure hard-label loss)
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
| Qwen3-0.6B ep3 len416 FULL-DATA refit + 3-fold OOF bias/rules | n/a (refit) | `0.755929` | `0.758499` -> `0.767129` w/ rules | `0.780` | Superseded; kept as fallback pack. |
| Qwen3-0.6B ep3 len416 FULL-DATA refit, KD from OOF-blend teacher (M7+M8+v6) + 3-fold OOF bias/rules | n/a (refit) | `0.782848` | `0.783540` -> `0.786984` w/ rules | `0.782` | Superseded by HCX-0.5B below; kept as fallback pack. Rules-layer transfer was negative this round (OOF rules->Public: -0.005 vs M7's +0.013) — see `leaderboard_calibration.md`. |
| HyperCLOVAX-SEED-0.5B (HCX-0.5B), champion recipe unchanged, base-model swap only, len384 | fixed 2stage `0.769796` | n/a | n/a | `0.7852` | Superseded by `kd_m8_refit` below; kept as the non-KD fallback pack (`hcx05b_refit.zip`). |
| HCX-0.5B + KD from OOF-blend teacher (M7+M8+v6), matched seed42 | fixed screen raw `0.7820` (mildly optimistic, see KD-fold-leak finding above) | n/a | n/a | `0.7827` | **Rejected**: -0.0025 vs non-KD HCX at the same seed, a clean matched-pair result. Do not repeat KD+rules on a same-fold-split teacher without the rule above. |
| `kd_m8_refit` (was `kd_hcx_m8`): HCX-0.5B + KD from M8 (Qwen3.5-0.8B) alone as teacher (`m8_qwen35_refit_train70k_fp16.pt`, full-refit not OOF), base recipe = `hcx05b_refit_s42`, alpha=0.5 temp=3, no rules layer | n/a (refit; matched-recipe seed42 fixed screen raw `0.783852` / 2stage `0.787801` — mildly optimistic, full-refit teacher saw val rows) | n/a | n/a | `0.7891` | **Current baseline**, absorbed 2026-07-07 and repackaged as `submissions/kd_m8_refit.zip`. +0.0039 vs non-KD HCX-0.5B, largest single jump since M7. |

Use OOF, not fixed-session validation, for future finalist promotion. For KD/
stacking recipes specifically, use matched-seed Public submissions, not local
fixed/OOF screens (see KD-fold-leak finding above).

## Package And Smoke

- `kd_m8_refit.zip` (current best): 512 MB
- `model/`: int8 encoder-only, 512 MB weights (HCX-0.5B, KD-trained)
- Fallbacks: `hcx05b_refit.zip` (512 MB, `0.7852`),
  `kd_m8blend_qwen3_refit.zip` (515 MB, `0.782`), `m7_qwen3_refit.zip`
  (539 MB, `0.780`)
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

- **HCX-0.5B is now the base line to build on for future levers**: it slots
  into the existing `current_v1` serializer, bias/rules, and cascade infra
  with just the base-model + tokenizer swap already done here, and its ~11%
  shorter tokenization reopens ensemble/cascade timing room the Qwen3-0.6B
  line didn't have. No rule-boosts layer has been tuned on it yet — an OOF
  rule-boost pass (same pipeline as M7/KD) is a plausible next increment.
- **Do not re-run KD with a same-fold-split OOF teacher and trust the local
  screen** — see the KD-fold-leak finding above. If attempting KD again,
  either build the teacher OOF on a different fold seed/split than the
  student's own OOF, skip rules-retuning on the KD student (bias-only tune),
  or decide purely via matched-seed Public submissions.
- HCX x Qwen3-0.6B logit-blend diversity does **not** survive KD from M8: a
  matched-seed42 held-out screen (2026-07-07) measured the kd_m8_refit student
  x m7 blend at `-0.0027` (w50) vs `+0.0086` for the non-KD HCX x m7 pair —
  KD from a Qwen-family teacher absorbed the cross-family signal. The
  cascade-on-kd-baseline lane is closed; do not revisit without a
  non-Qwen-family second leg showing a fresh blend gain.
- Make any new promotion decision from OOF logits, not fixed split alone.
- Prioritize weak-class gains for `list_directory`, `read_file`, `grep_search`, `web_search`, and `glob_pattern`.
- Keep rule and sparse ensemble changes only if they improve OOF after 2-stage bias tuning.
- Inference margin is no longer as tight as the Qwen3-0.6B line was (8:50-8:55):
  HCX-0.5B runs `6:29/10:00`, leaving ~3:31 — still verify with a timing check
  before adding any second model/ensemble leg to a package.
- Encoder-family re-screen (fair champion-recipe conditions) results:
  - `mdeberta-v3-base` (canonical lr 1e-5): raw `0.702023` / 2stage `0.721257` — closed, no signal.
  - `microsoft/deberta-v3-base` (EN): fixed 2stage `0.746753` — inside the base448 band, ensemble-diversity candidate only.
  - `kakaobank/kf-deberta-base`: prior qv600 signal not yet re-confirmed under champion recipe.
  - `bert-base-multilingual-cased`: possible ensemble-diversity candidate (sub-0.02 lever, not pursued further).

## References

- Experiment index: `experiments/results.csv`
- Detailed metrics and tuning artifacts: `experiments/artifacts/*.json`
- Dacon submission page: `https://dacon.io/competitions/official/236694/mysubmission`
