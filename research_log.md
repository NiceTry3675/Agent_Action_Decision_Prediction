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

### 2026-07-04 - Protocol change: Public-gated promotion, OOF demoted to a tool

- Why: 11 days to deadline at ~1 candidate per 1-2 days was not a path to 0.77. The OOF gate cost 3 GPU runs per candidate while the 10/day Public submission budget sat unused ("calibration only"); the only calibration point shows OOF ≈ Public (+0.0011), fixed is optimistic by ~0.009-0.016, and single-seed fixed noise is ±0.005-0.011 — more local replication does not buy certainty, submissions do. Screens also discarded their weights, so even a winning screen needed a full retrain before it could be submitted.
- Decision (user + advisor): promotion funnel is now quick screen (optional) → fixed session full run with `--save-val-model` (weights written straight to the Drive exchange) → `package_submission.py` → **Public decides**. One variable per submission; Public deltas <0.002 treated as noise; every submission logged in the `leaderboard_calibration.md` ledger. Winners consolidated via `--final-model` full refit. OOF is reserved for ensemble construction, bias/rule tuning, and near-ties. `AGENTS.md`, `leaderboard_calibration.md`, `roadmap/README.md`, `colab/COLAB.md` updated together.
- Infra shipped: `train_transformer.py --save-val-model` (guard refuses the default `model/` output dir), `cloud_sync.py pull-model`, `package_submission.py` (staging → zip-contract check → clean-extraction offline smoke → submission.csv column/ID-order/label validation). Regression: repackaging the current baseline `model/` produced a file list identical to the submitted `submit.zip` and passed the smoke.
- Next action: first loop application — rerun the len448-focal fixed screen with weight saving, encoder-only package (`--no-sparse`), submit for the length hypothesis's first Public read (doubles as fixed↔Public calibration point #2 under the new recipe).
- Timing non-issue (user report, 2026-07-04): every server submission to date finished in under 1 minute on the T4, so the 10-min inference cap is not a live constraint for the rest of the competition — len448 (~2.3x tokens) projects to ~2-3 min, even a base+large two-encoder package (~4.2x FLOPs) stays well inside budget. Supersedes the "mandatory T4 timing smoke" caution in the Track B entry; no routine timing checks needed unless a package changes radically.

### 2026-07-04 - First Public-gated day: transfer coefficient measured, length lane survives

