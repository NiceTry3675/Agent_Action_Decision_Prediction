# Research Decision Log

This file records decisions, not full experiment logs. Detailed metrics, per-class
tables, prediction distributions, confusion tables, artifact paths, and commands
belong in `experiments/results.csv` and `experiments/artifacts/*.json`.

## Current Baseline

- Public: `0.743`
- Package: XLM-R 5ep + replay_last1 + OOF rules + sparse SVC weight `4.0`
- Main validation signal: OOF 2-stage Macro-F1 `0.741881`
- Fixed cross-check: `0.751733`, treated as optimistic

## Operating Rules

- Human-facing docs stay short: `final_summary.md`, `leaderboard_calibration.md`, and this decision log.
- All experiment numbers go to `experiments/results.csv`.
- Detailed metrics, bias/rule settings, and confusion artifacts go to `experiments/artifacts/*.json`.
- Final submission reasoning is anchored to `script.py` plus `model/`.

## Key Decisions

### 2026-07-02 - Public target reached

- Why: The goal was a candidate likely to exceed Public Macro-F1 `0.74`.
- Evidence: OOF 2-stage `0.741881` mapped to reported Public `0.743`.
- Decision: Freeze the submitted XLM-R replay + rules + sparse SVC package as the current baseline.
- Next action: Treat future work as leaderboard improvement, not target recovery.

### 2026-07-02 - OOF is the promotion metric

- Why: Fixed-session validation consistently overestimated submission quality.
- Evidence: fixed `0.751733` vs Public `0.743`; OOF `0.741881` vs Public `0.743`.
- Decision: Use fixed-session runs for sanity checks only. Promote finalist packages from OOF.
- Next action: Tune bias, rules, and ensembles on OOF logits before final refit.

### 2026-07-02 - XLM-R 5ep became the canonical fixed baseline

- Why: A teammate shared a stronger XLM-R fixed-session result than the old 3ep handoff.
- Evidence: XLM-R 5ep no-replay fixed raw `0.7354`, 2-stage tuned `0.7389`; earlier XLM-R 3ep public anchor was `0.708057`.
- Decision: Do not spend time reproducing XLM-R 3ep except as a sanity check.
- Next action: Compare new XLM-R ideas against the 5ep baseline and save logits for ensemble candidates.

### 2026-07-02 - Replay_last1 was worth OOF validation

- Why: Replay improved fixed-session validation over the 5ep no-replay baseline.
- Evidence: replay fixed raw `0.739664`, old bias-tuned `0.743909`, 2-stage tuned `0.744625`.
- Decision: Promote replay_last1 cap10000 to 3-fold OOF; avoid replay_last2 unless new evidence appears.
- Next action: Keep replay only when OOF or weak-class F1 improves.

### 2026-07-02 - Rules and sparse SVC provided the submission margin

- Why: Plain replay OOF was below the target band.
- Evidence: replay OOF 2-stage `0.728569`; OOF rule boosts `0.735673`; sparse SVC weight `4.0` retune `0.741881`.
- Decision: Keep OOF-tuned rule boosts and sparse SVC in the final package.
- Next action: Any new ensemble component must beat the sparse package on OOF before packaging.

### 2026-07-02 - Alternative encoders are secondary

- Why: The XLM-R replay path became the stronger reference point.
- Evidence: XLM-Align and InfoXLM collapsed under the current recipe; mDeBERTa was slow and below active baselines; mBERT showed possible diversity only.
- Decision: Deprioritize alternative encoder search until a concrete ensemble-diversity need appears.
- Next action: Keep mBERT as the only near-term diversity candidate; leave mDeBERTa as a long-shot.

### 2026-07-03 - state_v2 length extension did not clear fixed gate

