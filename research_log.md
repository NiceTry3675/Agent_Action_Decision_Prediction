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
