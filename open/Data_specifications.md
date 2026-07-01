This is a problem where, at a specific point while an AI coding agent is conversing with a user and performing work, the agent's next action must be predicted as one of 14 classes.

# Distributed Data Structure

```text
open/

baseline_submit.zip : Example leaderboard submission file (zip) based on the baseline code (for reference)
data/
  - train.jsonl : Training input data (70,000 records)
  - train_labels.csv : Training label data (70,000 rows x 2 columns)
  - test.jsonl : Evaluation input data (5 sample records for format checking)
  - sample_submission.csv : Submission format file (2 columns)
```

Note: `test.jsonl` includes only 5 records in the distributed version for format checking. The actual evaluation data (30,000 records) is private and is processed by the evaluation server when code is submitted.

Note: `train.jsonl` and `test.jsonl` use the JSON Lines format, where each line is one sample (JSON object).

## Details

### 1) Training/Inference Data: `train.jsonl` and `test.jsonl`

Each line (one JSON object) represents the state of an agent session at a specific point in time and consists of the following 4 fields. (`test.jsonl` has the same structure but does not include the ground-truth `action`.)

- `id`: Unique sample identifier (e.g., `sess_sim_20260522_028750-step_02`)
- `session_meta`: Session and workspace metadata
  - `user_tier`: Subscription tier (`enterprise` / `pro` / `free`)
  - `language_pref`: Preferred language (`ko` / `en` / `mixed`)
  - `budget_tokens_remaining`: Remaining token budget (integer)
  - `turn_index`: Current turn number (smaller values indicate earlier points in the session)
  - `elapsed_session_sec`: Elapsed session time in seconds
  - `workspace`: Workspace state
    - `language_mix`: Language ratio in the codebase (e.g., `{"py": 0.45, "sql": 0.30}`, sum ~= 1.0)
    - `loc`: Total number of lines of code
    - `git_dirty`: Whether uncommitted changes exist (`true` / `false`)
    - `open_files`: List of open file paths (`[]` if none)
    - `last_ci_status`: Last CI status (`passed` / `failed` / `none`)
- `history`: Conversation and action records up to the previous point in time (chronological order, 0-12 entries, alternating `user` -> `assistant_action`)
  - User turn: `role`, `content` (utterance text)
  - Action turn: `role`, `name` (action name / one of the 14 classes), `args` (action-specific arguments), `result_summary` (result summary)
- `current_prompt`: Current (most recent) user utterance. The next action at this point is the prediction target.

### 2) Training Label Data: `train_labels.csv`

- `id`: Sample identifier linked to `train.jsonl`
- `action`: Prediction target. One of the following 14 classes:
  - `read_file`: read a file; `grep_search`: search by pattern; `list_directory`: list a directory; `glob_pattern`: search by glob pattern
  - `edit_file`: edit an existing file; `write_file`: create a new file; `apply_patch`: apply a patch/diff
  - `run_bash`: run a shell command; `run_tests`: run tests; `lint_or_typecheck`: run linting/type checking
  - `ask_user`: ask the user a question; `plan_task`: create a task plan; `web_search`: search the web; `respond_only`: respond without using tools

### 3) Model Inference Result Format File: `sample_submission.csv`

- `id`: Sample identifier from the evaluation data (`test.jsonl`)
- `action`: Predicted value (one of the 14 classes above)

Note: During inference, the `action` value for the prediction target must follow the 14 classes above, and the class names must match exactly, including letter case.