- Why: state_v2 needed to beat the current fixed-session replay baseline before spending OOF runs.
- Evidence: len384 fixed raw `0.727378` and len256 fixed raw `0.712615`, both below the `0.7457` G1 gate and current_v1 len192 fixed raw `0.739664`.
- Decision: Do not promote state_v2 len256/384 to 3-fold OOF.
- Next action: Shift near-term work back to OOF-safe ensembling/stacker diagnostics or a new one-variable serializer ablation.

### 2026-07-03 - state_v2 did not falsify length relief

- Why: The state_v2 rejection mixed a length change with a serializer swap.
- Evidence: state_v2 drops session_meta fields and compresses language mix, so the failed fixed runs are confounded by information loss.
- Decision: Supersede the interpretation of the prior state_v2 entry: it is evidence against that serializer path, not evidence against clean current_v1 length extension.
- Next action: Judge length relief only from current_v1 fixed-session screens and OOF-gated promotions.

### 2026-07-03 - M1 Phase 0 found a small OOF-only gain

- Why: Saved XLM-R len192 OOF logits allowed cheap retuning without retraining.
- Evidence: rule regrid improved rules from `0.735673` to `0.740149`; rerunning current_v1 sparse SVC on the new rules selected weight `2.5` and reached OOF `0.743183`. compact_events peaked at `0.742709`; Markov prior selected weight `0.0`.
- Decision: Keep the new rule artifact plus current_v1 sparse weight `2.5` as the best local OOF candidate, but do not treat it as a submitted baseline.
- Next action: Carry this OOF candidate into M2 planning; no Dacon submission was made.

### 2026-07-03 - M1 current_v1 length ablation failed fixed gate

- Why: Longer current_v1 inputs needed fixed-session raw Macro-F1 above `0.742664` and weak-class gains before OOF promotion.
- Evidence: len256 raw `0.728402`; len320 raw `0.735856`; both missed the gate and did not improve the `list_directory`/`read_file`/`grep_search`/`glob_pattern` cluster versus len192 raw.
- Decision: Do not run length OOF. Fall back to `max_length=192` for the XLM-R-base recipe.
- Next action: Move into M2 with the Phase 0 OOF candidate as the only M1 gain.

### 2026-07-03 - xlm-roberta-large is package-size blocked

- Why: The large model must be package-feasible before multi-hour A100 commitment.
- Evidence: fp16 xlm-roberta-large HF model plus tokenizer measured `1089.108 MB`, already above the ~900 MB warning threshold and too large to combine with the sparse SVC package under the 1 GB cap. The quick-val launch was lost when the Colab daemon/runtime went stale and produced no collected result.
- Decision: Stop Track B as package-size blocked for this milestone; do not build checkpoint/resume or run full fixed-session xlm-r-large now.
- Next action: Revisit only with a concrete quantization-or-replacement plan that preserves package feasibility.

### 2026-07-03 - Colab lane hardened after mid-run runtime reclaim

- Why: The L4 runtime was reclaimed mid-run with the GPU at 97%; the daemon was detached (`start_new_session`), so the kernel had been idle since the `[agent]` cell and Colab's ~90-min no-execution idle timer fired.
- Evidence: Last heartbeat 04:16 UTC at daemon uptime 88 min (daemon started 02:48 UTC, the last cell execution); three stale queue commands including two `@unassign` were left unconsumed — a restart landmine, since a day-old queued command had already fired late once (queued 07-02 17:47 UTC, executed 07-03 02:37 UTC).
- Decision: Keep the Drive command channel (SSH tunnels and Pro+ background execution evaluated and rejected). Run the daemon synchronously inside the `[agent]` cell so the busy kernel is the keepalive; expire queued commands older than `AADP_CMD_MAX_AGE_MIN` (30 min, rc=125); make the daemon queue loop survive Drive-mount OSErrors.
- Next action: Re-run `[bootstrap]` (pulls the hardened bundle) + the new `[agent]` cell on the fresh L4, then continue M2 through the hardened lane; do not relaunch the lost xlm-r-large screen (Track B already stopped as package-size blocked).

