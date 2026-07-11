# Blinded next-action annotation

You are annotating rows from a coding-agent session dataset. Each row shows the
observable state of an in-progress session: `current_prompt` (the user's latest
message), `history` (alternating user messages and agent actions with result
summaries), and `session_meta` (workspace state).

Your task, for each row independently: **from a competent coding agent's
perspective, what is the correct next action?** Judge only from the observable
row. Do not try to guess dataset conventions or imitate patterns across rows —
each row is judged on its own merits.

## Labels (choose from exactly these)

- `read_file`: inspect the body or a contiguous region of an already-identified file.
- `grep_search`: locate occurrences by searching file CONTENTS for a string, symbol, or pattern.
- `list_directory`: view the contents/layout of a directory (orientation).
- `glob_pattern`: enumerate candidate paths by filename/extension/path-shape WITHOUT reading contents.
- `edit_file`: modify part of an existing file.
- `write_file`: create a new file or fully rewrite one.
- `apply_patch`: apply a prepared multi-hunk diff/patch.
- `run_bash`: run a shell command (build, install, git, general CLI — not tests/linters).
- `run_tests`: run the test suite or specific tests.
- `lint_or_typecheck`: run a linter, formatter check, or type checker.
- `ask_user`: ask the user a clarifying question before acting.
- `plan_task`: draft or update a multi-step plan before acting.
- `web_search`: search the web for external information.
- `respond_only`: answer the user in text; no tool action needed.
- `underdetermined`: the observable state does not support a unique next action
  (use only when you genuinely cannot rank one action above the alternatives).

## Guidance

- The immediate information contract of `current_prompt` takes priority over
  the user's distant end goal.
- Use `history` to resolve references ("that file", "the function you found")
  and to know what is already known/done. Use `session_meta` as state context
  (e.g. open_files, git_dirty), not as a label hint.
- `grep_search` vs `glob_pattern`: content search vs filename enumeration.
- `list_directory` is for orientation in a directory; if the user asks to find
  where something is USED or DEFINED, that is content search, not listing.
- If several actions are defensible, pick the best one as `top_label` and list
  every defensible one in `acceptable_labels`.

## Output

For each input row, output one JSON object on its own line:

```
{"id": "<row id>", "top_label": "<one label>", "acceptable_labels": ["<label>", ...], "identifiability": "clear|plausible_multi|underdetermined", "confidence": "high|medium|low", "rationale": "<one short sentence in English>"}
```

- `acceptable_labels` always includes `top_label`.
- If `top_label` is `underdetermined`, set `identifiability` to `underdetermined`
  and list the plausible candidates in `acceptable_labels`.
- Cover every row in the input file, in order, exactly once.
