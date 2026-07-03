# Milestone 1 — Length ablation + xlm-r-large scouting

**Dates**: 07-03 → 07-05. **Status**: completed 2026-07-03.
See `README.md` for context (key findings, guardrails, dead ends) — not
repeated here.

## Outcome

- Track A Phase 0 adopted a small OOF-only improvement: rule regrid
  `0.740149` plus current_v1 sparse SVC weight `2.5` reached OOF
  `0.743183` (`20260703_sparse_weight_wide_regrid_after_rule_regrid`).
  compact_events (`0.742709`) and Markov prior (`0.740259`) did not beat it.
- Track A length ablation failed the fixed-session gate: len256 raw
  `0.728402`, len320 raw `0.735856`, both below the required `0.742664`
  and without the required weak-class gains. No length OOF was run.
- Track B stopped as package-size blocked: fp16 xlm-roberta-large HF model
  plus tokenizer measured `1089.108 MB`; the attempted quick-val run was
  lost with a stale Colab daemon and produced no collected result.
- No Dacon submission was made.

Two tracks run concurrently: **Track A** (main line, current_v1 length
ablation on XLM-R-base) and **Track B** (xlm-roberta-large early scouting,
run in parallel per explicit request). Track A is the priority; Track B is
opportunistic and must not block Track A.

## Track A — `max_length` ablation (headline experiment)

### Phase 0 (today, CPU-only, runs in the background of everything else)

Zero-retrain logit-reuse sweep over already-saved OOF artifacts
(`experiments/artifacts/20260702_oof_xlmr_base_len192_ep5_replay_last1_cap10000_oof_metrics.json`
+ matching rule-boosts artifact):

1. **Wider/finer sparse-SVC weight grid**:
   ```
   .venv/bin/python tune_sparse_svc_oof.py \
     --oof-artifact experiments/artifacts/20260702_oof_xlmr_base_len192_ep5_replay_last1_cap10000_oof_metrics.json \
     --rule-artifact experiments/artifacts/20260702_oof_rule_boosts_xlmr_base_len192_ep5_replay_last1_cap10000_rule_boosts.json \
     --text-serializer current_v1 --weights "0.5,1,1.5,2,2.5,3,3.5,3.75,4,4.25,4.5,4.75,5,5.5,6,6.5,7,8,9,10" \
     --tune-bias --experiment-id 20260703_sparse_weight_wide_regrid \
     --notes "Phase0: wide/fine sparse SVC weight regrid, shipped weight=4.0 came from an ad-hoc sweep outside this tool's own 0-1.0 default grid"
   ```
2. **Rule-boost regrid**:
   ```
   .venv/bin/python tune_oof_rule_boosts.py \
     --oof-artifact experiments/artifacts/20260702_oof_xlmr_base_len192_ep5_replay_last1_cap10000_oof_metrics.json \
     --max-rules 20 \
     --boost-values "-0.8,-0.6,-0.4,-0.3,-0.2,-0.1,0.1,0.2,0.3,0.4,0.6,0.8" \
     --experiment-id 20260703_rule_boosts_wide_regrid_max20
   ```
   If this beats `0.735673`, re-run step 1's sparse sweep against the new
   rule artifact (they interact).
3. **`compact_events_v1` as a second sparse channel** vs. the current XLM-R
   OOF base (previously only tested vs. weaker DistilBERT OOF, scored a
   competitive 0.741169 there): `tune_sparse_svc_oof.py --text-serializer
   compact_events_v1 --weights "0.5,1,...,8" ...`. Joint two-channel
   blending (current_v1-text + compact_events_v1-text sparse scores) needs a
   short one-off script — lowest priority, only if time allows.
4. **Markov prior retry on XLM-R OOF** (`tune_markov_prior_oof.py`,
   previously only tested vs. weaker DistilBERT logits, +0.0009 there —
   modest expectations here).

**Gate**: adopt whichever combination improves 2-stage OOF over `0.741881`.

### Step 1 — parallel fixed-session length screens

One variable (length) changed, everything else pinned to the proven recipe.

Local (RTX 3070 Ti):
```
.venv/bin/python train_transformer.py --device cuda \
  --base-model xlm-roberta-base --serializer current_v1 --max-length 256 \
  --epochs 5 --batch-size 16 --eval-batch-size 64 --lr 2e-5 \
  --class-weight-power 0.5 --label-smoothing 0.02 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 \
  --experiment-suffix len256_ep5_replay_last1_fixed \
  --notes "M1 TrackA: clean current_v1 length ablation len256 vs len192 replay fixed raw 0.739664"
```

Colab (`colab/cloud_sync.py push`, then `colab/cloud_sync.py cmd "python
colab/vm_agent.py launch train_transformer.py '<same args, --max-length
320>'"`, poll with `colab/cloud_sync.py hb`, pull when done) — same run,
simultaneously. If Colab OOMs at longer length, use
`--gradient-checkpointing`, not a smaller batch size (keep the comparison
clean).

