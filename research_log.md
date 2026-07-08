# Research Decision Log

This file is a short decision index, not the full experiment notebook.

Full historical log through 2026-07-06 is archived at
`archive/research_log_20260702_20260706.md`.

## Sources Of Truth

- Current submitted baseline: `final_summary.md`.
- Public calibration and submission ledger: `leaderboard_calibration.md`.
- Experiment rows and exact commands: `experiments/results.csv`.
- Detailed metrics, rules, bias vectors, and probe artifacts:
  `experiments/artifacts/*.json`.
- Active GPU handoff as of 2026-07-06: `handoff_20260706_gpu_plan.md`.
- The old planning directory was removed after the Qwen3/M8 pivot because it
  described the superseded XLM-R/length milestone plan.

## Logging Rules

- Keep this file short: decisions only, no confusion tables or per-class dumps.
- Do not restate the current baseline score here; link to `final_summary.md`
  instead, so the score cannot drift.
- New detailed experiment numbers go to `experiments/results.csv` and artifacts.
- Add only durable decisions or reversals here.

## Compressed History

- 2026-07-02: XLM-R replay + OOF rules + sparse SVC reached Public `0.743` and
  became the first working baseline. OOF calibrated better than fixed-session.
- 2026-07-03: Early serializer/length variants and alternative encoders mostly
  failed or became ensemble-only candidates. Colab command-channel reliability was
  hardened after runtime reclaim issues.
- 2026-07-04: Public-gated workflow replaced the earlier OOF gate for submission
  promotion; leak overrides were probed and rejected hard on Public (`0.710`).
  Encoder-family gains were too small/noisy to carry the target alone.
- 2026-07-05: Decoder models became the real break: Qwen2.5-0.5B refit reached
  Public `0.770`, then Qwen3-0.6B refit with OOF bias/rules reached Public
  `0.780`. This is the current baseline line.
- 2026-07-05: Qwen3.5-0.8B showed real OOF quality (`0.774046` after rules), but
  direct deployment timed out on the server. Work shifted from model-quality
  proof to inference-volume reduction.

## Recent Decisions

### 2026-07-06 - M8 cascade quality is valid, but timing needs volume reduction

- Evidence: M7 0.6B and M8 0.8B OOF logits over the same 70k rows show
  confidence routing works. Routing 15% of rows captures roughly half of the
  0.8B edge; routing 50% slightly beats 0.8B alone.
- Decision: Keep cascade as the main M8 deployment shape: 0.6B on all rows, 0.8B
  only on low-confidence rows, then tune bias/rules on blended OOF logits.
- Constraint: with current_v1 and fp16 DeltaNet, r=15% still projects around
  `733s`, requiring server speedup `s >= 1.22`.
- Artifact: `experiments/artifacts/20260706_cascade_routing_sim.json`.

### 2026-07-06 - current_v5 is rejected as a quality replacement

- Evidence: Qwen3-0.6B current_v5 OOF landed below current_v1 at every tier:
  raw `0.751161` vs `0.755929`, 2-stage `0.755129` vs `0.758499`, rules
  `0.761686` vs `0.767129`.
- Decision: Do not replace current_v1 with current_v5 for quality.
- Follow-up: v5's token savings remain useful only if a minimal v6 recovers the
  loss without giving up most of the speed benefit.
- Artifacts: `experiments/artifacts/v5_qwen3_06b_oof_len384_ep3_oof_metrics.json`,
  `experiments/artifacts/v5_qwen3_06b_oof_len384_ep3_rules_rule_boosts.json`.

### 2026-07-06 - xmeta rule recovery failed

- Evidence: Adding v5-dropped meta fields back into the rule vocabulary recovered
  only `+0.0002` on v5 rules and did not help v1.
- Decision: The v5 loss is not recoverable by coarse rule flags for
  tier/language preference/budget/elapsed/loc. Treat the likely culprits as turn
  exactness, language dominance representation, or transformer-internal
  interactions.
- Artifacts:
  `experiments/artifacts/v5_qwen3_06b_oof_rules_xmeta_rule_boosts.json`,
  `experiments/artifacts/m7_qwen3_oof_rules_xmeta_rule_boosts.json`.

### 2026-07-06 - fp16 DeltaNet fallback is adopted for the 0.8B cascade leg

- Evidence: `fp16_state` matched stock argmax on 99.976% of 4096 rows and sped the
  trained 0.8B eager fallback by about 1.21x, reducing the ratio from 2.43 to
  2.01.