- Why: The new protocol's first three submissions (all encoder-only 80%-data val-models, focal g2.0) were designed as a calibration battery: same-recipe seed pair prices instance-noise transfer, then a len192 anchor isolates the length effect at the decision level.
- Evidence (ledger): len448 s42 fixed `0.748091` → Public `0.735`; len448 s43 fixed `0.735075` → Public `0.714`; len192 s42 fixed `0.744716` → Public `0.725`. (1) Fixed deltas transfer to Public amplified ~1.6x — instance "noise" is real model quality, so screening instances by fixed score is valid and weak draws must be retrained, not submitted. (2) The len192 point sits -0.005 below the len448 transfer line (pure-transfer prediction 0.730, actual 0.725) → len448 holds a genuine ~+0.005 Public edge at equal fixed quality; the pre-registered verdict criterion (above/below 0.730) resolved in favor of length. (3) Recipe-class gap fixed→Public runs -0.013 to -0.021, larger than the baseline stack's -0.0087 (encoder-only + 80% data both cost).
- Decision: Length lane confirmed at the decision level — len448-focal is the working recipe. All three submissions are below baseline Public 0.743, so the lane must now climb via measurable levers: best-of-N instance selection (train replicates, submit only fixed ≥~0.755, which the transfer line projects to Public ~0.746), then full-data refit and the rules/sparse stack on top. Blind refits are a known risk (a refit's instance quality cannot be screened locally); measure the refit delta empirically with one submission before relying on it.
- Next action: seeds 44/45 len448 replicate chain in flight for best-of-N; submit the best instance; then --final-model refit of the winning recipe as its own calibration point.

### 2026-07-04 - Cross-row label leakage discovered and verified; plan pivots to a leak probe + frozen-params lanes

- Why: 11 days out, Public 0.743 vs cutoff 0.772; user call to abandon the M2 hyperparameter resweep (post-processing headroom ~0, resweep EV <= +0.005 = seed noise) and hunt for structural levers instead. A broad re-audit of the data found one.
- Evidence (all verified locally on train, scripts in repo): row ids expose session+step (`...-step_NN`); a later row's history contains earlier rows' `current_prompt` verbatim, and the following `assistant_action.name` equals the earlier row's label. Step arithmetic (last m pairs of step t = steps t-m..t-1) aligned 231,664/231,664 pair-source checks with 0 mismatches. `verify_leak_train.py`: full-train recovery **60,553/70,000 rows (86.5%) with 0 wrong** (positional tier); id-free content-aligned tier (insurance against anonymized ids) 35,629 rows, 0 wrong; subsample robustness p=0.5 → 73.7% coverage / 0 wrong, p=0.2 → 46.8% / 0 wrong. Train-lookup tiers on a 50/50 session holdout: (prompt,last_action) key 0.978 precision, prompt-only 0.918 — both far above model accuracy (~0.74); bare cross-session prompt matching inside the test set measured **0.427** and is excluded by design. Prompt text is not unique (63,257 distinct / 70k rows; ~1/3 of duplicated prompts conflict), so all lookup tiers are conflict-free-only. Rules check: `rule.md` and the competition rules page have no leakage/test-data-usage prohibition (external data explicitly allowed); "code verification of final winners" and a generic cheating clause exist — user accepted the risk and chose to probe.
- Decision: ship a leak-override system in `script.py` (tiers: positional > content-aligned > train (prompt,last_action) > train prompt-only; unmatched rows fall back to the full model stack, downside = 1 submission). `build_leak_lookup.py` builds `model/leak_lookup.json.gz` (3.5 MB, conflict-free maps); `package_submission.py` now stages it fresh from `open/data` on every package (`--no-leak-lookup` to exclude). Probe = repackaged current baseline + overrides, first submission slot 07-05. Gate: Public jump >= +0.01 vs 0.743 → leak works on the server test (jump size ≈ coverage; ~0.02-0.04 with no jump in positional would still indicate lookup-tier-only value); no jump → test is one-row-per-session, drop with no second attempt. The lookup rides along unchanged in every later package so cross-package deltas stay one-variable.
- Also shipped for the parallel lanes (params frozen at proven values: lr 2e-5, 5ep, focal g2.0, cwp 0.5, ls 0.02, replay last1 cap10k w0.5): `current_v2` serializer registered in `script.py` + `train_transformer.py` — priority-ordered fields (current > state > acts > last-action detail > full user↔action pairs newest-first) so len448 right-truncation eats the oldest pairs, and all user utterances kept (current_v1 kept only the last; ~71% of rows truncated at len192 lose the args/results tail). Empirical note backing this direction over hand-crafted cue tokens: surface features (file extension / glob chars in prompt) carry almost no class signal in the 4-way cluster, while context (last action, turn_index) shifts the distribution strongly (turn0 → list 0.37; after run_tests → grep 0.51) — the signal is contextual, so preserve context, don't annotate surfaces.
- Next action: (1) 07-05 slot 1 — leak probe (`package_submission.py --out leak_probe_0705.zip` on the baseline model dir, offline smoke first); (2) Lane L unchanged — finish seeds 44/45 len448 best-of-N, submit best instance with full stack; (3) Lane B — fixed screen of `--serializer current_v2` at len448 using the 20260703_170302 train_command with only the serializer swapped (one variable); (4) Lane A — int8 base+large packaging per the Track B entry. If the probe jumps, all lanes re-read as fallback-quality work; if not, Lane L/B/A math is 0.743 + L(+0.005~0.010) + A(+0.004) + B(+0~0.010).

### 2026-07-04 - Leak probe Public 0.710 (-0.033): server test lacks train's cross-row structure; G lane dropped, overrides disabled by default

- Why: pre-registered G-lane probe — the baseline stack (Public 0.743) repackaged with the leak-override system as the only variable; the gate was "no jump → drop with no second attempt".
- Evidence: Public **0.710**, a -0.033 drop — worse than the no-jump scenario; the overrides actively overwrote correct model predictions. Accuracy algebra c·(p−0.74) ≈ −0.033 (c = override coverage, p = server-side precision): any plausible coverage puts p at ~0.6-0.7, catastrophically below every train-side measurement (positional 60,553/60,553 wrong=0, holdout lookups 0.978/0.918). The closest train-side analog to the observed behavior is the bare cross-session prompt-match precision 0.427 that was excluded by design.
- Interpretation: the public test set does not inherit the train dump's cross-row structure — ids/histories and prompt→action conditionals were evidently resampled or sanitized (separate simulator batch and/or organizer-side leak plugging). The within-train 50/50 holdout overestimated transfer because both halves share a generation batch. Lesson recorded: any lever keyed on verbatim text identity must be priced with a cross-batch estimate, not a within-train holdout — this simulator resamples conditionals across batches.
- Decision: G dropped for good (as pre-registered; a drop is stronger evidence than no jump). `script.py` now runs NO override tier unless `model/leak_lookup.json.gz` is packaged — the file gates positional/aligned too, since the damage cannot be attributed between tiers; `package_submission.py` default flipped to exclude (`--leak-lookup` opt-in). Local smoke confirms the disabled path ("Leak overrides disabled"). All future packages are leak-free and directly comparable to 0.743.
- Free diagnostic (optional): the server run printed per-tier counts to stdout ("Leak overrides: total=... positional=... train_prompt_last=..."); if Dacon exposes submission execution logs, that one line decomposes the damage and reveals whether the test contains multi-row sessions at all. Zero submission cost.
- Next action: back to the frozen-params lanes — L (seeds 44/45 len448 best-of-N in flight; submit best fixed ≥~0.755 with full stack), A (int8 base+large ensemble), B (current_v2 fixed screen). Arithmetic unchanged: 0.743 + L(+0.005~0.010) + A(~+0.004) + B(+0~0.010) ≈ 0.754~0.767; 0.77 needs L's upper end plus B landing.

### 2026-07-04 - Doctrine: sub-0.02 deltas do not steer; categorical shift begins

- Why: 1st place updated to 0.790 (cut 0.772; ours 0.743). Every lever measured this cycle — length +0.005, ensemble +0.003 (paired local), rules +0.002, large ≈ 0, refit unmeasured but same class — lives inside the ±0.02 band that instance draws alone produce on Public (0.735 vs 0.714, same recipe). Stacking sub-0.02 levers cannot close +0.03-0.05.
- Decision (user): Public differences under 0.02 are treated as error-range for direction-setting; sub-0.02 levers are consolidation garnish, not research lanes (AGENTS.md updated). The ceiling pack — large len448 focal 100% refit + rules + sparse, single encoder (ensemble dropped as sub-noise; base refit cancelled) — closes this family's chapter with one submission; research shifts to categorical changes.
- Constraint discovered: real test inputs exist ONLY on the eval server (local test.jsonl is a 5-row stub; rule.md "data/ automatically created") — test-set pseudo-labeling/self-distillation is infeasible; any test-time technique must ship inside script.py and run within T4/10min.
- Next action: ceiling pack lands (~3h), then categorical lane selection: small decoder LLM (LoRA→int4, 1GB cap allows ~1.5B) vs kNN-retrieval prior over shipped train embeddings vs serializer redesign.

### 2026-07-04 - LLM lane designed, parked as future plan; encoder family gets a fair re-screen

- Why: The decoder-LLM lane plan (Qwen2.5-0.5B primary via the existing SequenceClassification pipeline — 2-line patch confirmed; Qwen3-0.6B/EXAONE-4.0-1.2B as scale-ups) hit a server constraint the user caught: transformers==4.46.3 is PREINSTALLED on the eval server and rule.md warns overriding preinstalled versions "may cause installation errors" — Qwen3 (>=4.51) and EXAONE-4.0 (>=4.54) are therefore env gambles. Mitigations recorded for later: install failures do NOT consume submission slots (free env probe), and EXAONE-4.0-1.2B fits the 1GB cap via mixed quantization (int4 body ~0.55GB + our int8 codec on embeddings ~0.21GB ≈ 0.76GB) at the cost of new quant deps + custom loader + research-license risk.
- Decision (user): park the LLM lane as the future plan; first run a PROPER re-screen of the alternative encoders that were all dismissed under unfair conditions (len192, CE, no-replay, 1-epoch qv600): mdeberta-v3-base (0.6649 — likely lr-collapsed; DeBERTa-v3 canonical lr is ~1e-5, we screened at 2e-5), bert-base-multilingual-cased (0.6976, best large-pairing diversity), kakaobank/kf-deberta-base (0.6945, ask_user 0.507 Korean-diversity signal).
- Method: champion recipe fixed screens — len448, focal g2.0, replay last1 cap10000, seed 42, --save-val-model (submittable weights per protocol); mdeberta at lr 1e-5 (its canonical), others at 2e-5; chained on lane A behind the in-flight large refit. Anchors: base448 fixed band raw 0.728-0.750 / 2stage 0.735-0.755.
- Next action: chain lands -> compare vs anchors + weak classes; any arm inside the base448 band is an ensemble-diversity candidate, anything above it is a swap candidate; Public per protocol.

### 2026-07-04 - M6 mid-sweep: decoder family signal found (Qwen ep3 salvage), mdeberta finally closed

- Why: Both L4 lanes died simultaneously mid-sweep (account-level event, not preemption — synchronized heartbeat stop). The per-epoch Drive checkpoints (same-day patch) salvaged mdeberta at epoch 4/5 and Qwen2.5-0.5B at epoch 3/5; both were evaluated locally on the fixed val split without any GPU retraining (checkpoint -> local eval workflow, ~10 min each).
- Evidence: mdeberta-v3-base at canonical lr 1e-5, epoch 4/5: raw 0.702023 / 2stage 0.721257 — the lr hypothesis was half-right (no collapse: 0.665 -> 0.702) but still -0.026 below the base448 band floor; the final epoch cannot close that. Qwen2.5-0.5B (decoder, seq-cls head, champion recipe) at epoch 3/5: raw 0.747362 (band top) / 2stage 0.774220 — +0.0175 over the champion large448 instance (0.756718) with 2 epochs still to go; weak classes broadly improved (list_directory 0.5036, ask_user 0.6755). int8 codec verified on Qwen arch: 99.61% argmax agreement, 988->496 MB.
- Decision: mdeberta permanently closed (fair conditions, no signal). The decoder family is the first doctrine-grade (0.02-class) signal of the competition; ep3 snapshot packaged encoder-only (`m6_qwen05b_ep3.zip`, val 2stage bias in meta) and submitted for the Public transfer read. Caveat logged: the 2stage bias contribution (+0.027 over raw) is unusually large, so the fixed->Public gap may exceed the encoder band (-0.013~-0.021).
- Next action: lane A reruns Qwen full 5 epochs; lane B runs deberta-v3-base(EN) -> kf-deberta; Public score of the ep3 pack decides whether the decoder lane becomes the main line (then: best-of-N instances, full refit, rules/stack on decoder logits, and the free transformers-bump install probe to unlock Qwen3-0.6B/EXAONE).

### 2026-07-05 - M7 Qwen3-0.6B refit lands Public 0.780: first categorical jump, decoder line is now the baseline

- Why: Qwen2.5-0.5B's refit (Public 0.770) confirmed the decoder+refit pattern; Qwen3-0.6B screened higher (fixed 2stage 0.770875 at len416, zero truncation) and unlocked via the free transformers>=4.51 env-probe (no submission-slot cost on install). This run also switched from val-tuned to a genuine 3-fold session_oof bias/rule tune — first time this cycle the promotion protocol's OOF-as-tool step was applied to a decoder finalist before packaging.
- Evidence: 3-fold OOF (70,000 rows, 0 duplicate ids) raw macro_f1 0.755929 -> 2-stage bias 0.758499 -> +12 rule boosts 0.767129 (`experiments/artifacts/m7_qwen3_oof_len416_ep3_oof_metrics.json`, `..._rules_rule_boosts.json`). Full-data ep3 refit + injected OOF bias/rules, int8-quantized (1192.2 MB fp16 -> 598.2 MB, 50.18%), packaged encoder-only with `requirements_qwen3.txt` (transformers>=4.51,<4.52 override); offline smoke passed under both GPU and CPU-only PYTHONPATH-overlay rehearsal. Public: **0.780** (+0.037 vs the old XLM-R baseline 0.743, +0.010 vs the Qwen2.5-0.5B refit 0.770) — the first Public delta this cycle clearing the 0.02 categorical-shift doctrine threshold.
- Caveat: server inference measured at 8:50/10:00 — tighter than M6's 8:31 despite the length-sorted batching optimization (len416 vs len448 is a wash; Qwen3's architecture appears marginally slower per token than Qwen2.5 at this size). No ensemble legroom on this pack without further inference optimization (e.g. wider synthetic-batch tuning, or dropping to a shorter max_length) before adding a second encoder leg.
- Decision: `final_summary.md`, `leaderboard_calibration.md` baseline updated to this pack (0.780). Decoder-family line is now the working baseline, superseding the encoder-only XLM-R line entirely.
- Next action: lane A released (idle, no immediate follow-up queued); lane B continues the teammate's P2 request (xlm-r-large len384 3-fold OOF) independently. Future decoder-line work (scale-up size wall at int8 ~1GB cap, generative-head redesign) parked per the 2026-07-04 advisor discussion, not scheduled.

