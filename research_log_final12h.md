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
- Rejected/fail-closed today: P1 soft prior (transfer), A4 hidden-kNN,
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

### 2026-07-15 ~02:10 — mgn125: NEW CHAMPION by 1.2e-5; widening axis closed

- `rfinal_mgn125.zip` Public **`0.7966244725`**, runtime `7:46` (projection
  7:57). `+0.0000118616` over `rfinal_r1i_seqx` — noise-level, but retained by
  Dacon max, so the team standing improves and the gap to 1st is now
  **`0.0019955275`**. Unlike the c0a8 tie, predictions did change: the net
  ensemble rescue in the widened [1.00,1.25) band is ~break-even, i.e. the
  ensemble-gain gradient is flat beyond margin 1.0. **Do not spend a slot on a
  1.5 widening.** The s202-voter sieve refit (`kd_sieve_ca_s202v_refit_s42`)
  is training on lane C as the remaining gap-scale card.

### 2026-07-15 ~01:40 — c0a8 swap probe: exact tie; margin-1.25 probe submitted

- `rfinal_c0a8_swap.zip` Public **`0.7966126109` — identical to the champion
  to 10 decimals**, runtime `7:17`. The member swap flipped zero hidden-test
  predictions: same-recipe-family members are prediction-equivalent inside the
  low-margin z-centered trio average. **Member-swap axis closed** absent a
  genuinely diverse strong member (none exists locally — the cross-family
  candidates are all ~0.70-0.75 class). Side value: the champion's exact
  score digits are now known (`rfinal_r1i_seqx` = `0.7966126109`; gap to 1st
  `0.79862` = `0.0019473891`).
- Submitted `rfinal_mgn125.zip` (last slot of the window): single variable =
  routing margin `<1.0 -> <1.25` (24.4% -> 31.9% routed). Basis: s202
  margin-band audit — [1.00,1.25) carries a 41.5% error rate, matching the
  routed band's density, while [1.25,1.50) drops to 24.5%; projected runtime
  `7:57`. Result pending. Duplicate build `rfinal_margin125.zip` (bit-identical
  script) removed; `rfinal_mgn125.zip` is the canonical artifact.
- Lane 1 (s202-voter consensus sieve refit) proceeds in the parallel session;
  payload `20260715_m7_m8_s202v_oof_consensus.pt` was reconstruction-verified
  here before handoff (4,039/70,000 counts changed; c=3 48,607 -> 49,325).

### 2026-07-15 ~00:50 — terminal-teacher screen REJECTED; c0a8 member-swap probe ready

- `kd_terminal_teacher_m8_screen_s42` (run `20260714_142018`, staged-base fix,
  full 3-arm plan, rc=0): raw/bias/2stage `0.782026/0.786332/0.787495` vs
  control `0.785381/0.789876/0.790594` — **deltas
  `-0.003355/-0.003544/-0.003099`, all tiers clearly negative**, no weak-class
  compensation (list `0.5223`, read `0.6281`, grep `0.6392`, glob `0.6508`).
- **Decision: reject; do not launch the staged refit; no slot spent. The
  terminal-token lane is now closed on both sides** (student pooling
  `-0.0020`, teacher signal `-0.0031..-0.0035`), consistent with every prior
  teacher-signal replacement failing (tm8 `0.7867`). The soft-target-quality
  thesis is answered negatively for this family.
- **`submissions/rfinal_c0a8_swap.zip` is submission-ready** (SHA256
  `f552bfe2...c92b29`, 1,004,646,637 B): champion pack with only the model_c
  weights swapped s7070-int4 -> c0a8-int4 (`int4-group128-v1` codec parity
  verified; fidelity 128/128 argmax, TV mean `0.0176`; clean-extraction CPU
  smoke ran the full 3-member ensemble + 12 rules). Pure Public probe —
  no honest local surface exists for member quality; seed-swap precedent
  ~`+0.0004`; costs one slot, zero score risk (Dacon keeps the max).
- Lane C A100 session remains up with a healthy keep-alive (release deferred
  to the user: teammate's corrected A/B re-runs may want a warm A100 with the
  HCX base already staged).

- Reopened the read_file logit bias with the one condition its earlier
  rejection named: tuning on the deployment model's own OOF (s202 folds, now
  local), on the full deployed pipeline (rules + R1i + seq-exec) with
  two-direction LOFO. Result: f1-tuned `-0.26` confirms `-0.000947` on f2;
  f2-tuned `-0.02` confirms `-0.000046` on f1; the preregistered team value
  `-0.14` is `+0.000055/-0.000648`. Fold2's entire grid is flat.
- **Decision: the bias signal is fold noise, not model calibration — closed
  for good; no slot spent.** Fourth decision-shaped lever rejected today.
- Prepared and hotfixed onto the lane C VM the single-variable full-refit plan
  `colab/terminal_teacher_refit_lane_c_plan.json` (exact kd_sieve_ca_s42
  command with only `--distill-logits` swapped to the terminal-M8 payload;
  output `kd_sieve_ca_tterm_refit_s42`) so the refit can launch the moment the
  screen reads out. Slot posture (user): teammate lanes are consuming slots —
  submit in priority order refit > (bias: dead) > member-swap probe, gated
  cards only, and lenient screen reading per user directive.

### 2026-07-14 ~23:00 — A4 hidden-kNN rejected at the s202 transfer gate

- Teammate handoff `s202_hidden_train70k_fp16.pt` (70,000 x 1024 fp16,
  "s202 kd_sieve_ca last-layer last-token pre-score hidden") landed at the
  repo root, unblocking the previously fail-closed A4 gate. Built A4-compatible
  caches per s202 OOF fold (fold rows = queries with honest OOF logits;
  datastore = the other 46.7k rows; label alignment cross-checked) and ran the
  unmodified `audit_hidden_knn_memory.py`.
- **Confirm-fold deltas: fold1 `-0.000408` macro / `-0.001427` Weak4; fold2
  `+0.000206` / `+0.000720`.** Sign-inconsistent across folds, pooled ~zero,
  rescue:harm 1.17/1.24, and the selected config is unstable (k=5,w=0.4 vs
  k=31,w=0.8). All of this under an *optimistic* bias: query hiddens come from
  the full-refit model that trained on those rows, a flattery deployment will
  not enjoy.
- **Decision: do not ship the kNN.** The clean champion-surface `+0.001062`
  (5/5 folds) evaporates on the deployed model's own surface. Third
  decision-shaped lever to die at this gate (read_file bias, P1, now A4) —
  consistent with the standing conclusion that only distribution-shaped
  mechanisms have ever paid on Public. Artifacts:
  `experiments/artifacts/20260714_s202_knn_gate_f{1,2}.json`.
- Lane C screen meanwhile healthy on the fresh session: staging + verifier
  arms passed, training at step 1500 with falling loss.

> **2026-07-15 correction (user-reported):** an implementation mistake was
> found in the lane A/B experiments below; the teammate plans to re-run both.
> Treat the two rejections as VOID pending the corrected re-experiments, not
> as closed lanes.

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
