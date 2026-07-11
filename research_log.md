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

### 2026-07-09 - Explorer4 conditional CE w0.40 failed on the KD champion screen

- Implemented loss-only Explorer4 conditional CE for true-label
  `list_directory/read_file/grep_search/glob_pattern` rows, with optional
  balanced 4-way class weights and no inference/script.py changes.
- First card `kd_v1_e4w040_screen` used the current v1 KD champion recipe plus
  `--explorer4-loss-weight 0.40 --explorer4-loss-balance` on C lane.
- Result versus `kd_hcx_m8_screen_s42` anchor: raw macro `0.783852 -> 0.782105`
  (`-0.001746`), 2stage `0.787801 -> 0.784597` (`-0.003204`). Raw Explorer4 sum
  was flat (`2.421909 -> 2.421670`), while 2stage Explorer4 sum fell
  `2.434411 -> 2.424220` (`-0.010191`). Class movement was not the desired
  broad lift: final `grep_search -0.014845`, `list_directory -0.003856`,
  `read_file +0.005341`, `glob_pattern +0.003169`.
- Decision: reject Explorer4 w0.40 on the v1 KD champion. Do not try the stronger
  `0.55` card. Any further Explorer4 attempt needs a different target, likely a
  much lighter weight or targeted pair treatment, and should first show raw
  improvement rather than only bias-surface reshuffling.

### 2026-07-08 - v7r-matched KD 스크린 결과: 가설 확인, 승격 기준 미달 — v7r 레인 최종 마감

- **2stage 0.788443** vs v1-teacher KD 앵커 0.787801 — **+0.000642**, 게이트(+0.002=0.789801) 미달, 노이즈 플로어(0.002) 안. **refit/패키징/Public 없음.**
- **메커니즘 확인**: v1-teacher KD 조합(v7: -0.002457, v7r: -0.004769) 대비 matched teacher로의 전환만으로 **+0.0054 스윙**(노이즈 밖) — "v1-teacher의 KL항이 학생을 v1-수준 불확실성으로 되끌어당긴다"는 07-08 낮 정정 가설이 실증적으로 확인됨. 다만 matched teacher가 v7r 논-KD 이득(+0.007) 전량을 스택하진 못하고 v1-teacher KD 수준(~0.7878)까지만 회복 — KD 자체의 안정화 효과와 v7r 이득이 부분 중복되는 것으로 해석.
- 약클래스: list_directory 0.5257(v1-teacher KD 앵커 0.5181 대비 개선), ask_user 0.6872(소폭 개선), grep_search 0.6466(소폭 하락) — 노이즈권 재배치, 방향성 신호 아님.
- **Decision: v7r 직렬화기 레인 최종 마감.** 논-KD 티어에서는 s777 페어로 검증된 확정 레버(+0.007)이나, 챔피언 팩이 KD 라인(`kd_m8_refit` Public 0.7891)이라 직접 배포 경로 없음. matched-teacher로도 승격 기준 미달 확인 — 이 이상 투입(alpha 재탐색 등)은 별도 사용자 판단 대기, 기본은 종료. 오늘 하루 소진 GPU: M8 v7r 재학습 ~9h + export + 스크린 3건, 신규 챌린저 9개(v8/v8t/v7rb/v7rc/v7rcgw/v9o/v9f×2/v9h) 전부 실패, v7r만 유일 생존 논-KD 레버로 남음.

### 2026-07-08 - v7r-matched KD 전량 리핏 착수 (사용자 판단: 게이트 미달이나 Public 시도)

- 사용자 지시: kd_v7r_matched_hcx05b_screen(2stage 0.788443, +0.000642 — 게이트 미달·노이즈권)을 그대로 전량 리핏해 Public 시도. Public이 최종 판정이라는 원칙, 방향은 양(+)이었다는 점 근거.
- 레인 A 재오픈(사용자), 워처로 하트비트 fresh 감지 후 자동 push+런칭. 커맨드는 `kd_m8_refit`(현 챔피언) 리핏 커맨드를 완전 미러 — `--serializer current_v1→current_v7r`, `--distill-logits`를 matched teacher 파일(`m8_v7r_refit_train70k_fp16.pt`)로 교체, 나머지 동일(alpha0.5 T3, seed42, `--final-model --final-only`).
- 참고: HCX(Llama계열) 리핏이라 transformers 오버라이드 불필요 — 신규 부트스트랩은 기본 4.46.3, 별도 조치 없이 안전.
- 완료 후: `package_submission.py --no-sparse` → 오프라인 스모크 → 사용자 확인 후 Public 제출(1슬롯 vs 0.7891). 15분 모니터 갱신.

### 2026-07-08 - refit 1차 즉시 실패(teacher .pt 누락)/2차 재개 — 재발 버그, 스테이징 관례 재확인

- 1차 런칭 즉시 실패: `FileNotFoundError: experiments/logits/m8_v7r_refit_train70k_fp16.pt` — 사용자가 새로 연 레인 A는 완전히 새 VM 인스턴스(A100, uptime 3.3분)라 로컬 디스크에 아무 것도 없고, `.pt`는 gitignore 대상(레포 컨벤션상 `.npz`만 추적)이라 `cloud_sync.py push`(tracked 파일만 번들)에 안 실림 — Screen 2 첫 런칭 때(07-08 오전)와 동일한 재발 버그.
- 복구: Drive(`gdrive:AADP_exchange/logits/m8_v7r_refit_train70k_fp16.pt`, 앞서 export 후 스테이징해 둔 파일)에서 VM 마운트 경로(`/content/drive/MyDrive/...`)로 `cp`(rclone CLI는 VM에 없음 — Drive는 마운트 파일시스템이라 cp로 충분) → `/content/AADP/experiments/logits/`로 복사, 크기 일치 확인(6858237 bytes) 후 재런칭(pid 4597), 정상 진행 확인.
- **일반화 교훈**: `.pt` teacher 로짓을 쓰는 모든 KD 런칭은 **레인(VM 인스턴스)이 바뀔 때마다** 재스테이징이 필요함 — push만으로 충분하다고 가정하지 말 것. 15분 모니터를 `kd_v7r_matched_refit` 추적으로 갱신.

