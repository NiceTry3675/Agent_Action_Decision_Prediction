# GPT-5.6 weak-action semantic relabel rerun — report

Date: 2026-07-11 (Asia/Seoul)

## Verdict

The redesigned experiment meets its preregistered **strong semantic
divergence** criterion. On a fresh probability sample from the 28,535-row
eligible weak-action frame, the recorded action was accepted by a two-of-three
GPT-5.6 consensus on only **71/237 resolved rows (30.0%)**. The equal-class
macro rate is 29.8% with a within-stratum session-block bootstrap 95% interval
of **24.7–35.1%**; its upper bound is below the preregistered 50% threshold.
Weighting the four classes by their eligible training-population frequencies
gives **36.7% (30.6–43.1%)**,
which reaches the same verdict.

The conclusion was not sensitive to the tested prompt framing. On the paired
80-row subset (79 rows resolved in both conditions), allowing reasonable
workflow prerequisites changed macro consensus acceptable agreement by
**-1.18 percentage points** (paired bootstrap 95% CI
**-6.25 to +3.95 pp**), far below the preregistered 10 pp sensitivity threshold.

This is an operational **GPT-5.6 semantic judgment**, not human ground truth or
a measured label-noise rate. The result shows semantic divergence; it does not
by itself identify generator-policy state as the cause.

## What was redesigned

- Declared population: all 28,782 training rows whose recorded action is one of
  `list_directory`, `read_file`, `grep_search`, or `glob_pattern`.
- Fresh sample: the prior 248 GPT-5.6-annotated IDs were excluded; 60 rows were
  randomly selected per recorded class (240 unique rows, seed `20260711`). One
  prior ID is `run_bash`, so the realized Weak4 sampling frame is 28,535 rows.
- Full observable input: `current_prompt`, complete `history`, and complete
  `session_meta`.
- True action, prediction, logits, stratum, original ID, row position, and train
  index were absent from annotation inputs. Each panel used different opaque
  aliases and order.
- Label space: all 14 competition actions; `underdetermined` remained a separate
  top-level no-unique-action outcome and was forbidden from acceptable action
  sets.
- Every row was judged independently three times under the semantic rubric:
  Panels A/B/C, 720 judgments total.
- A result-independent 80-row subset (20/class, seed `20260713`) was judged
  three more times under a workflow-aware rubric: Panels D/E/F, 240 judgments.
- Every 20-row chunk used a fresh `fork_turns="none"` model-agent context.
- Consensus top action required two votes. A concrete action entered the
  consensus acceptable set only when at least two panels included it. No
  truth-aware adjudication was used; three-way top disagreement stayed
  unresolved.

The exact design and thresholds were frozen in `DESIGN.md` before annotation.

## Primary results

### Consensus agreement with the recorded action

| Recorded class | Resolved N | Exact top | Recorded action acceptable |
| --- | ---: | ---: | ---: |
| `list_directory` | 58 | 3/58 = 5.2% (95% CI 1.8–14.1%) | 3/58 = 5.2% (1.8–14.1%) |
| `read_file` | 59 | 22/59 = 37.3% (26.1–50.0%) | 25/59 = 42.4% (30.6–55.1%) |
| `grep_search` | 60 | 30/60 = 50.0% (37.7–62.3%) | 34/60 = 56.7% (44.1–68.4%) |
| `glob_pattern` | 60 | 6/60 = 10.0% (4.7–20.1%) | 9/60 = 15.0% (8.1–26.1%) |
| **Resolved pooled rows** | **237** | **61/237 = 25.7%** | **71/237 = 30.0%** |

The equal-class exact-top macro is 25.6% (bootstrap 20.6–30.7%); the acceptable
macro is 29.8% (24.7–35.1%). Eligible-frame frequency weighting raises exact
agreement to 31.9% (25.7–38.1%) and acceptable agreement to 36.7%
(30.6–43.1%), because
the more semantically aligned `read_file`/`grep_search` classes are more common.
Even the weighted acceptable upper bound remains below 50%.

The exclusion cannot drive the verdict. If every one of the 247 excluded Weak4
rows is treated as acceptable, the full-28,782 sensitivity estimate is macro
30.5% (bootstrap upper 35.8%) and naturally weighted 37.3% (upper 43.6%).

