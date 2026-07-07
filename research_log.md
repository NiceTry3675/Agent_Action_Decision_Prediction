# Research Decision Log

This file is a short decision index, not the full experiment notebook.

Full historical log through 2026-07-06 is archived at
`archive/research_log_20260702_20260706.md`.

## Sources Of Truth

- Current submitted baseline: `final_summary.md`.
- Public calibration and submission ledger: `leaderboard_calibration.md`.
- Experiment rows and exact commands: `experiments/results.csv`.
- Detailed metrics, rules, bias vectors, and probe artifacts:
  `experiments/artifacts/*.json`.
- Active GPU handoff as of 2026-07-06: `handoff_20260706_gpu_plan.md`.
- The old planning directory was removed after the Qwen3/M8 pivot because it
  described the superseded XLM-R/length milestone plan.

## Logging Rules

- Keep this file short: decisions only, no confusion tables or per-class dumps.
- Do not restate the current baseline score here; link to `final_summary.md`
  instead, so the score cannot drift.
- New detailed experiment numbers go to `experiments/results.csv` and artifacts.
- Add only durable decisions or reversals here.

## Compressed History

- 2026-07-02: XLM-R replay + OOF rules + sparse SVC reached Public `0.743` and
  became the first working baseline. OOF calibrated better than fixed-session.
- 2026-07-03: Early serializer/length variants and alternative encoders mostly
  failed or became ensemble-only candidates. Colab command-channel reliability was
  hardened after runtime reclaim issues.
- 2026-07-04: Public-gated workflow replaced the earlier OOF gate for submission
  promotion; leak overrides were probed and rejected hard on Public (`0.710`).
  Encoder-family gains were too small/noisy to carry the target alone.
- 2026-07-05: Decoder models became the real break: Qwen2.5-0.5B refit reached
  Public `0.770`, then Qwen3-0.6B refit with OOF bias/rules reached Public
  `0.780`. This is the current baseline line.
- 2026-07-05: Qwen3.5-0.8B showed real OOF quality (`0.774046` after rules), but
  direct deployment timed out on the server. Work shifted from model-quality
  proof to inference-volume reduction.

## Recent Decisions

### 2026-07-06 - M8 cascade quality is valid, but timing needs volume reduction

- Evidence: M7 0.6B and M8 0.8B OOF logits over the same 70k rows show
  confidence routing works. Routing 15% of rows captures roughly half of the
  0.8B edge; routing 50% slightly beats 0.8B alone.
- Decision: Keep cascade as the main M8 deployment shape: 0.6B on all rows, 0.8B
  only on low-confidence rows, then tune bias/rules on blended OOF logits.
- Constraint: with current_v1 and fp16 DeltaNet, r=15% still projects around
  `733s`, requiring server speedup `s >= 1.22`.
- Artifact: `experiments/artifacts/20260706_cascade_routing_sim.json`.

### 2026-07-06 - current_v5 is rejected as a quality replacement

- Evidence: Qwen3-0.6B current_v5 OOF landed below current_v1 at every tier:
  raw `0.751161` vs `0.755929`, 2-stage `0.755129` vs `0.758499`, rules
  `0.761686` vs `0.767129`.
- Decision: Do not replace current_v1 with current_v5 for quality.
- Follow-up: v5's token savings remain useful only if a minimal v6 recovers the
  loss without giving up most of the speed benefit.
- Artifacts: `experiments/artifacts/v5_qwen3_06b_oof_len384_ep3_oof_metrics.json`,
  `experiments/artifacts/v5_qwen3_06b_oof_len384_ep3_rules_rule_boosts.json`.

### 2026-07-06 - xmeta rule recovery failed

- Evidence: Adding v5-dropped meta fields back into the rule vocabulary recovered
  only `+0.0002` on v5 rules and did not help v1.
- Decision: The v5 loss is not recoverable by coarse rule flags for
  tier/language preference/budget/elapsed/loc. Treat the likely culprits as turn
  exactness, language dominance representation, or transformer-internal
  interactions.
- Artifacts:
  `experiments/artifacts/v5_qwen3_06b_oof_rules_xmeta_rule_boosts.json`,
  `experiments/artifacts/m7_qwen3_oof_rules_xmeta_rule_boosts.json`.

### 2026-07-06 - fp16 DeltaNet fallback is adopted for the 0.8B cascade leg

- Evidence: `fp16_state` matched stock argmax on 99.976% of 4096 rows and sped the
  trained 0.8B eager fallback by about 1.21x, reducing the ratio from 2.43 to
  2.01.
- Decision: Use fp16 DeltaNet in the 0.8B leg, gated by package-level smoke. The
  implementation must rebind each layer instance's `chunk_gated_delta_rule`;
  module-global monkeypatching after model load does not execute.
- Artifact: `experiments/artifacts/m8_fp16_deltanet_probe.json`.

### 2026-07-06 - current_v6 minimum spec is fixed

- Evidence: Structural rule probes over turn_x/trigram/result/candidate/path/
  overlap/open_n were effectively null: `0.767109` vs baseline rules `0.767129`.
- Decision: Do not add a struct line to v6. current_v6 is only current_v5 plus
  exact-turn/bin pairing and language dominance marker.
- Implementation state: `script.py` and `train_transformer.py` now register
  `current_v6`.
- Spec: `fe_current_v6_spec.md`.
- Artifact: `experiments/artifacts/m7_qwen3_oof_rules_struct_rule_boosts.json`.

### 2026-07-06 - depth-cut is rejected

- Evidence: M8 layer-saturation probing showed no early plateau. K=16 retained
  94.9% of final-layer probe F1; K=20 retained 96.9%, implying too much quality
  loss for the timing saved.
- Decision: Do not pursue depth-cut/refit as an M8 deployment lever.
- Remaining path: token reduction via v6, row reduction via cascade, fp16 DeltaNet
  for the routed 0.8B rows.
- Artifact: `experiments/artifacts/m8_depthcut_layer_probe.json`.

### 2026-07-06 - M8 compile-cache timeout probe was shape-contaminated

- Evidence: `submissions/m8_ep3_cc.zip` ships `hf_meta.compile.batch_size=64`
  with buckets `{256,400}`, but its embedded Inductor cache contains generated
  input-shape guards for `(128,192)` and no `(64,...)` input cache. The cache
  appears to come from the maxpack batch-128/config-A path rather than the
  submitted batch-64 path.
- Decision: Do not use the `m8_ep3_cc.zip` timeout as strong evidence that a
  correctly baked compile-cache path cannot run. The eager timeout still proves
  direct stock fallback is too slow, but the compile-cache submission likely
  cold-compiled or cache-missed on the server.
- Follow-up: Any future compile-cache package must bake and verify cache shapes
  against the exact `hf_meta.compile` batch size and buckets before submission.

### 2026-07-06 - M8 C-lane timing closes the all-row 0.8B path

- Evidence: C-lane exact b64/{256,400} cache guard rebuilt the intended cache on
  trained weights and fp16 DeltaNet matched stock on the correctness slice
  (`argmax_agreement=1.0`, max diff `0.0088`, 18 rebound layers). The cache no
  longer showed batch-128 contamination, but `torch.compile` still opened long
  CPU-bound compile work during measure/warm-cache execution, so Colab/Public CPU
  differences aside, the current compile-cache recipe is not package-ready.
- Evidence: no-compile eager fp16 DeltaNet timing on the same C-lane T4:
  Qwen3 denominator `74.98s/4096`; M8 `current_v6:352` `109.61s/4096`
  (ratio `1.462`, projected `699s` on the M7 infer anchor), `current_v6:384`
  `733.6s` projected, `current_v6:400` `733.4s`, and `current_v1:400`
  `914.8s`.
- Decision: 0.8B all-row standalone is still over the 10-minute target even with
  fp16 DeltaNet and `current_v6:352`. Use M8 only as a routed/cascade leg unless
  a new non-compile kernel lever appears; `current_v6:352` permits roughly
  `<=86%` of rows through M8 under a 600s infer budget.
- Artifacts:
  `experiments/artifacts/20260706_c_m8_b64_cache_guard_measure.json`,
  `experiments/artifacts/20260706_c_m8_fp16_eager_v6_len.json`.

### 2026-07-06 - inference loader fixed for int8 decoder packages

