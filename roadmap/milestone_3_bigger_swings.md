# Milestone 3 — Bigger swings

**Dates**: 07-08 → 07-11. **Status**: candidates identified, sequencing and
final scope decided at the Checkpoint (see `README.md`) — do not start
executing here until the Checkpoint has run and this file's Status line has
been updated with the chosen sequencing.

## Candidates

### mBERT as a real second ensemble member

`bert-base-multilingual-cased` scored 0.6976 on a qv600 screen vs.
XLM-R-base's own 0.6887 under identical conditions, never taken past qv600.
Train through the full fixed→OOF pipeline on M1/M2's winning length/
hyperparameters; blend `xlmr_logits + w*mbert_logits + rules + sparse` via a
small weight sweep over saved OOF logits (analogous to M1 Phase 0's sparse
sweep).

**Only viable if the final base model is XLM-R-base** (package math:
553+356+58≈967MB, tight but fits) — **not viable if Track B's
xlm-roberta-large won M1/M2** (no room left under 1GB). This dependency is
exactly why this milestone's scope waits for the Checkpoint.

### Confusion-cluster feature engineering

The read_file/grep_search/list_directory/glob_pattern cluster needs sharper
features distinguishing "args reference a specific filename vs. a glob
pattern vs. a bare directory vs. nothing" — a discriminability fix, not a
data-volume fix. Requires adding tokens/features to
`serialize_transformer_sample_current` in `script.py` (or a new serializer
variant), then its own qv600→fixed→OOF cycle, validated in isolation before
combining with other M3 wins.

Highest engineering risk, highest single-shot potential (could close a
large chunk of the gap since it targets the dominant unsolved error mode
directly, or land at 0) — the strongest candidate for "go deep on one
thing" if the Checkpoint comes in short.

### xlm-roberta-large full commitment

If Track B's scouting (M1) came back healthy and the Checkpoint shows
headroom — this would already be underway from M1 Track B Step 4, so this
milestone here just covers whether to promote it to the final package.

## Documentation tasks

To be finalized at the Checkpoint re-plan, but at minimum:
- Whichever candidates are attempted get their own `research_log.md`
  decision entries (win or lose).
- The Checkpoint re-plan itself gets written into this file (replacing the
  "candidates identified, not yet sequenced" framing with the actual chosen
  sequencing and updated Status line) rather than a separate scattered note.