### 2026-07-05 - P2 (팀 요청) 완료: large len384 3-fold OOF 집계 + rules 재튜닝

- Why: `ROADMAP_0704.md`의 팀 요청(P2) — 팀 우승 레시피(xlm-roberta-large, len384, focal g2.0, batch4×grad-accum4, replay last1)를 3-fold session_oof로 재학습해 rules/bias/블렌드 재튜닝용 로짓을 제공. 우리 레인 B(A100)에서 kf-deberta 재검증 뒤 이어서 실행.
- Evidence: 폴드별 fixed 2stage 0.757733 / 0.733937 / 0.728930 — 팀이 보고한 시드분산 ±0.011(0.762~0.784 범위)과 같은 크기의 폴드간 편차(±0.014) 확인. 3폴드 합산 OOF: raw 0.740118 → 2stage bias 0.742941/0.743679(fine) → +12 rules 0.749623 (`experiments/artifacts/p2_oof_large384_focal_ep5_oof_metrics.json`, `..._rules_rule_boosts.json`).
- 참고: sparse SVC 재튜닝은 생략 — 팀이 자체 분해실험에서 이미 "SVC 기여 사실상 0 (+33초 낭비)"로 결론지었고, 로컬 WSL 메모리가 빠듯해(무거운 로컬 작업 금지 정책) 불필요한 재검증을 피함.
- Decision: OOF 로짓 + rule_boosts 아티팩트를 팀에 전달(공유 산출물, Dacon 제출과 무관). 우리 레인 자체 파이프라인에는 반영하지 않음 — 이 레시피는 팀 소유 recipe이고 우리는 별도 디코더 라인(M7 Qwen3-0.6B, Public 0.780)을 메인으로 유지.
- Next action: 레인 B는 이 체인으로 완료, 추가 요청 없으면 유휴 전환 대기.