- Evidence: `script.py` reconstructed int8-codec weights as fp32, loaded a fp32
  model, moved it to GPU, and only then converted to half in the caller. Decoder
  configs also kept `use_cache=True` in saved config, relying on library-version
  defaults during sequence-classification inference.
- Decision: `script.py` now restores int8 weights directly to fp16 on CUDA,
  halves the model before loading, moves only the half model to GPU, and
  explicitly disables decoder cache on model configs. Local offline smoke passed
  on the current package.

### 2026-07-06 - FLA Triton autotune workaround probe is ready

- Context: The strongest FLA repro reached the fast path but failed inside
  Triton's autotuner while compiling Gated DeltaNet KKT/solve on T4. Earlier
  causal-conv install failures were not decisive; the failure to close is the
  T4 Triton compile path itself.
- Prepared probe: `colab/m8_fla_autotune_workaround_probe.py` tests two
  workarounds in isolated subprocesses: FLA 0.5.1 `FLA_CACHE_MODE=default`
  config files to skip autotune, and runtime Autotuner narrowing to one config
  per relevant kernel. First gate is one-batch correctness/survival; only then
  run full timing.
- Result: C-lane T4 smoke showed `fastpath_active=True`, but FLA 0.5.1 failed
  all tested KKT/solve configs (`BK` 32/64 x warps 1/2/4) under both
  config-cache and Autotuner-narrowed paths. FLA 0.5.0 also failed all six
  narrowed KKT/solve configs. Every failure was the same Triton
  `PassManager::run failed` at `chunk_gated_delta_rule_fwd_kkt_solve_kernel`.
- Decision: Treat current FLA fast path as closed for the server-matched
  T4/torch2.7.1/triton3.3.1 stack. A real fix would require rewriting or
  replacing the KTT/solve Triton kernel, not just picking a safer autotune
  config.
- Artifacts:
  `experiments/artifacts/20260706_c_m8_fla_autotune_smoke.json`,
  `experiments/artifacts/20260706_c_m8_fla_kkt_grid.json`,
  `experiments/artifacts/20260706_c_m8_fla050_kkt_grid.json`.

### 2026-07-06 - causal-conv1d-only is not a speed lever

- Evidence: C-lane T4 A/B installed only `causal_conv1d 1.6.2.post1` after a
  no-causal baseline, with FLA absent in both variants. The causal variant
  activated `causal_conv1d_fn` in all 18 DeltaNet layers and preserved logits on
  the correctness slice (`argmax_agreement=1.0`, max diff `0.0156`).
- Result: same-runtime `current_v6:352` timing over 4096 rows was no-causal
  `112.36s` infer / `114.69s` tokenize+infer versus causal-only `112.47s`
  infer / `114.53s` tokenize+infer. Infer was `0.1%` slower; tokenize+infer was
  `0.14%` faster due to tokenization noise. The projected all-row M8 time stays
  about `698s`, far above the 600s target.
- Decision: causal-conv1d is usable but not useful for this sequence-classifier
  prefill workload. Close it as a deployment lever; the real bottleneck remains
  DeltaNet fallback scan/recurrence, not the short depthwise conv.
- Artifacts:
  `experiments/artifacts/20260706_c_m8_causal_only_ab.json`,
  `experiments/artifacts/20260706_c_m8_causal_only_ab_no_causal.json`,
  `experiments/artifacts/20260706_c_m8_causal_only_ab_causal_only.json`.

### 2026-07-06 - current_v6e is the final serializer-compression probe

- Evidence: current_v6's fixed screen narrowly beat v5 at the aggregate but
  shifted too many ambiguous `read_file` rows into `list_directory`/`grep_search`.
- Decision: Test one guarded variant before closing the serializer-compression
  lane: current_v5 + exact turn + `path/overlap/cand` explorer evidence, while
  reverting v6's language-dominance marker back to v5 top-2 language names.
- Spec: `fe_current_v6e_spec.md`.
- Gate: Qwen3-0.6B fixed screen at `max_length=400`; only OOF if read/list/ask
  class behavior recovers enough to justify a full fold set.

### 2026-07-06 - current_v6 OOF is not a v1 replacement

- Evidence: current_v6 fixed screen was only a narrow pass over v5
  (`0.764251` vs `0.763387`) and stayed below the v1 fixed anchor
  (`0.770875`). The 3-fold OOF aggregate was `0.756118`, above v5 OOF
  (`0.755129`) but below v1 OOF (`0.758499`).
- Evidence: deterministic rule tuning finished at `0.762935`, beating v5
  +rules (`0.761686`) but missing M7/v1 +rules (`0.767129`) by `0.004194`.
  Weak classes after rules: `list_directory=0.4798`, `read_file=0.5843`,
  `grep_search=0.6018`, `glob_pattern=0.6572`, `ask_user=0.6604`.
- Decision: Do not promote current_v6 as a final M7 replacement or Public
  package. Keep it as compression/timing evidence; current_v6e remains the last
  guarded serializer-compression probe before returning to current_v1
  cascade/timing work.
- Artifacts:
  `experiments/artifacts/20260706_current_v6_qwen3_06b_token_lengths.json`,
  `experiments/artifacts/v6_qwen3_06b_oof_len384_ep3_oof_metrics.json`,
  `experiments/artifacts/v6_qwen3_06b_oof_len384_ep3_rules_rule_boosts.json`.

### 2026-07-06 - current_v6e fixed screen closes serializer compression

- Evidence: exact-spec `current_v6e` len400 batch16 hit sustained fp16 NaN from
  step 1500 onward and was killed before producing a result. Cache was freshly
  rebuilt on the VM, so this was not stale-cache contamination.
- Result: stability rerun with `batch_size=8`, `grad_accum_steps=2` (effective
  batch 16) completed at fixed-screen macro `0.764074` (`raw=0.757528`,
  `bias=0.762722`). This is above v5 (`0.763387`) but just below v6
  (`0.764251`) and far below the v1 fixed anchor (`0.770875`).
- Weak classes after 2-stage: `list_directory=0.4795`, `read_file=0.5809`,
  `grep_search=0.6034`, `glob_pattern=0.6393`, `ask_user=0.6730`. The
  intended `read_file` recovery was partial but missed the `0.59` target, and
  the aggregate did not clear v6.
- Decision: Do not run current_v6e OOF. Close the Qwen3-0.6B serializer
  compression lane and return to current_v1 cascade/timing work before spending
  a Public slot.
- Artifacts:
  `experiments/artifacts/20260706_current_v6e_qwen3_06b_token_lengths.json`,
  `experiments/artifacts/20260706_094913_gpu_transformer_session_current_v6e_len400_replay-last1_v6e_qwen3_06b_screen_b8a2_metrics.json`,
  `experiments/logits/20260706_094913_gpu_transformer_session_current_v6e_len400_replay-last1_v6e_qwen3_06b_screen_b8a2_val_logits.pt`.

### 2026-07-06 - OOF-diversity 프로브: 압축 serializer는 대체재가 아니라 앙상블 레그였다

- Why: serializer 압축 레인 종료 조건이 "새 OOF-diversity 가설"이었음. v5/v6가
  v1의 열화판인지, 다르게 틀리는 전문가인지를 기존 OOF 로짓 4세트(m7v1/v5/v6/
  m8v1)의 softmax 블렌드로 GPU 없이 판정.
- Evidence (`experiments/artifacts/20260706_oof_diversity_probe.json`, 블렌드
  체인은 results.csv `20260706_blend_*` 행): argmax 불일치 10.4-10.9% (vs m7).
  체인 결과 — **0.6B 트리플(v1+v5+v6) raw 0.7632/2stage 0.7664/rules 0.7724
  (+0.0053 vs m7 rules 0.7671)**; **m7+m8+v6 트리플 raw 0.7709/2stage 0.7728/
  rules 0.7782 (+0.0111 vs m7, +0.0041 vs m8 단독 0.7740)**; 2-leg m7+v6
  2stage 0.7639 (rules는 results.csv에 자동 기록). 이득은 약클래스 집중
  (rules 후 list 0.5089, read 0.6104). 가중치 강건(0.6B 트리플은 균등가중
  동급). 마진 라우팅: 하위 25-30%만 두 번째 레그로 보내면 블렌드 이득의
  86-94% 회수 — 캐스케이드 구조와 정합.