### 2026-07-08 - refit 완료, 패키징 중 int8 변환 필요성 재확인

- kd_v7r_matched_refit 전량 리핏 완료(fp16 1090.6MB), pull 완료. `package_submission.py --no-sparse`가 fp16 그대로 패키징해 1031MB로 1024MB 제한 초과 — `package_submission.py`엔 int8 변환 로직이 없음(레포 컨벤션상 별도 `quantize_checkpoint.py` 수동 단계 필요, `script.py`의 `load_hf_model`이 `model.int8.safetensors` 존재 시 우선 로드).
- `quantize_checkpoint.py quantize` 실행: 1132.6MB → 568.2MB(50.17%, 170/219 텐서 양자화). 무결성 검증(`verify`, CPU 512샘플) 진행 중.

### 2026-07-08 - kd_v7r_matched 패키징 완료, Public 제출 대기

- int8 변환 완료(1132.6MB→568.2MB, argmax 99.80%). `experiments/incoming/models/kd_v7r_matched_refit_int8/`(fp16 model.safetensors 제거, int8만 유지) 구성 → `package_submission.py --no-sparse` → `submissions/kd_v7r_matched.zip`(512MB). GPU/CPU 클린 추출 오프라인 스모크 양쪽 통과(로컬 5행 스텁 — 정확성만 검증, 타이밍 무의미).
- 추론 시간 투영(로컬 스텁으론 실측 불가, 토큰비 기반): v7r mean 261.1 vs v1 mean 200.5(HCX, +30.2%) × 챔피언 실측 6:32/10:00 → **예상 ~8:30-8:35/10:00**, 여유 ~1:25-1:30분 — 챔피언 대비 빠듯하나 예산 안.
- **Decision: Public 제출 대기 — 사용자 실행.** matched-seed 비교 대상은 kd_m8_refit(0.7891); 스크린 단계 델타(+0.000642)는 노이즈권이라 결과가 어느 쪽으로 나와도 0.002 플로어 안일 가능성이 높음(사전 기대치 명시, 07-08 스크린 판정과 동일 기조).

### 2026-07-09 - kd_v7r_matched Public 결과: 0.787, v7r 레인 최종 종료

- Public **0.787** / runtime **7:57/10:00** (`kd_v7r_matched.zip`). vs 챔피언 `kd_m8_refit` 0.7891 → **-0.0021**, 0.002 노이즈 플로어를 근소하게 넘는 음(-) 방향. 사전등록 기대치(스크린 델타 +0.000642는 노이즈권 → 결과가 어느 방향이든 놀랍지 않음)와 부합.
- 런타임은 토큰비 기반 투영(~8:30-8:35)보다 여유 있게 나옴(7:57) — 투영이 보수적이었음.
- **Decision: `kd_v7r_matched` 반려, 챔피언 유지(`kd_m8_refit` 0.7891). v7r 직렬화기 레인 전체 최종 종료.** 논-KD 티어의 확정 레버(+0.007, s777로 재현)는 여전히 유효한 사실이나 챔피언이 KD 라인이라 배포 경로가 없었고, 유일한 스택 경로였던 matched-teacher KD도 스크린(게이트 미달)과 Public(음의 델타) 양쪽에서 승격 실패를 확인. 추가 v7r 계열 작업 없음.

### 2026-07-09 - current_v10 route/trail/open_rel serializer screen rejected; G4 bf16 lane requires controls

- Implemented `current_v10`: `current_v1` plus explicit `route`, `trail`, and `open_rel` lines from weak-class qualitative analysis. Token audit with HCX tokenizer on 70k train rows: `current_v1` mean/p95/max `200.5/294/386`; `current_v10` `265.2/359/454`; `current_v10 >384` only 531 rows and `>416` 30 rows, so first screen used len416.
- Lane C G4/Blackwell could not run the normal fp16 path: `kd_v10_route416_hcx05b_screen` failed at the first optimizer step with `non-finite grad norm: nan`. Retried as `--bf16` under a distinct suffix.
- `kd_v10_route416_bf16_hcx05b_screen`: raw `0.753897`, 2stage `0.756603`, Explorer4 macro `0.577376`.
- Because bf16 changed the training path, ran same-lane control `kd_v1_len384_bf16_g4_control`: raw `0.755598`, 2stage `0.759469`, Explorer4 macro `0.582852`. This shows most of the large gap vs the fp16 KD anchor (`0.787801`) is BF16/G4-path mismatch, not solely the serializer.
- Same-path delta still rejects the serializer as implemented: `current_v10_len416_bf16 - current_v1_len384_bf16 = -0.002866` macro. Weak class movement was not the desired broad lift: `list_directory -0.0154`, `read_file -0.0077`, `grep_search +0.0062`, `glob_pattern -0.0050`.
- **Decision: reject `current_v10` route/trail/open_rel schema in its current verbose form; no refit/Public.** Future serializer attempts should be shorter and pair-targeted, or must be tested on a comparable fp16-capable lane. G4/Blackwell `--bf16` results are not directly comparable to existing fp16 screens without a same-lane control.

### 2026-07-09 - current_v11s compact nav/scaffold serializer rejected on same-lane A100 control

- Implemented `current_v11s`: `current_v1` line skeleton plus early constant
  `reg:`, compact `nav:` evidence (`q/p/h/c/r/a/o`), and masked/bin
  `meta/workspace` values. Token audit with HCX tokenizer on 70k rows:
  mean/p95/p99/max `224.9/318/338/406`; no row exceeded len416 and only five
  rows exceeded len384.
- A100 lane B fp16 screen completed after patching the non-finite grad guard to
  let `GradScaler` skip/back off fp16 overflows while preserving the hard fail
  for non-scaler paths.
- Initial result versus `kd_hcx_m8_screen_s42` historical fp16 anchor: raw macro
  `0.783852 -> 0.779544`, 2stage `0.787801 -> 0.782107`
  (`-0.005694`). Explorer4 sum fell `2.434411 -> 2.410723`
  (`-0.023688`): `list_directory -0.0113`, `read_file -0.0078`,
  `grep_search -0.0069`, `glob_pattern +0.0023`.