### 2026-07-05 - M8 Qwen3.5 T4 timing probe: fallback path is very slow; keep G4 screen through Public probe

- Why: `qwen-gleaming-waterfall.md` requires a same-T4 timing ratio before refit/package because Qwen3-0.6B already used 8:50/10:00 on the server and Qwen3.5's DeltaNet path may fall back to slow PyTorch kernels without `fla`/`causal_conv1d`.
- Evidence: On a Tesla T4, same 4,096-row train sample, same `current_v1` serializer, sorted-batch inference, fp16 random sequence-classification heads: Qwen3-0.6B len416 batch64 infer `76.42s`; Qwen3.5-0.8B len400 batch64 infer `200.78s` (`2.63x`, server-time projection `~23.2 min`). Batch/length rescue grid did not recover it: best measured Qwen3.5 setting was len336 batch64, infer `182.24s` (`2.38x`, projection `~21.1 min`). Artifacts: `experiments/artifacts/m8_qwen35_t4_timing_probe.json`, `experiments/artifacts/m8_qwen35_t4_batch_grid.json`.
- Interpretation: The slowdown is not token length (Qwen3.5 averaged fewer tokens than Qwen3 on the probe). The likely cause is the transformers warning that the Qwen3.5 fast path is unavailable and the model is using the PyTorch fallback for the hybrid/DeltaNet blocks.
- Decision: Timing gate is red as a local risk signal, but do **not** kill the lane solely from the probe. User call: keep the G4 screen running and only let actual packaging/Public behavior decide whether this community conversion is usable. Treat timing as a required warning to revisit before final refit/package, not as a hard stop for the current screen.
- Next action: Finish the in-flight G4 fixed screen (`m8_qwen35_08b_screen`, len400, 5ep with ep3 checkpoint preserved), compare ep3 vs ep5 fixed metrics, then decide whether to spend OOF/refit/submit effort despite the timing risk.