- Decision: Use fp16 DeltaNet in the 0.8B leg, gated by package-level smoke. The
  implementation must rebind each layer instance's `chunk_gated_delta_rule`;
  module-global monkeypatching after model load does not execute.
- Artifact: `experiments/artifacts/m8_fp16_deltanet_probe.json`.

### 2026-07-06 - current_v6 minimum spec is fixed

- Evidence: Structural rule probes over turn_x/trigram/result/candidate/path/
  overlap/open_n were effectively null: `0.767109` vs baseline rules `0.767129`.
- Decision: Do not add a struct line to v6. current_v6 is only current_v5 plus
  exact-turn/bin pairing and language dominance marker.
- Implementation state: `script.py` and `train_transformer.py` now register
  `current_v6`.
- Spec: `fe_current_v6_spec.md`.
- Artifact: `experiments/artifacts/m7_qwen3_oof_rules_struct_rule_boosts.json`.

### 2026-07-06 - depth-cut is rejected

- Evidence: M8 layer-saturation probing showed no early plateau. K=16 retained
  94.9% of final-layer probe F1; K=20 retained 96.9%, implying too much quality
  loss for the timing saved.
- Decision: Do not pursue depth-cut/refit as an M8 deployment lever.
- Remaining path: token reduction via v6, row reduction via cascade, fp16 DeltaNet
  for the routed 0.8B rows.
- Artifact: `experiments/artifacts/m8_depthcut_layer_probe.json`.

### 2026-07-06 - M8 compile-cache timeout probe was shape-contaminated

- Evidence: `submissions/m8_ep3_cc.zip` ships `hf_meta.compile.batch_size=64`
  with buckets `{256,400}`, but its embedded Inductor cache contains generated
  input-shape guards for `(128,192)` and no `(64,...)` input cache. The cache
  appears to come from the maxpack batch-128/config-A path rather than the
  submitted batch-64 path.
- Decision: Do not use the `m8_ep3_cc.zip` timeout as strong evidence that a
  correctly baked compile-cache path cannot run. The eager timeout still proves
  direct stock fallback is too slow, but the compile-cache submission likely
  cold-compiled or cache-missed on the server.
- Follow-up: Any future compile-cache package must bake and verify cache shapes
  against the exact `hf_meta.compile` batch size and buckets before submission.

### 2026-07-06 - M8 C-lane timing closes the all-row 0.8B path

- Evidence: C-lane exact b64/{256,400} cache guard rebuilt the intended cache on
  trained weights and fp16 DeltaNet matched stock on the correctness slice
  (`argmax_agreement=1.0`, max diff `0.0088`, 18 rebound layers). The cache no
  longer showed batch-128 contamination, but `torch.compile` still opened long
  CPU-bound compile work during measure/warm-cache execution, so Colab/Public CPU
  differences aside, the current compile-cache recipe is not package-ready.
- Evidence: no-compile eager fp16 DeltaNet timing on the same C-lane T4:
  Qwen3 denominator `74.98s/4096`; M8 `current_v6:352` `109.61s/4096`
  (ratio `1.462`, projected `699s` on the M7 infer anchor), `current_v6:384`
  `733.6s` projected, `current_v6:400` `733.4s`, and `current_v1:400`
  `914.8s`.
- Decision: 0.8B all-row standalone is still over the 10-minute target even with
  fp16 DeltaNet and `current_v6:352`. Use M8 only as a routed/cascade leg unless
  a new non-compile kernel lever appears; `current_v6:352` permits roughly
  `<=86%` of rows through M8 under a 600s infer budget.
- Artifacts:
  `experiments/artifacts/20260706_c_m8_b64_cache_guard_measure.json`,
  `experiments/artifacts/20260706_c_m8_fp16_eager_v6_len.json`.

### 2026-07-06 - inference loader fixed for int8 decoder packages

- Evidence: `script.py` reconstructed int8-codec weights as fp32, loaded a fp32
  model, moved it to GPU, and only then converted to half in the caller. Decoder
  configs also kept `use_cache=True` in saved config, relying on library-version
  defaults during sequence-classification inference.
- Decision: `script.py` now restores int8 weights directly to fp16 on CUDA,
  halves the model before loading, moves only the half model to GPU, and
  explicitly disables decoder cache on model configs. Local offline smoke passed
  on the current package.

### 2026-07-06 - FLA Triton autotune workaround probe is ready

- Context: The strongest FLA repro reached the fast path but failed inside
  Triton's autotuner while compiling Gated DeltaNet KKT/solve on T4. Earlier
  causal-conv install failures were not decisive; the failure to close is the
  T4 Triton compile path itself.
