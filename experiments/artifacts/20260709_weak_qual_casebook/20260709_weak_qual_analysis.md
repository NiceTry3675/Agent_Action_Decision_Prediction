# Weak-Class Qualitative Analysis - 2026-07-09

## Scope

- Baseline inspected: `kd_hcx_m8_screen_s42` validation logits
  (`experiments/logits/20260707_100521_gpu_transformer_session_current_v1_len384_replay-last1_kd_hcx_m8_screen_s42_val_logits.pt`).
- Public submissions, heavy GPU work, and training were not run.
- Generated casebook: `experiments/artifacts/20260709_weak_qual_casebook/`.
- Eight subagents inspected per-label casebooks, history/session slices, model
  variants, teacher/student disagreement rows, and lexical slices. The shared
  stratified casebook has 340 records / 248 unique ids; per-label casebooks each
  contain 60 false negatives and 25 false positives.

## Baseline Weak-Class Surface

Raw `kd_hcx_m8_screen_s42` weak F1:

| class | raw F1 | 2-stage F1 | main raw misses |
| --- | ---: | ---: | --- |
| `list_directory` | 0.5167 | 0.5181 | `read_file` 181, `grep_search` 78, `glob_pattern` 40 |
| `read_file` | 0.6219 | 0.6129 | `list_directory` 324, `grep_search` 202, `glob_pattern` 102 |
| `grep_search` | 0.6328 | 0.6494 | `read_file` 495, `list_directory` 286, `glob_pattern` 88 |
| `glob_pattern` | 0.6505 | 0.6540 | `read_file` 176, `list_directory` 165, `grep_search` 85 |

The weak classes form a mostly closed file-discovery cluster. Global class bias
is not the right shape: the 2-stage bias improves `grep_search` but pushes many
`read_file`, `list_directory`, and `glob_pattern` rows into grep.

## Qualitative Findings

1. `list_directory` is an orientation action, not a literal `list` verb.
   It is selected when the user needs scope discovery before semantic work:
   top-level layout, directory contents, "what is in X", or "where are files
   spread". Correct `list_directory` is heavily cold-start: 353/580 raw-correct
   rows are turn 1 / no history. But false `list_directory` predictions are also
   common on cold-start `read_file`, `grep_search`, and `glob_pattern` rows, so
   this needs guards.

2. `read_file` means concrete implementation inspection.
   Exact filenames plus `open/read/show/열어/통째로/that file` are useful cues,
   especially when the file was just written/opened. But exact path tokens alone
   are weak: weak rows with file-like tokens split across `read_file`,
   `grep_search`, `glob_pattern`, and `list_directory`. Some true `read_file`
   rows also contain `where/search/find`, usually when recent context implies one
   concrete file.

3. `grep_search` is usage/existence reconnaissance.
   Good cues are `where used/called/referenced`, `import`, `hardcoded`,
   `남아있나`, `어디서 쓰`, `호출`, `참조`, `grep/search/find`. A global grep
   boost is unsafe: fixed 2-stage already shows the read/list/glob tradeoff.

4. `glob_pattern` is candidate-file-set enumeration.
   It often appears without literal glob syntax: recursive file inventory,
   file-type list, "which files", "all tsx/java/yml/tf files", "흩어져",
   "어디어디". It can look like grep when the wording asks "where is X", but the
   expected action is to enumerate candidate files before reading/searching.

5. History result summaries are underused.
   `listed N entries`, `found N matches`, and `M files matched` create different
   next-action priors. After a directory listing, the next step may be read,
   grep, glob, or list again. After grep/glob hits, prompts like "which files",
   "two hits", "남았는지", and "that file" split into different labels. The
   current model collapses many of these to `read_file`.

6. Teacher signal is useful but not hard truth.
   M8 KD improved the HCX weak surface mainly by increasing `read_file` and
   `list_directory` recall and improving `grep_search`/`glob_pattern` precision.
   Full-refit M8 teacher is stronger than the student on weak rows, but it still
   shares the same dominant confusions. Teacher/student weak disagreements:
   teacher correct / student wrong 327, student correct / teacher wrong 96,
   both wrong different 96; teacher label is in student top-2 on 430/519
   disagreements.
