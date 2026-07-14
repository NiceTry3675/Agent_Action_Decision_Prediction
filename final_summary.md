# Final Summary

## Current Public Baseline

### Team Public champion: gated 3-seed ensemble + rfinal rules (`0.7963584846`)

- Public Macro-F1: `0.7963584846` (`kd_ens3_trio_rfinal`, teammate result
  reported 2026-07-14), runtime `7:28/10:00`; rank 7 when reported. This is
  seed202 INT8 main + seed909 INT4 + seed7070 INT4, with the extra members
  restricted to the seed202 low-margin (`<1.0`) surface and row-wise centered
  logits averaged. The final archive is reported at about `959 MiB`.
- Current standing snapshot: 1st place is `0.79862`; the team is 7th at the
  displayed `0.79635` (exact score above), a gap of `0.0022615154`. The gap is
  slightly larger than the repo's `0.002` interpretation band, so another
  micro-rule-sized increment alone is unlikely to reach Public 1st.
- User-confirmed competition constraint: there are no additional model
  submissions in the final round. The active research objective is therefore
  to find a Public-winning breakthrough before the submitted artifact is
  frozen. Dacon automatically retains the highest score across submissions, so
  a later lower-scoring experiment does not replace `rfinal` or reduce the
  standing. Keep rfinal unchanged as a reproducible comparison artifact, not
  as a score-protection or last-submission requirement. This is a search
  direction, not a claim that a qualifying lever has already been found.
- `rfinal` applies ten immutable-base rule behaviors after the ensemble:
  low-budget R1/R1b; low-budget ask-user logit boost, constrained
  `glob_pattern` rerouting, recent-read `grep_search -> read_file`, and
  `plan_task -> ask_user`; failed-CI execution-to-patch; clean-SIM
  `apply_patch -> edit_file`; a result-count read rule; and a narrow
  turn/history garnish. Every mask is computed from the original ensemble
  prediction, so rule outputs never cascade into later masks.
- The rule stack passed the reported five-fold dual-seed diagnostic
  (three seed42 folds plus two surviving seed202 folds): stack deltas were
  positive in all folds (`+0.00154` to `+0.00289`) with zero rule collisions.
  This is strong consolidation evidence, but not exact trio OOF: seed909 and
  seed7070 were full-refit-only members and no aligned OOF for them was
  reported.
- Public improved `+0.0008984846` over the R1+R1b champion, `+0.0020984846`
  over the no-rule ens2 base, and `+0.0024768420` over the exact locally
  reproducible champion `0.7938816426`. The full rule line therefore clears
  the `0.002` interpretation band relative to the no-rule ensemble, although
  it combines multiple validated rules and a third member rather than
  isolating one causal lever.
- A score-preserving speed build tied exactly at `0.7963584846` but ran in
  `7:37`; SDPA, member preload, and threaded overlap were all within server
  timing variance. The unmodified eager `rfinal` (`7:28`) remains the current
  champion reference. A separate chain/leak probe also tied exactly,
  confirming no usable train-test session overlap.
- Artifact boundary: the exact ens2 R1+R1b predecessor archive and two s202
  OOF folds were handed off in Slack, but the final trio archive, seed7070
  component, exact ten-rule patch scripts, and training commands are not in
  this repo. `kd_sieve_ca_s42.zip` remains the exact local fallback.

### Previous team Public champion: gated 2-seed ensemble + budget R1 + R1b (`0.79546`)

- Public Macro-F1: `0.79546` (`kd_ens2_s202s909_r1b`, teammate result reported
  2026-07-13). Despite the suffix, this pack contains **both R1 and R1b** on
  top of the ens2 base. Its exact archive was handed off in Slack on
  2026-07-14 (reported 871 MB stored / 723 MB deflated); server runtime was
  not reported.
- R1b is the second low-budget policy rule:
  `budget_tokens_remaining < 5000 AND pred == apply_patch -> edit_file`.
  It covers 37 train rows at precision `0.757`. It shares R1's mechanism: the
  generator changes policy as budget is exhausted, while the model cannot
  reliably learn the numeric threshold because of digit tokenization.
- R1 (`web_search -> ask_user`) and R1b (`apply_patch -> edit_file`) have
  disjoint source predictions, so they cannot collide. Both are evaluated in
  the post-ensemble rule block immediately after ensemble-logit averaging and
  finalizing `pred_ids`.
- Public improved `+0.00012` over the R1 pack, `+0.00120` over the no-rule
  ensemble, and `+0.0015783574` over the exact local champion `0.7938816426`.
  The new increment is far inside the repo's `0.002` interpretation band and
  had no reported held-out/OOF delta at submission time. A 2026-07-14 recovery
  of the 70k champion-recipe diagnostic OOF now reproduces R1b on all folds
  (`+0.000312/+0.000197/+0.000125`; 28 rescues/8 harms; pooled `+0.000212`).
  That surface is level-2 KD/consensus-contaminated, so R1b remains promoted
  only as the highest final-score instance, not standalone causal evidence.