- Prepared probe: `colab/m8_fla_autotune_workaround_probe.py` tests two
  workarounds in isolated subprocesses: FLA 0.5.1 `FLA_CACHE_MODE=default`
  config files to skip autotune, and runtime Autotuner narrowing to one config
  per relevant kernel. First gate is one-batch correctness/survival; only then
  run full timing.
- Result: C-lane T4 smoke showed `fastpath_active=True`, but FLA 0.5.1 failed
  all tested KKT/solve configs (`BK` 32/64 x warps 1/2/4) under both
  config-cache and Autotuner-narrowed paths. FLA 0.5.0 also failed all six
  narrowed KKT/solve configs. Every failure was the same Triton
  `PassManager::run failed` at `chunk_gated_delta_rule_fwd_kkt_solve_kernel`.
- Decision: Treat current FLA fast path as closed for the server-matched
  T4/torch2.7.1/triton3.3.1 stack. A real fix would require rewriting or
  replacing the KTT/solve Triton kernel, not just picking a safer autotune
  config.
- Artifacts:
  `experiments/artifacts/20260706_c_m8_fla_autotune_smoke.json`,
  `experiments/artifacts/20260706_c_m8_fla_kkt_grid.json`,
  `experiments/artifacts/20260706_c_m8_fla050_kkt_grid.json`.

### 2026-07-06 - causal-conv1d-only is not a speed lever

- Evidence: C-lane T4 A/B installed only `causal_conv1d 1.6.2.post1` after a
  no-causal baseline, with FLA absent in both variants. The causal variant
  activated `causal_conv1d_fn` in all 18 DeltaNet layers and preserved logits on
  the correctness slice (`argmax_agreement=1.0`, max diff `0.0156`).
- Result: same-runtime `current_v6:352` timing over 4096 rows was no-causal
  `112.36s` infer / `114.69s` tokenize+infer versus causal-only `112.47s`
  infer / `114.53s` tokenize+infer. Infer was `0.1%` slower; tokenize+infer was
  `0.14%` faster due to tokenization noise. The projected all-row M8 time stays
  about `698s`, far above the 600s target.
- Decision: causal-conv1d is usable but not useful for this sequence-classifier
  prefill workload. Close it as a deployment lever; the real bottleneck remains
  DeltaNet fallback scan/recurrence, not the short depthwise conv.
- Artifacts:
  `experiments/artifacts/20260706_c_m8_causal_only_ab.json`,
  `experiments/artifacts/20260706_c_m8_causal_only_ab_no_causal.json`,
  `experiments/artifacts/20260706_c_m8_causal_only_ab_causal_only.json`.

### 2026-07-06 - current_v6e is the final serializer-compression probe

- Evidence: current_v6's fixed screen narrowly beat v5 at the aggregate but
  shifted too many ambiguous `read_file` rows into `list_directory`/`grep_search`.
- Decision: Test one guarded variant before closing the serializer-compression
  lane: current_v5 + exact turn + `path/overlap/cand` explorer evidence, while
  reverting v6's language-dominance marker back to v5 top-2 language names.
- Spec: `fe_current_v6e_spec.md`.
- Gate: Qwen3-0.6B fixed screen at `max_length=400`; only OOF if read/list/ask
  class behavior recovers enough to justify a full fold set.

### 2026-07-06 - current_v6 OOF is not a v1 replacement

- Evidence: current_v6 fixed screen was only a narrow pass over v5
  (`0.764251` vs `0.763387`) and stayed below the v1 fixed anchor
  (`0.770875`). The 3-fold OOF aggregate was `0.756118`, above v5 OOF
  (`0.755129`) but below v1 OOF (`0.758499`).
- Evidence: deterministic rule tuning finished at `0.762935`, beating v5
  +rules (`0.761686`) but missing M7/v1 +rules (`0.767129`) by `0.004194`.
  Weak classes after rules: `list_directory=0.4798`, `read_file=0.5843`,
  `grep_search=0.6018`, `glob_pattern=0.6572`, `ask_user=0.6604`.
- Decision: Do not promote current_v6 as a final M7 replacement or Public
  package. Keep it as compression/timing evidence; current_v6e remains the last
  guarded serializer-compression probe before returning to current_v1
  cascade/timing work.
- Artifacts:
  `experiments/artifacts/20260706_current_v6_qwen3_06b_token_lengths.json`,
  `experiments/artifacts/v6_qwen3_06b_oof_len384_ep3_oof_metrics.json`,
  `experiments/artifacts/v6_qwen3_06b_oof_len384_ep3_rules_rule_boosts.json`.

### 2026-07-06 - current_v6e fixed screen closes serializer compression