- Same-lane A100 fp16 `current_v1` control (`kd_v1_len384_a100_b_control`)
  reproduced above the historical anchor: raw `0.785381`, 2stage `0.790594`,
  Explorer4 sum `2.446432`. Against this matched control, `current_v11s` is
  decisively lower: 2stage `0.790594 -> 0.782107` (`-0.008486`), Explorer4 sum
  `2.446432 -> 2.410723` (`-0.035709`).
- Prediction distribution shifted toward `grep_search` (`+71`) and away from
  `list_directory` (`-89`), while unrelated priors also moved (`web_search`
  F1 `-0.031`, `run_tests -0.0065`). This suggests the compact nav/masked-meta
  package disrupts the broader v1 representation rather than cleanly improving
  weak-class boundaries.
- Same-lane weak-class deltas confirm the failure mode: `list_directory -0.0166`,
  `read_file -0.0129`, `grep_search -0.0046`, `glob_pattern -0.0016`; predicted
  `grep_search` rose `+169` while `list_directory/read_file/glob_pattern` all
  fell.
- **Decision: reject `current_v11s`; no refit/Public.** Any next serializer probe
  should preserve raw v1 meta/workspace first and test only one additive cue
  family. The coupled package of masked meta/workspace plus compact nav is too
  disruptive even though the token profile is acceptable.

### 2026-07-10 - 휴먼 시맨틱 리레이블: 약클래스 라벨은 생성기 정책 기록, 의미 이해 레인 개념적 종료

- 07-09 약클래스 케이스북의 248개 고유 행을 블라인드 리레이블
  (`experiments/artifacts/20260710_human_relabel/`): 1차 6청크 + 독립 감사 2인
  (80행) + 불일치 8행 판정. 어노테이터 간 신뢰도는 높음(감사자 상호 91.3%,
  3자 만장일치 81.3%)인데 데이터셋 라벨과의 정확 일치는 21.0%, acceptable
  허용 시 31.0%. `list_directory`는 60행 중 정확 일치 1행.