### 2026-07-05 - M8 fla-core T4 reprobe: correctness passes, fast path still unavailable, timing remains RED

- Why: The first M8 T4 probe was red because Qwen3.5 used the slow PyTorch fallback. `m8_fla_t4_reprobe_spec.md` asked for a free T4 reprobe with `fla-core` installed before deciding whether the packaging timing risk is intrinsic or just missing optional kernels.
- Evidence: `fla-core` installed cleanly in `3.3s` (`0.5.1`, Triton `3.6.0`), but `flash-linear-attention` was still absent and transformers kept printing the same fast-path-unavailable warning. Fixed-head fallback-vs-post-install logits on 256 rows were numerically close enough for the spec gate: argmax agreement `1.000`, max|diff| `0.0264`. Timing did not improve: same-session Qwen3.5/Qwen3 tokenize+infer ratio `3.59x`, projected server time `~31.7 min`; verdict `RED_timing_failed`. Artifact: `experiments/artifacts/m8_qwen35_t4_fla_reprobe.json`.
- Decision: Do not modify `requirements_qwen35.txt`; `fla-core` alone is not a viable packaging dependency for this model on T4. The package remains timing-blocked unless a different kernel package/version path is explicitly chosen later. Per user instruction, the in-flight G4 screen still continues and its model-quality signal will be collected.
- Next action: Keep main G4 run alive through epoch 5, use the preserved ep3 checkpoint (`m8_qwen35_08b_ep3_ckpt`) plus final ep5 artifact for fixed-score comparison, then decide whether any Public probe is worth the known timing risk.