- Evidence: exact-spec `current_v6e` len400 batch16 hit sustained fp16 NaN from
  step 1500 onward and was killed before producing a result. Cache was freshly
  rebuilt on the VM, so this was not stale-cache contamination.
- Result: stability rerun with `batch_size=8`, `grad_accum_steps=2` (effective
  batch 16) completed at fixed-screen macro `0.764074` (`raw=0.757528`,
  `bias=0.762722`). This is above v5 (`0.763387`) but just below v6
  (`0.764251`) and far below the v1 fixed anchor (`0.770875`).
- Weak classes after 2-stage: `list_directory=0.4795`, `read_file=0.5809`,
  `grep_search=0.6034`, `glob_pattern=0.6393`, `ask_user=0.6730`. The
  intended `read_file` recovery was partial but missed the `0.59` target, and
  the aggregate did not clear v6.
- Decision: Do not run current_v6e OOF. Close the Qwen3-0.6B serializer
  compression lane and return to current_v1 cascade/timing work before spending
  a Public slot.
- Artifacts:
  `experiments/artifacts/20260706_current_v6e_qwen3_06b_token_lengths.json`,
  `experiments/artifacts/20260706_094913_gpu_transformer_session_current_v6e_len400_replay-last1_v6e_qwen3_06b_screen_b8a2_metrics.json`,
  `experiments/logits/20260706_094913_gpu_transformer_session_current_v6e_len400_replay-last1_v6e_qwen3_06b_screen_b8a2_val_logits.pt`.

### 2026-07-06 - OOF diversity reframes compression serializers as ensemble legs

- Evidence: softmax blending existing OOF logits (`m7v1`, v5, v6, `m8v1`) showed
  10.4-10.9% argmax disagreement from M7. The 0.6B triple v1+v5+v6 reached raw
  `0.7632`, 2-stage `0.7664`, rules `0.7724` (`+0.0053` vs M7 rules). The
  m7+m8+v6 triple reached raw `0.7709`, 2-stage `0.7728`, rules `0.7782`
  (`+0.0111` vs M7, `+0.0041` vs M8 alone). Gains concentrated in weak classes
  after rules, including `list_directory=0.5089` and `read_file=0.6104`.
- Decision: Stop treating compressed serializers as v1 replacements. Reuse them
  only as diversity legs. The practical deployment lanes became routed cascade
  and KD; direct two-leg deployment looked over budget before a T4 replica check.
- Artifact: `experiments/artifacts/20260706_oof_diversity_probe.json`; blend rows
  live in `experiments/results.csv` as `20260706_blend_*`.

### 2026-07-07 - Cascade infrastructure works, but quality was not enough

- Evidence: `casc_v6m7_r20.zip` routed all rows through v6 len384, sent the
  bottom 20% margin rows to M7 len416, blended 50/50, and retuned r20
  bias/rules. OOF chain was raw `0.759208`, 2-stage `0.761703`, rules
  `0.767901`, only `+0.0008` over M7 rules. Sparse embedding patching reduced
  the package to `948.2MB` and passed clean CPU offline smoke.
- Public result: runtime `9:08/10:00`, Public `0.772`. The routed cascade fits
  the server budget, but quality was below M7 Public `0.780`.
- Decision: Keep the cascade machinery as validated infrastructure. Do not
  promote the v6+m7 quality recipe.

### 2026-07-07 - Same-split OOF-blend KD is optimistic locally

- Evidence: Qwen3-0.6B student + m7+m8+v6 OOF-blend teacher screened at raw
  `0.780809`, bias `0.783273`, 2-stage `0.784007`, `+0.0131` over the v1 fixed
  anchor. The refit package `kd_m8blend_qwen3_refit.zip` scored Public `0.782`
  at `8:54/10:00`; its OOF chain was raw `0.782848`, 2-stage `0.783540`, rules
  `0.786984`.
- Decision: The raw KD signal transferred, but the 2-stage/rules layer reversed
  (`0.786984` OOF rules to `0.782` Public). Same-fold teacher/student stacking
  creates an optimistic tuning surface, so future KD/stacking promotion must use
  matched-seed Public or a teacher OOF split disjoint from the student split.
- Note: int8 validation for this refit showed weight mean relative error
  `0.952%`, logit max abs `0.069`, and argmax agreement `99.12%` on 1024
  samples. This was recorded as a risk, not a blocker.

### 2026-07-07 - HCX-0.5B superseded Qwen3-0.6B and exposed KD fold leak