- Weak-boundary evidence now covers `web_search -> ask_user` and
  `apply_patch -> edit_file`. No new F1 values were shared for the required
  audit set (`list_directory`, `read_file`, `grep_search`, `glob_pattern`,
  `web_search`, `lint_or_typecheck`, or `run_tests` vs `run_bash`), so none
  are inferred.
- Artifact status: the exact R1b archive is now available as a Slack
  attachment and contains the submitted script, seed202 INT8 and seed909 INT4
  models, and their metadata. Two s202 OOF folds were also handed off; s909
  has no OOF. None has been absorbed into this repo, the exact training
  commands remain unreported, and the local generic cascade/rule engine is
  still not an exact reproduction.

### Earlier team champion: gated 2-seed ensemble + budget R1 (`0.79534`)

- `kd_ens2_s202s909_r1.zip` adds
  `budget_tokens_remaining < 5000 AND pred == web_search -> ask_user` after
  ensemble `pred_ids` finalization. It changes about 19 hidden-test rows.
- R1 was derived on the seed42 champion OOF surface, where all three folds
  were positive (`+0.00104/+0.00096/+0.00055`, mean about `+0.00085`,
  precision `0.67`). Deployment on ens2 reached Public `0.79534` (`+0.00108`)
  in `7:27/10:00`.
- The team reported a 723 MB package with build, offline smoke, and flip
  verification complete. The exact archive and code remain team-side.

### Earlier team champion: gated 2-seed ensemble (`0.79426`)

- `kd_ens2_s202s909.zip`: champion-recipe seed202 INT8 main model + seed909
  INT4 secondary model. The secondary forward runs only on rows where the
  main-model `margin < 1.0` (about 25%), then the two logits are averaged; a
  pre-secondary `430s` time guard falls back to the main model when needed.
- Public Macro-F1 `0.79426`, runtime `7:28/10:00`, immediately superseded by
  R1. This is `+0.0003783574` over the exact seed42 local champion and about
  `+0.00049` over the documented standalone seed202 result `0.79377`.
- The team reported OOF `+0.00183`, a 757 MB pre-submission package, and
  successful timing/smoke checks. The exact seed909 artifact, ensemble code,
  blend metadata, and package are team-side only.

### Previous team and current locally reproducible champion: sieve × conditional-alpha (`0.7938816426`)

- Public Macro-F1: `0.7938816426` (`submissions/kd_sieve_ca_s42.zip`, reported
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
absorbed) -> OOF-consensus gradient sieve `0.7917` -> sieve ×
conditional-alpha `0.7938816426` (current locally reproducible champion) ->
gated seed202+seed909 ensemble `0.79426` -> budget-R1 ensemble `0.79534` ->
budget-R1+R1b ensemble `0.79546` -> **gated seed202+seed909+seed7070 trio +
ten-rule rfinal `0.7963584846`** (current team Public champion). The
non-KD HCX pack and both Qwen3-0.6B packs remain valid fallbacks
(`submissions/hcx05b_refit.zip`,
`submissions/m7_qwen3_refit.zip`, `submissions/kd_m8blend_qwen3_refit.zip`).

## Model Configuration

### Team Public champion: gated seed202+seed909+seed7070 trio + rfinal (`0.7963584846`)

- Team-side `kd_ens3_trio_rfinal`: seed202 INT8 main, seed909 INT4 and
  seed7070 INT4 auxiliaries, low-margin routing at seed202 raw-logit margin
  `<1.0`, row-wise centered-logit averaging, and the ten immutable-base rule
  behaviors summarized above. Reported runtime is `7:28/10:00`; reported
  archive size is about `959 MiB`.
- The exact final trio archive/config is not local. Do not infer the third
  checkpoint or its command from other team-side seed artifacts.

### Previous team champion: gated 2-seed ensemble + budget R1 + R1b (`0.79546`)

- Team-side `kd_ens2_s202s909_r1b` uses the ensemble configuration below and
  includes both low-budget overrides after ensemble `pred_ids` finalization:
  R1 maps `web_search -> ask_user`; R1b maps `apply_patch -> edit_file`.
  Their source predictions are disjoint, so rule order cannot create a
  collision. R1b's train audit is 37 rows at precision `0.757`.
- Package size, runtime, and smoke status were not reported for R1b.
- The exact implementation and both deployed seed artifacts are not local;
  no training command should be inferred by substituting seed values into the
  seed42 command.

### Previous team champion: gated seed202+seed909 ensemble + R1 (`0.79534`)

