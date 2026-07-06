# Leaderboard Calibration

This file tracks only the relationship between local validation and Public score.
Experiment details live in `experiments/results.csv`.

## Public Anchors

| Submission | Fixed | OOF | Public | Gap | Note |
| --- | ---: | ---: | ---: | --- | --- |
| DistilBERT current serializer, 3ep | `0.710721` | n/a | `~0.698` | about `-0.0127` vs fixed | Fixed and random validation were optimistic. |
| XLM-R 3ep handoff | `0.718793` | n/a | `0.708057` | `-0.010736` vs fixed | Historical anchor only; superseded by XLM-R 5ep. |
| XLM-R 5ep no-replay handoff | `0.738900` tuned | n/a | expected `0.726-0.728` | expected negative vs fixed | Canonical fixed-session baseline before replay. |
| XLM-R replay + OOF rules + sparse SVC w4 | `0.751733` | `0.741881` | `0.743` | `+0.001119` vs OOF, `-0.008733` vs fixed | Current baseline; OOF calibrated well, fixed was optimistic. |

## Current Baseline

- Package: Qwen3-0.6B (decoder, len416 focal) ep3 FULL-DATA refit + 3-fold OOF class bias
  (2-stage) + 12 OOF rule boosts, int8 encoder-only, `requirements_qwen3.txt`
  (transformers>=4.51,<4.52 override) — `m7_qwen3_refit.zip`.
- Main signal: OOF 2-stage `0.758499` -> +12 rules `0.767129` -> Public `0.780`.
- Superseded: XLM-R 5ep replay_last1 cap10000 + OOF rule boosts + sparse SVC weight `4.0`
  (Public `0.743`).
- ⚠ inference 8:50/10:00 — tightest margin to date; no further legroom for an ensemble
  leg on this pack without optimizing further (sorted-batch inference already applied).

## Notes

- The largest recurring errors remain around `grep_search`, `read_file`, `list_directory`, and `glob_pattern`.
- Alternative encoder notes are summarized in `final_summary.md`; full rows stay in `experiments/results.csv`.

## Promotion Rule (2026-07-04: Public-gated)

- Public = final score, 100% — no private holdout (user-confirmed 2026-07-05;
  not stated in rule.md). Chasing Public is chasing the target itself:
  best-of-N instance selection across seed refits is legitimate and directly
  rewarded. Binding constraint: the 10/day slot budget. Recipe judgments
  still belong to OOF (instance draws confound single Public deltas).
- Quick screen: reject bad ideas only.
- Fixed-session full run (`--save-val-model`): sanity, weak-class inspection,
  and submittable weights.
- Public: the decision metric — package, smoke, submit (10/day budget).
  Deltas < 0.002 are noise; one variable per submission.
- OOF: tool for ensemble construction and bias/rule tuning, not a gate.

## Submission Ledger

Every Dacon submission gets one row, at submission time; fill Public when the
score lands.