### 2026-07-03 - Daemon-side unassign never worked; moved release to the kernel side

- Why: `@unassign` on the live L4 returned `'NoneType' object has no attribute 'kernel'` — `google.colab.runtime.unassign()` requires the IPython kernel and the daemon is a plain subprocess, so commanded and idle auto-unassign had been failing silently since the lane was built.
- Evidence: done record `20260703_062722` on the live L4; the idle CU-safety path calls the same API.
- Decision: the daemon now exits with code 86 on `@unassign`/idle-limit and the synchronous `[agent]` cell, which runs in the kernel, performs the actual `runtime.unassign()`.
- Next action: release the current L4 manually from the Colab UI (it still runs pre-fix code); the new mechanism applies from the next bootstrap.

### 2026-07-03 - Korean-specialized encoder screen: kf-deberta lands at mBERT tier

- Why: The Korean-model idea needed a tokenizer gate and a screen before any long run; serialized current_v1 inputs are only ~8% Korean letters, so Korean-centric vocabs (klue/roberta, koelectra) fragment the English/code-heavy text (+35-38% tokens, trunc@192 ~85%) and were rejected without training.
- Evidence: kakaobank/kf-deberta-base passed the tokenizer gate (mean 262 tokens = XLM-R parity, unk 0.02%) and screened at qv600 macro-F1 `0.694504` vs mBERT `0.697580` and mdeberta `0.664922` on the same no-replay recipe; its `ask_user` F1 `0.507` beats both XLM-R base `0.474` and large `0.486` screens, a possible Korean-prompt diversity signal.
- Decision: Keep kf-deberta-base alongside mBERT as the two diversity candidates; no OOF promotion without a concrete ensemble-diversity hypothesis. Track B (xlm-r-large +0.026) stays the priority.
- Next action: Decide the xlm-r-large fixed-session path (A100 short run vs per-epoch Drive checkpointing) before any diversity work.

### 2026-07-03 - qv600 diversity probe: mBERT is the ensemble partner, kf-deberta is not

- Why: The diversity candidates needed a concrete ensemble hypothesis before any OOF spend; all four encoder screens share the same quick600 val set (seed 42), so a logit-level probe was free.
- Evidence: equal-weight softmax ensembles on the shared 600 samples: large+mbert `0.7209` vs large alone `0.7148` (best pair; highest disagreement `0.117` with lowest both-wrong `0.250`); large+kfdeb `0.7090` falls below large alone and kf-deberta improves no combination. Details: `experiments/artifacts/20260703_qv600_encoder_diversity_probe.json` (recipe caveat noted there).
- Decision: mBERT is the single diversity candidate for the OOF ensemble hypothesis (large+mbert, contingent on the Track B fixed gate); kf-deberta is demoted to a targeted `ask_user` idea only (base+kfdeb ask_user `0.568`), superseding the two-candidate framing of the earlier kf-deberta entry.
- Next action: If the large fixed run passes the gate, run large OOF first; consider mbert OOF only after large OOF logits exist to test the pairing.

### 2026-07-03 - script.py learned the int8 codec path (packaging pre-work)

- Why: If large passes the fixed gate the package must ship int8 weights, so the inference-side loader needed validation before betting the milestone on it.
- Evidence: sandbox smoke with the current base package quantized (model.safetensors removed): submissions identical to the fp16 reference on GPU and CPU-only; full-pipeline (bias+rules+SVC) agreement 1999/2000 on train samples.
- Decision: `script.py` loads `hf_model/model.int8.safetensors` (inline dequant loader, fail-loud on mismatch) when present; the fp16 path and the submitted package are untouched.
- Next action: Codec gate 2 unchanged — measure the OOF Macro-F1 delta on the trained large checkpoint before packaging.

### 2026-07-03 - Track B fixed gate passed, with a weak-class caveat