- Evidence: swapping only the base model to
  `naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B` with the champion
  current_v1 recipe produced fixed 2-stage `0.769796`, Public `0.7852`, and
  runtime `6:29/10:00`. HCX tokenized the same current_v1 text about 11% shorter
  than Qwen3-0.6B (mean 200 vs 226 at len384, near-zero truncation).
- Diversity: HCX and Qwen3-0.6B were tied on aggregate fixed validation but had
  complementary classes: HCX ahead on `plan_task` `+0.037`, `read_file` `+0.028`,
  `list_directory` `+0.025`, `web_search` `+0.010`; Qwen ahead on `ask_user`
  `+0.041` and slightly on `lint_or_typecheck`. Error overlap was 82%, with
  608/531 mutual rescues. A fixed logit blend reached 2-stage `0.7725`, but a
  two-model package remained over timing/size constraints.
- KD fold-leak evidence: matched-seed HCX student KD with the same m7+m8+v6
  OOF-blend teacher looked strong locally (`0.7697` -> `0.7820` raw,
  `ask_user +0.075`) but scored Public `0.7827`, `-0.0025` versus non-KD HCX.
- Decision: Adopt the KD/stacking rule above as a hard method rule. Local
  fixed/OOF screens are not promotion evidence for same-split OOF teacher KD.
  The teacher export remains useful; only the promotion method changed.

### 2026-07-07 - HCX and KD artifacts were absorbed into repo-owned packages

- HCX non-KD absorption: handoff weights and fixed-val logits were copied into
  repo paths, tuned class bias was injected from `hcx_screen_val_logits.pt`, and
  `submissions/hcx05b_refit.zip` was rebuilt at 512MB. Clean CPU offline smoke
  passed, including columns, ID order, labels, and int8 load.
- M8-teacher KD absorption: `kd_hcx_m8` was verified as HCX-0.5B student KD from
  M8 Qwen3.5-0.8B full-refit train70k logits, not a 3-fold OOF stitch. The final
  recipe is base `hcx05b_refit_s42`, teacher
  `experiments/logits/m8_qwen35_refit_train70k_fp16.pt`, alpha `0.5`,
  temperature `3.0`, no rules layer.
- Verification: the delivered deployed int8 pack was proven bit-exactly derived
  from the fp16 checkpoint by re-quantizing all 219 tensors with the repo
  int8-rowwise-v1 codec. The rebuilt package is `submissions/kd_m8_refit.zip`;
  see `final_summary.md` for the current submitted baseline score.

### 2026-07-07 - HCX seed variance is wider than previous calibration

- Evidence: `hcx05_s777` used the exact non-KD HCX-0.5B recipe with only seed
  42 -> 777 changed. Public was `0.765`, runtime `6:27/10:00`, a `-0.0202`
  delta from the seed42 instance.
- Decision: Treat single-seed HCX Public deltas under about `0.02` as
  inconclusive recipe evidence. Because Public is the final score, this also
  makes best-of-N seed selection more valuable on the HCX line than previously
  assumed.

### 2026-07-07 - R-Drop helps non-KD HCX, but not the KD champion

- Non-KD screen: R-Drop alpha `1.0`, dropout `0.1`, same seed/split as the HCX
  anchor improved raw `0.765997` -> `0.773416`, bias `0.768743` -> `0.775251`,
  and 2-stage `0.769796` -> `0.776188` (`+0.0064`). Weak-class deltas included
  `ask_user +0.0526`, `grep_search +0.0132`, `glob_pattern +0.0063`,
  `list_directory +0.0024`, and `read_file -0.0092`.
- Ablation: dropout-only scored 2-stage `0.768854`, essentially the non-KD
  anchor and `-0.0073` versus R-Drop. The useful term is the symmetric KL
  consistency term, not dropout itself.
- KD stack screen: adding R-Drop to the M8-teacher KD recipe scored raw
  `0.779322`, bias `0.783698`, 2-stage `0.784191` versus KD anchor 2-stage
  `0.787801` (`-0.0036`).
- Public result: `kd_rdrop_hcx_s42.zip` scored Public `0.788` with runtime
  `6:37/10:00`, `-0.0011` versus the KD package and inside the 0.002 noise
  floor, but in the same negative direction as the screen.
- Decision: R-Drop is a real non-KD HCX lever, but it is rejected on the KD
  champion line. No further KD+R-Drop stacking attempts are planned.

### 2026-07-07 - Ensemble/cascade diversity after KD was not useful

- HCX x XLM-R probe: HCX+XLM-R base448 improved fixed 2-stage by only `+0.0020`
  at w50 and `+0.0041` when val-tuned. HCX+XLM-R large448 improved `+0.0073`
  but exceeded package size. HCX+m7 was stronger (`+0.0086` to `+0.0088`) but
  also blocked by size/timing.
