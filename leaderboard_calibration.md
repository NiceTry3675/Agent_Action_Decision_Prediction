# Leaderboard Calibration

This file tracks only the relationship between local validation and Public score.
Experiment details live in `experiments/results.csv`.

## Public Anchors

| Submission | Fixed | OOF | Public | Gap | Note |
| --- | ---: | ---: | ---: | --- | --- |
| DistilBERT current serializer, 3ep | `0.710721` | n/a | `~0.698` | about `-0.0127` vs fixed | Fixed and random validation were optimistic. |
| XLM-R 3ep handoff | `0.718793` | n/a | `0.708057` | `-0.010736` vs fixed | Historical anchor only; superseded by XLM-R 5ep. |
| XLM-R 5ep no-replay handoff | `0.738900` tuned | n/a | expected `0.726-0.728` | expected negative vs fixed | Canonical fixed-session baseline before replay. |
| XLM-R replay + OOF rules + sparse SVC w4 | `0.751733` | `0.741881` | `0.743` | `+0.001119` vs OOF, `-0.008733` vs fixed | Current baseline; OOF calibrated well, fixed was optimistic. |

## Current Baseline

- Package: XLM-R 5ep replay_last1 cap10000 + OOF rule boosts + sparse SVC weight `4.0`.
- Main signal: OOF 2-stage `0.741881` -> reported Public `0.743`.
- Fixed split `0.751733` was optimistic and should not be used for finalist selection.
- The Public `0.743` result clears the `0.74` target.

## Promotion Rule

- Quick screen: reject bad ideas only.
- Fixed-session: sanity check and weak-class inspection.
- OOF: finalist selection, bias tuning, rule tuning, and ensemble tuning.
- Public: final calibration anchor after packaging and smoke checks.

## Notes

- The largest recurring errors remain around `grep_search`, `read_file`, `list_directory`, and `glob_pattern`.
- Alternative encoder notes are summarized in `final_summary.md`; full rows stay in `experiments/results.csv`.
