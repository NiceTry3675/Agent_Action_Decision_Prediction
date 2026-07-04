# Research Roadmap: Public Macro-F1 0.743 → 0.77+

This directory is the active, forward-looking research plan toward Public
Macro-F1 ≥ 0.77 (from the current submitted `0.743`). It is separate from
`research_log.md` (past decisions only) and `final_summary.md`/
`leaderboard_calibration.md` (current submitted state only) — this is where
the *plan* for future work lives, split into milestones so it can be
reviewed and re-planned incrementally instead of as one long document.

**Execution model**: this roadmap is written to be picked up cold by any
execution agent/session — exact commands, exact file paths, exact gates.
Documentation is a first-class deliverable of each milestone, not an
afterthought (see each milestone's "Documentation tasks").

Milestones:

| Milestone | File | Target dates | Status |
|---|---|---|---|
| M1 | [milestone_1_length_and_xlmr_large.md](milestone_1_length_and_xlmr_large.md) | 07-03 → 07-05 | Completed 2026-07-03: Phase 0 OOF `0.743183`; length failed fixed gate; xlm-r-large package-size blocked |
| M2 | [milestone_2_hparam_resweep.md](milestone_2_hparam_resweep.md) | 07-05 → 07-07 | **Dropped 2026-07-04** (accelerated plan below: params frozen at proven values, EV ≤ +0.005) |
| Checkpoint | (see below) | 07-07/08 | Go/no-go + reprioritization |
| M3 | [milestone_3_bigger_swings.md](milestone_3_bigger_swings.md) | 07-08 → 07-11 | Candidates identified, sequencing set at Checkpoint |
| M4 | [milestone_4_consolidation.md](milestone_4_consolidation.md) | 07-11 → 07-14 | Fixed process |
| Buffer | — | 07-14 → 07-15 | No new experiments |

Only M1 is fully detailed. M2-M4 are intentionally left as scoped-but-
lighter placeholders — they get re-planned (a short new pass, not a full
re-exploration) using each prior milestone's actual results before
execution starts, since locking in exact grids/weights now would mostly be
guessing.

## 2026-07-04 Accelerated plan (supersedes the milestone sequencing above)

Full background in `research_log.md` (2026-07-04 leak entry). Lanes, in
priority order — hyperparameters are frozen at the proven recipe everywhere
(lr 2e-5, 5ep, focal γ2.0, cwp 0.5, ls 0.02, replay last1 cap10k w0.5):

| Lane | What | Command sketch | Gate |
|---|---|---|---|
| ~~G — leak probe~~ | Cross-row label leakage: later rows' histories pin earlier rows' labels (train: 86.5% coverage, 0 wrong; see `verify_leak_train.py`). | probe submitted 07-04 (`leak_probe_0705.zip`) | **DEAD 07-04: Public 0.710 (-0.033)** — overrides actively wrong on the server; the public test lacks the train dump's cross-row structure. All tiers off unless `--leak-lookup` repackages the lookup file. Dropped for good |
| **L — len448 full stack** | Finish seeds 44/45 best-of-N (in flight), take best fixed instance, retune bias/rules/sparse on its val logits (`make_val_tune_artifact.py` → tune chain), package, submit | per ledger protocol; expected Public +0.005~0.010 | fixed ≥ ~0.755 before submitting (transfer ×1.6) |
| **A — int8 base+large ensemble** | Per the 07-04 Track B entry: int8 both encoders (~883 MB with sparse), 2-encoder softmax averaging in `script.py`, ens-chain artifacts | `quantize_checkpoint.py` quantize+verify → `inject_stack_meta.py` → package | verify argmax agreement ≥ 99.5%; expected Public ~+0.004 |
| **B — `current_v2` serializer @ len448** | Registered in `script.py`/`train_transformer.py`: priority-ordered fields, all user utterances kept (newest first) so truncation eats the oldest pairs | 20260703_170302 train_command with `--serializer current_v2` only (one variable), fixed screen → OOF full chain if ≥ baseline−noise | full-chain OOF ≥ 0.745 by 07-07, else drop; expected +0~0.010 |
| C — 4-way specialist | One fold screen only; the cluster's conditional distributions are flat given surface features, so expect simulator stochasticity | after B verdict | cluster F1 +0.03 on the screen or drop |

Dropped by this plan: M2 resweep, Qwen-0.5B decoder (FLOPs put 30k rows at
10-20 min on the T4 — cap violation), multi-seed packaging (below measured
heterogeneous-ensemble EV), any new post-processing (OOF gains deflate ~2.7x
to Public). G died 07-04 (Public 0.710, -0.033): the arithmetic is
0.743 + L + A + B ≈ 0.754~0.771 and 0.77 needs L's upper end plus B landing.

## Context

Current submitted baseline: **Public 0.743** (OOF 2-stage `0.741881`):
XLM-R-base + `current_v1` serializer + `max_length=192` + replay_last1 + 12
OOF rule boosts + sparse SVC ensemble (weight 4.0). Target: **Public ≥ 0.77**
(+0.027), fast, with liberal Colab GPU use.

Hard constraints not in `AGENTS.md` but binding on this whole roadmap:
- **Deadline 2026-07-15** (`rule.md`). Max 10 submissions/day — since the
  2026-07-04 protocol change this budget IS the validation channel
  (Public-gated promotion; ledger in `leaderboard_calibration.md`).

### Why the weak classes are weak

`read_file`/`grep_search`/`list_directory`/`glob_pattern` are *not* rare
(~41% of data combined) — they're weak from a **4-way mutual confusion
cluster** (discriminability problem, not imbalance). Top confusion cells:
`grep_search→read_file` (~2700-3100), `read_file→list_directory` (~1700-
2000), `glob_pattern→read_file`/`list_directory` (~1000-1150).

### Key finding #1 — `max_length=192` truncates the predictive tail, but longer current_v1 did not validate

Tokenizing `current_v1` text with the real xlm-roberta-base tokenizer: mean
261.5 tokens, **~70.7% of rows truncated at `max_length=192`**, right-side
truncation, silently dropping the `args:`/`results:` tail — exactly the
action-outcome signal the rule-boost system already proves predictive (e.g.
`ci:passed → lint_or_typecheck`). Inference cost of longer length is trivial
(len384 projects to ~3-5 min for 30k rows, well under the 10-min cap).
M1 tested clean current_v1 length screens anyway: len256 raw `0.728402` and
len320 raw `0.735856` both missed the fixed gate, so no length OOF was run.

### Key finding #2 — the state_v2 rejection does not falsify the length hypothesis

This corrects the framing of the 2026-07-03 `research_log.md` decision. The
prior state_v2 length experiment (len256/320/384, all rejected) never
tested "current_v1 unchanged, just longer" — it confounded length with a
switch to `state_v2`, a **strictly information-poorer serializer** (drops
`user_tier`, `language_pref`, `budget_tokens_remaining`,
`elapsed_session_sec` entirely; collapses the 5-language mix-with-ratios
down to a single dominant-language token). The more defensible read is "the
input distribution changed too aggressively," not "length extension doesn't
help." M1 then ran the clean current_v1 length test and rejected it at the
fixed-session gate, so the state_v2 interpretation is corrected but the
length-relief branch is now retired for this recipe.

### Confirmed dead ends — do not repeat

`xlm-align-base`/`infoxlm-base` (collapsed under default recipe, likely an
LR/warmup mismatch for those specific checkpoints — see M1's Track B note on
why this doesn't predict xlm-roberta-large's behavior), `state_v2`/
`recent_pairs_v1`/`hybrid_v1` as *primary* serializers, current_v1 length
extension to 256/320 under the M1 recipe, `replay_mode=last2` (no gain over
last1), hashed-embedding linear/MLP models, "kitchen sink" sparse feature
channels.

### Infra baseline

**Cloud lane** (`colab/COLAB.md`) is functionally proven (3/3 round trips
worked) but hasn't produced a win yet (its 3 runs were the state_v2
ablation). No new infra work needed to *use* it. **Local RTX 3070 Ti (8GB)
is a second, independent GPU lane** — proven XLM-R runs already ran there —
giving free 2-way parallelism (local + Colab) with nothing new to build.