- 결정적 검증: 225/248행에서 train 세션의 다음 스텝을 재구성했고 225건 전부
  `train_labels.csv`와 일치 — 조인/조립 버그 배제. 라벨은 합성 에이전트가
  실제 취한 행동이며, 그 행동이 요청 의미와 자주 단절됨(예: "airflow 참조 다
  찾아줘" → `list_directory(path=models)`). 약클래스 타깃은 의미적 액션 선택이
  아니라 **합성 에이전트 정책 모방** 문제.
- 케이스북의 정답-행 feature 통계가 같은 결론을 독립적으로 지지 (규약 주의:
  `last`=직전 액션, `prev`=두-단계-전 액션; weak_rows_all 원자료로 검증): glob
  정답의 82%가 두-단계-전 액션 grep_search(해당 슬라이스 정확도 505/513=98.4%),
  list 정답의 88%가 얕은 히스토리(액션 ≤1개, 슬라이스 정확도 510/573=89.0%;
  완전 무히스토리는 353/580=61%) — 모델의 약클래스 정답은 프롬프트 의미가
  아닌 최근-액션 전이 사전확률에서 나옴.
  07-09 v10/v11s 직렬화기 실패(의미 강화 → grep 쏠림 → 약클래스 하락)를
  구조적으로 설명.
- 한계: 오류-편중 층화 표본이므로 21%는 전체 라벨 품질 추정치가 아님. 전역
  노이즈율 주장 전에 정답-행 매칭 표본 어노테이션이 선행돼야 함(미수행).
- **Decision: 프롬프트 의미 이해 강화 레인(의미 지향 직렬화기, 휴먼 라벨 학습
  타깃) 개념적 종료. 휴먼 라벨(`human_relabels_final.jsonl`)은 진단 감사
  세트로만 사용 — 최적화 타깃은 원 라벨 유지.** 후보 변경은 이 248행에서
  데이터셋 일치/휴먼 라벨 일치를 이중 리포팅해 정책 학습인지 의미 학습인지
  구분. 잔여 헤드룸 탐색은 생성기 상태(직전 액션·경로·결과 요약) 조건부
  방향 — 학습 개입(조건부 CE)은 07-09에 실패했으므로, 시도한다면 기존 OOF
  툴체인 기반 prev-action 조건부 바이어스/룰의 로짓 후처리가 우선 경로.

### 2026-07-10 - 조건부 α KD 팀 챔피언 갱신 및 Weak4 specialist 팀원 의견 기록

- 팀원 제출 `condalpha-KD` Public **0.78962**로 챔피언 갱신. 기존
  `kd_m8_refit`의 정확 점수 `0.78913` 대비 `+0.00049`; M8 teacher와 나머지
  레시피는 유지하고, teacher-matched 원본 중 정답이 Weak4인 행만 KD alpha
  `0.5 -> 0.7`로 높인 단일 변경(그 외 matched 원본 `0.5`, replay/unmatched는
  기존처럼 KD 제외, T3). 0.002 노이즈권이므로 방향성 증거로 해석하지 않되
  Public 최고점이라 팀 챔피언으로 승격. 정확한 아카이브/체크포인트와 팀원 측
  weak-alpha 옵션은 로컬 인계 전이며, 현재 직접 재현 가능한 패키지는
  `kd_m8_refit.zip`.
- **팀원 의견 2 — 라우팅:** 기존 저신뢰 캐스케이드 실측이 고침 274 < 망침
  349였으므로, specialist cap을 낮은 Weak4 내부 마진 순으로 고정하지 말 것.
  T4 예산이 허용하면 main-Weak4 전량 라우팅하고, cap이 필요하면 selector를
  비교한 뒤 confirm에서 `rescue > harm`일 때만 채택. 설계 확정이 아닌 검토
  주의사항으로 기록.
- **팀원 의견 3 — 순서:** T4 실측으로 route cap을 먼저 확정한 뒤 최종 alpha
  튜닝/confirm을 수행할 것. cap이 바뀌면 alpha 튜닝과 confirm도 다시 실행.
  역시 현재 채택 결정이 아니라 구현 계획 검토 의견으로 기록.

### 2026-07-10 - Weak4 cap 정책: 저마진-우선 고정 철회, uncapped 우선 + selector 실증 판정 (추후 구현 시 적용)

- "고침 274 < 망침 349" 해석 확정: 출처는 기존 **전역-마진 저신뢰 캐스케이드**
  실측이라 현 specialist 설계로 직접 이전되지 않음 — (a) 전역 마진 라우팅이라
  weak↔non-weak family flip이 harm에 포함되지만 현 설계는 family-lock으로 그
  채널이 구조적으로 0, (b) 2차 leg가 weak4 특화 모델이 아닌 일반 강모델,
  (c) α=0이 no-op으로 수렴하는 튜닝 블렌드가 아니었음. confirm `rescue > harm`
  게이트가 동일 실패 모드를 이미 조건으로 걸고 있어, 우려가 현실이어도 비용은
  레인 시간이지 오염 제출이 아님.
- 다만 유효한 경고로 수용: rescue와 harm이 모두 저마진 구간에 몰린다는 실증
  — "낮은 내부 마진 순 라우팅이 rescue를 극대화한다"는 cap 설계 가정의 반대
  증거. 저마진 행은 main이 간신히 맞춘 행이라 뒤집기(harm)도 가장 쉬움.
- **Decision (Weak4 레인 구현 시 적용):** ① T4 실측이 허용하면
  **uncapped(전량 main-argmax-Weak4 라우팅)가 1순위** — selector 문제 자체
  소멸. ② cap이 런타임상 불가피할 때만 튜너에 내부-마진 밴드별 rescue/harm
  분해 리포트를 추가해 tune set에서 selector를 비교하고, confirm에서
  `rescue > harm`일 때만 채택. ③ cap 변경 시 α 튜닝·confirm 재실행
  (팀원 의견 3과 동일 순서 — 채택).
- 참고: condalpha-KD 챔피언 갱신(Weak4-true 행 표적 KD α 단일 변경)은 weak4
  표적 개입 방향의 약한 순풍이나, 0.002 노이즈권 해석은 유지.

### 2026-07-10 - Weak4 routed specialist screen rejected at the plain-v1 control

- A100 fp16 matched screens completed from the leak-free `kd_hcx_m8_screen`
  warm-start: `spec_v1` (`current_v1`) and `spec_nav` (`weak_nav_v1`), both LoRA
  r16, Weak4-only conditional focal loss, seed42/session/len384/ep2. The saved
  adapters use PEFT 0.19.1 and share the same code fingerprint. Detailed metrics
  and alpha grids are in `experiments/artifacts/20260710_spec_v1_weak4_router_tuner.json`
  and `experiments/artifacts/20260710_spec_nav_weak4_router_tuner.json`.
- Fixed-anchor routing selected 4,200 of 5,745 main-Weak4 predictions (cap 0.30).
  Both tune-set optima were alpha 0. At alpha 0.05, full macro deltas were
  `spec_v1 -0.000350` and `spec_nav -0.000339`; larger blends deteriorated further.
  Hard specialist replacement also corrected fewer routed rows than the main
  conditional decision, so this is a specialist-recipe failure rather than a
  `weak_nav_v1`-only failure.
- Routed token audits had no len384 overflow for either serializer. Therefore
  truncation does not explain the result; the plain `current_v1` specialist
  control itself fails to add signal.
- **Decision: reject this Weak4 routed-specialist recipe; no `spec_paths`, final
  refit, int8/T4 rehearsal, package, or Public submission.** Keep the artifacts
  for diagnosis. Future work, if resumed, must change the learning objective or
  routing/selection evidence rather than iterate the serializer on this recipe.

### 2026-07-10 - Weak4 uncapped route_fraction=1.0 audit confirms rejection

- Followed the recorded team recommendation and reran alpha tuning plus the
  60/40 session confirm split with `route_fraction=1.0`. This routes all 5,745
  main-argmax-Weak4 rows, versus 4,200 rows at cap 0.30. Reports:
  `experiments/artifacts/20260710_spec_v1_weak4_router_r100_tuner.json` and
  `experiments/artifacts/20260710_spec_nav_weak4_router_r100_tuner.json`.
- Both uncapped runs again selected alpha 0 and failed the quality gate. Through
  alpha 0.50, the added high-margin rows almost never changed class, so the
  alpha-grid macro deltas were effectively identical to cap 0.30. At alpha 1.0,
  uncapped hard replacement was slightly worse, not better.
- Small-alpha confirm rescue occasionally exceeded harm, but tune and full-val
  deltas remained negative; this fails the pre-registered requirement that the
  signal generalize across tune, confirm, and full validation. Routed len384
  overflow was zero for `spec_v1` and one row for `spec_nav`, too small to explain
  the failure.
- **Decision: the low-margin cap is not the cause of the specialist failure.
  Keep the recipe rejected and do not reopen final refit/Public.**

### 2026-07-10 - generator-policy mixture CPU audit: real latent regime, no main-model lift

- Added `audit_generator_policy_mixture.py` and audited all 70k rows with
  deterministic session-hash 5-fold CV. Two coarse sources have usable support:
  `sess_sim` 64,975 rows/8,330 sessions and `sess_au` 5,025 rows/1,099 sessions.
  Full reports are `experiments/artifacts/20260710_generator_policy_mixture_audit.json`
  plus split-seed replications `_s777.json` and `_s2026.json`.
- The latent policy regime is real and stable. MI(label; source) is 0.0143 bits;
  source adds 0.0460 bits over last-action alone and 0.0685 bits over prev-last.
  Current-v1 Weak4 true-row error is 41.7% on sim versus 8.8% on au; the error
  gap and source-conditioned confusion difference have the same direction in all
  five held-out session folds. Source-specific transition matrices also differ
  substantially.
- Policy-only grouped OOF benefits from source interactions, but the strong main
  already absorbs nearly all usable signal. Across fold seeds 42/777/2026,
  source-prev-last versus raw main changes macro by `+0.00036/-0.00080/-0.00027`;
  Weak4 changes `-0.00252/-0.00361/-0.00301`. Source alone is consistently
  negative. The initially attractive gain over a prev-last control was only
  recovery from that control's own degradation, not a gain over raw main.
- Local test stub IDs parse with the same scheme but contain only five `sess_sim`
  rows; hidden evaluation source coverage is not locally verifiable.
- **Decision: do not open a GPU/model-training lane for coarse source-prefix
  conditioning.** Preserve the audit because it verifies the generator-mixture
  hypothesis, but the stated promotion condition (session-grouped improvement
  over current main) fails. A future revisit needs a finer, test-available regime
  identifier or a main error slice that source conditioning improves directly.

### 2026-07-10 - 팀 공유(Slack) 반영: 교사축 격자 완결, 조건부 α 메커니즘·학생 의존성, llama 교사 레인

- **균일 α 교사 서열 최종(HCX-0.5B 학생, seed42, Public)**: m8 `0.78913` >
  q35 `0.78851` > coder `0.78805` > AX `0.78638` ≈ gemma `0.78627` — Qwen
  혈통이 상단 독점. train 일치율·체형·헤드룸은 필요조건일 뿐 순위 예측기가
  아님(3번째 확인; gemma는 train 0.856 골든존인데도 최하위권).
- **exa2 교사 팩 전면 사용 금지** (팀 판정): Public `0.77875`(non-KD 이하),
  원인은 세션 암기로 인한 홀드아웃 지표 인플레. q2b 팩도 캘리브레이션상 보류.
- **조건부 α는 교사축으로 전이, 교사 weak-헤드룸에 비례**: gemma-ep3×조건부
  (`gemca`) Public `0.78750` — 균일 gemma `0.78627` 대비 **+0.0012**로, m8의
  +0.0005보다 2.4배. gemma의 weak 고침가능 2,400행 vs 망침 687행(m8은
  689:781로 소진 상태)이 근거.
- **메커니즘 규명(노진산, 교사-학생 분포 전수 대조)**: condalpha의 이득은
  "약4를 잘 배워서"가 아님 — 조건부 α 후 weak train acc는 오히려 하락
  (`0.6505→0.6461`)했고 대신 mid 클래스가 급등(ask_user `0.732→0.768`,
  plan `0.869→0.886`, lint `0.779→0.787`). 약4 행(=정책 기록 = 라벨 노이즈)의
  CE 압력을 낮추자 공유 표현이 풀려 mid가 좋아진 것 — **조건부 α = 노이즈
  완충재**. 07-10 휴먼 리레이블 결론과 정합. 보조: solved 4종(edit/write/
  apply/respond)은 교사-학생 KL~0.002로 α 무반응(rest α의 실효 대상은 mid
  6종), weak 중 read_file만 교사 신호 잔존(+0.007).
- **HCX 조건부 격자 완결(목원주)**: P1(rest0.4/weak0.7) Public `0.78768`,
  P2(rest0.5/weak0.8) Public `0.78837` — 이웃(rest↓, weak↑, weak↓) 전부 하회,
  **(0.5/0.7)=0.78962 국소최적 확정**. rest 0.4 차원 전부 폐쇄(비약4 4.1만
  행의 KD 정규화 효과를 깎는 손실), P3(0.4/0.8) 영구 폐기. 미탐색 신좌표
  (0.6/0.7)은 금요일 아침 배분 예정.
- **조건부 α의 학생 의존성 발견(김태연)**: Qwen 학생×gemma 조건부 `0.78765`
  vs 같은 조합 전역 α `0.78824` (**-0.0006, 부호 반전**). 전역 α로 이미 교사를
  잘 흡수하는 학생(Qwen)에겐 조건부가 weak CE 삭감 비용만 남김. HCX +0.0012와의
  부호 간극 0.0018은 노이즈 바닥보다 커서 "학생 의존" 작업가설 채택 —
  김태연 llama 2종(전역+조건부)이 재검증점. 가이드: 전역 먼저, 전역이 0.789+
  일 때만 조건부 시도.
- **llama-3.1-8B 교사 팩 완성(노진산)**: `NousResearch/Meta-Llama-3.1-8B`,
  train argmax `0.8574`, 홀드아웃 ep3 `0.7968`. m8과 same-alt 84%·유니크 9.3%
  → HCX 학생에겐 중복 교사, Qwen 학생에겐 이질 1급이라 김태연 라인이 최적
  수요처. 로짓 `teacher_llama_train70k_fp16.pt` 및 교사 아카이브 v2.1
  (OneDrive `dacon_제출대기/teacher_archive_v21_20260710.zip`, 31파일) 공유됨.
- 잔여 계획(참고): 오늘 밤 q35ca(q35 균일 `0.78851`+조건부 전이분으로 0.789+
  도전), llama 균일→llama×조건부→(0.6/0.7) rest 상향 프로브, 내일 아침 제출
  큐 5장 + 시드/머신 리롤(best-of-N) 축, 김태연 Qwen×llama 전역(내일, 12시
  이후 llama 결과 보고 제출).

### 2026-07-10 - v7r + R-Drop 논-KD 스택 스크린 게이트 실패 — 반려, refit 없음

- 가설: 논-KD 라인에서 개별 검증된 두 레버(v7r +0.007 이중시드, R-Drop
  +0.0064 ablation-분리)의 가산 스택. 스크린은 v7r 앵커 대비 단일 변수
  (+R-Drop), G4(RTX PRO 6000 계열, 앵커와 동일 하드웨어 클래스), 65:58.
- 결과: raw `0.768698`(-0.004319), 2stage `0.773597` vs 앵커 `0.776961`
  (**-0.003364**), 통과선 `0.780961` 대비 -0.007364. R-Drop KL은 전 epoch
  양수 — 구현 실패가 아닌 실제 성능 하락. list/grep 소폭 이득(+0.0098/+0.0033)
  보다 ask_user(-0.0329)·web_search(-0.0141)·glob(-0.0073) 손실이 압도.
- **Decision: v7r+R-Drop 반려, 후속 refit 없음, LANE A 해제.** R-Drop의
  +0.0064는 v1 전용이며 v7r로 전이되지 않음 — "레버는 서로 스택되지 않는다"
  패턴이 논-KD 라인에서도 재현. R-Drop은 어느 챔피언 후보 라인에도 미채택으로
  최종 마감. 결과/로짓은 results.csv·artifacts에 pull 완료.

### 2026-07-10 - KD 챔피언 × v7r 논-KD 블렌드 오프라인 프로브: 게인 없음, 캐스케이드 카드 폐기

- 기존 s42 fixed val 로짓 두 벌(kd_hcx_m8_screen 2stage 0.787801, v7r 논-KD
  0.776961)로 07-07 블렌드 프로브와 동일 방법론(softmax 가중 블렌드 스윕 +
  2-stage 튜닝) 오프라인 측정. GPU/슬롯 비용 0.
- 결과: 오류 중복 87.8%(HCX×Qwen 페어 82%보다 높음), rescue 380/497.
  최적 w(kd)=0.70에서 raw 0.784148, 2stage 0.788422 — **kd 단독 대비
  +0.000621**, w50은 -0.00147. 비교: 논-KD HCX×m7 +0.0086, kd×m7 -0.0027.
- 해석: KD 학생은 v7r 직렬화기 다양성도 이미 흡수 — "KD가 교차 신호를
  흡수한다"는 07-07 판정이 동일-베이스(HCX) 직렬화기 축에서도 재확인. kd 레그
  로짓의 경미한 낙관(full-refit teacher가 val 행을 봄)을 감안해도 +0.004
  기준선에 크게 미달.
- **Decision: kd×v7r 라우티드 캐스케이드/통합팩 카드 폐기.** 아티팩트
  `experiments/artifacts/20260710_kd_v7r_nonkd_blend_probe.json`.

### 2026-07-10 - selective privileged mode residual frozen probe 실패 — full screen 없음

- Leak-free `kd_hcx_m8_screen` fp16 checkpoint에서 70k pooled hidden/parent-logit
  cache를 A100으로 export했다. 저장 전 historical anchor와 저장 후 fp16
  artifact의 차이는 14,001행 중 6 argmax, max logit `0.015625`, raw macro
  `-0.000017`이었다. Export된 parent 기준 zero-init residual은 14,001행 전부
  logit exact/argmax 100%/correction 0을 통과했다.
- Fixed-train session 3-fold의 fold-local StandardScaler + L2 logistic(`C=0.01`)
  predictability는 prior NLL 대비 `read 1.3309→1.0556`, `edit 0.6928→0.4154`,
  `run_bash 1.5777→1.1725`로 통과했다. 고정 taxonomy는 args/intention-only
  `K=6/2/5`; result/count/raw identity는 사용하지 않았다.
- Frozen arms 결과는 C0 `0.783834`, C1(λ=0) `0.783979`, actual mode M
  `0.783688`; M은 C0 대비 `-0.000146`, C1 대비 `-0.000292`, 5개 permutation
  최고점 대비 `-0.000331`, selected-action F1 합도 `-0.006902`였다. Mode
  conditional NLL 자체는 validation에서 세 action 모두 개선됐지만 coarse
  action 개선으로 전이되지 않아 7개 gate 중 핵심 G1–G4가 모두 실패했다.
- **Decision: discrete selective event-mode lane 반려; matched full HCX screen,
  refit, Public 제출 없음.** Cache는 continuous event embedding/future-trajectory
  auxiliary target의 값싼 후속 검증용으로만 보존한다. 상세 결과:
  `experiments/artifacts/20260710_selective_mode_probe_report.json`, taxonomy:
  `experiments/artifacts/20260710_selective_mode_taxonomy.json`.

### 2026-07-10 - 학생 스케일업(HCX-1.5B) KD 스크린 게이트 실패 — 레인 폐기

- 가설: 0.5B 학생이 용량 병목이라 teacher 신호를 다 못 받는다 → 1.5B 학생이면
  KD가 더 전달된다. 챔피언 스크린 레시피(`kd_hcx_m8_screen_s42`)에서 베이스만
  `HyperCLOVAX-SEED-Text-Instruct-1.5B`로 교체, 나머지 전부 동일(M8 teacher
  alpha0.5 T3, v1, len384, ep3, focal g2.0, replay last1, s42). Lane C G4, 71분.
- 결과: raw 0.779971 / bias 0.784254 / **2stage 0.785769** — 앵커 0.787801 대비
  **-0.002032**, 통과선 0.7938(+0.006)에 -0.008. 사전 등록한 애매 구간
  (+0.003~+0.008)에도 못 들어가는 음수라 same-lane 0.5B 컨트롤 불요.
- Per-class(2stage, vs 앵커): ask_user +0.0245, lint +0.0147, read_file +0.0096
  vs plan_task -0.0251, grep_search -0.0198, web_search -0.0131,
  list_directory -0.0079. 체계적 용량 이득이 아니라 클래스 간 재배치 —
  약클래스 이득도 read_file 하나뿐.
- 해석: 3x 파라미터로도 KD 품질이 안 오른다 = 병목은 학생 용량이 아니라
  teacher 신호/데이터 자체. EXAONE 1.2B 품질 스크린 대안도 같은 논리로 기대값
  하락(동급 용량대에서 용량 효과 부재 확인). 서빙 스파이크·int4 패키징 착수 안 함.
- **Decision: 학생 스케일업 레인 전체 폐기(사전 등록 규칙대로). 1-2B 학생 밴드
  질문은 답을 얻었고 닫힘.** metrics:
  `experiments/artifacts/20260710_143349_gpu_transformer_session_current_v1_len384_replay-last1_kd_hcx15b_m8_screen_s42_metrics.json`.

### 2026-07-11 - 생성기 FSM + 템플릿 메모리 사전검증: 구조는 실재, 승격 게이트 실패

- history를 합쳐 70,000행에서 73,181 trajectory node를 복원했다(충돌 0,
  미복원 gap 24). AU 1,099 sessions는 실제로 485개 primary scenario의 sibling
  변형이므로, 정책표/튜닝은 `sess_au_<primary>`를 묶은 scenario hash fold로
  전면 재검증했다.
- 고정 KD main에 nested scenario-CV residual을 얹은 결과 coarse/A1 action
  suffix/A2 args+result event/A3 prompt/combined Macro delta는 각각
  `-0.000002/-0.000001/-0.000157/+0.000040/-0.000117`; 어느 arm도 2/5를 넘는
  양수 fold가 없었다. 긴 suffix support는 k4 23.0%에서 k6 1.1%로 급락했고,
  cross-scenario prompt memory의 Weak4 고순도 support도 사실상 없었다.
- 허용 필드만 사용한 exact 1024d hidden kNN A4는 5/5 fold 양수였지만 Macro
  `+0.001062`, Weak4 `+0.003717`, untouched confirm `+0.000574/+0.002010`,
  rescue/harm `71/50=1.42`로 사전 게이트 `+0.003/+0.008/2.0`을 모두 못 넘었다.
  14d parent-logit kNN 대조는 confirm 음수였다. A5의 tune-only directed-pair
  효용 게이트는 사전 support/precision 조건을 통과한 pair가 없어 no-op이었다.
- 보수성 한계: residual lookup은 scenario-disjoint지만 기존 session-split main은
  validation 14,001행 중 AU sibling 775행을 train에서 본 surface다. 이 잠재적
  낙관이 있는 상태에서도 게이트에 크게 못 미쳐 scenario-grouped main 재학습은
  정당화되지 않는다.
- **Decision: 독립 GPU 학습, refit, 패키징, Public 제출 없음.** Trajectory 복원과
  A4 hidden retrieval은 작은 잔차 신호를 확인한 진단/통합팩 후보로만 보존한다.
  독립 레인 재개 조건은 test에서도 관측 가능한 더 강한 scenario key 또는 clean
  nested Macro `+0.003` 이상이다. 상세 결과:
  `experiments/artifacts/20260711_generator_fsm_nested_fixed_s42.json`,
  `experiments/artifacts/20260711_hidden_knn_a4_prevalidation.json`.

### 2026-07-11 - Lane A consensus sieve refit / Lane C ChatML screen

- Lane C `chat_v1_contract` non-KD screen: raw `0.758443`, 2-stage
  `0.762287`, versus current_v1 anchors `0.765997/0.769796`. The registered
  `0.775796` gate missed by `0.013509`; **reject and do not run ChatML KD**.
- Lane A used the M7/M8/v6 OOF consensus only for the permitted full-data
  refit (70,000 originals + 10,000 replay), with zero bias/rules. The fixed
  validation path is fail-closed because its OOF sources overlap that holdout.
  Consensus alignment, class-normalized mean `1.0`, fp16 finiteness, and the
  14-way head contract passed. The int8 deployment copy (`170/219` tensors,
  568.2 MB weights) was packaged as `submissions/kdm8_sieve_s42.zip`; 5-row
  offline smoke passed. **Decision: Public-only candidate; no local score is
  claimed before a Public result.** Public result: `0.7917`, improving the
  prior team champion `0.78962` by `+0.00208` and clearing the registered
  `0.002` noise floor. **Promote `kdm8_sieve_s42.zip` as the new team and
  locally reproducible champion.**

### 2026-07-11 - T2 반사실 미래잔차 레인 착수: Stage F0 donor 감사 GO

- 레인 배분(사용자 지시): 시브 × condalpha 스택은 팀원, 이 레포는
  `handoff_20260710_final_two_theories.md` §3 T2(반사실 미래 궤적 특권잔차
  증류). 핸드오프에 07-11 상태 애드덤 추가(시브 챔피언 0.7917 반영, frozen
  감사 parent는 leak-free `kd_hcx_m8_screen`, 배포 parent는 시브 챔피언).
- F0 (`audit_future_donors.py`): next-user 복원은 직접 step+1 행 +
  positional witness 합의(fail-closed, conflict 0)로 coverage 86.50%
  (Weak4 94.97%; `respond_only`는 전부 terminal이라 0% — T2에서 구조적
  마스크). donor 매칭 `action×source×turn_bin×language_pref`, K=4,
  same-session/AU-scenario/동일문 제외, id-hash 결정론 — 복원행 기준
  full-K 99.75% (Weak4 99.86%), 제외 위반 0. P1/P2 길이 AUC 0.5004,
  future 토큰 p100=81(HCX). next-user-only 분류 프로브 acc 15.2%로 직접
  누출 채널 미미. **Decision: GO** —
  `experiments/artifacts/20260711_future_recovery_donor_audit.json`,
  payload `experiments/privileged_targets/20260711_future_nextuser_donors.json.gz`.
- F1/F2 준비 완료: `export_future_hidden_cache.py`(GPU, future 텍스트
  `future_user:` 접두 별도 인코딩, current truncation 없음, 체크포인트
  sha fail-closed) + `probe_future_residual_gate.py`(3-fold scenario-grouped,
  대칭 LOO T1/T2 비교, λ/clip inner-tune, SPERM 층화 permutation). 유닛
  테스트 16개 통과(합성 e2e 포함). 07-10 hidden 캐시(70k, leak-free screen)
  로컬 pull 및 payload↔캐시 id/sha 정합 검증 통과. GPU 인코딩은 VM 재부착
  대기 — eval 표면은 fixed-val 14,001 중 full-K 12,025행(Weak4 5,438).

### 2026-07-11 - T2 frozen teacher gate REJECT — 반사실 미래잔차 레인 전면 종료

- Stage F1 (`probe_future_residual_gate.py`, eval=fixed-val full-K 12,025행,
  3-fold scenario-grouped, 대칭 LOO 비교, λ/clip inner-tune): pooled macro
  P0 `0.708325` / T1(actual oracle) `0.669399` / T2(donor oracle) `0.708700`.
  **T1−T2 macro `-0.0393`, Weak4 `-0.0639`, 3/3 fold 음수.** λ는 3개 fold
  모두 grid 최솟값 0.25로 수렴(inner tune이 delta 제거를 선호) — actual
  future oracle이 parent보다 나쁘고(rescue/harm 168/309), donor oracle은
  P0+노이즈 수준. F2도 동반 음성: S1−P0 `-0.0037`, S1−SPERM `-0.0009`,
  게이트 전항 실패.
- 해석: same-action donor로 action 노출 성분을 상쇄하고 나면 실제 next-user의
  행별 잔차는 착취 가능한 신호가 아니라 오히려 유해(Weak4 최대 타격). 07-10
  selective mode probe(모드 NLL 개선이 action 선택으로 전이 안 됨)·FSM 널
  결과와 방향 일치 — 미래 관측 계열의 세 번째 독립 음성.
- **Decision: 사전 등록된 즉시 종료 조건("actual이 donor보다 좋지 않으면
  종료") 발동. T2 미래 특권잔차 레인 전면 종료, full screen/refit/Public
  없음. 파라미터 grid 연명 금지 조항 준수.** 핸드오프 두 이론 모두 해소:
  T1=시브로 소비(Public 양성), T2=frozen gate 종료. artifacts:
  `experiments/artifacts/20260711_future_p1_p2_teacher_gate.json`,
  `..._future_student_recovery_gate.json`,
  `..._future_recovery_donor_audit.json`.

### 2026-07-11 - 가중치 수프 레인 클린-표면 연결성 게이트 실패 — 슬롯 0으로 종료, 잔여 경로는 레시피 스택

- `soup_merge_eval.py` 신규: 스트리밍 fp32 가중 평균 병합 + 고정 세션 스플릿
  (seed42, 14,001행) 평가 하니스. 하니스 검증: `kd_hcx_m8_screen` 재평가 raw
  `0.783756` vs 기록 `0.783852` (Δ -0.0001, fp16 배치 비결정성 수준).
- 오염 표면(리핏 — val 행이 학습에 포함): `kd_m8_consensus_sieve_refit` ×
  `kd_condalpha_refit` λ=0.5 중간점 raw `0.801366` vs 끝점
  `0.820640`/`0.831912`. 딥이지만 리핏 암기 희석 효과와 진짜 손실 장벽을 이
  표면에서는 구분 불가 — 해석 보류.
  (`experiments/artifacts/20260711_soup_interp_sieve_x_condalpha.json`)
- 클린 표면(스크린 — val 미학습): ① 동일-레시피 리롤 페어 `kd_hcx_m8_screen`
  × `kd_v1_len384_a100_b_control`(lane B에서 pull): raw `0.783756`/`0.785129`,
  λ=0.5 중간점 `0.783044` — 두 끝점 모두 아래(-0.0007/-0.0021). 수프의
  분지-중심 이득이 가장 유리한 페어에서도 부재. ② KD-loss 축 페어
  (× `kd_rdrop_hcx05b_screen` `0.779398`): 중간점 `0.772500` — 양끝 대비
  -0.007/-0.011, 명확한 V자 장벽.
  (`experiments/artifacts/20260711_soup_interp_clean_screens.json`)
- **Decision: HCX-0.5B 라인 가중치 평균(soup/그랜드 수프) 레인 종료.** 가장
  유리한 동일-레시피 리롤 페어에서도 중간점이 최고 끝점을 넘지 못해 승격
  게이트(≥ best +0.003 raw) 도달 불가능. 리핏 가중 평균(sieve×condalpha
  soup)도 같은 근거로 미승격. 총비용: 슬롯 0, 로컬 GPU 소량.
- 시브×condalpha의 잔여 경로는 가중치 평균이 아닌 **레시피 스택**(단일 리핏에
  consensus sieve + 조건부 KD α 동시 적용). 이를 위해 팀 측 옵션
  `--distill-alpha-weak`를 로컬 파서에 구현(teacher-matched 원본 Weak4-true 행
  α=0.7, 그 외 matched 0.5, replay/unmatched KD 제외 — 마스크가 α 스케일을
  운반, 미설정 시 기존과 비트 동일). 단위테스트
  `tests/test_distill_alpha_weak.py`, sieve 테스트 회귀 통과.

### 2026-07-11 - sieve × condalpha 레시피 스택 리핏 완료 — Public-only 후보 패키징 (제출 대기)

- lane A A100에서 `kd_sieve_condalpha_refit_s42` 완료(~61분). 정확히
  `kdm8_sieve_s42` 레시피 + `--distill-alpha-weak 0.7` 단일 변수(가중치 평균
  soup 레인 사망 후 시브×condalpha 조합의 잔여 경로). 런 로그로 두 레버 적용
  검증: distill matched 70000/80000(replay 1만 KD 제외), alpha_weak=0.7
  weak_rows=28782; consensus sieve 히스토그램은 챔피언 런과 동일
  (c0 12,776 / c3 48,607), head full gradient.
- int8 배포본: 170/219 tensors, 568.2 MB, fp16 대비 argmax 일치
  **512/512 (100%)** — 역대 팩 최고 충실도. 패키지
  `submissions/kd_sieve_ca_s42.zip` (512 MB), GPU·CPU 오프라인 스모크 모두
  통과. 추론 경로/아키텍처는 6:32급 kd_m8_refit 계열과 동일.
- **Decision: Public-only 후보로 제출 대기.** 비교 기준은 `kdm8_sieve_s42`
  Public `0.7917` (매칭 seed42, 단일 변수). 두 레버가 모두 "약클래스 노이즈
  행의 hard-label 압력 완화" 축이라 겹칠 위험을 사전 등록 — 결과가 어느
  방향이든 조건부 α의 시브-위 가산성에 대한 정보값이 있음.

### 2026-07-11 - kd_sieve_ca_s42 Public 0.7938 — 신규 팀 챔피언 승격, 조건부 α의 시브-위 가산성 확인

- Public **0.7938** / 런타임 **5:58/10:00** (`kd_sieve_ca_s42.zip`). 시브
  챔피언 `kdm8_sieve_s42` 0.7917 대비 **+0.0021** — 매칭 seed42 단일 변수
  비교에서 0.002 노이즈 플로어 초과. "두 노이즈-완충 레버가 겹칠 것"이라는
  사전 등록 리스크는 기각: 백본-그래디언트 시브와 손실-믹스 조건부 α는
  가산됨.
- **Decision: `kd_sieve_ca_s42.zip`을 팀·로컬 챔피언으로 승격.** 문서 3종
  갱신 완료. 역대 체인: kd_m8_refit 0.78913 → condalpha 0.78962 → sieve
  0.7917 → **sieve×condalpha 0.7938**.
- 후속 후보 (슬롯 여유 시, 우선순위 검토 필요): ① 시브 베이스에서 α_weak
  격자 재탐색(0.6/0.8 — 단 condalpha 단독 격자에서 (0.5/0.7)이 국소최적이었
  으므로 기대 제한적), ② 시브 강도 격자(c-weights) × 현 α_weak 고정,
  ③ 팀원 레인(교사축·시드 리롤)에 조합 레시피 전파 — 이제 모든 신규 리핏의
  베이스 레시피는 sieve×condalpha가 기준.
