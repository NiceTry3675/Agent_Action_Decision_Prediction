# Leaderboard Calibration

## Known Submission Calibration

| Submitted model | Fixed session local | Random local | Public Macro-F1 | Local-Public gap | Likely cause | Validation adjustment decision |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `distilbert-base-multilingual-cased`, current serializer, 3 epochs, session-tuned class bias | 0.710721 | 0.722073 | ~0.698 | ~0.0127 vs fixed, ~0.0241 vs random | Single fixed split and random split are optimistic; weak exploration classes still confuse each other heavily. | Keep fixed session as fast control, treat random as historical only, require finalist OOF plus weak-class gains before the next serious submission. |
| `xlm-roberta-base`, Slack handoff run, max_length 192, 3 epochs, class-bias tuned | 0.718793 | not shared | 0.708057 | 0.010736 vs session | Transformer session split remains optimistic; the Canvas notes a possible mismatch from tuning bias on an 80% split and applying it to a 100% refit. | Treat as the current public calibration anchor and reproduce inside this repo before replacing the local finalist baseline. |
| Final package: XLM-R replay + rules + sparse SVC weight 4.0 | 0.751733 fixed cross-check | not run | 0.743 reported | -0.008733 vs fixed, +0.001119 vs OOF tuned | OOF was a good predictor for this package; fixed split remained optimistic. | Target met. Use OOF/rules/sparse path as the new submission baseline. |

## Current Interpretation

- The final XLM-R replay + rules + sparse package reached reported Public Macro-F1 0.743, clearing the 0.74 target.
- The Slack-shared 3-epoch `xlm-roberta-base` submission is now only a historical public anchor at 0.708057, with a session-local score of 0.718793 and a -0.0107 local-public gap.
- The new canonical fixed-session baseline is `xlm-roberta-base`, max_length 192, 5 epochs, no replay: raw 0.7354 and 2-stage bias-tuned 0.7389. It is stronger than the earlier 3-epoch XLM-R handoff, but its expected Public score is only about 0.726-0.728 after calibration.
- Live Dacon pages found on 2026-07-02 KST: submission tab `https://dacon.io/competitions/official/236694/mysubmission`, public leaderboard `https://dacon.io/competitions/official/236694/leaderboard`. The public web view is logged out; uploading `submit.zip` requires a logged-in team account.
- Live leaderboard snapshot before this result: rank 1 was 0.77975, rank 4 was 0.74359, and a 0.70805 row matching the prior XLM-R 3ep public anchor appeared on the board. The current package is reported at 0.743, so it is in the target band.
- The biggest repeated local errors are `grep_search -> read_file`, `read_file -> list_directory`, `grep_search -> list_directory`, and `run_bash -> run_tests`.
- Future candidates should improve fixed-session Macro-F1, save validation logits, and show broader weak-class improvement before final refit or leaderboard submission.

## Local Candidate Tracking