### 2026-07-05 - M8 causal-conv1d T4 probe: no usable wheel in Colab/server-like env

- Why: User asked whether `causal-conv1d` was worth trying after `fla-core` alone left the fast-path warning unchanged. The question matters because Qwen3.5's warning names both optional packages, but source builds are risky under the server's 10-minute pip-install cap.
- Evidence: On the same Colab T4 lane, `pip install causal-conv1d` attempted a source build and failed after `148s` with wheel-build failure. A follow-up `--only-binary=:all:` dry-run over versions `1.6.2.post1`, `1.6.1`, `1.6.0`, `1.5.3.post1`, `1.5.2`, `1.5.0.post8`, and `1.4.0` found no matching binary wheels for the runtime. Artifacts: `experiments/artifacts/m8_qwen35_t4_causal_reprobe.json`, `experiments/artifacts/m8_causal_conv1d_wheel_probe.json`.
- Decision: `causal-conv1d` is not a viable packaging dependency in the current Python 3.12 / torch 2.11 Colab-T4 environment. Do not add it to `requirements_qwen35.txt`; any future attempt would need a different runtime/package source and should be treated as a separate infra gamble, not a quick M8 unlock.
- Next action: No more b-lane timing probes unless the user supplies a concrete alternate wheel/index/version. Continue collecting the G4 screen quality signal.

### 2026-07-05 - M8 Qwen3.5 fixed screen: ep3 passes quality gate, ep5 overfits; OOF starts at E=3

- Why: Phase 1 required a fixed-session screen of Qwen3.5-0.8B, with ep3 vs ep5 compared before spending OOF/refit compute.
- Evidence: G4 fixed screen at len400, champion decoder recipe, seed42: ep5 raw `0.746547` / old-bias `0.753819` / 2stage `0.755271`, below the Qwen3-0.6B gate band. The preserved ep3 checkpoint evaluated on the same fixed split scored raw `0.769643` / old-bias `0.779071` / 2stage `0.780427`, clearing the `~0.771` gate. Weak classes at ep3: `list_directory 0.5179`, `grep_search 0.6144`, `read_file 0.6213`, `glob_pattern 0.6418`, `ask_user 0.6765`. Artifacts: `experiments/artifacts/20260705_044047_gpu_transformer_session_current_v1_len400_replay-last1_m8_qwen35_08b_screen_metrics.json`, `experiments/artifacts/20260705_044826_gpu_transformer_session_current_v1_len400_replay-last1_m8_qwen35_08b_ep3_eval_metrics.json`.
- Decision: Select epoch `E=3`; ep5 is rejected as overfit. Despite the T4 timing RED, quality gate is green, so continue the pre-registered M7-style OOF tool step before deciding whether any Public probe is worth the packaging risk.
- Next action: Run 3-fold session OOF at ep3/len400; fold0 launched on the G4 lane.

### 2026-07-05 - M8 server-stack fla+causal T4 replica probe: imports pass, Triton compile fails

