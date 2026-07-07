# Teacher Soft-Label Exports

Generated for the student/KD lane on 2026-07-07 KST.

## Files

- `m8_qwen35_refit_train70k_fp16.pt`
  - Single M8 teacher export.
  - Source model: `m8_qwen35_refit` (`igorktech/Qwen3.5-0.8B-Base-LM`, `current_v1`, max length 400).
  - Values: raw sequence-classification logits.
  - Shape: `[70000, 14]`, dtype `float16`.

- `m8_qwen35_refit_train70k_fp16.npz`
  - Same payload as above, NumPy compressed format.

- `teacher_m7m8v6_train70k_fp16.pt`
  - Additional ensemble teacher.
  - Source payload: `20260706_blend_m7m8v6_fold_all_val_logits.pt`.
  - Values: log-probability style softmax blend scores, stored in the `logits` field for KD compatibility.
  - Shape: `[70000, 14]`, dtype `float16`.

- `teacher_m7m8v6_train70k_fp16.npz`
  - Same payload as above, NumPy compressed format.

## Payload Schema

The `.pt` files are dictionaries with:

- `ids`: lexicographically sorted train ids.
- `logits`: fp16 tensor `[70000, 14]`.
- `classes`: class names in column order.
- `labels`: gold label names, aligned to `ids`.
- `y_true`: integer gold labels, aligned to `ids`.
- `metadata`: source and export details.

The `.npz` files contain equivalent arrays plus `metadata_json`.

## Class Order

```text
0 read_file
1 grep_search
2 list_directory
3 glob_pattern
4 edit_file
5 write_file
6 apply_patch
7 run_bash
8 run_tests
9 lint_or_typecheck
10 ask_user
11 plan_task
12 web_search
13 respond_only
```