| Date | Candidate (experiment id / package) | Local fixed | Public | Gap | Note |
| --- | --- | ---: | ---: | ---: | --- |
| 2026-07-02 | baseline: XLM-R replay + rules + sparse w4 (`baseline_0702.zip`) | 0.751733 | 0.743 | -0.008733 | pre-ledger; anchor row repeated from above |
| 2026-07-04 | len448 focal g2.0 encoder-only val-model, seed42 (`m3_len448_s42.zip`, `...m3_focal_len448_ep5_replay_last1_seed42_valmodel`) | 0.748091 | 0.735 | -0.013091 | first Public-gated loop; -0.008 vs baseline Public, but confounded: encoder-only (no rules/sparse), 80% train data (val-model), and a weak instance draw (same-seed rerun of m2 len448 0.755278 — run-to-run noise ±0.007-0.009). seed43 + len192-focal pair will deconfound |
| 2026-07-04 | len448 focal g2.0 encoder-only val-model, seed43 (`m3_len448_s43.zip`, `...m3_focal_len448_ep5_replay_last1_seed43_valmodel`) | 0.735075 | 0.714 | -0.020925 |
| 2026-07-04 | len192 focal g2.0 encoder-only val-model, seed42 (`m3_len192_s42.zip`, `...m3_focal_len192_ep5_replay_last1_seed42_valmodel`) | 0.744716 | 0.725 | -0.019716 | length-pair VERDICT: pure-transfer prediction (no length effect) was ≈0.730; actual 0.725 is -0.005 below the len448 transfer line → len448 holds a real ~+0.005 Public edge at equal fixed quality. Length lane survives. Path to beat baseline 0.743: best-of-N len448 instance (fixed ≥0.755 projects Public ≈0.746) + refit/stack | transfer-coefficient probe RESULT: fixed delta -0.013 → Public delta -0.021 (transfer ≈ 1.6x, co-moving). Fixed DOES rank same-recipe instances; weak draws are genuinely weak models. Gap for this recipe class -0.013 to -0.021 (grows for weaker instances). Instance selection by fixed score is a valid lever |
| 2026-07-04 | leak probe: baseline stack + leak overrides (`leak_probe_0705.zip`) | 0.751733 (stack unchanged) | 0.710 | n/a (overrides invisible to local fixed) | G-lane VERDICT: **-0.033 vs the identical stack's 0.743** with overrides as the only variable → overrides actively wrong on the server. Accuracy algebra c·(p−0.74)≈−0.033 puts server-side override precision ~0.6-0.7 at any plausible coverage, vs 1.000/0.978/0.918 measured on train — the public test does not carry the train dump's cross-row structure. Leak system disabled by default (all tiers gated on the lookup file; `--leak-lookup` to opt in), dropped for good |
| 2026-07-04 | M4: xlm-r-large len448 focal, int8 + 12 val-tuned rules, no sparse (`m4_large448_s42.zip`, `...m4_large_len448_focal_ep5_replay_last1_s42_valmo`) | 0.767660 | 0.741 | -0.026660 |
| 2026-07-04 | M5a: 2-encoder ens (large448+base448 s42 val-models, both int8) + ens-tuned bias/12 rules (`m5a_ens448_s42.zip`) | 0.771038 | pending | | ensemble lever isolated vs M4 (same large leg + same tuning method; adds base leg + ens tuning): ens val 2stage 0.760109 (+0.0034 over large alone) + rules → 0.771038. M4 gap -0.027 applied naively → ~0.744; refit legs come next (M5b) | | LARGE VERDICT: -0.002 vs baseline despite the best fixed to date. Decomposed: val-tuned rules delivered ~+0.002 (val showed +0.011 — ~5x discount, worse than OOF's 2.7x); the large encoder itself sits ~-0.010 BELOW the base-derived transfer line (encoder-only implied ~0.739 vs predicted 0.749) — large's fixed advantage does not transfer, echoing its len192 OOF result (+0.006 fixed → +0.0013 OOF). Large ≠ a Public lever in this family |

| 2026-07-04 | M6: Qwen2.5-0.5B decoder, epoch-3/5 salvage, int8 encoder-only (`m6_qwen05b_ep3.zip`) | 0.774220 | not submitted | | shelved (2 slots left today; the full 5-epoch pack is the stronger probe). DECODER FAMILY PROBE: fixed 2stage +0.0175 over the champion large448 despite 2 fewer epochs (raw 0.747 = band top; weak classes broadly improved, list_directory 0.504). Caveat: 2stage bias gain unusually large (+0.027); encoder-only val-model gap band was -0.013~-0.021 |

| 2026-07-04 | M6: Qwen2.5-0.5B ep3 FULL-DATA refit, int8 encoder-only (`m6_qwen05b_refit.zip`) | n/a (refit unscreenable; ep3 val instances raw 0.7474/0.7509, 2stage 0.7597/0.7742) | **0.770** | +0.010~+0.023 vs ep3 val 2stage | DECODER LINE CONFIRMED: +0.027 over our old baseline in one day, -0.003 vs team best 0.7733, -0.002 vs the cut. Refit lever flipped the gap positive on decoders too (teammate pattern replicated). ⚠ inference 8:31/10:00 — only 1:29 margin: no ensemble legroom until optimized (max_length 416 covers Qwen p100=409; eval batch 64) |
| 2026-07-05 | M7: Qwen3-0.6B ep3 len416 FULL-DATA refit + proper 3-fold OOF bias/12 rules, int8 encoder-only, transformers 4.51 override (`m7_qwen3_refit.zip`) | n/a (refit unscreenable; OOF raw 0.755929 -> 2stage 0.758499 -> +12 rules 0.767129) | **0.780** | +0.013~+0.024 vs OOF tiers | NEW BASELINE, first categorical (0.02+) jump of the competition: +0.010 vs M6 decoder, +0.037 vs the old encoder baseline 0.743. First submission with a genuine OOF-based bias/rule tune (not val-tuned) feeding a refit pack. ⚠ inference 8:50/10:00 — only 1:10 margin (worse than M6's 8:31 despite sorted-batch inference; transformers>=4.51 override installed clean, no submission-slot cost) |
| 2026-07-05 | M8 timing probe A: Qwen3.5-0.8B ep3 screen weights + OOF bias/12 rules, int8, eager fallback, transformers 5.13 override (`m8_ep3_probe.zip`) | n/a (OOF raw 0.766602 -> 2stage 0.767849 -> rules 0.774046) | **timeout 10:00** | n/a | expiring-slot probe, projected ~20min — timed out as projected. PURCHASED INFO: transformers 5.13 installs clean on server torch 2.7.1 (unlocks all future 5.x packs); server speed ≤ ~2.2x of the T4 replica |
| 2026-07-05 | M8 timing probe B: same pack + torch.compile(reduce-overhead) + shipped T4-baked inductor/triton cache, buckets {256,400} b64 (`m8_ep3_cc.zip`) | n/a (same OOF tiers) | **timeout 10:00** | n/a | **2026-07-06 correction:** zip audit found `hf_meta.compile.batch_size=64`/buckets `{256,400}` but embedded Inductor cache guards only for `(128,192)` input shape, likely from maxpack batch128/config-A. Treat the timeout as cache-miss/cold-compile contaminated; do not use it as a valid server-speed upper bound. Eager probe A still proves stock direct fallback is too slow. |