- KD x m7 probe: `kd_hcx_m8_screen` x M7 softmax blend scored 2-stage
  `0.785075`, `-0.002727` versus the KD student alone; the best weight sweep
  gained only `+0.000529`.
- Decision: Close XLM-R and m7 cascade/ensemble lanes on top of the KD package.
  M8-teacher KD already absorbed most Qwen-family diversity that M7 would add.

### 2026-07-07 - current_v2 and post-hoc referee lanes are closed

- current_v2 screen: HCX len448 current_v2 scored raw `0.765973`, bias
  `0.767803`, 2-stage `0.768813` versus v1 `0.769796`. `ask_user` improved
  `+0.0507`, but `list_directory` and `read_file` each fell `-0.0185`.
- Referee probe: pair-boundary signals existed under diagnostic GroupKFold, but
  the deployable top2-pair population had much lower error. OOF-trained referee
  transfer was only about `+0.0002` to `+0.0003`.
- Decision: Close the multi-turn information-recovery lane and the post-hoc
  rule/referee lane. Do not spend a Public slot there.

### 2026-07-07 - M8+HCX two-teacher KD is packaged, but screen signal is flat

- Evidence: M8+HCX 50/50 softmax teacher screen scored raw `0.784021`, bias
  `0.786454`, 2-stage `0.787757` versus M8-only KD anchor raw `0.783852`,
  bias `0.787077`, 2-stage `0.787801`. The 2-stage delta was `-0.00004`, with
  only small class reshuffling (`ask_user +0.0085`, `grep_search -0.011`).
- Package state: `kd_m8hcx_refit` completed with all-zero class bias and no
  rules. Int8 validation reached `100%` argmax agreement on 512 samples, and
  `submissions/kd_m8hcx_s42.zip` passed GPU and CPU offline smoke.
- Decision: Treat Public submission, if used, as a pure matched-seed test of
  whether adding the HCX full-refit teacher changes deployment behavior. The
  screen itself gives no positive signal.

### 2026-07-08 - M9 package collapse was a RoPE config compatibility bug

- Context: M9 9B teacher refit/export completed normally
  (`m9_qwen35_9b_refit_train70k_fp16.pt/.npz`, 70k x 14 fp16 full-refit
  forward). The first repo package for the HCX M9-KD student collapsed on Public
  to `0.702`, while a teammate package with the same teacher scored `0.785`.
- Root cause: `transformers 5.13.0` saved Llama RoPE as
  `rope_parameters={"rope_theta":500000}` only. The submission requirement
  `transformers>=4.51,<4.52` ignored that key and loaded `rope_theta=10000.0`,
  changing positional embeddings at inference.
- Fix: backport `rope_theta: 500000`, `rope_scaling: null`, and
  `torch_dtype: "float16"` into the saved config, and add an HF config
  normalizer in `package_submission.py`. The corrected `kd_m9_refit.zip` passed
  GPU and CPU smoke with the expected config.
- Decision: Treat the corrected package as the valid M9-KD candidate; the
  `0.702` package is invalid evidence.

### 2026-07-08 - Qwen Korean tokenizer issue is token budget, not text corruption

- Evidence: On 70k current_v1 serialized train rows, Qwen3-0.6B and Qwen3.5-0.8B
  round-tripped all 51,831 Korean-containing rows and 21,840 unique Korean
  chunks exactly: decoded replacement character count `0`, token-string
  replacement character count `0`, unknown token usage `0`.
- Nuance: individual byte-fragment token IDs can decode to replacement-looking
  characters when decoded one by one, but the full ID sequence decodes back to
  the original Korean string. NFD-style Hangul jamo normalize to NFC in Qwen/HCX;
  no mismatch appeared in the actual train distribution.
- Token cost: average row length was Qwen3-0.6B `224.6`, Qwen3.5-0.8B `216.0`,
  HCX `200.5`. Korean rows had Qwen3/HCX ratio `1.145`; non-Korean rows ratio
  `1.088`. Mixed code/Korean chunks were near equal cost, while pure Korean
  chunks carried the penalty.
- Decision: Reject the hypothesis that Qwen corrupts Korean text with U+FFFD.
  The real issue is merge granularity and token-budget cost. Do not exclude
  Qwen3.5 teachers on tokenizer-corruption grounds.
- Artifacts:
  `experiments/artifacts/20260708_qwen_korean_tokenizer_probe.json`,
  `experiments/artifacts/20260708_qwen_korean_roundtrip_probe.json`.