Package budget: current zip ~529MB/1GB (hf_model fp16 ~553MB +
`sparse_svc.pkl` ~58MB), ~470MB headroom. **Verified via parameter-count
estimate** (matches known real xlm-roberta-large specs): xlm-roberta-large
in fp16 is **~1.1-1.2GB by itself** — may not fit under the 1GB cap even
alone, and almost certainly not alongside `sparse_svc.pkl`, without zip
compression carrying it (current package compresses ~13%, landing
large-alone at ~970MB zipped — tight) or quantization. See M1 Track B.

Full data: 70,000 rows / 9,429 sessions, 64% Korean-dominant.

## Guardrails (unchanged from `AGENTS.md` — do not violate)

- Promotion metric is **session_oof 3-fold, 2-stage bias-tuned**.
  Fixed-session is sanity-only (historically optimistic by ~0.009-0.016 vs
  Public). Quick-val (`--quick-val-size 600`) is cheap-rejection only, and
  is **specifically unreliable for length changes** — go straight to
  fixed-session for length experiments.
- **One variable per experiment.** No submission unless a candidate beats
  the current baseline OOF.
- Replay stays fixed at `--replay-mode last1 --max-replay-samples 10000
  --replay-sample-weight 0.5` throughout, except when M2 explicitly targets
  it as the swept variable.