- Decision: FE 레인은 "v1 대체" 프레임에서 "다양성 레그" 프레임으로 전환.
  배포 경로 우선순위 — (1) m7+m8 캐스케이드의 품질 상금이 OOF-rules 0.778급
  으로 정량화됨(타이밍 벽은 기존 그대로, 라우팅 산수에 위 회수율 사용),
  (2) **KD 증류(타이밍 비용 0)**: `train_transformer.py`에 `--distill-logits/
  --distill-alpha/--distill-temp`(OOF 교사, replay 행 자동 제외 = 누수 없음)
  + `--optim adamw8bit` 추가, 2k행 로컬 스모크 통과. (3) 2-leg 직접 탑재는
  ~635s 초과 예상으로 T4 레플리카 실측 전 비추천.
- 실행: 로컬 3070 Ti(8GB)에서 KD 스크린 야간런 개시 — Qwen3-0.6B 학생,
  current_v1 len416 챔피언 레시피 + batch4×accum4 + grad-ckpt + adamw8bit,
  교사 = m7+m8+v6 블렌드(alpha 0.5, T 2.0), pid 기록
  `experiments/local_runs/kd_m8blend_qwen3_screen.log`. 게이트: fixed 2stage
  vs v1 앵커 0.770875 — 단 교사 폴드모델이 스크린 val 세션을 학습에 봤으므로
  스크린 수치는 약간 낙관 가능성, go/no-go 신호로만 쓰고 판정은 Public.
- Caveat: 블렌드는 폴드모델 기준 — 배포 팩은 refit 레그라 이득 크기 미검증.
  serializer-다양성 vs 시드-노이즈 다양성은 동일 serializer 2시드 OOF 없이는
  분리 불가(배포 가치엔 무영향).

## Current Next Step

KD 스크린(로컬 야간런) 판정 대기. 통과 시 KD refit 팩(체인: OOF 교사 재사용,
rules는 학생 자체 OOF로 재튜닝)이 Public 후보. 병행: m7+m8 캐스케이드 타이밍
(서버 복제 프로브)이 열리면 0.778급 OOF 체인이 상한. serializer 신규 압축
변형은 계속 닫힘 — 다양성 레그 프레임에서만 재사용.

### 2026-07-07 - 2-leg 캐스케이드 팩 조립 완료 (casc_v6m7_r20.zip) — 타이밍 프로브 준비

- Why: OOF-diversity 발견의 첫 배포 시도. v6 refit(Drive에 이미 존재)과 m7 refit을 재학습 없이 조립 — 품질보다 **캐스케이드 인프라·서버 타이밍 실측**이 목적 (성공 시 같은 인프라로 m8 leg 교체 = 0.778 체인 후보).
- 설계: v6 base 전 행(len384, 토큰 -20%) → base 마진 하위 **20% fraction-based 라우팅**(테스트 분포 무관 결정적 예산) → m7 leg(current_v1, len416) 0.5/0.5 확률 블렌드 → r20 분포 재튜닝 bias+12rules. OOF 체인: raw 0.759208 / 2stage 0.761703 / rules **0.767901** (M7 0.767129 +0.0008 = 품질 동급). K=20 선택 근거: 예상 ~555s/600s, K=25는 워스트케이스 타임아웃.
- 사이즈 해법: 두 int8 leg 원본 zip ~1079MB(한도 초과) → **임베딩 무손실 스파스 패치**: 두 leg의 embed int8 행 93% 동일(드리프트 0.30%) → m7 leg엔 상이한 10,805행 패치+자체 scale만 저장, 로드 시 v6에서 복원. **비트 단위 완전 복원 검증** (앞선 통째-공유 방식은 argmax 99.463%로 게이트 미달 → 폐기). m7 leg 613.9→469.6MB, zip **948.2MB**.
- 구현: `script.py` — `meta.cascade`(base/secondary/route_fraction/blend), `encoder_probs` tokenizer_dir, `load_int8_state_dict` shared_tensors 패치 복원(`__patch_rows__`/`__patch_idx__`). requirements는 초도 빌드에서 4.46.3 유입 버그 발견 → `requirements_qwen3.txt`(4.51 오버라이드, M7 동일)로 재빌드.
- 스모크: 클린 추출+CPU 오프라인 — 캐스케이드 전 경로 완주(공유 텐서 복원, 1/5행 라우팅), 컬럼/ID순서/라벨 검증 통과.
- 운영 교훈(2연속 WSL 사망 반영): 학습·대형 검증은 Colab, 로컬은 zip 조립+스텁 스모크만. fp32 전량 로드 비교 금지(int8 레벨 lazy 비교로 대체), 스테이징은 /tmp 금지(재부팅 소실) repo 디스크 사용.
- 병행: KD 스크린은 Colab G4에서 재런치되어 진행 중 (teacher=m7+m8+v6 블렌드, gate fixed 2stage vs 0.770875).
- Next: 사용자 제출 판단 대기. 제출 시 원장(leaderboard_calibration.md)에 기록 — 관측 대상은 (a) 총 추론 시간 vs 600s, (b) Public vs M7 0.780.

### 2026-07-07 - KD 스크린 게이트 통과: 2stage 0.784007 (프로젝트 fixed 최고)

- Evidence: Qwen3-0.6B 학생 + OOF 블렌드 교사(m7+m8+v6, 2stage 0.7728, alpha 0.5 T2.0), 챔피언 v1 레시피 fixed 스크린 — raw `0.780809` / bias `0.783273` / 2stage `0.784007`. v1 앵커 `0.770875` 대비 **+0.0131**, M8 0.8B 스크린(0.7804)도 상회. 약클래스 전반 상승: list 0.5106, read 0.6054, grep 0.6254, ask 0.6875. 행: results.csv `20260706_164744_..._kd_m8blend_qwen3_screen`.
- Caveat(사전 등록): 교사 폴드모델이 스크린 val 세션을 학습에 봤으므로 수치 일부는 낙관 가능. 학생 OOF도 같은 구조의 경미한 낙관을 공유(교사·학생 폴드 분할 동일) — 3중 중첩 없이 불가피한 표준 스태킹 리스크. 최종 판정은 Public.
- Decision: 게이트 통과 → 풀체인 진행. KD 학생 3-fold OOF(레인 A, chain_runs.py로 체이닝) → aggregate/rules(로컬 단독) → KD refit(--final-only, 아티팩트 주입) → int8 패키징 → Public. 부수 확인: KD 인프라(teacher OOF 정렬·replay 제외)와 8bit/ckpt 패치가 실전 검증됨.

### 2026-07-07 - KD refit 완료, int8 코덱 손실 소폭 증가 (기록용, 배포 진행)

- Evidence: `kd_m8blend_qwen3_refit` int8 검증(1024 샘플) — weight mean_rel error 0.952% / logit max_abs 0.069 / **argmax agreement 99.12%** (1024 샘플 결과). M6/M7 전례 99.61%(1024/2000 샘플) 대비 소폭 열화 — KD 학습이 만든 weight 분포가 row-wise int8 코덱에 약간 더 민감한 것으로 추정.
- Decision: 하드 게이트 문서화 이력 없음(과거 수치는 관측치이지 명시적 컷오프 아님); 절대 영향은 1024행 중 9행 argmax 반전(0.88%)으로 작음 → 패키징 계속 진행. 리스크로 기록만.

### 2026-07-07 - KD refit submitted: Public 0.782, new baseline (+0.002 vs M7)

- Evidence: `kd_m8blend_qwen3_refit.zip` Public **0.782**, runtime **8:54/10:00**. OOF chain was raw 0.782848 / 2stage 0.783540 / rules 0.786984.
- Transfer analysis: raw OOF (0.782848) landed almost exactly on Public (-0.0008) — the strongest raw->Public match seen this competition. But the 2-stage/rules layers gave **negative** transfer this round: rules 0.786984 -> Public 0.782 is -0.005, inverted from M7's rules 0.767129 -> Public 0.780 (+0.013).
- Interpretation: matches the pre-registered caveat exactly — teacher OOF (m7+m8+v6 fold models) and student OOF share the identical 3-fold split, so bias/rules tuning saw an optimistic surface (student predictions on val folds benefit from teacher signal computed on models that never saw those same folds as teachers, but the coincidence of split boundaries still correlates residual errors across teacher and student folds). The optimism partially reversed on Public. Net effect still positive because the raw KD signal itself is large (+0.027 OOF raw vs M7).
- Decision: **New baseline.** `final_summary.md`, `leaderboard_calibration.md` updated. M7 pack (`m7_qwen3_refit.zip`, Public 0.780) kept as fallback, not deleted. Do not treat future same-split KD+rules combinations as a free +0.02 lever — the rules layer specifically needs either a held-out slice for the student or discounted expectations when teacher/student share a fold split.
- Next: if another KD round is attempted, prefer a teacher OOF built on a *different* fold seed/split than the student's OOF, or skip rules-retuning on KD-student OOF and reuse a bias-only tune to reduce the optimistic-surface risk.

