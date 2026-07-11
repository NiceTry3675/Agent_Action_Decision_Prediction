# GPT-5.6 Parallel-Agent Semantic Relabel Report - 2026-07-10

## Scope and protocol

- Source: the weak-class error-enriched stratified casebook from
  `kd_hcx_m8_screen_s42`.
- Unit: 248 unique rows (the 340 casebook records contain 92 duplicates across
  false-negative and false-positive strata).
- Six primary GPT-5.6 model agents each read a disjoint blinded chunk in
  parallel. Dataset labels, model predictions, logits, and casebook strata were
  removed.
- Two additional GPT-5.6 audit agents independently reviewed the same 80
  priority rows. A separate GPT-5.6 adjudication pass reviewed the eight rows
  where both auditors disagreed with the primary agent.
- Labels describe the next action that the GPT-5.6 agents judged a reasonable
  coding agent would take from the observable state: `list_directory`,
  `read_file`, `grep_search`, `glob_pattern`, `other`, or `underdetermined`.

No human annotators participated in this audit. Agreement statistics below
measure consistency among independently executed GPT-5.6 agents, not
inter-human reliability. The existing `human_*` artifact and field names are
legacy schema names retained for compatibility; they refer to GPT-5.6 semantic
judgments in this report.

This is an error-enriched diagnostic sample. Agreement rates below do not
estimate label quality over the full 70k train set or the full validation set.

## GPT-5.6 agent consistency

| Check | Result |
| --- | ---: |
| Auditor A vs auditor B | 73/80 (91.3%) |
| All three agents unanimous | 65/80 (81.3%) |
| At least two agents agree | 80/80 (100%) |
| All three chose different labels | 0/80 |
| Main-review adjudications | 8/80 |

The large disagreement with the dataset is therefore not explained by one
GPT-5.6 agent applying an idiosyncratic boundary. This consistency does not,
however, constitute evidence of human agreement.

## GPT-5.6 semantic labels

| GPT-5.6 semantic label | Count | Share |
| --- | ---: | ---: |
| `grep_search` | 99 | 39.9% |
| `read_file` | 58 | 23.4% |
| `underdetermined` | 40 | 16.1% |
| `glob_pattern` | 26 | 10.5% |
| `list_directory` | 13 | 5.2% |
| `other` | 12 | 4.8% |

154 rows were `clear`, 54 were `plausible_multi`, and 40 were
`underdetermined`.

## Dataset label versus GPT-5.6 semantic judgment

| Dataset label | N | Exact GPT-5.6 agreement | Dataset label acceptable | Most common GPT-5.6 judgments |
| --- | ---: | ---: | ---: | --- |
| `list_directory` | 60 | 1 (1.7%) | 5 (8.3%) | grep 25, read 18, unclear 9, glob 7 |
| `read_file` | 60 | 16 (26.7%) | 27 (45.0%) | grep 22, read 16, unclear 10 |
| `grep_search` | 67 | 24 (35.8%) | 30 (44.8%) | grep 24, read 16, unclear 12 |
| `glob_pattern` | 60 | 11 (18.3%) | 15 (25.0%) | grep 28, glob 11, unclear 9, read 8 |

Across all 248 rows, exact agreement was 52/248 (21.0%); allowing every label
the GPT-5.6 agent marked reasonable raised it to 77/248 (31.0%). Even among the
154 agent-clear rows, the dataset label agreed on only 37 rows (24.0%).

## What the generator actually did

For 225/248 rows, the following session step was present in train data, so the
hidden target action and its arguments could be reconstructed. All 225 recovered
action names exactly matched `train_labels.csv`. This rules out a label join or
casebook assembly bug: the label records the synthetic coding agent's actual
next action. The agent action itself is often semantically disconnected from the
current request.

| Current request (abridged) | Dataset / recovered action | GPT-5.6 semantic label |
| --- | --- | --- |
| "airflow 참조하는지 다 찾아줘" | `list_directory(path=models)` | `grep_search` |
| "operator를 dag들 중 누가 쓰는지 전체에서 찾아줘" | `list_directory(path=models/marts)` | `grep_search` |
| "하드코딩된 거 있나 grep" | `list_directory(path=tests)` | `grep_search` |
| "레포에 tf 파일이 몇 개나 흩어져 있는지" | `list_directory(path=cmd)` | `glob_pattern` |
| "login을 어디어디서 import 하는지" | `glob_pattern(**/*.py)` | `grep_search` |
| "Cargo.toml에서 그 부분 어떻게 쓰는지" | `grep_search(timeout, benches/)` | `read_file` |
| "app 디렉토리에 뭐가 있는지 보여줘" | `glob_pattern(**/*.go)` | `list_directory` |

These are not subtle `grep` versus `glob` boundary cases. The chosen path,
pattern, or symbol is frequently unrelated to the explicit request.

## Interpretation

The weak classes contain two different prediction problems:

1. **Semantic action selection.** A GPT-5.6 semantic agent chooses a tool from
   the missing information: directory layout, known-file contents, content
   occurrences, or path candidates.
2. **Synthetic-agent policy imitation.** The competition target is the action
   the simulated agent happened to take, including path drift, action inertia,
   and apparently incoherent intermediate choices.

`list_directory` is the clearest case. In this error sample, the GPT-5.6 agents
rarely judged it to be an orientation request; most target rows were judged to
be content searches or concrete file reads. Improving semantic understanding
alone can therefore leave `list_directory` F1 unchanged or even reduce it.

## Testable consequences

- Use the legacy-named `human_relabels_final.jsonl` as a diagnostic audit set,
  not as replacement training truth. Public evaluation rewards generator
  imitation, not agreement with GPT-5.6 semantic judgment.
- Report candidate changes twice on these rows: agreement with dataset truth and
  agreement with the GPT-5.6 semantic label. A feature that only improves the
  former is learning generator policy; one that improves the latter is learning
  the GPT-5.6-defined semantic intent.
- Test a two-part predictor: semantic weak-class logits from `current_prompt`,
  plus a small residual over recent action sequence/result/path state for the
  generator-policy correction. The GPT-5.6 labels provide the semantic target
  for analysis, while the original labels remain the optimization target.
- Before estimating global noise rates, annotate a matched sample of correctly
  predicted weak rows. The present sample deliberately conditions on model
  errors and cannot establish how frequent semantic mismatch is overall.