- Every new artifact/logit goes through `experiments/results.csv` (via the
  scripts' own logging) and `experiments/artifacts/*.json` — never
  hand-edit. Cloud rows only via `colab/cloud_sync.py pull`.

## Parallelization

- Phase 0 logit-reuse work (CPU) runs concurrently with any GPU job, always
  — disjoint resources.
- **Local GPU + one Colab session = free 2-way GPU parallelism**, no new
  infra needed. M1 uses both simultaneously (Track A local+Colab length
  screens); Track B's Step 4 (xlm-r-large) reuses this by running during
  M2's mostly-local hyperparameter work.
- **Do not build a second concurrent Colab session/exchange root** by
  default — not worth the engineering cost given the GPU-hour budget is
  comfortable (~19-29 hours needed across M1-3 excluding xlm-r-large, over
  9 remaining days). Reconsider only if Track B is fully committed AND
  timing gets genuinely tight (Decision Point C below).

## Expected value per lever (qualitative — genuinely uncertain)

| Lever | Plausible gap closure | Confidence | Risk |
|---|---|---|---|
| Phase 0 (logit reuse) | landed at OOF `0.743183` (+0.001302) | Done | Low |
| M1 Track A (length) | no gain; fixed gate failed | Done | Retired |
| M1 Track B scouting (Steps 1-2) | package-size blocked at `1089.108 MB` fp16 | Done | High package risk |
| M1 Track B full commit (Steps 3-4) | +0.005 to +0.02 if it works | Low-medium | High — checkpointing gap + tight/uncertain package size |
| M2 (hparams) | +0.002 to +0.006 | Medium | Low-medium |
| M3 mBERT | +0.002 to +0.008 | Medium | Medium; only viable if base stays XLM-R-base |
| M3 confusion features | 0 to +0.02 | Low-medium, highest variance | High |

**Honest reachability read**: stacking conservative M1+M2 estimates
(~+0.014) plausibly lands OOF around 0.755-0.758 by the Checkpoint — short
of the ~0.768-0.771 OOF that would comfortably clear Public 0.77. **Reaching
0.77 most likely requires the length win landing at the high end AND at
least one M3 lever (or a successful xlm-r-large commitment) also landing.**
It's a real possibility 0.77 isn't reachable in 12 days under this
protocol's conservative, OOF-gated promotion discipline — the Checkpoint and
calibration-submission cadence exist so that whatever level is reached by
07-14 is locked in as a valid, smoke-tested submission rather than an
unfinished bet.

## Checkpoint (07-07/08)

Compare combined OOF against the trajectory needed for 0.77 Public (working
assumption OOF ≈ Public ± 0.01, given only one calibration point exists):
- **OOF ≥ ~0.755**: on track; proceed into M3 with confidence.
- **OOF ~0.745-0.755**: on track but not clearly enough; M3 becomes
  load-bearing — pick the single highest-conviction candidate and go deep
  rather than splitting effort across all of them.
- **OOF < ~0.745**: 0.77 is unlikely reachable in the remaining days under
  this protocol's conservative gates. Reprioritize toward locking in
  defensible incremental gains: submit a calibration check now, then run
  M3's cheapest/most-certain item first.

This is also where M3's scope gets finalized — see `milestone_3_bigger_swings.md`.

## Decision points to revisit with the user (not pre-committed)

- **A. M1 Track B Step 3 (checkpointing build)** — default is **yes,
  build it**, conditional only on Track B Steps 1-2 not ruling out
  xlm-r-large. Revisit if Steps 1-2 look bad.
- **B. Whether to promote xlm-r-large to the final package**, given it must
  fully replace XLM-R-base (not add to an ensemble) and the package-size
  math is tight/uncertain until Step 1's real measurement lands. Decide at
  the Checkpoint using real numbers, not the estimate.
- **C. Second parallel Colab session** — default no; revisit only if B is
  heading toward "yes" and the Checkpoint shows time pressure.
- **D. Calibration submission cadence** — default ~4-6 across 12 days (see
  `milestone_4_consolidation.md`); confirm acceptable or adjust.

## Verification

Every promoted candidate is checked at 3 levels before being trusted, per
the existing (unmodified) protocol: fixed-session sanity + weak-class F1
check → 3-fold session_oof (the real promotion gate) → post-packaging smoke
test (`TRANSFORMERS_OFFLINE=1` and CPU-only, from a clean zip extraction)
before any Public submission. No candidate skips the OOF gate regardless of
how promising a fixed-session or quick-val number looks.

## Handoff notes for the execution agent

- Read `AGENTS.md` first — it defines the validation protocol and doc
  structure this roadmap builds on; nothing here overrides it.
- Log format discipline: `research_log.md` gets short, dated decision
  entries only (rule/evidence/decision/next-action, matching its existing
  entries) — never confusion tables or per-class dumps there. Detailed
  metrics go to `experiments/artifacts/*.json` (produced automatically by
  the tuning scripts). Numeric experiment rows go to
  `experiments/results.csv` automatically via the training/tuning scripts —
  never hand-edit it.
- Real artifact filenames referenced in `milestone_1_length_and_xlmr_large.md`
  (verified to exist as of 2026-07-03):
  `experiments/artifacts/20260702_oof_xlmr_base_len192_ep5_replay_last1_cap10000_oof_metrics.json`,
  `experiments/artifacts/20260702_oof_rule_boosts_xlmr_base_len192_ep5_replay_last1_cap10000_rule_boosts.json`.
  Logit filenames are timestamp-prefixed — always `ls experiments/logits/`
  to confirm before writing an `aggregate_oof.py --pattern`.
- Replay leakage rule is non-negotiable: only fold-aware replay in
  `train_transformer.py` is safe; validation-session examples must never
  enter training via replay.
- When a milestone needs re-planning at its checkpoint, update that
  milestone's file in place (status line + content) rather than scattering
  ad-hoc notes elsewhere — keep this directory the single source of truth
  for the active plan.
