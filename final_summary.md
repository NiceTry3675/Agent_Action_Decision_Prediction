# Final Summary

## Current Public Baseline

### Team and local Public champion: sieve × conditional-alpha stack (`0.7938`)

- Public Macro-F1: `0.7938` (`submissions/kd_sieve_ca_s42.zip`, reported
  2026-07-11). Server inference `5:58/10:00`.
- Exactly the `kdm8_sieve_s42` recipe below with one added variable:
  `--distill-alpha-weak 0.7` — teacher-matched original rows whose true label
  is Weak4 get KD alpha `0.7`; other matched originals stay at `0.5`,
  replay/unmatched rows stay KD-masked, temperature stays `3.0` (the team
  condalpha semantics, now implemented in this repo's `train_transformer.py`
  with unit test `tests/test_distill_alpha_weak.py`).
- `+0.0021` over the sieve champion `0.7917` at matched seed42 with a single
  variable — clears the `0.002` noise floor, so this is directional evidence
  that conditional alpha adds on top of the consensus sieve (the two levers
  buffer weak-row label noise through different mechanisms: backbone-gradient
  sieve vs loss-mix shift).
- Run-log verification: distill matched 70000/80000 (replay 10k KD-masked),
  `alpha_weak=0.7` on 28,782 Weak4-true originals; sieve histogram identical
  to the champion run (c0 12,776 / c3 48,607), head full gradient.
- Artifacts fully local: fp16 at `experiments/incoming/models/kd_sieve_condalpha_refit/`,
  int8 deploy copy (170/219 tensors, 568.2 MB, argmax agreement **512/512 =
  100%**, best of any pack) at `.../kd_sieve_condalpha_refit_int8/`. GPU and
  CPU offline smokes passed. Full-refit-only: no fixed-val interpretation.

### Previous champion: OOF-consensus gradient sieve (`0.7917`, `kdm8_sieve_s42.zip`)

- Starts from the reproducible `kd_m8_refit` recipe and changes only the
  hard-label backbone gradient by per-row OOF agreement from M7/M8/v6:
  correctness count `c=0/1/2/3` maps to raw scales `0/0.25/0.75/1`, normalized
  to mean `1.0` inside each true class. The classifier head keeps the full
  hard-label gradient, the M8 KD branch is unchanged, and replay stays at
  scale `1.0`.
- `+0.00208` over conditional-alpha KD `0.78962` and `+0.00257` over
  `kd_m8_refit` `0.78913`. This clears the `0.002` measurement-noise floor,
  so the consensus sieve is promoted as directional Public evidence as well
  as the highest observed instance.
- This consensus artifact is explicitly full-refit-only; using it with a
  held-out local validation split is fail-closed because its OOF source models
  overlap such a holdout. The exact local int8 package is 512 MB and passed
  zip-extracted offline smoke.

### Previous team Public champion: conditional-alpha KD (`0.78962`)

- Public Macro-F1: `0.78962` (teammate submission, reported 2026-07-10).
- Same HCX-0.5B student, M8 full-refit teacher, and champion recipe as
  `kd_m8_refit`; the only change is KD alpha `0.5 -> 0.7` on teacher-matched
  original rows whose true label is Weak4 (`list_directory`, `read_file`,
  `grep_search`, `glob_pattern`). Other teacher-matched original rows remain
  at alpha `0.5`, replay/unmatched rows remain excluded from KD, and
  temperature stays `3.0`. Team-side reported options are
  `--distill-alpha 0.5 --distill-alpha-weak 0.7`; the weak override option is
  now implemented locally in `train_transformer.py` and covered by
  `tests/test_distill_alpha_weak.py`.
- `+0.00049` over the exact prior score `0.78913`. This is inside the `0.002`
  measurement-noise band and is now superseded by the sieve × conditional-alpha
  stack.
- Artifact status: **fp16 checkpoint absorbed 2026-07-11** (teammate handoff)
  at `experiments/incoming/models/kd_condalpha_refit/` (219 fp16 tensors,
  HCX-0.5B seq-cls head verified; model.safetensors sha256 `552868fe...9478`);
  available as weight-space (soup/best-of-N) material. The exact submission
  archive remains team-side, but the checkpoint and weak-alpha semantics are
  now both locally reproducible.

### Prior local baseline: M8 KD (`kd_m8_refit.zip`, `0.78913`)

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
  Repackaged through this repo's own `package_submission.py`; offline CPU
  smoke passed. This remains the clean pre-sieve fallback.
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
`0.78913` (`kd_m8_refit.zip`) -> conditional-alpha KD `0.78962` (artifact
absorbed) -> OOF-consensus gradient sieve `0.7917` -> **sieve ×
conditional-alpha `0.7938`** (current team and locally reproducible Public
champion). The
non-KD HCX pack and both Qwen3-0.6B packs remain valid fallbacks
(`submissions/hcx05b_refit.zip`,
`submissions/m7_qwen3_refit.zip`, `submissions/kd_m8blend_qwen3_refit.zip`).

## Model Configuration

### Team and local Public champion: sieve × conditional-alpha HCX-0.5B KD (`0.7938`)