- Why: The A100 fixed-session run was the go/no-go for spending OOF compute on xlm-r-large.
- Evidence: large fixed raw `0.743910` vs base `0.739664` (+0.0042); 2-stage `0.750671` vs `0.744625`. But the exploration cluster regressed on the same split (list_directory 0.505→0.464, read_file 0.604→0.564, grep_search 0.610→0.593) — the screen's read_file gain did not replicate; the aggregate gain comes from other classes. Run: 86 min on A100 (loss stable, no large-model divergence at lr2e-5/batch8).
- Decision: Proceed to 3-fold session OOF with the identical recipe on the already-up A100; promotion is judged only there, where bias/rule/SVC retuning addresses the weak-class mix.
- Next action: aggregate_oof + retune on large OOF logits; then codec gate 2 (OOF delta int8 vs fp16) on a trained large checkpoint.

### 2026-07-03 - Track B gate 1 passed: xlm-r-large screen beats base

- Why: Track B was package-size blocked; the int8 storage codec (`quantize_checkpoint.py`) reopens it only if large actually beats base.
- Evidence: qv600 screen rerun macro-F1 `0.714846` vs XLM-R-base same-recipe screen `0.688651` (+0.026); weak-class cluster improves (read_file 0.475→0.533, plan_task 0.548→0.617). Codec measured on the base checkpoint: 556→280 MB (50.3%), argmax agreement 99.61% on 1024 serializer-real samples, mean relative weight error 0.65%; projected large int8 ~546 MB + sparse SVC fits the 1 GB cap.
- Decision: Reopen Track B conditionally with the int8-codec package plan; supersede the package-size stop of the earlier entry.
- Next action: fixed-session xlm-r-large run, gated on per-epoch Drive checkpointing or an A100 short-wall-clock run; codec gate 2 = OOF Macro-F1 delta measured on the trained large checkpoint.
### 2026-07-04 - Stacker and sparse-hyperparameter headroom ruled out on the ensemble line

- Why: Idle local compute probed whether a learned meta-model or sparse retune could add to the base+large ensemble chain without new GPU runs.
- Evidence: fold-aware stacker (LogReg/HistGB on base+large log-probs + sparse scores) best `0.741618` after bias tuning — below the linear chain `0.746763` and even the base baseline; per-class bias search optimizes macro-F1 directly, which likelihood-based meta-learners do not. Sparse screens at w=2: C=0.02 `-0.0004`, C=0.15 `-0.0013`, char 400k identical to current (min_df=2 vocab never reaches the 220k cap). Artifacts: `20260704_oof_stacker_probe_base_large_sparse.json` + 3 screen rows (commit e46f0fe).
- Decision: Keep the linear blend + rules + sparse SVC (C=0.05) chain as-is; no stacker. This line's remaining headroom is ~0 after the 2.7x OOF-optimism correction — spend idle local time elsewhere (e.g. re-evaluating the ensemble pool when the teammate's focal OOF logits arrive).
- Next action: none on this line; length x focal screens are the active lane.

### 2026-07-04 - Priority pivot: length x focal first, ensemble packaging deferred

