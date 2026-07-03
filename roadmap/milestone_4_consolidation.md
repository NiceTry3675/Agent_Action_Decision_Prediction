# Milestone 4 — Consolidation & submission

**Dates**: 07-11 → 07-14. **Status**: process fixed regardless of what
M1-M3 produce; content (which model/ensemble gets packaged) depends on
their outcomes.

## Process

1. Re-run the **full tuning chain from scratch** (`aggregate_oof.py` →
   `tune_oof_rule_boosts.py` → `tune_sparse_svc_oof.py`) on the final
   combined logit stack — component-level tunings don't compose
   additively, bias/rules/sparse-weight must be re-derived against the
   final error pattern.
2. Final refit:
   ```
   .venv/bin/python train_transformer.py ... --final-model --save-fp16 \
     --rule-boosts-path <final> --class-bias-artifact <final>
   .venv/bin/python train_sparse_svc_final.py --retune-artifact <final> \
     --sparse-weight <winning> --text-serializer current_v1
   ```
   (the sparse SVC step only applies if sparse SVC is still part of the
   final package — see M3's mBERT/xlm-r-large dependency note).
3. Rebuild `submit.zip` exactly per `README.md` at the repo root
   (`script.py`, `requirements.txt`, `model/` at archive root only).
4. Smoke test exactly per `AGENTS.md`, from a clean zip extraction with
   `data/` added:
   ```
   TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 python script.py
   CUDA_VISIBLE_DEVICES='' python script.py
   ```
   Verify: columns exactly `id,action`; ID order matches
   `sample_submission.csv`; all 14 valid labels only; zip <1GB; archive root
   exactly 3 entries; real (not extrapolated) inference timing — this
   matters more than usual given the longer `max_length` from M1.
5. Update `final_summary.md`, `leaderboard_calibration.md`, `research_log.md`
   minimally per `AGENTS.md`'s "where results go" rules.
6. **Calibration submissions** (~4-6 total across the 12 days, each gated on
   beating the current OOF baseline post-repackaging+smoke):
   - One after M1 if length clears OOF (re-anchor the OOF→Public gap
     early — only one calibration point exists as of the roadmap's start).
   - One after M2's combined package.
   - One or two during/after M3 as wins land.
   - One final one in M4.
   - Always keep the last known-good package ready as a guaranteed fallback
     before 07-15.
7. **Freeze by end of 07-14.** 07-15 is buffer only — verify the submission
   registered correctly, no further changes.

## Documentation tasks

- Every calibration submission gets a one-line `research_log.md` entry:
  package contents, OOF number, resulting Public score, and the OOF→Public
  gap (to keep refining the working "OOF ≈ Public ± 0.01" assumption used
  throughout this roadmap).
- The final submitted package's full configuration goes into
  `final_summary.md` in the same format as the current entry (Model
  Configuration table + Scores table).
