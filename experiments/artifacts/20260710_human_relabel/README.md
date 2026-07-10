# Human Semantic Relabel - 2026-07-10

This audit relabels the unique rows in the weak-class stratified casebook from
a human information-need perspective. Annotators are blinded to the dataset
label, model prediction, and logits.

## Labels

- `list_directory`: unfiltered or lightly scoped orientation; answer is the
  contents/layout of a directory or nearby workspace area.
- `read_file`: inspect the body or a contiguous region of an already identified
  file.
- `grep_search`: locate occurrences by testing file contents for a string,
  symbol, semantic feature, or behavior.
- `glob_pattern`: enumerate candidate paths by filename, extension, path shape,
  or file type without inspecting file contents.
- `other`: the requested next action is outside the four weak classes.
- `underdetermined`: the observable row does not support a unique next action.

## Annotation fields

- `human_label`: one label above.
- `acceptable_labels`: every label a reasonable coding agent could choose next.
- `information_need`: `orient_layout`, `inspect_known_file`,
  `locate_content_occurrence`, `enumerate_path_candidates`, `other`, or
  `unclear`.
- `identifiability`: `clear`, `plausible_multi`, or `underdetermined`.
- `confidence`: `high`, `medium`, or `low`.
- `rationale_ko`: one concise Korean sentence grounded in the row.

History is used only to resolve references and determine what is already known.
Session metadata is state context, not a proxy label. The immediate information
contract of `current_prompt` takes priority over the user's distant end goal.

## Outputs

- `human_relabels_final.jsonl`: final 248-row annotation set with original
  truth/prediction, reconstructed generator action when available, human label,
  acceptable labels, confidence, rationale, and review status.
- `summary_final.json`: aggregate human-label, agreement, and audit statistics.
- `human_relabel_report.md`: methodology, representative cases, interpretation,
  and testable consequences.
- `annotations/`: six blinded primary chunks and two independent 80-row audits.
- `audit_comparison.jsonl` and `adjudications.jsonl`: reviewer votes and the
  main review of eight disputed rows.