### 2026-07-07 - 신기록: HCX-0.5B refit Public 0.7852; KD-fold-leak 룰 확정

- Why: 챔피언 레시피(current_v1, focal, ep3, replay last1 cap10000, len384)에서 base model만 Qwen3-0.6B→`naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B`(HCX-0.5B, 0.5B)로 교체해 제출. 같은 blend 교사(m7+m8+v6, `export_teacher_logits.py`/`colab/run_teacher_export.py`로 export)로 HCX student KD도 병행 실험.
- Evidence: HCX-0.5B non-KD refit — fixed 2stage 0.769796 → **Public 0.7852**, 추론 6:29/10:00 (Qwen3-0.6B 라인 8:55 대비 -2:26, 주원인은 HCX 토크나이저 자체가 동일 current_v1 텍스트에서 ~11% 적은 토큰, mean 200 vs Qwen 226, len384 절단 사실상 0). 이전 최고(0.782, KD Qwen3-0.6B) 대비 +0.0032, M7(0.780) 대비 +0.0045(노이즈 룰 0.002 초과, 유의미). rules 레이어 없음(bias만). HCX vs Qwen3-0.6B 동일 fixed 14001행 비교: 총점 동급이나 클래스별 상보적 — HCX 우세(plan_task +0.037, read_file +0.028, list_directory +0.025, web_search +0.010), Qwen 우세(ask_user +0.041, HCX recall 0.56로 약함). 오류중첩 82%, 상호구제 608/531건, 로짓블렌드 fixed 2stage 0.7725(+0.0027) — 다양성 실재하나 2모델 배포는 타이밍 초과로 미채택.
- Evidence(KD-fold-leak, 핵심): 같은 blend 교사로 HCX student KD를 seed42 매치드로 실행 — 로컬 스크린은 +0.0122(raw 0.7697→0.7820, ask_user +0.075)로 대박처럼 보였으나 **동일 seed Public은 0.7827로 non-KD HCX(0.7852)보다 -0.0025**. 원인: 교사 블렌드가 fold별 OOF 예측인데 각 fold 모델은 train의 ~2/3을 학습 → 전체적으로 보면 학생의 로컬 val 행이 교사 학습데이터에서 완전히 배제된 게 아니라서, KD 타깃을 통해 val 정보가 간접 흡수됨. 이게 **로컬 fixed/OOF 스크린만 인플레이션시키고 Public엔 반영 안 됨.**
- Decision: 이 발견은 우리 자신의 2026-07-07 KD 엔트리(rules 레이어 OOF→Public 전이 -0.005)와 방향이 같고, 매치드시드라 훨씬 깨끗한 증거임 — 두 관측이 서로를 확증. **공통 룰로 채택: KD/스태킹 계열은 로컬 fixed/OOF 스크린으로 승격 판단 금지, matched-seed Public 제출로만 판정.** 교사 export 자체는 계속 유효(판정 방법만 교체). `final_summary.md`(Current Public Baseline을 HCX-0.5B 0.7852로 갱신), `leaderboard_calibration.md`(2행 + 방법론 기록) 갱신 완료.

### 2026-07-07 - HCX-0.5B 팩 흡수: 이 repo에서 직접 재현·검증 가능한 로컬 팩으로 승격

- Why: HCX-0.5B refit 산출물(`handoff_hcx_0707/`: hf_model 가중치, hf_meta.json, fixed-val 로짓)을 이 repo의 정식 경로로 흡수해 독립 검증하고, 우리 자체 서브미션 파이프라인(`package_submission.py`, offline smoke)으로 재포장.
- Evidence: 인계받은 `hf_meta.json`의 `class_bias`가 전부 0으로 비어있었음(final-refit 자체는 val split이 없어 별도 아티팩트에서 주입해야 하는데, 그 주입 단계가 handoff엔 빠져 있었음) — 첨부된 `hcx_screen_val_logits.pt`(fixed seed42, 14001행, raw 0.765997→old-bias 0.768743→2stage 0.769796)에서 tuned class_bias를 추출해 주입. rule_boosts 레이어는 원래 없음(레시피에 없었음, 정상).
- 작업: `experiments/incoming/models/hcx05b_refit/`로 복사 + bias 주입, val 로짓은 `experiments/logits/20260707_hcx05b_len384_screen_seed42_val_logits.pt`로 명명 이관, `experiments/results.csv`에 fixed-screen 행(`20260707_hcx05b_len384_screen_seed42`)과 refit 행(`20260707_hcx05b_refit`) 등록, `experiments/artifacts/20260707_hcx05b_refit_metrics.json`에 레시피·스코어·아키텍처 비교·KD 결과 종합 기록. `package_submission.py --requirements requirements_qwen3.txt`로 `submissions/hcx05b_refit.zip`(512MB) 재포장 — HCX는 Llama-family라 서버 stock 4.46.3에서도 로드되지만 기존 오버라이드를 그대로 사용(무해).
- 검증: 클린 추출 CPU 오프라인 스모크 전 항목 통과(컬럼/ID순서/라벨 유효성/int8 로드).
- Decision: `submissions/hcx05b_refit.zip`이 이제 이 repo가 직접 재현·재검증 가능한 현재 baseline. `final_summary.md`/`leaderboard_calibration.md`를 이 팩 기준으로 갱신(개인·채널 언급은 문서에서 제거, 기술적 내용만 유지 — 원본 handoff 자료는 `handoff_hcx_0707/`에 untracked로 보존).
- Next: HCX-0.5B 위에 OOF rule-boosts 레이어(M7/KD와 동일 파이프라인) 튜닝이 가장 자연스러운 다음 증분. KD 재시도 시엔 위 KD-fold-leak 룰(matched-seed Public 판정, 또는 교사 OOF를 학생과 다른 fold 분할로) 적용.

### 2026-07-07 - 신기록: kd_hcx_m8 Public 0.789 (M7 이후 최대 단발 도약)

- Evidence: HCX-0.5B student + M8(Qwen3.5-0.8B) 단독 teacher KD — Public **0.789**, 추론 6:32/10:00. 논-KD HCX(0.7852) 대비 +0.0038(노이즈 룰 0.002 초과), M7 돌파(0.780) 이후 최대 단발 도약. 추론시간은 논-KD HCX(6:29)와 사실상 동일 — KD는 학습 손실만 바꾸고 아키텍처/길이는 그대로라 타당. 기본 학습 레시피는 `hcx05b_refit_s42`(current_v1, len384, ep3, lr2e-5, batch16, focal g2.0, replay last1 cap10000, seed42)와 동일 — 여기에 M8 teacher KD만 추가.
- Interpretation(미확인, 가설): 앞선 m7+m8+v6 블렌드 교사 KD는 Public에서 역전(-0.0025)됐는데, 이번엔 M8 **단독** teacher로 양의 전이. 블렌드 교사가 fold별 OOF 스티칭이라 겪은 누수 메커니즘이 단일 teacher 구성에선 다르게 작용했을 가능성 — 단 정확한 teacher 로짓 출처(OOF fold vs full-refit)·레시피(alpha/temp/rules)를 확보 전엔 확정 아님.
- Decision: `final_summary.md`(Current Public Baseline → 0.789, `kd_hcx_m8`), `leaderboard_calibration.md`(행 추가) 갱신. 기본 레시피는 위처럼 확정(= hcx05b_refit_s42)이나, **KD 전용 하이퍼파라미터(alpha/temperature)·teacher 로짓 출처·rules 레이어 여부·실물 가중치는 아직 이 repo에 미동기화** — HCX-0.5B 흡수 때처럼 실물 확보 시 동일 절차(bias 주입 확인, 재포장, 독립 스모크)로 흡수 필요. 그 전까지는 Public 점수·추론시간·기본 레시피만 신뢰 가능한 사실이고 KD 세부값은 추정.
- Next action: 가중치/val 로짓 동기화되면 흡수. 병행: M8 단독-teacher KD 패턴이 유효하면 이 repo가 직접 재현 가능한 Qwen3-0.6B student 라인에도 같은 구성(블렌드 대신 M8 단독) 재시도 가치 있음 — 단 매치드시드 Public 판정 원칙은 유지.