### 2026-07-08 - current_v7r is the surviving non-KD serializer

- current_v7 screen: v1-preserving state/echo features scored raw `0.768097`,
  bias `0.771651`, 2-stage `0.772532`, `+0.002736` over v1. `grep_search`
  improved `+0.0073`, `glob_pattern +0.0044`, and `ask_user +0.0441`, while
  `plan_task -0.0112` was logged as a non-target class tradeoff rather than an
  automatic veto.
- v1-teacher KD screens: v7-KD scored 2-stage `0.785344`, `-0.002457` versus
  the KD anchor. v7r-KD scored 2-stage `0.783032`, `-0.004769`. Later review
  identified this as teacher-serializer mismatch: half the loss was KL to an M8
  teacher trained on current_v1 input, which can pull v7/v7r students back
  toward v1 uncertainty.
- v7r screen: adding an early `$` register line to v7 scored raw `0.773017`,
  bias `0.776367`, 2-stage `0.776961`, `+0.004429` over v7 and `+0.007165`
  over v1. `plan_task` recovered strongly (`+0.0251` vs v7), with broad class
  gains except a small `grep_search` regression versus v1.
- Seed replication: v1_s777 scored 2-stage `0.772125`; v7r_s777 scored
  `0.779066`, delta `+0.006941`, nearly matching seed42. This confirms the
  non-KD v7r effect despite wide HCX seed variance.
- Decision: Keep `current_v7r` as the frozen non-KD serializer winner. It needs
  a matched v7r teacher before any KD conclusion is valid.

### 2026-07-08 - Later serializer variants failed; close the serializer search

- current_v8/v8t: v8 scored 2-stage `0.764886`, `-0.012075` versus v7r and
  below v1. v8t scored `0.757931`, another `-0.006955`. Decision: replacing or
  deleting natural low-information tokens is harmful; register/wall tokens must
  be additive and early, not substitutions or tail junk.
- Tag schema v9: v9o scored `0.759719` (`-0.017242` vs v7r); v9f at len384
  scored `0.774215` (`-0.002746`); v9f at len416 scored `0.770220`
  (`-0.006741`); v9h scored `0.760221` (`-0.016740`). Decision: all tag-schema
  variants are rejected.
- v7rb: adding turn/lang bin copies to the state line scored `0.770929`,
  `-0.006032` versus v7r. Decision: reject; it appears to dilute the register
  mechanism.
- v7rc/v7rcgw: deleting meta while preserving reg scored `0.774413`,
  `-0.002548` versus v7r. The combined loc deletion + args/results wall +
  compact langs variant scored `0.756450`, `-0.020511` versus v7r and created
  a new `ask_user -> plan_task` confusion pattern. Decision: reject the whole
  meta-deletion family.
- Final decision: no more serializer variants from this generation. `current_v7r`
  is the only surviving candidate and the matched-teacher path should use it.

### 2026-07-08 - Post-hoc Public probes did not beat the KD champion

- Fixed-val bias plus weak-class sparse residual (`kdm8_fvb_sp`) scored Public
  `0.786` versus `kd_m8_refit` at `0.7891`. The fixed-val 2-stage bias surface
  did not transfer, and the sparse residual was too weak to rescue it. Decision:
  do not inject fixed-val class bias into the final KD champion.
- Test-batch label-shift/prior calibration (`kdm8_pcal`) kept the original
  zero-bias champion and added only a conservative transductive prior bias
  (`prior_blend=0.25`, `bias_scale=0.35`, cap `0.18`, strong classes
  protected). Public was `0.7890`, user-reported `-0.00007` versus the champion.
  Decision: neutral and not promoted; do not stack it with fixed-val bias/sparse.

### 2026-07-08 - EXAONE 1.2B direct classifier is architecturally valid but not deployable all-row

- Evidence: `transformers 4.51.3` does not recognize `exaone4`, but
  `transformers 4.54.1` constructs `Exaone4ForSequenceClassification` and a
  fp16 forward pass succeeds. EXAONE token lengths favor `current_v1` for
  quality (`mean=248.6`, p95 `374`, p99 `410`) and `current_v5/current_v6` for
  speed (`mean≈202-205`, p95 `325-328`).
- Timing: on the same local RTX 3070 Ti probe, the current HCX KD pack projected
  30k-row inference at `235s` local and is known to run `392s` on the server.
  EXAONE fp16 projected `615s` local on `current_v1` and `504-512s` on
  `current_v5/current_v6`; batch64-192 did not improve throughput. HCX-ratio
  projection puts EXAONE all-row server time around `840-1024s`, above the
  `600s` budget.
