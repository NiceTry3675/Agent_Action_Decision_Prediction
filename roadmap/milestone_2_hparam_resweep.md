# Milestone 2 — Hyperparameter resweep

**Dates**: 07-05 → 07-07. **Status**: scoped, re-plan at M1 close using
real numbers — do not execute against the details below until M1 has
produced a winning length (or falsified the length hypothesis) and, if
attempted, Track B's Step 2 screen result for xlm-roberta-large.

## Scope (fixed)

`class_weight_power`, `label_smoothing`, `lr`, `replay_sample_weight`,
`max_replay_samples` were only ever tuned on small/noisy DistilBERT-len160
quick-val screens *before* XLM-R+replay+rules+sparse became the winning
stack — never revisited on XLM-R. Re-sweep on M1's winning length (whichever
base model M1 ends up favoring — XLM-R-base or, if Track B fully lands,
xlm-roberta-large) using `--quick-val-size 600` (a legitimate screen for
these, unlike for length — see `README.md` guardrails) to prune a small
grid, take top 2-3 to fixed-session, best one to 3-fold OOF (same
aggregate/rule/sparse rebuild as M1's Step 3).

## Left to the M1-close re-plan

- Exact grid values (should center around current defaults —
  `class_weight_power=0.5`, `label_smoothing=0.02`, `lr=2e-5`,
  `replay_sample_weight=0.5`, `max_replay_samples=10000` — adjusted based on
  what M1 finds about the model's behavior at the new length).
- Whether to run this against XLM-R-base or xlm-roberta-large or both.
- Whether M2 needs to wait for Track B Step 4 to resolve before picking
  which base model to tune (if Track B is still running when M2 starts,
  M2 can proceed on XLM-R-base and re-target xlm-roberta-large later if it
  wins, since the sweep methodology is base-model-agnostic).

## Gate

Beat the M1 OOF baseline by any real margin; expect modest, incremental
gains — cap this milestone at 2 days even if the grid isn't exhausted.

## Documentation tasks

- Once the grid is finalized (at the M1-close re-plan), replace this
  "Left to the M1-close re-plan" section with the actual grid and update
  the Status line.
- Record the winning hyperparameter combination (or "no improvement found,
  keeping M1 defaults") as a `research_log.md` decision entry once the
  final gate resolves.
