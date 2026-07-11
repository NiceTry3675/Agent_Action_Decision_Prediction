# GPT-5.6 weak-action semantic relabel rerun — preregistration

Date: 2026-07-11 (Asia/Seoul)

This experiment is a clean rerun of the semantic-label claim in
`../20260710_human_relabel/`. It does not modify or reuse that artifact's final
annotations.

## Question and estimand

For training rows whose recorded next action is one of `list_directory`,
`read_file`, `grep_search`, or `glob_pattern`, how often is that recorded action
also a semantically defensible next action for a competent coding agent given
the full observable row?

The semantic judgment is explicitly an operational **GPT-5.6 model judgment**,
not human ground truth. Agreement among repeated GPT-5.6 runs measures
same-model run stability; it is not inter-human reliability and cannot rule out
a model-family-wide semantic bias.

## Why the original design is not reused

The original 248 rows were sampled from model errors, so their 21.0% exact
agreement cannot estimate the weak-action population. Its 80-row reliability
sample was also selected after seeing the primary annotations: low-confidence,
non-clear, or dataset-inacceptable rows were placed first. The six-class label
space collapsed ten real actions into `other`, and only rows where both audit
agents overruled the primary agent were adjudicated. Those choices mix
population estimation, reliability estimation, and adjudication.

## Sampling

- Population: all 28,782 rows in `open/data/train_labels.csv` whose recorded
  action is one of the four weak actions.
- Exclusion: the 248 IDs annotated in the 2026-07-10 GPT-5.6 artifact, to keep
  this sample fresh.
- Sample: 60 rows per recorded weak action (240 unique rows), simple random
  sampling within class with seed `20260711`.
- Primary aggregate: equal-class macro average. A population-weighted aggregate
  is secondary and uses the four sampling-frame class counts.
- Inputs contain full `current_prompt`, `history`, and `session_meta`. Recorded
  action, model prediction, logits, stratum, original ID, and generator successor
  are hidden while annotation is in progress.

## Annotation design

- Model: GPT-5.6 for every annotation pass.
- Label space: all 14 competition actions plus `underdetermined`.
- Panels A, B, and C independently annotate all 240 rows (720 judgments). Their
  row aliases and orders differ, and no panel can read another panel's outputs.
- A paired prompt-sensitivity subset is fixed before annotations: 20 rows per
  recorded class (80 total), sampled from the 240 with seed `20260713`.
  Panels D, E, and F independently annotate all 80 under a workflow-aware
  instruction that permits reasonable prerequisite/orientation steps while
  still forbidding hidden-target imitation (240 additional judgments).
- If all three top labels differ, the row remains unresolved. There is no
  outcome-aware fourth adjudicator and no forced label.
- Annotation tasks are split into 20-row chunks. A fresh model-agent context is
  used per chunk so that one long context cannot propagate a local convention
  through the whole sample.

## Frozen annotation rule

Annotators answer: "From the observable state, what should a competent coding
agent do as its next atomic action to advance the latest request?" They must not
imitate dataset conventions. The latest request dominates stale goals; history
resolves references and completed work; metadata supplies state but is never a
label hint. `acceptable_labels` contains all actions that are independently
defensible, not merely imaginable.

## Endpoints and decision rules

Primary endpoints use the three-panel majority. Rows with three different top
labels are retained as unresolved and excluded only from consensus endpoints:

1. Recorded action equals the semantic top label (`exact`).
2. Recorded action belongs to `acceptable_labels` (`acceptable`).
3. Class-macro exact and acceptable rates with stratified bootstrap 95% CIs.
4. Per-class Wilson 95% CIs.
5. On the paired 80-row subset, the change in exact and acceptable agreement
   between semantic and workflow-aware consensus, with paired bootstrap CIs.

Reliability endpoints are reported separately:

- Pairwise raw top-label agreement and Cohen's kappa over all 240 rows.
- Pairwise acceptable-set Jaccard similarity.
- Three-way unanimity, any-majority rate, and Fleiss' kappa over all 240 rows.
- Panel-specific recorded-action agreement, to expose systematic panel drift.

Preregistered interpretation:

- **Strong semantic alignment:** the lower 95% CI of macro acceptable agreement
  is at least 0.70 and every class point estimate is at least 0.60.
- **Strong semantic divergence:** the upper 95% CI of macro acceptable agreement
  is below 0.50.
- **Mixed/class-specific:** neither rule holds. Class contrasts, especially
  `list_directory`/`glob_pattern` versus `read_file`/`grep_search`, are reported
  without upgrading a post-hoc pattern into the primary verdict.
- **Rubric-sensitive:** the absolute paired change in macro acceptable agreement
  under the workflow-aware prompt is at least 0.10. In this case, any semantic
  alignment/divergence verdict is explicitly qualified as prompt-dependent.

These thresholds determine only the semantic-audit verdict. The competition
target remains the recorded generator action regardless of the result.

## Reproducibility and stop rules

- `build_inputs.py` deterministically creates blinded A/B/C semantic inputs and
  D/E/F workflow-aware inputs without
  writing an official-label manifest.
- `analyze.py` reconstructs the sealed mapping, validates all schemas, computes
  every endpoint, and writes the merged rows and summary.
- Missing rows, duplicate aliases, invalid labels, or cross-panel sample
  mismatches are hard failures.
- The design and thresholds above are written before any new annotation output
  is inspected.