The strongest divergence is not a subtle grep/glob boundary: among recorded
`list_directory` rows, the consensus top action was more often `read_file`
(21), `grep_search` (20), or `glob_pattern` (6) than `list_directory` (3).
Recorded `glob_pattern` rows were most often judged `read_file` (26) or
`grep_search` (23), with only 6 exact top matches.

### Same-model run stability

| Reliability check | Result |
| --- | ---: |
| Three-way top-label unanimity | 200/240 = 83.3% |
| A top-label majority exists | 237/240 = 98.8% |
| Fleiss κ, top label | 0.829 |
| Pairwise raw top agreement | 86.7–90.8% |
| Pairwise Cohen κ | 0.800–0.865 |
| Pairwise acceptable-set mean Jaccard | 0.892–0.910 |
| Three-way exact acceptable-set unanimity | 187/240 = 77.9% |

Three rows had three different top labels and remain unresolved. Per the frozen
design they are excluded from both primary consensus endpoints. The early
all-row sensitivity is 61/240 exact and 73/240 acceptable and is retained in
`summary.json`; `DEVIATIONS.md` records the correction.

These are strong test-retest figures for the same GPT-5.6 rubric. They are not
inter-human agreement and do not establish semantic truth outside this model.

## Prompt-framing sensitivity

The semantic rubric prioritizes the latest prompt's immediate action contract.
The workflow-aware rubric permits a rational prerequisite/orientation action
when the full state makes it the best next step. Both use the same 14 actions,
input rows, opaque IDs, and three-vote consensus.

| Paired resolved subset | Semantic | Workflow-aware | Macro change |
| --- | ---: | ---: | ---: |
| Exact top agreement | 13/79 = 16.5% | 12/79 = 15.2% | -1.25 pp |
| Acceptable agreement | 18/79 = 22.8% | 17/79 = 21.5% | -1.18 pp |

- Paired bootstrap 95% CI: exact `-3.75 to 0.00 pp`; acceptable
  `-6.25 to +3.95 pp`.
- Cross-prompt consensus-top agreement: 76/79 = 96.2%, Cohen κ `0.947`.
- Acceptability changed from false to true on 2 rows and true to false on 3;
  one semantic-unresolved row was excluded from paired endpoints.

The tested workflow framing therefore did not rescue recorded-action semantic
agreement and did not trigger the 10 pp rubric-sensitivity qualification.

## Important source-family heterogeneity

An exploratory subgroup check found a large difference by ID source family:

| Source family | N in sample | Eligible-frame weighted exact | Eligible-frame weighted acceptable |
| --- | ---: | ---: | ---: |
| `sess_sim` | 226 (223 resolved) | 27.6% (bootstrap 21.7–33.7%) | 32.9% (26.7–39.2%) |
| `sess_au` | 14 | 88.2% (68.6–100%) | 88.2% (68.6–100%) |

This split was not a preregistered primary endpoint, and AU has only 14 rows
(nine are `read_file`), so it must not be treated as a precise AU estimate. The
unweighted descriptive rates are sim 59/223 = 26.5% acceptable and AU 12/14 =
85.7%; the table corrects for the class-balanced design using eligible-frame
inverse-inclusion weights.
Still, it is too large to hide: the semantic-divergence result is driven by the
simulated-session population. That is also the dominant eligible frame:
26,502/28,535 rows (92.9%) are `sess_sim`, versus 2,033 `sess_au` rows.

The old 248-row artifact was even more simulation-heavy (242 `sess_sim`, 6
`sess_au`). Accordingly, the old phrase "weak-class labels are generator-policy
records" should be narrowed to a claim about the simulated-session regime, not
stated as a universal property of every source family. A larger AU-stratified
sample would be required before making an AU conclusion.

## Comparison with the 2026-07-10 result

The old error-enriched casebook reported 21.0% exact and 31.0% acceptable
agreement. The fresh eligible-frame sample reports 25.6% exact and 29.8%
acceptable on the equal-class macro; eligible class-frequency weighting reports
31.9% and 36.7%.

So the old 21% exact figure was not a population estimate and was somewhat
pessimistic relative to natural weighting, but its qualitative direction
survives a substantially stronger design. In particular, `list_directory` and
`glob_pattern` remain sharply semantically divergent. The rerun does **not**
validate the old 91.3% figure as a global reliability estimate; it replaces it
with full-sample three-run measures.