### 2026-07-07 - HCX-0.5B 시드 분산 캘리브레이션: seed777이 seed42보다 -0.0202

- Evidence: `hcx05_s777` — `hcx05b_refit_s42`(non-KD HCX-0.5B)와 레시피 완전 동일, seed만 42→777. Public **0.765** (seed42 대비 -0.0202), 추론 6:27/10:00(seed42의 6:29와 사실상 동일 — 순수 시드 교체 확인).
- Decision: 베이스라인 변경 아님(kd_hcx_m8 0.789가 최고 유지). 다만 이 델타가 이 repo에서 지금까지 캘리브레이션된 시드 노이즈 밴드(±0.007~0.011, XLM-R/Qwen3-0.6B 라인 기준)의 약 2배 — **HCX-0.5B는 이 레시피에서 시드 분산이 그 라인들보다 실제로 더 클 수 있음.** 향후 HCX 라인의 단발 Public 델타(특히 <0.02)를 레시피 효과로 해석할 때 이 넓어진 밴드를 반영할 것.
- 부수 함의: Public=최종점수 100%(private 홀드아웃 없음) 하에서 시드 분산이 크다는 건 HCX 라인에 한해 best-of-N 시드 낚시의 기대값이 실질적으로 크다는 뜻 — 단 종반 우선순위 독트린(best-of-N은 대형 레버 소진 후 종반에)과 저울질 필요.
- `final_summary.md`(Current Public Baseline 절에 캘리브레이션 노트 추가), `leaderboard_calibration.md`(행 추가) 갱신.

### 2026-07-07 - kd_hcx_m8 teacher 출처 확인: M8 full-refit train70k 로짓, fold-OOF 아님

- Evidence: teacher 로짓 파일 `experiments/logits/m8_qwen35_refit_train70k_fp16.pt`(+`.npz`)가 이미 이 repo에 존재 — 직접 로드해 검증: `{ids, logits[70000,14] fp16, classes, y_true, metadata}`, `metadata.source="hf_model_export"`, `metadata.model_dir=".../models/m8_qwen35_refit"`(M8의 **full-refit** 모델), `row_count=70000`. 즉 3-fold OOF 스티칭이 아니라 **M8 full-refit 모델이 자기 학습셋 70000행 전체에 forward pass한 예측**. rules 레이어는 없음, 나머지(base 학습 세팅)는 `hcx05b_refit_s42`와 동일 — 사용자 확인.
- Decision: `final_summary.md`/`leaderboard_calibration.md`의 "unconfirmed 가설"을 검증된 사실로 갱신 — 앞선 m7+m8+v6 블렌드 KD가 겪은 fold-간 val 누수 경로가 이 teacher 구성엔 구조적으로 없음(단일 full-refit 모델의 train-set 예측이라 fold 경계 자체가 없음). 남은 미확인 항목은 KD alpha/temperature와 실제 HCX student 가중치뿐.

### 2026-07-07 - kd_hcx_m8 레시피 완성: alpha=0.5, temperature=3

- Evidence: KD 하이퍼파라미터 확정 — alpha 0.5, temperature 3. 이로써 `kd_hcx_m8`의 전체 레시피(base=`hcx05b_refit_s42` 그대로, teacher=M8 full-refit train70k 로짓, rules 없음, alpha/T)가 다 확정됨. 유일하게 남은 건 **실제 파인튜닝된 student 가중치 파일** — `naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B`는 공개 사전학습 베이스 체크포인트명일 뿐이고, 이미 흡수한 `hcx05b_refit`(논-KD 파인튜닝 결과물)과는 다른 별도의 파인튜닝 결과물(KD 손실 포함 학습)이라 아직 이 repo에 없음.
- Decision: 문서(final_summary.md/leaderboard_calibration.md)에 alpha/T 반영, 가중치 미보유 사실 명확화. 실제 가중치 확보 시 HCX 흡수 때와 동일 절차로 흡수.

### 2026-07-07 - R-Drop 스크린 게이트 통과: fixed 2stage +0.0064 vs HCX v1 앵커

- Evidence: `train_transformer.py`에 R-Drop 신규 구현(`--rdrop-alpha`, `--dropout` — 디코더 계열 기본 dropout이 0이라 별도 오버라이드 필요; attention_dropout만 실체 존재·sdpa에서 실제 적용 확인). alpha=1.0, dropout=0.1, HCX-0.5B 챔피언 레시피(v1, len384, focal, replay last1) **동일 seed42, 동일 fixed split**으로 앵커(`20260707_hcx05b_len384_screen_seed42`)와 비교: raw 0.765997→0.773416(+0.0074), bias 0.768743→0.775251(+0.0065), 2stage 0.769796→0.776188(+0.0064). 세 티어 전부 일관 양의 델타 — 학습 seed·split이 동일해 통상적 시드 노이즈 비교보다 깨끗한 대조. 약클래스: ask_user +0.0526(0.6310→0.6836), grep_search +0.0132, glob_pattern +0.0063, list_directory +0.0024, **read_file -0.0092**(유일 역행).
- Decision: 게이트 통과(+0.0064는 이 repo의 시드 노이즈 밴드 상단 근접이나, 동일 seed·split 대조라 노이즈보다 신호일 가능성이 큼). 다음 실험으로 dropout=0.1만 켜고 rdrop_alpha=0인 대조군을 레인 B에 즉시 런칭 — R-Drop의 KL 항 자체의 기여인지, 단순 dropout 정규화 추가 효과인지 분리 목적(둘 다 이 레시피에 처음 등장하는 변수라 원인 분리 없이는 승격 불가). 대조군이 앵커 수준으로 돌아가면 KL 항이 핵심(정식 R-Drop 채택), 대조군도 오르면 저비용 dropout=0.1 단독 추가가 더 나은 레버(R-Drop의 2배 학습비용 불필요).

### 2026-07-07 - HCX x XLM-R 인코더 블렌드 프로브 (CPU, 무-슬롯): 레인 닫기 권고

- Why: 챔피언(HCX-0.5B 라인)에 인코더(XLM-R) 다양성 레그를 붙일 가치가 있는지,
  기존 fixed seed42(14001행) val 로짓만으로 GPU/제출 없이 판정.
- Method: repo 표준 `train.py` 2stage 튜닝 재사용 — 재현 검증: HCX 단독
  2stage `0.769796`(기록치와 일치), m7 단독 `0.770845`(앵커 0.770875와 3e-5
  차이). 블렌드는 softmax 확률 가중평균, w는 raw 스윕.
- Evidence (`experiments/artifacts/20260707_hcx_xlmr_blend_probe.json`,
  results.csv `20260707_blend_*` 3행): 단독 — xlmr_base448 2stage `0.748091`,
  xlmr_large448 `0.756718`. 블렌드(vs HCX 단독 2stage) — HCX+base448 w50
  `+0.0020` / w70(val-튜닝) `+0.0041`; HCX+large448 w50 `+0.0073`;
  **HCX+m7(Qwen3-0.6B) w50 `+0.0086` / w55 `0.778619` (+0.0088)**.
- 판정: (1) XLM-R 다양성은 실재하나 **도미네이트** — 동일 방법론에서 Qwen
  레그가 base/large 모두 상회. (2) 배포 가능한 유일한 XLM-R 레그는 base448
  (HCX 512MB + int8 ~280MB < 1GB)인데 이득이 fixed 단일시드 노이즈 밴드
  (±0.005) 안. large448은 사이즈 초과(512+~560MB; 크로스-vocab이라
  casc_v6m7식 임베딩 패치 공유 불가). (3) **부수 발견**: 동일 방법론 재계산에서
  HCX+m7 fixed 2stage `0.7784-0.7786` — 기록된 handoff 벤치마크 `0.7725`
  (+0.0027)보다 +0.006 높음. 기존 벤치마크가 다른 블렌드 방식이었을 가능성;
  HCX x Qwen 페어의 다양성 상금이 기록보다 클 수 있음. 단 배포 블로커는 동일
  (합계 1110MB > 1GB, 임베딩 공유 불가, 07-07 워크숍 타이밍 기각 ~924s).
