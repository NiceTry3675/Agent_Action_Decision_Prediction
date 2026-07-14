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
  hidden-state payload on the deployment surface — gate cannot run),
  relational hidden-KD, and exact-predecessor replay metadata.

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
- **Lanes A/B completed and released:** relational-KD and replay-predecessor
  screens both failed the matched A100 control and their conditional refits
  are closed. Results were auto-collected and pulled; the 1.1 GB screen models
  remain on Drive and are intentionally not downloaded. See
  `experiments/artifacts/20260714_breakthrough_lane_ab_screen_decision.json`
  and `experiments/manifests/20260714_lane_[ab]_*.json`.

### If a screen passes

Full sieve×condalpha refit with the single validated variable (~1.5 h A100)
→ package int8 (explicit `--hf-dir`; never the stale default `model/`) →
offline smoke → Public. R1i + seq-exec can be re-appended to any new pack's
script (implementation in `rfinal_r1i_seqx.zip` / this repo's diagnostic
`experiments/artifacts/20260714_s202_transfer_p1_r1i_seqexec.py`).

---

## Entries

### 2026-07-14 ~22:42 — A/B breakthrough screens completed; both refits closed

- Both fixed-session seed42 A100 screens completed in ~51 minutes, saved all
  epoch checkpoints, auto-collected successfully, and were pulled by explicit
  run name after normal VM release. The SIGKILL cleanup-bypass recovery did not
  interrupt either detached trainer; no replacement VM or extra seed was used.
- **Lane A relational hidden-KD:** raw/bias/2-stage
  `0.784092/0.787127/0.787876`, versus matched control
  `0.785381/0.789876/0.790594`; deltas
  `-0.001289/-0.002749/-0.002718`. The implementation gate passed: all 70,000
  original rows aligned, 10,000 replay rows were relation-masked, and the
  hidden payload matched canonical teacher logits at `0.997129` argmax
  agreement (`max_abs=0.107422`).
- **Lane B exact-predecessor replay metadata:** raw/bias/2-stage
  `0.782022/0.784410/0.785709`; deltas
  `-0.003358/-0.005466/-0.004885`. Its audit exactly matched the card: 48,853
  tail candidates, 46,775 exact predecessors, 2,078 fail-closed missing drops,
  and the unchanged class-balanced cap selected 10,000.
- Priority classes make the rejection directional rather than a near-tie:
  isolated `read_file`/`glob_pattern` gains could not offset losses in
  `grep_search`, `web_search`, and `lint_or_typecheck`; predecessor replay also
  hurt `run_bash` and `run_tests`. **Decision:** launch neither champion refit,
  pull neither screen model, and spend no Public slot. Full per-class deltas:
  `experiments/artifacts/20260714_breakthrough_lane_ab_screen_decision.json`.