**Gate per candidate**: fixed-session raw macro-F1 beats `0.739664` by
**≥+0.003**, and `list_directory`/`read_file`/`grep_search`/`glob_pattern`
F1 specifically move up (per `AGENTS.md`'s weak-class reporting rule) — if
aggregate improves but those four don't, treat the signal as untrustworthy.
Log wall-clock and confirm projected 30k-row inference time stays
comfortably under 10 min.

**Branch**:
- len256 & len320 both clear, len320 better → try len384 (diminishing-
  returns check), take best forward.
- len256 clears, len320 doesn't → stop, take len256.
- Both fail → truncation-relief hypothesis falsified for this recipe;
  record in `research_log.md`, fall back to len192, move into M2 a day
  early.

### Step 2 — promote the single winning length to 3-fold session_oof

`--split session_oof --fold-id 0|1|2`, same recipe. Confirm exact filenames
in `experiments/logits/` before aggregating (timestamp-prefixed, don't
hardcode a guessed glob), then:
```
.venv/bin/python aggregate_oof.py --pattern "..." --experiment-id ...
```

### Step 3 — rebuild the tuning chain from scratch

On the new OOF base (`tune_oof_rule_boosts.py` then `tune_sparse_svc_oof.py`
against the new `*_oof_metrics.json` — rules/weight are specific to a given
logit source's error pattern, not transferable from the len192 artifacts).

**Final gate**: new 2-stage OOF beats `0.741881` by ≥+0.003 to become the
new candidate baseline. If it clears comfortably, spend one calibration
submission here to re-anchor the OOF→Public gap early (only one such data
point currently exists — see `milestone_4_consolidation.md`).

## Track B — xlm-roberta-large early scouting (parallel, opportunistic)

**Why scout this now instead of deferring to M3**: xlm-roberta-large is the
*same architecture family* as xlm-roberta-base, which is the current
best-performing model — a scale-up of a proven recipe, not an untested
architecture. That's a materially different risk profile from
`xlm-align-base`/`infoxlm-base` (different pretraining checkpoints
entirely, which collapsed under the default recipe — plausibly an
LR/warmup mismatch specific to those checkpoints' head initialization, not
evidence that "big multilingual encoders don't work here"). Scouting is
cheap and informative even if the full commitment later isn't taken.
Recommendation: **scout now (Steps 1-2, near-zero cost), but gate the
expensive full commitment (Steps 3-4) on scouting results** rather than
committing multi-hour A100 time blind.

### Step 1 (near-zero cost, do today)

Real package-size measurement: instantiate `xlm-roberta-large` via
`AutoModelForSequenceClassification`, save fp16, measure actual on-disk
size (don't rely on the README's parameter-count estimate — confirm it). If
fp16-alone already exceeds ~900MB, note that shipping it will require
either dropping the sparse SVC ensemble entirely, or int8/dynamic
quantization (added engineering risk + possible accuracy loss) — flag this
explicitly before investing further.

### Step 2 (cheap, ~15-20 min)

Squeeze into whichever GPU lane frees up first during Track A's Step 1.
Quick-val screen:
```
.venv/bin/python train_transformer.py --device cuda \
  --base-model xlm-roberta-large --serializer current_v1 --max-length 192 \
  --quick-val-size 600 --epochs 1 --batch-size 8 \
  --experiment-suffix xlmr_large_qv600_screen \
  --notes "M1 TrackB: xlm-roberta-large quick-val screen, same recipe as working xlm-r-base screen"
```
(same recipe as the working xlm-r-base screen, just swap `--base-model`;
use a smaller `--batch-size`, e.g. 8, if it doesn't fit at 16 given the
larger model).

**Gate**: does it collapse like xlm-align/infoxlm (near-zero macro-F1, mode
collapse to one class), or does it land in a reasonable range (roughly
comparable to or above xlm-roberta-base's own qv600 baseline of 0.6887)? If
it collapses, xlm-r-large is dead — record as a dead end and stop (don't
burn A100 hours debugging LR/warmup for it under this timeline). If
healthy, proceed.

### Step 3 (conditional on Steps 1-2 passing, real engineering task)

Minimal per-epoch checkpointing: `train_transformer.py` currently has no
mid-run save/resume. Before committing multi-hour A100 time, add a small
save-after-each-epoch mechanism (model + optimizer/scheduler state + epoch
number to a path that survives a Colab recycle, e.g. synced via the
existing Drive exchange) and a way to resume from it. Scope this narrowly —
it only needs to survive one long run, not become general infrastructure.
`colab/COLAB.md`'s own failure-mode table already flags this as a
prerequisite for multi-hour large-model runs; don't skip it just because
the deadline is tight — losing a full A100 run to a recycle costs more time
than building the safety net.

### Step 4 (full commitment, runs during M2)

Fixed-session `xlm-roberta-large` + current_v1 + M1's winning length, on
Colab A100, timed to run *while M2's hyperparameter resweep uses local
quick-val + occasional Colab confirmation runs* — this reuses the existing
local+Colab 2-lane setup rather than needing a third lane.

**Gate**: beat the best XLM-R-base fixed-session candidate from Track A. If
yes, promote to 3-fold OOF (expensive — 3x fixed-session cost on A100).

**Reminder**: if xlm-r-large ends up winning, it must *replace*
xlm-roberta-base in the final package (not add to an ensemble) under the
1GB cap — this changes M3's mBERT-ensemble plan (see
`milestone_3_bigger_swings.md`), so the M2/M3 re-plan needs this result
before finalizing scope.

## Documentation tasks for Milestone 1

- Add a short `research_log.md` decision-log entry correcting the framing
  of the 2026-07-03 state_v2 entry: the rejection reflects an
  information-loss confound (serializer swap dropped session_meta fields),
  not evidence against length extension. Do not delete/edit the original
  entry — add a new dated entry that supersedes its interpretation, per the
  log's own "records decisions, not full experiment logs" convention.
- Record Track A's length-ablation outcome (winner or "falsified, fell back
  to len192") as its own decision-log entry once Step 3's final gate
  resolves.
- Record Track B's outcome at whichever step it stops (collapsed at
  screening / package-size blocked / promoted to OOF) — this is valuable
  even on a negative result, to avoid re-litigating xlm-r-large later.
- All numeric results go to `experiments/results.csv` (automatic via script
  logging) and `experiments/artifacts/*.json` — the decision log stays
  short and narrative only, per `AGENTS.md`.
- Once this milestone closes, update its Status line at the top of this
  file and in `README.md`'s milestone table.