| Candidate | Fixed session raw | Fixed session tuned | OOF raw | OOF tuned | Public Macro-F1 | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `current_v1 + replay_last1 cap10000`, max_length 192, 3 epochs | 0.712909 | 0.718145 | not run | not run | not submitted | Keep as GPU candidate only; superseded by the stronger length-256 run. |
| `current_v1 + replay_last1 cap10000`, max_length 256, 4 epochs | 0.726448 | 0.731768 | 0.721322 | 0.725204 | not submitted | Strongest validated transformer so far, but OOF is below the fixed split and below the 0.74 target; do not final refit yet. Use as the finalist baseline for the next search. |
| `current_v1 + replay_last1 cap10000`, max_length 256, 4 epochs + OOF rule boosts | 0.726448 | 0.732621 | 0.721322 | 0.728511 | not submitted | Small consistent improvement over the finalist baseline; keep as a possible add-on, but still below the target. |
| `current_v1 + replay_last1 cap10000`, length 256 + OOF rule boosts + sparse SVC ensemble | 0.726448 | 0.735248 | 0.721322 | 0.730254 | not submitted | Best local candidate so far; improves fixed and OOF, but still below the 0.74 target and needs more gain before final refit/submission. |
| `xlm-roberta-base`, Slack shared, max_length 192, 3 epochs | not shared | 0.718793 | not run | not run | 0.708057 | Historical public anchor only; superseded locally by the 5-epoch XLM-R baseline. |
| `xlm-roberta-base`, Slack shared, max_length 192, 5 epochs, no replay | 0.7354 | 0.7389 | not run | not run | expected ~0.726-0.728 | Current canonical fixed-session baseline. Retrain under this repo if validation logits are needed; next direct comparison is replay_last1 cap10000 with 2-stage bias tuning. |
| `xlm-roberta-base`, current_v1, max_length 192, 5 epochs + replay_last1 cap10000 | 0.739664 | 0.744625 | 0.723739 | 0.728569 | not submitted | Fixed split was optimistic by about 0.016; not submission-ready. Test OOF rule boosts and sparse SVC ensemble, but do not final refit unless OOF moves near 0.74. |
| `xlm-roberta-base` replay + OOF rule boosts + sparse SVC ensemble, sparse weight 4.0 | not run | not run | 0.739852 combined raw | 0.741881 | not submitted | Best OOF candidate so far and above 0.74. Submission path now depends on packaging sparse SVC inference plus rule boosts and validating artifact size/runtime. |
| Final package: `xlm-roberta-base` replay + rules + sparse SVC weight 4.0 | 0.751733 fixed cross-check without final ensemble bias retune | not retuned on fixed | 0.739852 combined raw | 0.741881 | 0.743 reported | `submit.zip` cleared the 0.74 Public target; keep as achieved submission baseline. |
| `current_v1 + replay_last1 cap10000`, max_length 256, 5 epochs, lr 1e-5 | 0.722781 | 0.729483 | not run | not run | not submitted | Revisit only; slower and below the lr 2e-5 candidate on fixed validation. |

## Next Calibration Needs

- Treat `xlm-roberta-base`, max_length 192, 5 epochs, replay_last1 cap10000 as a strong fixed-session candidate but not validated for submission: OOF 2-stage tuned is only 0.728569.
- The current best OOF stack is XLM-R replay + OOF rule boosts + sparse SVC with sparse weight 4.0: 2-stage tuned OOF 0.741881.
- Final packaging is verified for the XLM-R replay + rules + sparse SVC weight 4.0 stack: `submit.zip` is 529MB and passed a zip-extracted offline smoke test with valid columns, id order, and labels.
- Dacon submission result is reported as Public Macro-F1 0.743, meeting the 0.74 target. Next work should be treated as leaderboard improvement, not target recovery.
- Compare fixed-session, OOF, and public deltas after each serious submission.
- Tune class bias, logit boosts, and ensembles on OOF logits rather than random validation.
- Prioritize fixes for the persistent OOF weak classes and confusions: `list_directory`, `read_file`, `grep_search`, `web_search`, and `lint_or_typecheck`.

## Serializer Screen Decisions

| Serializer quick screen | Replay | Quick Macro-F1 | Decision |
| --- | --- | ---: | --- |
| `current_v1`, max_length 160, 1 epoch | `last1`, cap10000 | 0.692097 | Keep as the Stage 1 serializer baseline. |
| `hybrid_v1`, max_length 160, 1 epoch | `last1`, cap10000 | 0.395243 | Reject for promotion; structured event text overwhelms/truncates useful signal at this budget. |
| `compact_events_v1`, max_length 160, 1 epoch | `last1`, cap10000 | 0.420960 | Reject for promotion; far below baseline and misses multiple weak classes entirely. |

## Alternative Encoder Screen Decisions

| Encoder quick screen | Serializer | Quick Macro-F1 | Decision |
| --- | --- | ---: | --- |
| `microsoft/xlm-align-base`, max_length 192, 1 epoch | `current_v1` | 0.133759 | Reject; far below XLM-R and DistilBERT screens, with `write_file` and `ask_user` at 0 F1. Do not spend full fixed-session or replay time here. |
| `microsoft/infoxlm-base`, max_length 192, 1 epoch | `current_v1` | 0.009553 | Reject; collapsed to predicting `read_file` for all 600 quick-val rows. Do not spend full fixed-session or replay time here. |
| `microsoft/mdeberta-v3-base`, max_length 192, 1 epoch, batch 8 | `current_v1` | 0.664922 | Do not promote; only matches the older DistilBERT no-replay quick screen, trails the active quick baselines, and is slower with slow-tokenizer fallback. |
| `bert-base-multilingual-cased`, max_length 192, 1 epoch | `current_v1` | 0.697580 | Keep only as possible ensemble diversity if the current package misses Public 0.74; do not full-validate before submitting the XLM-R finalist package. |

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