- Package: `submissions/kd_sieve_ca_s42.zip` (512 MB), fp16 source at
  `experiments/incoming/models/kd_sieve_condalpha_refit/`, int8 deployment
  copy at `experiments/incoming/models/kd_sieve_condalpha_refit_int8/`.
- Same configuration as the consensus-sieved pack below, with one added
  training variable: teacher-matched original Weak4 rows use KD alpha `0.7`
  instead of `0.5`. Other matched originals stay at `0.5`, replay/unmatched
  rows remain KD-masked, and temperature stays `3.0`.

### Previous champion: consensus-sieved HCX-0.5B KD (`0.7917`)

- Package: `submissions/kdm8_sieve_s42.zip` (512 MB), fp16 source at
  `experiments/incoming/models/kd_m8_consensus_sieve_refit/`, int8 deployment
  copy at `experiments/incoming/models/kd_m8_consensus_sieve_refit_int8/`.
- Same HCX-0.5B/M8-KD `current_v1`, len384, ep3 recipe as `kd_m8_refit`; only
  the hard-label backbone gradient is consensus-scaled. Head gradient, KD
  logits/loss, replay behavior, zero bias, and no-rules inference path remain
  unchanged.

### Earlier team Public champion: conditional-alpha HCX-0.5B KD (`0.78962`)

- Same configuration as `kd_m8_refit` below, with one training-only override:
  teacher-matched original Weak4 rows use KD alpha `0.7`; other matched
  original rows use `0.5`; replay/unmatched rows remain KD-masked; temperature
  remains `3.0`.
- The fp16 checkpoint is present locally; only the exact teammate submission
  archive remains team-side.

### Prior local baseline: HCX-0.5B KD refit (`kd_m8_refit.zip`, `0.78913`)

- Identical to the `hcx05b_refit` configuration below (base model, serializer,
  length, epochs, regularization, replay) — the only change is the added KD
  term in the training loss:
  `--distill-logits m8_qwen35_refit_train70k_fp16.pt --distill-alpha 0.5
  --distill-temp 3.0` (teacher = M8 Qwen3.5-0.8B full-refit forward pass over
  all 70000 train rows; replay rows keep pure hard-label loss by construction)
- Inference add-ons: none — `class_bias` all zeros (final refit, untuned), no
  rule boosts. This exact configuration scored Public `0.78913` / `6:32`.
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
| `kd_m8_refit` (was `kd_hcx_m8`): HCX-0.5B + KD from M8 (Qwen3.5-0.8B) alone as teacher (`m8_qwen35_refit_train70k_fp16.pt`, full-refit not OOF), base recipe = `hcx05b_refit_s42`, alpha=0.5 temp=3, no rules layer | n/a (refit; matched-recipe seed42 fixed screen raw `0.783852` / 2stage `0.787801` — mildly optimistic, full-refit teacher saw val rows) | n/a | n/a | `0.78913` | Prior team/local baseline, absorbed 2026-07-07 and repackaged as `submissions/kd_m8_refit.zip`. +0.0039 vs non-KD HCX-0.5B. |
| `condalpha-KD`: `kd_m8_refit` recipe, with KD alpha raised `0.5 -> 0.7` only on teacher-matched original Weak4 rows; other matched originals stay at `0.5`, replay/unmatched rows stay KD-masked, temp `3.0` | n/a (teammate submission; fp16 checkpoint absorbed 2026-07-11 at `experiments/incoming/models/kd_condalpha_refit/`) | n/a | n/a | `0.78962` | Previous team Public champion; +0.00049 vs `kd_m8_refit`, inside the 0.002 noise band. |
| `kdm8_sieve_s42`: `kd_m8_refit` recipe + M7/M8/v6 OOF-correctness gradient sieve on the hard-label backbone branch only; full hard-label head, unchanged M8 KD, class-normalized scales, replay unsieved, zero bias/rules | n/a (full-refit-only by construction) | n/a | n/a | `0.7917` | Previous champion. +0.00208 vs `condalpha-KD`, clearing the 0.002 noise floor; exact package locally smoke-tested. |
| `kd_sieve_ca_s42`: `kdm8_sieve_s42` recipe + `--distill-alpha-weak 0.7` as the only variable (matched Weak4-true originals KD alpha 0.7, other matched 0.5, replay/unmatched KD-masked, T3) | n/a (full-refit-only) | n/a | n/a | `0.7938` | **Current team and local Public champion.** +0.0021 vs `kdm8_sieve_s42` at matched seed42, above the 0.002 noise floor — conditional alpha adds on top of the sieve. Runtime 5:58/10:00; int8 argmax fidelity 100% (512/512). |

Use OOF, not fixed-session validation, for future finalist promotion. For KD/
stacking recipes specifically, use matched-seed Public submissions, not local
fixed/OOF screens (see KD-fold-leak finding above).

## Package And Smoke

- Current champion: `kd_sieve_ca_s42.zip` (`0.7938`), 512 MB; GPU and CPU
  zip-extracted offline smokes passed.
- Previous champion: `kdm8_sieve_s42.zip` (`0.7917`), 512 MB; zip-extracted
  offline smoke passed.
- Previous local baseline: `kd_m8_refit.zip` (`0.78913`), 512 MB.
- Conditional-alpha artifact (`0.78962`): fp16 checkpoint is local; the exact
  teammate submission archive remains team-side.
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
