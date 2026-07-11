# GPT-5.6 weak-action semantic relabel rerun

This is a separate, preregistered rerun of the 2026-07-10 GPT-5.6 semantic
relabel study. It leaves the old artifact untouched.

## Headline

- Fresh class-balanced probability sample: 240 rows, 60 per weak action, from
  the 28,535-row eligible complement after excluding prior annotated IDs.
- Three independent GPT-5.6 judgments per row: 720 primary judgments.
- Recorded action accepted by resolved consensus: **71/237 = 30.0%**;
  equal-class macro **29.8%**, bootstrap 95% CI **24.7–35.1%**.
- Eligible class-frequency weighted acceptable rate: **36.7%**, 95% CI
  **30.6–43.1%**. Even assigning all 247 excluded Weak4 rows acceptable leaves
  the full-frame weighted upper bound at **43.6%**.
- Full-sample same-model stability: Fleiss κ **0.829**, three-way unanimity
  **83.3%**.
- Paired workflow-aware sensitivity audit: **-1.18 pp** macro acceptable
  change, below the preregistered 10 pp rule for this tested prompt pair.
- Preregistered verdict: **strong semantic divergence**; the tested-pair rubric
  sensitivity rule did not trigger.

This is GPT-5.6 operational semantic judgment, not human ground truth. The
exploratory design-weighted source split is material: `sess_sim` acceptable
agreement is 32.9% (n=226), while `sess_au` is 88.2% (n=14, underpowered). The
broad old doctrine should therefore be narrowed to the dominant
simulated-session regime.

## Git scope

Only `README.md`, `REPORT.md`, `DESIGN.md`, and `DEVIATIONS.md` are intended for
Git. Raw inputs/results, derived JSON, scripts, provenance, and checksums remain
available in the local workspace but are intentionally gitignored.

## Files

- `REPORT.md`: results, interpretation, class/source breakdowns, and limits.
- `DESIGN.md`: frozen design, endpoints, and decision thresholds.
- `AUDIT_OF_20260710.md`: read-only audit of the old experiment.
- `DEVIATIONS.md`: realized estimand, unresolved correction, source weighting,
  and integrity clarifications found during independent review.
- `INSTRUCTIONS.md`, `INSTRUCTIONS_WORKFLOW.md`: exact annotation prompts.
- `inputs/`: blinded opaque-ID inputs for six panels.
- `results/`: 48 raw annotation chunks / 960 judgments.
- `annotations_merged.jsonl`: deterministic row-level consensus and raw votes.
- `summary.json`: all metrics and confidence intervals.
- `agent_runs.json`, `PROVENANCE.md`, `VALIDATION_LOG.md`,
  `checksums.sha256`: execution and integrity trail.
- `build_inputs.py`, `common.py`, `analyze.py`, `test_pipeline.py`: complete
  sample, validation, consensus, and analysis pipeline.

## Verify

The commands below are for the complete local artifact; they are not available
from a docs-only Git checkout.

```bash
(cd experiments/artifacts/20260711_gpt56_relabel_rerun && sha256sum -c checksums.sha256)
.venv/bin/python experiments/artifacts/20260711_gpt56_relabel_rerun/test_pipeline.py
.venv/bin/python experiments/artifacts/20260711_gpt56_relabel_rerun/analyze.py
(cd experiments/artifacts/20260711_gpt56_relabel_rerun && sha256sum -c checksums.sha256)
```
