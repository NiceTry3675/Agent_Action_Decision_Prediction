# OOF Error Text Signals 20260707

Source: `experiments/artifacts/kd_m8blend_oof_len416_ep3_rules_rule_boosts.json`

Recomputed base macro: `0.783540`
Recomputed rules macro: `0.786984`

Files:
- `run_bash__run_tests.jsonl` records=2052 wrong={'run_bash->run_tests': 450, 'run_tests->run_bash': 402} contrast={'run_bash': 600, 'run_tests': 600}
- `list_directory__read_file.jsonl` records=3639 wrong={'list_directory->read_file': 860, 'read_file->list_directory': 1579} contrast={'list_directory': 600, 'read_file': 600}
- `read_file__grep_search.jsonl` records=4928 wrong={'read_file->grep_search': 1258, 'grep_search->read_file': 2470} contrast={'read_file': 600, 'grep_search': 600}
- `list_directory__grep_search.jsonl` records=2971 wrong={'list_directory->grep_search': 476, 'grep_search->list_directory': 1295} contrast={'list_directory': 600, 'grep_search': 600}
- `glob_pattern__read_file.jsonl` records=2401 wrong={'glob_pattern->read_file': 767, 'read_file->glob_pattern': 434} contrast={'glob_pattern': 600, 'read_file': 600}
- `glob_pattern__list_directory.jsonl` records=2157 wrong={'glob_pattern->list_directory': 746, 'list_directory->glob_pattern': 211} contrast={'glob_pattern': 600, 'list_directory': 600}
- `glob_pattern__grep_search.jsonl` records=2146 wrong={'glob_pattern->grep_search': 497, 'grep_search->glob_pattern': 449} contrast={'glob_pattern': 600, 'grep_search': 600}
- `run_bash__lint_or_typecheck.jsonl` records=1831 wrong={'run_bash->lint_or_typecheck': 353, 'lint_or_typecheck->run_bash': 278} contrast={'run_bash': 600, 'lint_or_typecheck': 600}
- `run_tests__lint_or_typecheck.jsonl` records=1802 wrong={'run_tests->lint_or_typecheck': 283, 'lint_or_typecheck->run_tests': 319} contrast={'run_tests': 600, 'lint_or_typecheck': 600}

JSONL records include true/pred labels, prompt, last events, session_meta, selected text features, top5 predictions, and serialized current_v1 text.
