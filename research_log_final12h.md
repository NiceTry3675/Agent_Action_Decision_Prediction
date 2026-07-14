# Research Log — Final 12 Hours

Continuation of `research_log.md` (frozen at the 2026-07-14 s202-transfer-gate
entry). All new entries from 2026-07-14 ~23:00 KST onward go here. Deadline:
**2026-07-15 (Wed) 10:00 KST**; the final day has the full 10 submission slots.
Dacon retains the team's highest score, so lower-scoring probes cost only slots.

## State snapshot (2026-07-14 ~23:00 KST)

- **Team Public champion: `submissions/rfinal_r1i_seqx.zip`, displayed
  `0.7966`, runtime `7:29`.** Exact rfinal trio archive (models byte-identical)
  plus two s202-transfer-validated hard rules (R1i, sequence-exec) in
  `script.py`. Public 1st is `0.79862`; **remaining gap ~`0.00202`**.
- Previous champion `kd_ens3_trio_rfinal` (`0.7963584846`) and its exact
  archive, the ens2 R1b archive, and seed202 OOF folds 1/2 are local
  (`experiments/manifests/20260714_team_champion_assets.json`).
- Local reproducible fallback: `kd_sieve_ca_s42.zip` (`0.7938816426`).

### Standing protocol adopted today

- **s202 transfer gate (validated end-to-end):** any post-hoc
  calibration/prior/rule lever must be re-measured on the deployed main
  model's own OOF surface (seed202 folds, local) before shipping. It admitted
  R1i + seq-exec (landed `+0.00024` Public) and rejected the P1 soft prior,
  which was `+0.0005` on the seed42 surface but negative on s202 — the same
  failure mode as the rejected `read_file -0.14` bias.
- Rejected/fail-closed today: P1 soft prior (transfer), A4 hidden-kNN (no
  hidden-state payload on the deployment surface — gate cannot run).

### Active lanes

- **Lane C (this session): terminal-teacher KD student screen**
  (`kd_terminal_teacher_m8_screen_s42`) — the last unresolved orthogonal
  training lever. Teacher (terminal-token M8, train argmax `0.809`) is done and
  verified; the student screen was stalled ~51 min by an HF Xet download hang
  (CLOSE-WAIT socket, cache stuck at 200 MB), the hung child was killed via
  the Drive cmd channel, and a staged-base fix (`colab/stage_hcx05b_base.py`,
  SHA-verified install from `AADP_exchange_b/assets/hcx05b_base`) plus a plan
  relaunch are queued on the daemon. Caveat: the lane C CLI session state was
  wiped by a transient 404/401 and keep-alive is dead — the VM survives on the
  Drive control plane only and may idle out; if reclaimed, remount is needed.
  Screen gate: control raw/bias/2stage `0.785381/0.789876/0.790594`.
- **Lanes A/B (parallel session): relational-KD and replay-predecessor
  screens/refits** — see `colab/relational_kd_lane_a_*.json`,
  `colab/replay_predecessor_lane_b_*.json`, and
  `experiments/manifests/20260714_lane_[ab]_*.json`. Both new
  `train_transformer.py` axes default off (`--relational-kd-weight 0.0`,
  `--replay-meta-mode current`); the default path is bit-identical
  (unit-tested).

### If a screen passes

Full sieve×condalpha refit with the single validated variable (~1.5 h A100)
→ package int8 (explicit `--hf-dir`; never the stale default `model/`) →
offline smoke → Public. R1i + seq-exec can be re-appended to any new pack's
script (implementation in `rfinal_r1i_seqx.zip` / this repo's diagnostic
`experiments/artifacts/20260714_s202_transfer_p1_r1i_seqexec.py`).

---

## Entries