## Audit findings that change how the old report should be read

The read-only audit in `AUDIT_OF_20260710.md` found:

- the 80-row reliability queue was selected after primary labels, from a
  211-row priority pool; 69/80 had already rejected the dataset label as
  unacceptable and only 1/80 matched it exactly;
- the eight final adjudication inputs exposed `dataset_true`, model prediction,
  and all earlier votes;
- the original `id`, `row_pos`, and `train_index` made label lookup possible;
- annotators saw reduced metadata, while final rows were later populated with
  full metadata; this differed on all 248 rows;
- ten real actions were collapsed into `other`;
- prompt/model parameters, run IDs, hashes, and transcripts were not preserved;
- the report's displayed per-dataset-label table totals 247 because one
  `run_bash`-truth row was omitted.

The old generator-action reconstruction remains useful: 225 recoverable
successor actions all match the casebook label, and a direct audit confirmed
all 248 casebook labels match `train_labels.csv`. This proves the target records
an action, not that the action is semantically ideal.

## Operational interpretation

What survives:

- The competition target is frequently different from the GPT-5.6 semantic
  next action in the dominant simulated-session regime.
- Replacing recorded labels with GPT-5.6 relabels would optimize the wrong
  target. The original labels should remain the training/evaluation truth.
- A serializer or auxiliary objective that only improves direct semantic intent
  can reduce generator-policy imitation, especially for `list_directory` and
  `glob_pattern`.

What should be weakened:

- Do not call these labels "human truth", "global label noise", or proof of a
  causal generator-policy mechanism.
- Do not use the old 91.3% audit number as population reliability.
- Narrow the conceptual doctrine to simulated sessions. The AU signal is
  exploratory but points in the opposite direction and deserves a separately
  powered sample if that source matters operationally.

This result does not change the Public target or by itself justify reopening a
model-training lane. It improves the diagnosis and the scope of the earlier
claim.

## Limitations

- All judgments are GPT-5.6 runs; there are no human annotators or cross-family
  semantic validators in this rerun.
- The orchestration runtime does not expose a model snapshot hash, temperature,
  sampling seed, reasoning setting, or provider request ID. Those are recorded
  as unavailable rather than inferred.
- File-access blinding is procedural, not enforced by a separate filesystem
  sandbox. Opaque aliases prevent direct joins, and every task explicitly
  prohibited other-file access.
- The core sample is 240 rows. Class estimates have visible uncertainty; AU is
  especially underpowered. Source-family estimates are exploratory and
  design-weighted; raw subgroup proportions are not population estimates.
- The study estimates semantic alignment, not why the synthetic generator chose
  a particular action. A causal policy-state claim would need an intervention
  or matched state/history conditions.
- Only two similar prompt framings were tested, and the paired bootstrap holds
  the six realized model-run votes fixed while resampling rows. Failure to cross
  the 10 pp rule means only that this tested pair did not trigger; it does not
  establish invariance to every rubric or generation run.

## Reproduction and integrity

Raw annotations, scripts, and checksum material are intentionally local-only and
gitignored; Git retains the four core documents. The following verification is
therefore for the complete local workspace, not a docs-only checkout.

From the repository root:

```bash
(cd experiments/artifacts/20260711_gpt56_relabel_rerun && sha256sum -c checksums.sha256)
.venv/bin/python experiments/artifacts/20260711_gpt56_relabel_rerun/test_pipeline.py
.venv/bin/python experiments/artifacts/20260711_gpt56_relabel_rerun/analyze.py
(cd experiments/artifacts/20260711_gpt56_relabel_rerun && sha256sum -c checksums.sha256)
```

The final raw coverage is 48 files / 960 judgments. One schema-only retry is
documented in `VALIDATION_LOG.md`; it removed a non-action meta value from an
acceptable-action list without changing the judgment. `agent_runs.json` records
the 48 fresh task IDs, prompt condition, input/output paths and hashes, and the
retry. `summary.json` and `annotations_merged.jsonl` are deterministic derived
outputs.

`DEVIATIONS.md` records the eligible-frame estimand, unresolved-row correction,
source-weighting rule, bootstrap scope, and input-manifest timing caveat.