- Decision(권고): XLM-R 인코더 앙상블 레인은 닫는다 — 유일한 배포 가능 구성이
  노이즈 밴드 안이고 더 나은 레그(Qwen)에 도미네이트됨. 우선순위 레인
  (current_v2, soup/SWA, full-refit 멀티티처 KD)은 그대로. HCX+m7 페어의
  실제 상금 크기 재평가는 별도 논의 사항(사이즈 블로커 해결책 없이는 사장).

### 2026-07-07 - KD가 다양성을 먹었다: kd_hcx_m8 x m7 캐스케이드 레인 닫음

- Why: 전날 프로브(HCX_nonkd x m7 fixed 2stage +0.0086)가 배포 중인 kd_hcx_m8
  (M8 teacher KD) 위에서도 살아있는지 확인. 배포 인스턴스는 val split이 없어
  레인 C(GPU)에서 동일 레시피(alpha0.5 T3, M8 full-refit teacher, `--split
  session` seed42)로 스크린 1런을 돌려 진짜 held-out val 로짓을 확보
  (`20260707_100521_..._kd_hcx_m8_screen_s42_val_logits.pt`, fixed 2stage
  `0.787801` — 논-KD HCX 동일시드 `0.769796` 대비 +0.0180, 이 스크린 자체의
  절대치는 교사가 val을 학습에 본 표준 KD 낙관을 포함하므로 참고치일 뿐 판단
  근거 아님).
- Evidence (`experiments/artifacts/20260707_kd_hcx_m8_m7_blend_probe.json`,
  results.csv `20260707_blend_kd_hcx_m8_m7qwen3_fixed`): 동일 방법론(softmax
  블렌드, repo 표준 2stage) — **kd_hcx_m8_screen x m7 w50 2stage `0.785075`,
  KD 학생 단독(`0.787801`) 대비 `-0.002727`**(블렌드가 오히려 하락). 최적 w
  탐색(w85, 거의 KD 학생 단독)도 `+0.000529`뿐 — 게이트(+0.005) 명확히 미달.
  대조: 같은 m7 페어링이 논-KD HCX 위에서는 +0.0086(전날 프로브)이었음.
- Interpretation: M8(Qwen3.5-0.8B) teacher KD가 m7(Qwen3-0.6B)이 제공하던
  Qwen-계열 디코더 다양성을 이미 흡수 — 둘 다 같은 패밀리라 방향은 합리적.
  KD가 "의도한 대로" 작동했다는 신호이기도 함.
- Decision: **kd_hcx_m8 베이스 위에 m7 캐스케이드를 얹는 레인 닫음** — 사이즈/
  타이밍 문제를 풀 가치가 없음(품질 상금이 애초에 없음). XLM-R 등 완전히
  다른 아키텍처 패밀리 페어링은 미검증으로 남지만 XLM-R 단독 성능이 약하고
  (0.743) 사이즈 블로커도 동일해 우선순위 낮음, 재론하지 않음. 가동 중인
  우선순위 레인(current_v2, soup/SWA, full-refit 멀티티처 KD)에 집중.

### 2026-07-07 - kd_m8_refit 흡수: 팀원 KD 가중치를 repo 재현 가능 팩으로 승격 (Public 0.7891, 새 baseline)

- Why: 팀원 레인에서 학습·제출된 `kd_hcx_m8` 가중치 2종(fp16 원본 + 배포 int8 팩)을 인계받음. 레시피는 hcx05b_refit 챔피언 레시피 + M8 full-refit 교사 KD(`--distill-logits m8_qwen35_refit_train70k_fp16.pt --distill-alpha 0.5 --distill-temp 3.0`, seed42), Public **0.7891** / 추론 6:32.
- 검증(핵심): fp16 전 텐서를 repo의 int8-rowwise-v1 코덱으로 재양자화해 배포 int8 팩과 대조 — **219/219 텐서 비트 단위 일치**(양자화 170 + passthrough 49, scale 포함). 배포되어 0.7891을 찍은 모델이 정확히 이 fp16 체크포인트임을 직접 증명. 인계 zip 자체(SHA 93afe41b...)는 로컬에 없어(내용물만 전달) zip 해시 대조는 불가 — fp16 sha256 `9e684faa...b1842`를 repo측 앵커로 기록. 팩 동봉 script.py는 커밋 e66fd51 버전과 정확히 일치(CRLF만 차이), 고유 변경 없음.
- 작업: fp16 → `experiments/incoming/models/kd_m8_refit/`, 배포 int8 → `experiments/incoming/models/kd_m8_refit_int8/` (repo 관례: `<name>`=fp16, `<name>_int8`=int8). `package_submission.py --no-sparse --requirements requirements_qwen3.txt`로 `submissions/kd_m8_refit.zip`(512MB) 재포장, 클린 추출 오프라인 CPU 스모크 통과. class_bias는 전부 0(final refit 미튜닝) — 0.7891 인스턴스 그대로 유지, 바이어스 주입 안 함(주입은 별도 Public 검증 없인 금지). results.csv `20260707_kd_m8_refit` 행, `experiments/artifacts/20260707_kd_m8_refit_metrics.json` 등록. 인계 폴더(`kd_hcx_m8/`, `kd_m8_refit/`) 삭제.
- Decision: **새 Public baseline 0.7891** — `final_summary.md`/`leaderboard_calibration.md` 갱신 완료. 이 팩이 이제 repo에서 직접 재현·재검증 가능한 최고 성적 팩(이전 hcx05b_refit 0.7852는 non-KD 폴백으로 강등). fp16 원본 보존으로 가중치 공간 레버(seed soup/SWA/best-of-N)가 이 baseline 위에서 즉시 가능해짐.
- Next: 이 baseline 위 생존 레인은 기존 우선순위 그대로(current_v2 스크린, soup/SWA, full-refit 멀티티처 KD). 같은 날 판정된 kd×m7 캐스케이드 게이트 FAIL(위 엔트리)로 m7 페어링은 닫힘. lane-C 매치드 스크린의 2stage bias(0.787801) 주입은 미검증 마이크로 레버로만 보류 — 적용하려면 슬롯 1개로 Public 판정 필요.

### 2026-07-07 - R-Drop 어블레이션 판정: 이득은 KL 항에서 온다 — KD+R-Drop 스택 스크린 착수

- Evidence: dropout-only 대조군(`20260707_102926_..._dropout_only_hcx05b_screen`, dropout=0.1, rdrop_alpha=0, 그 외 R-Drop 스크린과 완전 동일 seed42/split) — raw 0.765322 / bias 0.767503 / 2stage **0.768854**. 논-KD 앵커(0.769796) 대비 -0.0009로 사실상 동일(노이즈), R-Drop(0.776188) 대비 -0.0073. 즉 **dropout 추가 자체는 무익하고, +0.0064는 R-Drop의 symmetric KL 일관성 항이 만든다** — 동일 seed·동일 split 3자 대조(앵커/dropout-only/R-Drop)로 원인 분리 완료. 부수 관찰: R-Drop 스크린에서 read_file만 -0.009 역행.
- Decision: R-Drop(alpha 1.0, dropout 0.1)을 검증된 학습 레버로 채택. 다음 게이트는 **KD와의 스택 여부** — 마침 lane-C의 kd_hcx_m8 matched 스크린 앵커(fixed 2stage `0.787801`, seed42, 동일 split·동일 M8 teacher)가 생겨서, KD+R-Drop 스크린을 같은 조건으로 돌리면 스크린끼리의 델타는 teacher-낙관을 양쪽이 공유하므로 유효한 대조가 됨(KD 레시피 '절대치' 판정은 여전히 Public 전용 — 이건 R-Drop 추가분의 상대 대조). 리스크 인지: KD soft target이 이미 일관성 정규화와 유사한 효과를 줄 수 있어 논-KD에서의 +0.0064가 KD 위에서 축소·소멸할 수 있음 — 그래서 리핏/슬롯 전에 스크린 게이트를 둠. 통과 시 `--final-model` 리핏 → Public matched-seed vs **0.7891**(kd_m8_refit).
- 인프라 노트: M8 teacher 로짓(`m8_qwen35_refit_train70k_fp16.pt`, gitignored)은 rclone으로 `AADP_exchange_b/logits/` 경유 VM에 스테이징(70000×14 로드 검증). R-Drop 구현은 리뷰 가드 포함(`--rdrop-alpha>0`에 `--dropout` 필수) — 기본 경로는 기존과 비트 동일(리뷰 확인).