- Team-side `kd_ens2_s202s909_r1.zip` adds the R1
  `web_search -> ask_user` low-budget override to the ens2 pack. Reported
  package size/runtime are 723 MB and `7:27/10:00`; build, smoke, and flip
  verification were reported complete.

### Previous team champion: gated seed202+seed909 ensemble (`0.79426`)

- Team-side package `kd_ens2_s202s909.zip`: seed202 INT8 main model, seed909
  INT4 secondary model, secondary inference on rows with `margin < 1.0`
  (about 25%), logit averaging, and a `430s` pre-secondary time guard.
  Reported package size/runtime are 757 MB and `7:28/10:00`.
- The local generic cascade is only analogous: its blend space and rule/time
  behavior differ, so it is not a drop-in reproduction of this package.

### Previous team and current local champion: sieve × conditional-alpha HCX-0.5B KD (`0.7938816426`)

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
| `kd_sieve_ca_s42`: `kdm8_sieve_s42` recipe + `--distill-alpha-weak 0.7` as the only variable (matched Weak4-true originals KD alpha 0.7, other matched 0.5, replay/unmatched KD-masked, T3) | n/a (full-refit-only) | n/a | n/a | `0.7938816426` | **Current locally reproducible champion; previous team champion.** +0.0021 vs `kdm8_sieve_s42` at matched seed42, above the 0.002 noise floor — conditional alpha adds on top of the sieve. Runtime 5:58/10:00; int8 argmax fidelity 100% (512/512). |
| `kd_ens2_s202s909`: seed202 INT8 main + seed909 INT4 secondary; secondary runs where main-model `margin < 1.0` (about 25%), logits averaged, `430s` pre-secondary fallback guard | n/a (team-side seed refits) | team-reported gated ensemble `+0.00183` | n/a | `0.79426` | **Same-day intermediate team champion.** `+0.0003783574` vs the exact seed42 local champion and about `+0.00049` vs standalone seed202 `0.79377`; runtime 7:28. Public increments are inside the 0.002 band, so promote the instance without a broad causal claim. |
| `kd_ens2_s202s909_r1`: preceding ensemble + `budget_tokens_remaining < 5000 AND pred == web_search -> ask_user` immediately after ensemble `pred_ids` finalization | n/a (team-side inference rule) | team-reported seed42 rule OOF `+0.00085`; folds `+0.00104/+0.00096/+0.00055`, precision `0.67` | n/a | `0.79534` | **Previous team Public champion.** `+0.00108` vs the no-R1 ensemble and `+0.0014583574` vs the exact seed42 local champion; runtime 7:27. All-positive seed42 OOF support; superseded by R1+R1b. |
| `kd_ens2_s202s909_r1b`: preceding R1 pack plus `budget_tokens_remaining < 5000 AND pred == apply_patch -> edit_file`; contains both R1 and R1b, whose source predictions are disjoint | n/a (team-side inference rule) | recovered champion-recipe diagnostic OOF `+0.000312/+0.000197/+0.000125`, pooled `+0.000212`; train audit: 37 rows, precision `0.757` | n/a | `0.79546` | **Previous team Public champion.** `+0.00012` vs R1, `+0.00120` vs ens2, and `+0.0015783574` vs the exact local champion. The recovered OOF is not the exact team ensemble surface; superseded by rfinal. |
| `kd_ens3_trio_rfinal`: seed202 INT8 + seed909/seed7070 INT4 low-margin centered-logit trio, plus ten immutable-base rule behaviors | n/a (team-side full-refit members) | five-fold dual-seed rule diagnostic `+0.00154` to `+0.00289`, all positive, zero collisions; not exact trio OOF | n/a | `0.7963584846` | **Current team Public champion.** Runtime `7:28`; about 959 MiB. `+0.0008984846` vs R1+R1b, `+0.0020984846` vs ens2, and `+0.0024768420` vs the exact local champion. The unmodified rfinal is selected after a score-identical speed build ran slower (`7:37`). |

Use OOF, not fixed-session validation, for future finalist promotion. For KD/
stacking recipes specifically, use matched-seed Public submissions, not local
fixed/OOF screens (see KD-fold-leak finding above).

## Package And Smoke

- Current team champion: team-side `kd_ens3_trio_rfinal`
  (`0.7963584846`), reported about 959 MiB / `7:28`. Exact final archive and
  scripts are not local; the score-identical speed variant is not selected.
- Previous team champion: `kd_ens2_s202s909_r1b` (`0.79546`). Its exact
  831 MB Slack attachment (reported 871 MB stored / 723 MB deflated) and two
  s202 OOF folds were handed off team-side, but have not been absorbed into
  this repo.
- Earlier team champion: team-side `kd_ens2_s202s909_r1.zip`
  (`0.79534`), reported 723 MB / `7:27`; build, offline smoke, and flip
  verification were reported complete.