- Size/quantization: BF16 weights are `2.4G`, so plain submission is impossible.
  A custom int4 storage codec would likely solve zip size but not speed if it
  dequantizes to fp16 at load. Official AWQ config is recognized, but the
  AutoAWQ path adds fragile/deprecated dependencies and was not package-ready.
- Decision: do not spend a Public slot on EXAONE 1.2B as a direct all-row
  classifier. Keep it only as an offline teacher or as a routed low-confidence
  cascade/referee leg unless an actual int4 compute path is proven.
- Artifact:
  `experiments/artifacts/20260708_exaone_direct_classifier_feasibility.json`.

## Current Next Step

The active lane is M8 Qwen3.5-0.8B teacher refit with `--serializer current_v7r`
as the only recipe change from the original M8 full refit. A restart was needed
because Qwen3.5 requires `transformers>=5.13,<5.14` plus `safetensors==0.8.0`;
the third launch was running normally after that override. Before any later HCX
student run on the same lane, revert the environment to `transformers==4.46.3`
or otherwise verify the Llama RoPE config is read correctly.

After the M8 v7r teacher finishes: export train70k logits, run the v7r-matched
KD screen against the existing KD screen anchor (`0.787801`), and only then
decide whether to refit, package, and spend a Public slot.

### 2026-07-08 - M8 v7r-teacher 재학습 성공, 로짓 재export → v7r-matched KD 스크린 파이프라인 가동

- M8(Qwen3.5-0.8B) `current_v7r` 전량 리핏 성공 완료(fp16 아티팩트 1454MB, 원본 M8과 동일 규격). pull 완료.
- 발견: 이 프로젝트에 이미 export 전용 격리 venv(`/content/venv311`, torch2.7.1+transformers5.13) 관례가 있었으나(`colab/run_teacher_export.py` 주석) 이번 VM 인스턴스엔 존재하지 않음(확인됨) — 재생성 없이 이미 5.13이 깔린 시스템 파이썬으로 직행(요구사항 이미 충족).
- 로짓 재export 런칭(M9 handoff 문서의 정확한 커맨드 패턴 재사용): `export_teacher_logits.py --model-dir .../m8_v7r_refit --serializer current_v7r --max-length 400 --dtype fp16 --output experiments/logits/m8_v7r_refit_train70k_fp16.pt`. **주의**: 이 스크립트는 results.csv에 행을 안 남겨 데몬 자동수집이 안 걸림 — rclone 수동 스테이징 필요(오케스트레이터에 반영).
- 오케스트레이터(`after_export_kd_screen.py`) 가동: export 완료 대기 → 로짓 Drive 스테이징+로컬 pull → **transformers를 4.46.3으로 원복(검증 포함, 실패 시 KD 런칭 중단)** → v7r-matched KD 스크린 자동 런칭(`kd_v7r_matched_hcx05b_screen`, teacher=v7r-재학습 M8, alpha0.5 T3, vs KD 앵커 0.787801 +0.002=0.789801).
- 가설: v1-teacher KD 실패(v7-KD -0.0025, v7r-KD -0.0048)의 원인이 teacher/student 직렬화기 불일치(KL 항이 v1-수준 불확실성으로 되끌어당김)였다면, matched teacher는 이 역-그래디언트를 제거해 v7r의 논-KD 이득(+0.007)이 스택될 것으로 기대 — 단 KD 자체의 안정화 효과와 일부 중복 가능성은 사전등록된 리스크.
- 15분 모니터를 이 파이프라인(export→스테이징→원복→KD스크린) 추적으로 교체.

### 2026-07-09 - Test-batch graph-only backfill infra split from train lookup

- Implemented a separate `test_batch_graph_backfill` meta switch in `script.py`.
  It runs only same-submission-batch history graph recovery and never reads the
  train-derived `leak_lookup.json.gz`.
- The graph function is stricter than the old bundled leak tier: positional
  step matches must also verify that the source row's `current_prompt` equals
  the later row's history user text, and all candidate actions must be
  conflict-free before overriding.
- Packaging flags: `package_submission.py --test-graph-backfill` enables
  positional+aligned graph recovery; `--test-graph-aligned-only` disables
  id/step positional matching. Both are mutually exclusive with `--leak-lookup`.
- Local train audit with the new function preserved the previous internal-graph
  ceiling: full positional `60,553/70,000` correct, noid aligned-only
  `35,629/70,000` correct, and p=0.2 subsample `6,592/14,090` correct; all had
  zero wrong rows. This is infrastructure only, not yet a Public result.
