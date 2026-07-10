# OOF Error Text Signal Summary - 2026-07-07

Source rows: `kd_m8blend_oof_len416_ep3_rules` 70k session OOF surface.
Predictions use logits + class bias + the 12 existing deterministic rule boosts.

Important caveat: this is the broadest local OOF surface, not the current
`kd_m8_refit` HCX/M8-full-refit Public baseline. The closest current-recipe
surface is the 14,001-row held-out screen
`experiments/logits/20260707_100521_gpu_transformer_session_current_v1_len384_replay-last1_kd_hcx_m8_screen_s42_val_logits.pt`.
That screen has the same major confusion shape, so the signals below should be
re-tested there or on a fresh HCX OOF before packaging.

## Generated Files

- `README.md`, `manifest.json`: extraction manifest.
- `*_preview.md`: readable samples.
- `*.jsonl`: all bidirectional wrong rows for the pair plus hard-correct
  contrast rows.
- `*_token_lift.tsv`: rough token/flag lift tables for seeding inspection.
- `hcx_kd_screen_pair_counts.json`: current-recipe held-out pair counts.

## Pair Counts

OOF wrong counts:

- `run_bash__run_tests`: `run_bash->run_tests=450`,
  `run_tests->run_bash=402`.
- `list_directory__read_file`: `list_directory->read_file=860`,
  `read_file->list_directory=1579`.
- `read_file__grep_search`: `read_file->grep_search=1258`,
  `grep_search->read_file=2470`.
- `list_directory__grep_search`: `list_directory->grep_search=476`,
  `grep_search->list_directory=1295`.
- `glob_pattern__read_file`: `glob_pattern->read_file=767`,
  `read_file->glob_pattern=434`.
- `glob_pattern__list_directory`: `glob_pattern->list_directory=746`,
  `list_directory->glob_pattern=211`.
- `glob_pattern__grep_search`: `glob_pattern->grep_search=497`,
  `grep_search->glob_pattern=449`.

Current HCX KD held-out screen wrong counts:

- `run_bash__run_tests`: `77` / `80`.
- `list_directory__read_file`: `158` / `314`.
- `read_file__grep_search`: `313` / `394`.
- `list_directory__grep_search`: `112` / `276`.
- `glob_pattern__read_file`: `156` / `91`.
- `glob_pattern__list_directory`: `157` / `37`.
- `glob_pattern__grep_search`: `125` / `67`.

## Main Findings

Broad lexical rules are mostly unsafe. The same words appear on both sides of
the weak-class boundaries:

- `test/spec/suite` is not a safe `run_tests` override. True `run_bash` often
  means "run this concrete command", including test/lint/build commands.
- `open/read/show/check` is not a safe `read_file` override. True
  `list_directory` and `grep_search` rows often use view verbs while the target
  is still unresolved.
- `files/folder/list` is not a safe `list_directory` override. It often appears
  in grep/file-set requests.
- `search/find` is not a safe `grep_search` override. It may mean find files,
  inspect a found file, or continue from a prior result.

The useful separators are composite: prompt intent + target specificity +
recent action/result + model top-pair/margin.

## File Exploration Cluster

Useful class semantics:

- `read_file`: a concrete file is selected or named, and the prompt asks to
  view it. Strong cues: exact slash path or basename plus
  `open/read/show/pull up/열어/보여/까보`, `that file/그 파일`, or previous
  list/glob/grep result narrowed to a candidate and the user selects one.
- `list_directory`: one directory inventory or navigation context. Strong cues:
  `repo root`, `what's under/in`, `src 밑/루트`, folder/directory contents,
  `구조`, `뭐뭐 들어있나`, `펼쳐봐`.
- `grep_search`: content lookup for a symbol/string/pattern. Strong cues:
  `references/usages/occurrences`, `where used/called/defined`,
  `어디서 쓰는지/호출/정의/참조`, quoted literals, function/member names.
- `glob_pattern`: a set of files by filename pattern, extension, or file
  category. Strong cues: `*.py`, `**/*.yml`, `glob`, `pattern/패턴`,
  `all .vue files`, `sql files 전부`, `files scattered/흩어져/어디어디`.

OOF-testable feature candidates:

- `prompt_exact_file_open`: view verb + exact path/basename or overlap with
  open files / last action args.
- `prompt_symbol_usage_search`: usage/reference/call/definition wording,
  excluding file-set and directory-inventory cues.