- Earlier team champion: team-side `kd_ens2_s202s909.zip`
  (`0.79426`), reported 757 MB / `7:28`; exact archive is not local.
- Current locally reproducible champion: `kd_sieve_ca_s42.zip`
  (`0.7938816426`), 512 MB; GPU and CPU zip-extracted offline smokes passed.
- Previous champion: `kdm8_sieve_s42.zip` (`0.7917`), 512 MB; zip-extracted
  offline smoke passed.
- Previous local baseline: `kd_m8_refit.zip` (`0.78913`), 512 MB.
- Conditional-alpha artifact (`0.78962`): fp16 checkpoint is local; the exact
  teammate submission archive remains team-side.
- Fallbacks: `hcx05b_refit.zip` (512 MB, `0.7852`),
  `kd_m8blend_qwen3_refit.zip` (515 MB, `0.782`), `m7_qwen3_refit.zip`
  (539 MB, `0.780`)
- Locally held archive root: `script.py`, `requirements.txt`, `model/`
- Local zip-extracted offline smoke passed with
  `TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1`
  under a `transformers>=4.51,<4.52` PYTHONPATH overlay (matches the shipped
  `requirements_qwen3.txt`).
- Local CPU-only smoke passed with `CUDA_VISIBLE_DEVICES=''`.
- Local smoke output file: `output/submission.csv`
- Local smoke output columns: exactly `id,action`
- Local smoke row count and id order match `sample_submission.csv`.
- All local smoke predictions are in the 14 valid labels.

## Next Improvement Candidates

- **Primary objective: close the `0.0022615154` gap to Public 1st.** Treat
  additional sub-`0.002` rule garnish as consolidation only. Prioritize a new,
  orthogonal mechanism with plausible standalone scale above the gap. Keep an
  unchanged rfinal package for comparison, but submit lower-scoring probes when
  they buy useful information: the leaderboard retains the highest score and
  imposes no score penalty for a worse later submission. Only slots and time
  are consumed.
- **Use A100 compute aggressively.** With about 15 hours remaining and a full
  A100 training run taking about 1.5 hours, GPU cost is not a reason to defer a
  credible breakthrough hypothesis. Favor genuinely orthogonal experiments
  that can finish, package, and smoke within the remaining wall clock; do not
  spend the window polishing already exhausted axes.
- **Endgame team base is `kd_ens3_trio_rfinal`**. Rule, leak, transductive,
  speed-path, TTA, relabeling, and architecture-edit lanes are closed by
  today's evidence. Locally, `kd_sieve_ca_s42.zip` remains the reproducible
  fallback.
- **Do not re-run KD with a same-fold-split OOF teacher and trust the local
  screen** — see the KD-fold-leak finding above. If attempting KD again,
  either build the teacher OOF on a different fold seed/split than the
  student's own OOF, skip rules-retuning on the KD student (bias-only tune),
  or decide purely via matched-seed Public submissions.
- HCX x Qwen3-0.6B logit-blend diversity does **not** survive KD from M8: a
  matched-seed42 held-out screen (2026-07-07) measured the kd_m8_refit student
  x m7 blend at `-0.0027` (w50) vs `+0.0086` for the non-KD HCX x m7 pair —
  KD from a Qwen-family teacher absorbed the cross-family signal. The
  cross-architecture cascade-on-KD lane remains closed. This does not apply to
  the now-validated same-recipe seed-diversity ensemble axis.
- Make any new promotion decision from OOF logits, not fixed split alone.
- Prioritize weak-class gains for `list_directory`, `read_file`, `grep_search`, `web_search`, and `glob_pattern`.
- Keep rule and sparse ensemble changes only if they improve OOF after 2-stage bias tuning.
- The current champion package runtime is `7:28/10:00`. Three attempted speed
  optimizations were timing-neutral; keep the eager rfinal artifact as the
  comparison reference while breakthrough candidates are evaluated separately.
- Encoder-family re-screen (fair champion-recipe conditions) results:
  - `mdeberta-v3-base` (canonical lr 1e-5): raw `0.702023` / 2stage `0.721257` — closed, no signal.
  - `microsoft/deberta-v3-base` (EN): fixed 2stage `0.746753` — inside the base448 band, ensemble-diversity candidate only.
  - `kakaobank/kf-deberta-base`: prior qv600 signal not yet re-confirmed under champion recipe.
  - `bert-base-multilingual-cased`: possible ensemble-diversity candidate (sub-0.02 lever, not pursued further).

## References

- Experiment index: `experiments/results.csv`
- Detailed metrics and tuning artifacts: `experiments/artifacts/*.json`
- Dacon submission page: `https://dacon.io/competitions/official/236694/mysubmission`