- Why: Teammate Slack update (노진산 07-03): focal-γ2.0 single transformer hit Public `0.7477` (their OOF 0.7350; focal line runs OOF→Public +0.013 conservative), +12 rules `0.7499`; their 5-way explorer specialist failed (read_file 0.57 / list_directory 0.51 even alone) — evidence the explorer confusion (~60% of all errors) is missing information at len192, not a modeling problem. Their measured seed variance is ±0.011 (fixed single runs, 0.7249–0.7463).
- Evidence (fairness re-audit of our own length results): the 07-01 len256 runs were confounded (batch 24, 4ep, one lr1e-5); the state_v2 len256/320/384 rejections were confounded by an information-poorer serializer. The only clean pair — 07-03 M1 Track A len256 `0.728402` / len320 `0.735856` raw vs len192 `0.739664` — is a single-seed, cross-day/GPU comparison whose deltas (-0.011/-0.004) sit inside the ±0.011 seed band, with an upward len256→len320 trend (+0.0075) cut off before len384 (mean current_v1 length 261.5 tokens; ~71% truncated at 192). "Length doesn't help" was never established.
- Also: their calibration finding — post-processing/blending increments measured on OOF are ~2.7x optimistic vs Public (rules OOF +0.0066 → Public +0.0022; fixed cross-check +0.0025 predicted it) — downgrades the expected Public gain of our base+large ensemble (+0.0049 OOF) to roughly +0.002.
- Decision: Length extension with focal loss is the top experiment lane (user call, teammate-endorsed); base+large ensemble packaging is deferred, not cancelled. Judge length runs paired within one session/GPU on the explorer-cluster per-class deltas, not single-run aggregates; promote only via OOF.
- Next action: obtain `handoff_jinsan_0703.zip` (Slack, 6.6MB — focal train_transformer.py, their P1 focal 3-fold OOF logits, rules/sparse artifacts); port `--loss focal --focal-gamma 2.0` function-level into our train_transformer.py; then paired fixed screens len192(anchor)/len384/len448 all-focal on one GPU session; winner → 3-fold OOF; their focal OOF logits also join the ensemble pool later. Arm choice per the 2026-07-04 coverage measurement (`experiments/artifacts/20260704_current_v1_token_length_coverage.json`): truncation 192→70.8%, 320→36.1%, 384→10.6%, 448→1.0% (p99=448, max 607) — the old len256/320 arms never actually removed truncation, and the weak classes are the most truncated (lint_or_typecheck 92%, run_tests 89%, web_search 86% at len192), except list_directory (30% — its weakness is not truncation).

### 2026-07-04 - Track B OOF verdict: large alone is marginal, base+large ensemble is the finalist

- Why: All 3 large OOF folds collected (A100 released); the promotion decision belongs to the full OOF tune chain, and with base OOF logits on the identical folds the two-encoder ensemble was free to evaluate.
- Evidence: large single full chain `0.743198` (raw `0.727784` → 2-stage `0.729941` → rules `0.736276` → sparse w4 `0.743198`) vs base baseline `0.741881` — only +0.0013, and the fixed split's +0.006 did not carry over (fixed-is-optimistic confirmed again). Equal-weight softmax ensemble (w=0.5) of base+large OOF logits through the same chain: 2-stage `0.736781` → rules `0.742042` → sparse w2 `0.746763` (+0.0049 over baseline). Every weak class improves vs baseline (read_file +0.009, glob_pattern +0.006, grep_search/web_search +0.005, list_directory +0.005); the fixed split's exploration-cluster regression did not reproduce on OOF. Artifacts: `20260703_oof_ens_xlmr_base_large_w50_*`, `20260703_oof_sparse_svc_plus_rules_ens_xlmr_base_large_w50_sparse_svc.json`.
- Decision: Do not package large as a single-model swap (+0.0013 would likely be eaten by int8 + retune noise). Promote the base+large w0.5 ensemble (rules from the ens chain, sparse weight 2.0, 2-stage bias) as the finalist candidate.
- Next action: (1) final xlm-r-large refit on full training data (new A100; base final model already exists in the package); (2) int8-quantize the large final checkpoint — codec gate 2 = agreement/val delta on the trained large weights; both encoders must be int8 to fit 1 GB (base 280 MB + large ~546 MB + sparse); (3) extend `script.py` to two-encoder softmax averaging with the ens-chain bias/rules/SVC artifacts; (4) mandatory T4 timing smoke (Colab T4, synthetic ~30k test.jsonl) — base+large ≈ 4.2x base FLOPs, the 10-min cap is the main packaging risk; (5) package, offline smoke, submit for calibration. mBERT OOF (probe's best pair) stays a follow-up, not a blocker.