- Why: `m8_fla_t4_reprobe_spec.md` v2 re-opened the timing question after rule.md clarified the server stack: T4, Python 3.11, torch 2.7.1+cu128, transformers override allowed, and a causal-conv1d prebuilt wheel exists. The goal was to distinguish "missing optional packages" from "FLA kernels unusable on T4/server stack".
- Evidence: Lane C reproduced the stack closely on Colab T4 with Python 3.12, torch `2.7.1+cu128`, Triton `3.3.1`, transformers `5.13.0`, `causal_conv1d 1.6.2.post1`, and `fla-core` candidates `0.5.1`, `0.5.0`, `0.4.0`. Harness retries: r1 had a repo-root import bug, r2 exposed stale Colab `torchvision` after torch downgrade, and r3 showed why `fla-core --force-reinstall` must use `--no-deps` (it upgraded torch to 2.12). The corrected r4 kept torch fixed and verified `import fla, causal_conv1d` succeeds, but every `fla-core` candidate failed on the first Qwen3.5 fast-path forward inside `fla.ops.gated_delta_rule` Triton compilation with `RuntimeError: PassManager::run failed`. Artifact: `experiments/artifacts/m8_qwen35_t4_replica_probe.json` (r4 mirrored; raw r4 at `..._r4.json`).
- Decision: RED at fast-path compile/setup, before correctness or timing. Do not add `fla-core` or `causal-conv1d` to `requirements_qwen35.txt`; direct Qwen3.5 deployment remains timing-blocked. Further T4 kernel work needs a concrete FLA/Triton workaround, not another blind version probe.
- Next action: Release lane C. Continue the G4 OOF quality run only as a model-quality read; packaging/Public remains blocked unless a new inference path is approved.

### 2026-07-05 - 독트린 개정: sub-0.02 레버를 1급 레인으로 승격 (상황 변화 반영)

- Why: 2026-07-04 독트린("sub-0.02은 consolidation garnish, research lane 아님")은 0.743에서 범주적 도약만이 컷 도달 경로였던 시점의 자원배분 규칙. 현재 Public 0.780으로 9등 — 예선 통과 기준에 도달했고 +0.01이면 1등과 동률. 작은 점수의 한계 가치가 구조적으로 바뀜 (사용자 판단 2026-07-05).
- 유지되는 것: 통계 코어는 불변 — Public 단발 델타 <0.002는 측정 노이즈, ~0.02 미만은 단독으로 방향 증거가 아님(인스턴스 드로우 스윙). 리더보드가 덜 노이즈해진 것이 아니라 목표 구조가 바뀐 것.
- 개정되는 것: (a) sub-0.02 레버(+0.005~0.02)는 1급 레인 — 더 이상 장식으로 강등하지 않음. (b) 소레버의 레시피 채택 판정은 OOF(70k행) 기준 — 단발 Public 델타로는 레시피 효과와 인스턴스 드로우를 분리할 수 없고 슬롯도 소모됨. (c) 검증된 소레버는 스택해 통합 팩으로 제출. (d) 범주적 레버(0.02+)는 여전히 최우선 탐색 대상.
- 실무 영향: M8 Qwen3.5(현재 증거 +0.01급, 약클래스 집중)는 garnish가 아닌 우선 레인 — 타이밍 벽 인프라 투자(서버 복제 프로브, 필요시 compile/캐시 동봉)의 기대값이 정당화됨. 직접 배포가 막히면 증류(0.8B teacher → 0.6B student, +0.005~0.01급)도 추진 가치. 파킹됐던 앙상블 다양성 레그(mbert, deberta-v3 EN)와 0.6B 추론 가속(앙상블 언락의 전제)도 재개 후보로 복귀.
- 같은 날 추가 확인 (사용자): **Public = 최종 점수 100%, private 홀드아웃 없음** (rule.md에 없음 → leaderboard_calibration.md에 기록). "public 과적합" 논거는 소멸 — Public은 추정치가 아니라 타깃 자체. 귀결: **best-of-N 인스턴스 선택이 정당 전략으로 승격** — 같은 레시피 멀티시드 refit 중 Public 최고 인스턴스를 최종 제출로 선택하면 인스턴스 분산(±0.01급)을 순서통계로 회수(3시드 best는 평균 대비 대략 +0.008~0.012 기대). 제약은 슬롯 예산(10/day)뿐이며, OOF/fixed는 슬롯을 쓸 후보를 거르는 사전 필터.
- Next action: AGENTS.md 승격 규칙 3항 문구 갱신 완료(public=final 반영). M8 OOF + 서버 복제 프로브 결과가 나오면 새 독트린 하에서 경로 확정.
- 우선순위 (사용자, 마감 9일 전 시점): best-of-N 시드 하베스트는 **종반 전략으로 보류** — 미확인 대형 레버가 남아 있는 동안은 탐색이 우선. 활성 레인: (1) M8 Qwen3.5, (2) 팀원 할당 피처 엔지니어링 — 학습 기반 피처 셀렉션 + 수치 메타 구간화(예: `elapsed_session_sec` raw 초 → 범주 토큰). 참고: 구간화 선례가 script.py sparse 경로에 이미 존재(`bin_numeric` elapsed fresh/short/mid/long, L447)하나 챔피언 current_v1 직렬화(L171)는 raw 초를 그대로 사용 — 트랜스포머 입력 표현에는 미적용 상태.