### 2026-07-07 - 멀티턴 복구(current_v2) 스크린: 널 — 정보-복구 레인 닫음

- Why: current_v1이 74.6% 행에서 이전 user 발화를 버린다는 실측(70k 토큰화 재측정: v1 mean 200.5 → v2 mean 284.9, len448에서 7.1% 행만 절단)에 근거한 "정보 추가 방향" serializer 가설. current_v2는 07-05 Lane B 큐에 있다가 실행되지 않았던 것을 이번에 실행.
- Evidence (`20260707_120118_..._v2_hcx05b_screen`, HCX-0.5B 챔피언 레시피 len448, 동일 seed42/fixed split): raw 0.765973 / bias 0.767803 / 2stage **0.768813** vs v1 앵커 0.769796 — **-0.0010, 동률(노이즈)**. 약클래스: ask_user +0.0507(0.6310→0.6817)로 크게 개선됐지만 list_directory -0.0185, read_file -0.0185가 상쇄. 탐색 클래스 모호성이 이전 user 턴으로 풀리지 않는다는 데이터-정찰 가설(라벨이 텍스트 밖 시뮬레이터 잠재상태에서 결정됨)과 정합.
- Decision: **멀티턴 정보-복구 레인 닫음** — 같은 날 R-Drop과 달리 aggregate 개선 없음, len448의 추론시간 비용(~1.4x 토큰)까지 고려하면 배포 근거 전무. v5/v6/v6e 전례대로 널 스크린 후 변형 추격은 하지 않는다. 단 교차 관찰 기록: R-Drop(+0.053)과 v2(+0.051)가 둘 다 ask_user를 크게 올림 — ask_user는 표현/일관성 민감 클래스로 보이며, R-Drop 채택 시 이 클래스 개선은 그쪽에서 이미 확보됨.

### 2026-07-07 - KD+R-Drop 스택 게이트 FAIL: KD 위에서 R-Drop은 역효과 — 챔피언 라인에는 미채택

- Evidence (`20260707_121230_..._kd_rdrop_hcx05b_screen`, kd_hcx_m8 레시피 + rdrop alpha1.0/dropout0.1, 동일 seed42/split/teacher): raw 0.779322 / bias 0.783698 / 2stage **0.784191** vs KD 앵커(`20260707_100521_..._kd_hcx_m8_screen_s42`) raw 0.783852 / bias 0.787077 / 2stage 0.787801 — **전 티어 일관 -0.004 안팎(2stage -0.0036)**. 사전 등록한 리스크 그대로: KD soft target이 이미 일관성 정규화 역할을 하고 있어, 그 위에 R-Drop KL을 얹으면 과잉 정규화로 역행.
- Decision: **챔피언(KD) 라인에 R-Drop 미채택 — 스택 레인 닫음.** R-Drop의 지위 정리: (1) 논-KD HCX 라인에서는 실증된 +0.0064 레버(어제 어블레이션으로 KL 항 기여 확인), (2) KD 위에서는 -0.0036 역효과, (3) KD 대체재도 아님(논-KD+R-Drop 0.7762 << KD 0.7878). 용처가 있다면 논-KD 다양성 레그/teacher 학습 쪽뿐. alpha를 0.3-0.5로 낮춘 재스택은 기대값 낮아 추격하지 않음(음의 상호작용 확인된 마당에 최선 시나리오가 앵커 동률 수준).
- 오늘 사용자 지시 2개 테스트 최종 정리: **멀티턴 복구(v2) 널, R-Drop 논-KD 유효/KD 스택 실패** — 둘 다 챔피언 팩을 바꾸지 못함. 남은 고기대값 레인: 가중치 공간(soup/SWA/best-of-N — kd_m8_refit fp16 흡수로 즉시 가능), full-refit 멀티티처 KD(teacher export 필요).

### 2026-07-07 - 진행 중 2건: KD+R-Drop Public 프로브 리핏 + 2-teacher(M8+HCX) KD 결정

- KD+R-Drop 리핏 (레인 B A100, 사용자 결정): 스크린 -0.0036이 노이즈권이므로 Public으로 최종 판정. kd_m8_refit 조건 완전 미러(M8 teacher alpha0.5 T3, seed42, bias/rules 무주입) + R-Drop만 추가한 `--final-model` 리핏 → `kd_rdrop_hcx_s42.zip` 예정, 판정 vs **0.7891**.
- Teacher export 2건 완료 (레인 A L4, 슬롯 무소모): `m7_qwen3_refit_train70k_fp16.pt`(train acc 0.8352), `hcx05b_refit_train70k_fp16.pt`(0.8188) — 둘 다 full-refit forward 70000×14 fp16, `experiments/logits/`에 로컬 확보. **부수 발견: M8 teacher도 train acc 0.8115로 one-hot 암기가 아님** — "near one-hot이라 dark knowledge 제한" 우려는 과대평가였음.
- **kd_rdrop_hcx05b_refit 패키징 완료** (레인 B A100, 리핏 소요 ~1h52m: R-Drop 이중 forward 태그(스크린 실측 스텝당 ~1.8배) × full-data 80000행 3에폭=15000스텝, 스크린 대비 큰 것은 이 두 배수의 곱): fp16→int8 코덱 변환(1132.6MB→568.2MB), 512-샘플 무결성 검증 **argmax 99.80%(511/512)** — 이 repo 팩 중 최고 코덱 충실도(M6/M7 99.61%, kd_m8_refit 99.12% 대비). `submissions/kd_rdrop_hcx_s42.zip`(512MB) GPU+CPU 양쪽 클린추출 오프라인 스모크 통과(컬럼/ID순서/라벨 유효). class_bias 전부 0, rules 없음 — kd_m8_refit과 조건 완전 일치, 유일 변수는 R-Drop.

### 2026-07-07 - kd_rdrop_hcx_s42 Public 0.788: KD+R-Drop 스택 기각이 Public에서도 확인 — R-Drop 레인 최종 폐쇄

- Evidence: Public **0.788**, 추론 **6:37/10:00**(논-rdrop kd_m8_refit의 6:32 대비 +0:05, 노이즈 범위 — R-Drop은 학습 손실만 바꾸므로 예상대로 추론 경로 무변화). vs kd_m8_refit(0.7891, 동일 seed42/레시피, 유일 변수 R-Drop): **-0.0011, 0.002 노이즈 플로어 이내**.
- Decision: 스크린 단계 판정(-0.0036, KD 위 R-Drop 역효과)이 **matched-seed Public에서도 같은 방향으로 확인** — 크기는 줄었지만(스크린 -0.0036 → Public -0.0011) 방향 일치. 베이스라인 불변(kd_m8_refit 0.7891 유지). **R-Drop 레인 최종 정리**: 논-KD HCX 단독에서는 실증된 +0.0064 레버(KL 항 기여, dropout-only 대조로 분리 확인)지만, 챔피언(KD) 라인에는 스크린·Public 이중으로 기각 확정 — 추가 스택 재시도 계획 없음.
- 인프라 부수 성과: int8 코덱 무결성 이 repo 최고치(99.80%) 기록, 레시피 matched-seed 비교 파이프라인(스크린→풀리핏→패키징→Public) 전 과정 재검증됨 — 다음 KD 변형(2-teacher M8+HCX 등)에 그대로 재사용 가능.
- 멀티티처 구성 결정(사용자): **M8+HCX 2-teacher 50/50 softmax 평균** (`teacher_m8hcx_fullrefit_train70k_fp16.pt`, 평균 train acc 0.8160). M7은 제외 — 근거: kd_hcx_m8 학생 x m7 추론 블렌드가 음수(-0.0027, "KD가 다양성을 먹었다" 엔트리)라 교사로 섞어도 희석 우려. 실패했던 m7+m8+v6 블렌드 KD와의 구조적 차이: 그건 OOF-스티치(fold-leak + 2/3-데이터 약체 fold 모델), 이번 건 전부 full-refit forward라 해당 경로 부재. 리스크(사전 등록): HCX teacher가 학생과 동일 seed/레시피/베이스의 born-again 레그라 한계 정보가 작을 수 있고, M8 신호가 50%로 희석됨 — 판정은 matched-seed42 Public 1슬롯, vs 0.7891. kd_rdrop 리핏 종료 후 레인 B에 순차 런칭 예정.

