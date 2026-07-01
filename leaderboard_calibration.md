# Leaderboard Calibration

## Known Submission Calibration

| Submitted model | Fixed session local | Random local | Public Macro-F1 | Local-Public gap | Likely cause | Validation adjustment decision |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `distilbert-base-multilingual-cased`, current serializer, 3 epochs, session-tuned class bias | 0.710721 | 0.722073 | ~0.698 | ~0.0127 vs fixed, ~0.0241 vs random | Single fixed split and random split are optimistic; weak exploration classes still confuse each other heavily. | Keep fixed session as fast control, treat random as historical only, require finalist OOF plus weak-class gains before the next serious submission. |

## Current Interpretation

- The current public score is below both local validation scores, so single-split gains are not sufficient evidence for a better submission.
- The biggest repeated local errors are `grep_search -> read_file`, `read_file -> list_directory`, `grep_search -> list_directory`, and `run_bash -> run_tests`.
- Future candidates should improve fixed-session Macro-F1, save validation logits, and show broader weak-class improvement before final refit or leaderboard submission.

## Local Candidate Tracking

| Candidate | Fixed session raw | Fixed session tuned | OOF raw | OOF tuned | Public Macro-F1 | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `current_v1 + replay_last1 cap10000`, max_length 192, 3 epochs | 0.712909 | 0.718145 | not run | not run | not submitted | Keep as GPU candidate only; superseded by the stronger length-256 run. |
| `current_v1 + replay_last1 cap10000`, max_length 256, 4 epochs | 0.726448 | 0.731768 | 0.721322 | 0.725204 | not submitted | Strongest validated transformer so far, but OOF is below the fixed split and below the 0.74 target; do not final refit yet. Use as the finalist baseline for the next search. |
| `current_v1 + replay_last1 cap10000`, max_length 256, 4 epochs + OOF rule boosts | 0.726448 | 0.732621 | 0.721322 | 0.728511 | not submitted | Small consistent improvement over the finalist baseline; keep as a possible add-on, but still below the target. |
| `current_v1 + replay_last1 cap10000`, length 256 + OOF rule boosts + sparse SVC ensemble | 0.726448 | 0.735248 | 0.721322 | 0.730254 | not submitted | Best local candidate so far; improves fixed and OOF, but still below the 0.74 target and needs more gain before final refit/submission. |
| `current_v1 + replay_last1 cap10000`, max_length 256, 5 epochs, lr 1e-5 | 0.722781 | 0.729483 | not run | not run | not submitted | Revisit only; slower and below the lr 2e-5 candidate on fixed validation. |

## Next Calibration Needs

- Treat the `current_v1 + replay_last1 cap10000`, length-256, 4-epoch OOF result as the current finalist baseline: raw 0.721322, tuned 0.725204.
- Compare fixed-session, OOF, and public deltas after each serious submission.
- Tune class bias, logit boosts, and ensembles on OOF logits rather than random validation.
- Prioritize fixes for the persistent OOF weak classes and confusions: `list_directory`, `read_file`, `grep_search`, `web_search`, and `lint_or_typecheck`.

## Serializer Screen Decisions

| Serializer quick screen | Replay | Quick Macro-F1 | Decision |
| --- | --- | ---: | --- |
| `current_v1`, max_length 160, 1 epoch | `last1`, cap10000 | 0.692097 | Keep as the Stage 1 serializer baseline. |
| `hybrid_v1`, max_length 160, 1 epoch | `last1`, cap10000 | 0.395243 | Reject for promotion; structured event text overwhelms/truncates useful signal at this budget. |
| `compact_events_v1`, max_length 160, 1 epoch | `last1`, cap10000 | 0.420960 | Reject for promotion; far below baseline and misses multiple weak classes entirely. |

## Loss Weight Screen Decisions

| Class weight power | Quick Macro-F1 | Decision |
| ---: | ---: | --- |
| 0.25 | 0.669840 | Reject; lower weighting weakens several weak classes. |
| 0.50 | 0.692097 | Keep as the active default. |
| 0.75 | 0.684071 | Reject; higher weighting hurts `read_file` and does not beat baseline. |

## Label Smoothing Screen Decisions

| Label smoothing | Quick Macro-F1 | Decision |
| ---: | ---: | --- |
| 0.00 | 0.679076 | Reject; hurts `read_file` and trails the baseline. |
| 0.02 | 0.692097 | Keep as the active default. |
| 0.05 | 0.680512 | Reject; hurts `ask_user` and trails the baseline. |

## Rule Boost Screen Decisions

| Candidate | OOF tuned | Fixed-session tuned | Decision |
| --- | ---: | ---: | --- |
| Baseline `current_v1 + replay_last1`, length 256, 4 epochs | 0.725204 | 0.731768 | Current finalist baseline. |
| Baseline + 6 deterministic OOF-tuned rule boosts | 0.728511 | 0.732621 | Keep as a finalist add-on. Gain is consistent but too small to justify final refit/submission alone. |
| Baseline + Markov/action prior | 0.725495 | not run | Reject; OOF gain is negligible. |
| Rule boosts + Markov/action prior + OOF bias retune | 0.729425 | 0.729958 | Reject; OOF improves, but fixed-session validation gets worse than rule boosts alone. |
| Rule boosts + fold-aware sparse SVC ensemble | 0.730254 | 0.735248 | Keep as the strongest current complementary component; not enough for submission alone. |