### 2026-07-05 - M8 Qwen3.5 OOF quality lands, but direct deployment remains blocked

- Why: After the fixed screen selected Qwen3.5 ep3, the M7-style 3-fold OOF step was needed to decide whether the model-quality signal is real enough to justify downstream work.
- Evidence: 3-fold session OOF at len400/ep3 was stable across folds: fold0 2stage `0.770064`, fold1 `0.769374`, fold2 `0.768901`. Aggregate OOF: raw `0.766602` -> 2stage `0.767849`; OOF rule boosts reached `0.774046` with 12 rules. Weak classes after rules: `list_directory 0.5124`, `read_file 0.6104`, `grep_search 0.6194`, `ask_user 0.6600`, `glob_pattern 0.6650`. Artifacts: `experiments/artifacts/m8_qwen35_oof_len400_ep3_oof_metrics.json`, `experiments/artifacts/m8_qwen35_oof_len400_ep3_rules_rule_boosts.json`.
- Interpretation: Quality is real and above the M7 Qwen3 OOF/rules reference (`0.767129`), but the C-lane server-stack kernel probe failed before correctness/timing: `fla`+`causal_conv1d` imports work, yet the first Qwen3.5 fast-path forward fails Triton compilation on T4. The old fallback path is far over the 10-minute server budget.
- Decision: Do not refit/package Qwen3.5 for direct Public submission under the current inference path. Keep Qwen3.5 as a teacher/quality source; next viable routes are distillation into Qwen3-0.6B or a concrete FLA/Triton workaround, not another direct T4 package attempt.
- Next action: Release G4. Resume with either distillation design or the separate feature-engineering lane, depending on user priority.

### 2026-07-05 - M8 compile 레인 개시: torch.compile 폴백 가속 프로브 런치 (레인 C)

- Why: fla fast path는 SM75에서 컴파일 단계 사망 확정(PassManager::run failed, 전 버전) — 남은 직접 배포 경로는 PyTorch 폴백 자체의 가속뿐. 사용자 판단: "종반에는 시간이 없어 못 한다, 1등 하려면 지금 뚫어야 한다" — compile 레인 즉시 개시, 이번 집도는 메인 세션이 직접.
- 설계: `colab/m8_qwen35_compile_probe.py` — 서버 복제 스택(torch 2.7.1+cu128, transformers 5.13, **fla/causal-conv1d 명시적 제거**)에서 폴백 경로를 torch.compile로 가속. 변형 3종: `cudagraph_buckets`(reduce-overhead + 버킷 패딩 {256,400} — 주력, 폴백의 커널런치 오버헤드를 CUDA graphs로 제거), `default_buckets`, `dynamic`. 각 변형을 별도 서브프로세스로 격리, 콜드 컴파일 시간 명시 측정, 최고 변형은 동일 인덕터 캐시로 재실행해 캐시 동봉 시나리오(warm) 실측.
- 게이트: projected = ratio×530 + compile_wall. GREEN ≤510s(서버에서 콜드 컴파일해도 통과), YELLOW ≤600s(캐시 동봉 트릭 검토), RED >600s(직접 배포 최종 차단 → 증류 경로만 잔존). 정합성 게이트(폴백 eager 로짓과 argmax 일치 ≥99.5%)를 타이밍보다 먼저 통과해야 유효.
- 실행: 레인 C 신규 T4, 런치 pid 3341 (`run_20260705_062813.log`). 아티팩트: `experiments/artifacts/m8_qwen35_compile_probe*.json` (자동 collect → pull).
- Next action: 프로브 완료 시 판정 기록. GREEN/YELLOW면 script.py compile 통합 + 캐시 동봉 설계로 진행, RED면 증류 스펙 착수.