### 2026-07-07 - 피처 레인 정밀 판정: 사후 referee 사망(+0.0003), v1 정보-완전성 실측, current_v7은 스크린 준비만

- Why: signal_summary(`oof_error_text_signals_20260707`)의 신호가 배포 가치가 있는지, "v1 최적화 없이 넘어가는 게 맞나"를 CPU 진단(무-GPU, 무-슬롯)으로 판정.
- Evidence(정보 인벤토리): v1 트렁케이션 캡(액션8/결과8/args 4키·10개/open6)은 70k 중 발동 0행(result_summary 120자 초과 11행 제외). v1이 버리는 유일한 정보는 이전 user 턴(74.6%) — v2 스크린으로 이미 널. **"정보 추가" 방향 serializer는 인벤토리상 공허 — 닫음.**
- Evidence(경계 신호는 실재하나 배포 스케일에서 소멸): 페어 경계 증분 AUC는 세션 GroupKFold에서 실재(glob 클러스터 +0.07~0.12, run_bash↔run_tests +0.09; read↔grep -0.005 사망; 신호원은 렉시컬이 아닌 액션 전이+결과 버킷). 그러나 진단 모집단(오답+hard-correct, 오답률 41-76%)과 달리 실배포 게이트(top2==pair) 모집단은 오답률 14-26%라, OOF-학습 referee의 배포 추정은 **OOF CV +0.0002 / 14k matched 스크린 전이 +0.0003 / 스크린 자체 CV -0.0025** — signal_summary의 "+0.0001 마스크"가 이 계열 전체의 정직한 상한임을 확인. **사후 룰/referee 레인 닫음, 슬롯 투입 금지.** 아티팩트: `oof_error_text_signals_20260707/{diag_serializer_headroom_20260707.json, pair_referee_oof_trained.json}` + 진단 스크립트 동봉.
- 준비(실행 아님): 마지막 잔존 피처 셀 = 표현 정규화. `current_v7`(= v1 + state 라인: last/prev action·result 버킷·ptype·rerun, current 라인 직후 삽입이라 절단에 안 잘림) 구현·등록 완료(script.py/train_transformer.py), 70k 전행 직렬화 검증(비-state 라인은 v1과 바이트 동일), HCX 토큰 mean +19.6/max 383으로 **len384 무절단 유지(one-variable 성립)**. 기대값은 referee 결과로 하향 조정(같은 피처를 사후적으로는 +0.0003밖에 못 벌음; v7은 인코더가 학습 중 비선형으로 활용한다는 좁은 베팅) — **유휴 레인에서만** 논-KD seed42 fixed 스크린 vs 앵커 0.769796 → 통과 시 KD 스크린 vs 0.787801 → 그 후에만 refit/슬롯.

### 2026-07-08 - 어텐션 싱크 프로브 → current_v7 재설계 (조기 state + ptype 앵커 + tail echo)

- Why(사용자 가설): "압축 serializer(v5/v6)의 실패는 저정보 토큰 제거로 남은 토큰 간 어텐션이 평준화된 탓" — xmeta 널(버린 필드 *내용*은 무가치)과 v5<v1(제거는 유해)의 모순을 "구조적 역할"로 해소하는 가설. 문헌 정합(attention sink/register/filler-token).
- Evidence(배포 kd_m8_refit fp16, CPU 프로브 64+48행): (1) position-0 싱크가 전체 어텐션 질량의 **0.609** — 주된 싱크는 meta 숫자들(질량 0.018)이 아니라 첫 토큰. (2) 같은 행을 v5로 직렬화하면 정규화 엔트로피 **+0.0243, 63/64행에서 더 평평** — 평탄화 방향 실측 확인(단 v1-학습 모델에 v5 입력은 OOD 교란, 인과 미확립). (3) 분류 토큰(마지막 토큰)의 비-싱크 어텐션은 강한 꼬리-국소: **마지막 40토큰이 0.263**, 중간 전 구간 0.199, 중간 라인들(meta/actions/last_user) 직접 참조 0.01-0.02. 아티팩트: `diag_serializer_headroom_20260707.json`의 attention_sink_probe 절 + `attn_sink_probe.py`/`attn_lasttok_view.py`.
- Decision(설계 원칙 채택 + v7 재설계): 검증된 serializer에서 **토큰 제거 금지**(압축 3연패의 메커니즘 후보), 추가는 짧게·스키마 일관·**앞쪽 배치**, 핵심 신호는 **중복 앵커**로 대비 생성. current_v7 갱신 — v1 전 라인 보존 + 조기 state 라인(last/prev **둘 다 버킷**, `ptype=<class>:<실제 매치 문자열>` 앵커, rerun) + **최종 라인 tail echo**(state2: 분류 토큰의 국소 창에 신호 배치, 측정 (3)이 근거). 70k 전행 재검증(무결성 통과), HCX 토큰 mean +40.6(241.3)/p99 358/max 402 — **len384 초과 0.075%는 잉여 echo만 잘림**(조기 사본 생존, 허용). 추론 비용 ~+20% 토큰(예상 ~7:50/10:00). 스크린 게이트·우선순위는 전 엔트리와 동일(유휴 레인 한정, 논-KD vs 0.769796 → KD vs 0.787801 → refit/슬롯). v6e 실패는 압축 베이스 위 실험이라 이 셀을 오염하지 않음(가설의 핵심 귀결).
- 추가(같은 날, 필러 치환 프로브 + v7r 준비): (1) meta 값을 `$`벽으로 치환해도 어텐션 구조가 v1과 기계적 동등(흡수 0.038 vs 0.033) — "역할=구조" 가설의 기계 수준 확증. 단 **끝 배치 junk는 분류 토큰 어텐션 0.250을 점거** — 꼬리는 신호 전용 자리(v7 echo 설계 재확인). 치환/v5+패딩 형태는 배포상 지배됨(최선이 v1 동률). (2) **추가 형태는 승리 채널 존재**(상수 K/V 레지스터: 행별 잡음→학습가능 상수 바이어스, ViT register 전례) → `current_v7r`(= v7 + `reg: $$$…` 동일토큰 16개 조기 라인) 구현·등록. 벽 토큰 감사: `#~%*-_.`은 연속열 1토큰 붕괴(슬롯 1개)로 부적합, `|` 필드구분자 충돌, `@` 코퍼스 1.17% 충돌 — 결선($/^/&/░) 어텐션 프로브에서 **$ 우승**(흡수 0.034 최대, 충돌 0.02%, 중립 임베딩). v7r 토큰 mean 261.3/max 422, len384 초과 0.625%(echo만 잘림). **실험 계획서: `handoff_20260708_v7_serializer_screens.md`**(3단 스크린: v7 논-KD vs 0.769796 → 통과 시 v7 KD vs 0.787801 + v7r vs v7 → refit/슬롯 vs 0.7891; 유휴 레인 한정, 2-teacher/M9 후순위).

### 2026-07-07 - 2-teacher(M8+HCX) KD 스크린: 완전 동률 — 풀리핏 진행 (레인 B는 이후 종료 예정)

- Evidence (`20260707_154645_..._kd_m8hcx_screen_s42`, teacher=M8+HCX 50/50 softmax 평균 full-refit 로짓, kd_hcx_m8_screen_s42와 동일 seed42/split/레시피, 유일 변수는 teacher 구성): raw 0.784021 / bias 0.786454 / 2stage **0.787757** vs KD-only 앵커(`kd_hcx_m8_screen_s42`) raw 0.783852 / bias 0.787077 / 2stage 0.787801 — **2stage 델타 -0.00004, 사실상 완전 동률**(raw는 오히려 +0.0002 우세). 약클래스: list_directory +0.0026, read_file +0.0019, glob_pattern +0.0017, ask_user +0.0085 소폭 개선, grep_search -0.011 하락 — 재분배 수준, 방향성 신호 아님.
- Decision(사용자 지시): 동률/우세 시 풀리핏+패키징 진행 — `kd_m8hcx_refit` 런칭(레인 B, kd_m8_refit 컨벤션 완전 준수: bias/rules 무주입, seed42, alpha0.5/T3). 완료 후 판정은 matched-seed Public(0.7891) 대비로만 유효(KD/스태킹 표준 룰). **이 리핏+패키징을 끝으로 레인 B와 이 세션의 야간 자율 작업 종료** — 사용자 지시로 범위 축소(추가 레인/실험 없음).