- `prompt_file_set_request`: wildcard/glob/pattern, or extension + all/every/
  files/전부/싹/어디어디.
- `prompt_dir_inventory`: root/folder/directory/tree/contents/구조/뭐뭐/펼쳐,
  excluding file-set and symbol-search cues.
- `last_result_source:{glob,list,grep,read}` and
  `last_result_count:{zero,one,few,many}`. Current `result_found` is too coarse.
- `slash_file_path`, `bare_filename`, and `symbol_like_path` should be separate
  features. Bare filenames/extensions alone should not drive `read_file`.

Candidate rules to tune, not hard-code:

- `last_action:list_directory + result_count:many + prompt_read_words`
  -> boost `read_file`.
- `top=read_file + last_action:read_file` -> small boost `list_directory`,
  especially when prompt has broaden/locate wording.
- `top=read_file + recent/last grep_search + locate/listish prompt`
  -> boost `list_directory`.
- `top=list_directory + last_action:glob_pattern + matched files + no inventory
  words` -> boost `read_file`.
- `top=grep_search + recent read_file + prompt view words`
  -> boost `read_file`.
- `top=read_file + usage/reference/call/where-used + symbol/literal`
  -> boost `grep_search`.
- `prompt_symbol_usage_search + top_pair glob_pattern>read_file`
  -> boost `grep_search`.
- `last_action:glob_pattern + result_count:many + top_pair read_file>grep_search
  + no exact file selection` -> boost/suppress toward `grep_search`.

## Shell/Test/Lint Cluster

Useful class semantics:

- `run_bash`: literal shell execution lane. Cues include named commands or
  runtime actions: `npm run`, `npx`, `python`, `node`, `bash`, `go build`,
  `go vet`, `go test`, `cargo build/clippy/test`, `uvicorn`, `runserver`,
  `pip install`, `docker build`, `dbt`, `airflow dags test`, `script`,
  `dry-run`, `server`, `boot`, `install`, `migration`, `console`, `traceback`.
- `run_tests`: structured test lane. More likely for abstract `run the tests`,
  `suite`, `regression`, `related tests`, `that test file`, when no exact shell
  command is named.
- `lint_or_typecheck`: structured static-check lane. More likely for abstract
  `type-check`, `static analysis`, `lint`, `unused import`, `type hints`,
  `format`, especially with a file/module target and no literal command.

OOF-testable feature candidates:

- `literal_shell_cmd_present`: executable command regex. Boost `run_bash` only
  when `run_bash` is already top-3 or margin is small.
- `structured_test_request`: test/suite/spec/case/regression words + no literal
  command + no static/build/install/server words.
- `structured_lint_request`: type/lint/static/unused/format words + file/module
  target + no literal command + no runtime/test words.
- `rerun_inherits_last_action`: `again/rerun/다시/한번 더/방금 그거/그 테스트`
  inherits last shell/test/lint family unless prompt names a new family.
- `runtime_smoke_shell`: server/app boot, URL load, console, traceback,
  browser/simulator, Android/iOS, migration/install/dry-run/training-step.
- `post_lint_to_tests`: recent clean lint/typecheck + current tests/regression/
  behavior wording -> boost `run_tests`.
- `post_tests_to_lint`: recent test runner + current type/lint/static/compile
  wording -> boost `lint_or_typecheck`, excluding literal shell commands.
- `test_path_target`: `tests/`, `*.test.*`, `*.spec.*`, `src/test/...`; with
  test words boost `run_tests`, with static words boost `lint_or_typecheck`.

Anti-rules:

- Do not map `pytest/jest/vitest/go test/cargo test` blindly to `run_tests`.
  Literal command phrasing often means true `run_bash`.
- Do not map `tsc/mypy/ruff/eslint/clippy/go vet` blindly to
  `lint_or_typecheck`; literal command phrasing often means true `run_bash`.
- Do not use CI status, dirty state, recent `run_tests`, or open test path alone
  as an override.

## Next Step

Add the feature families above to a temporary OOF rule-search script or to
`tune_oof_rule_boosts.py` behind opt-in flags, then tune on the broad OOF
surface and cross-check on the current HCX KD held-out screen. Only promote
small gated boosts that survive both surfaces; the best individual custom masks
seen by agents were around `+0.0001` macro on the broad OOF surface.
