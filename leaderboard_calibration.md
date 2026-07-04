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

## Promotion Rule (2026-07-04: Public-gated)

- Quick screen: reject bad ideas only.
- Fixed-session full run (`--save-val-model`): sanity, weak-class inspection,
  and submittable weights.
- Public: the decision metric — package, smoke, submit (10/day budget).
  Deltas < 0.002 are noise; one variable per submission.
- OOF: tool for ensemble construction and bias/rule tuning, not a gate.

## Submission Ledger

Every Dacon submission gets one row, at submission time; fill Public when the
score lands.

| Date | Candidate (experiment id / package) | Local fixed | Public | Gap | Note |
| --- | --- | ---: | ---: | ---: | --- |
| 2026-07-02 | baseline: XLM-R replay + rules + sparse w4 (`submit.zip`) | 0.751733 | 0.743 | -0.008733 | pre-ledger; anchor row repeated from above |
| 2026-07-04 | len448 focal g2.0 encoder-only val-model, seed42 (`submit_m3_focal_len448.zip`, `...m3_focal_len448_ep5_replay_last1_seed42_valmodel`) | 0.748091 | 0.735 | -0.013091 | first Public-gated loop; -0.008 vs baseline Public, but confounded: encoder-only (no rules/sparse), 80% train data (val-model), and a weak instance draw (same-seed rerun of m2 len448 0.755278 — run-to-run noise ±0.007-0.009). seed43 + len192-focal pair will deconfound |
| 2026-07-04 | len448 focal g2.0 encoder-only val-model, seed43 (`submit_m3_focal_len448_seed43.zip`, `...m3_focal_len448_ep5_replay_last1_seed43_valmodel`) | 0.735075 | pending | | transfer-coefficient probe: fixed is -0.013 below the seed42 arm; if Public co-moves, fixed screens rank candidates despite optimism — if not, fixed deltas are meaningless for Public. len448 fixed spread across 3 instances now 0.735-0.755 |

## Notes

- Server inference wall time: every submission to date finished in under 1
  minute on the eval T4 (user-observed, 2026-07-04). The 10-min cap is not a
  binding constraint — len448 single-encoder projects ~2-3 min, a two-encoder
  base+large package ~4-5 min.

- The largest recurring errors remain around `grep_search`, `read_file`, `list_directory`, and `glob_pattern`.
- Alternative encoder notes are summarized in `final_summary.md`; full rows stay in `experiments/results.csv`.
