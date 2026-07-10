# Weak-Class Qualitative Casebook

Source: `experiments/logits/20260707_100521_gpu_transformer_session_current_v1_len384_replay-last1_kd_hcx_m8_screen_s42_val_logits.pt` (`kd_hcx_m8_screen_s42`, raw logits; 2-stage bias fields included for comparison).

Files:
- `summary.json`: aggregate counts and feature flags.
- `weak_errors_all.jsonl`: every validation row where true label is one of list_directory, read_file, grep_search, glob_pattern and raw prediction is wrong.
- `weak_rows_all.jsonl`: all rows where true or predicted label is weak.
- `weak_stratified_cases.jsonl`: combined stratified case sample across weak labels, 340 records.
- `<label>_cases.md/jsonl`: per-label readable false negatives plus false positives.

Raw weak per-class F1: {'list_directory': 0.5167037861915368, 'read_file': 0.6219081272084805, 'grep_search': 0.6328217237308147, 'glob_pattern': 0.6504751847940866}
2-stage weak per-class F1: {'list_directory': 0.5181159420289855, 'read_file': 0.6128686327077748, 'grep_search': 0.6493927125506073, 'glob_pattern': 0.6540335679480238}
