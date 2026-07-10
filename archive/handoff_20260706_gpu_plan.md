# 2026-07-06 GPU 실행 플랜 핸드오프 (실행 에이전트용)

마감 ~07-14. 현 베이스라인 Public 0.780 (M7 Qwen3-0.6B v1 팩), +0.01이면 1등 동률.
전략 문맥: `research_log.md` 2026-07-06 엔트리들, `fe_current_v6_spec.md`,
`fe_raw_data_review.md`. 프로토콜: `AGENTS.md`(승급 규칙)·`colab/COLAB.md`(레인 조작).

## 오늘 확정된 사실 (재논쟁 금지)

| 판정 | 수치 | 기록 |
|---|---|---|
| v5 순수 교체 기각 (3-fold OOF) | raw 0.7512/2stage 0.7551/+rules 0.7617 vs v1 0.7559/0.7585/0.7671; 손실 탐색 클래스 집중 | research_log 07-06 |
| xmeta 룰 회수 실패 | +0.0002 (목표 -0.0054) — v5 계보 종결, 후속은 v6 | 〃 |
| fp16 DeltaNet 채택 | argmax 일치 99.976%@4096, 스톡 대비 1.21x (ratio 2.43→2.01) | `m8_fp16_deltanet_probe.json` |
| 몽키패치 함정 | 폴백은 레이어 인스턴스에 생성 시 바인딩 — **모듈 전역 패치 무효**, 인스턴스 리바인딩 필수 | 〃 |
| 조건부 MI | elapsed/pace/budget/burn 사망(≤0.007 bits\|turn); turn 정확값>구간화; lang 지배도 생존; hist_trunc≡turn>6 | `fe_current_v6_spec.md` |
| 캐스케이드 산수 | v1+fp16 r=15% ≈733s→s≥1.22 / v6 목표 ≈610-630s→s≥1.03-1.05 (앵커: 서버 530=로드52+추론478, s∈(1,1.85) 열림) | research_log |

## 진행 중 (인계 시점)

1. **depth-cut 프로브: 완료 — 기각.** 분류 신호가 최상층까지 상승, 조기
   포화 없음: K=16 → 최종 대비 94.9%, K=20 → 96.9% (절대 ~-0.025급 손실 vs
   0.8B 전체 엣지 +0.0095) — 산수 성립 불가
   (`experiments/artifacts/m8_depthcut_layer_probe.json`). **아래 5번의
   depth-cut 카드는 폐기됨.** 레인 A 런타임은 유휴 해제된 상태 — 다음 GPU
   작업(v6 스크린) 시 새로 열고 0단계 위생부터 (직전 런타임에 5.13이
   깔렸었음; 새 런타임이면 어차피 4.46.3부터 시작).
2. **로컬 struct 룰 프로브: 완료 — 널 판정.** 0.767109 vs 기준 0.767129,
   struct 룰 1개만 한계 선택 (`m7_qwen3_oof_rules_struct_rule_boosts.json`).
   → **v6 최종형 = v5 + turn 정확값 병기 + lang 지배도 마커** (struct 라인
   없음, `fe_current_v6_spec.md` 개정본 기준). 기대값은 "v1 동급 품질 +
   토큰 -19%"의 타이밍 레버 — 품질 상승 베팅 아님.

## GPU 큐 (레인 A, 순서대로)

### 0. 레인 위생 (매 런타임 공통)

- 새 런타임 붙으면: 코드 push → cmd로 번들 압축 해제 → **모델에 맞는
  transformers 설치** (0.6B=`4.51.3`, 0.8B=`>=5.13,<5.14`; 기본 4.46.3은 둘 다 불가).
- 유휴 45분 자동 해제 — 판정 대기가 길어지면 다음 런을 먼저 큐잉하거나
  더미 cmd로 타이머 리셋.
- 스크린도 제출 가능 가중치 필수: `--save-val-model --save-fp16 --output-dir
  /content/drive/MyDrive/AADP_exchange/models/<suffix>`.

```bash
# 코드 push + VM 번들 갱신 + transformers 설치 (예: 0.6B 준비)
.venv/bin/python colab/cloud_sync.py push
.venv/bin/python colab/cloud_sync.py cmd 'python -c "import glob,tarfile; b=sorted(glob.glob(\"/content/drive/MyDrive/AADP_exchange/code/code_*.tar.gz\"))[-1]; tarfile.open(b).extractall(\"/content/AADP\"); print(b)"'
.venv/bin/python colab/cloud_sync.py cmd 'pip install -q "transformers==4.51.3" && python -c "import transformers; print(transformers.__version__)"'
```

### 1. ~~depth-cut 프로브 수거~~ — 완료·기각 (위 "진행 중" 1번 참조)

### 2. v6 직렬화 구현 (GPU 아님 — 스크린의 전제)

**v6 최종형 = v5 + turn 정확값 병기(`turn=07/mid`) + lang 지배도 마커
(`lang=py!ts`/`py~ts`, top1≥0.7 기준)** — struct 라인은 프로브 널 판정으로
제외 (`fe_current_v6_spec.md` 개정본). v5 서식 기준 두 줄만 수정:
meta 라인의 turn 토큰, workspace 라인의 lang 토큰. `script.py` 직렬화 함수 +
`train_transformer.py` serializer choices 등록. 구현 후 v6×Qwen3 토크나이저
p100 재측정 → max_length 확정 (토큰 +3 수준이라 len384 유지 예상).

### 3. v6 0.6B fixed 스크린 (G4 ~30분, transformers 4.51.3)

```bash
.venv/bin/python colab/cloud_sync.py launch train_transformer.py -- \
  --base-model Qwen/Qwen3-0.6B --lr 2e-5 --device cuda --split session \
  --serializer current_v6 --max-length <2번에서 확정, 예상 384> \
  --epochs 3 --batch-size 16 --eval-batch-size 64 --class-weight-power 0.5 \
  --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 \
  --no-research-log --seed 42 --save-val-model --save-fp16 \
  --epoch-checkpoint-dir /content/drive/MyDrive/AADP_exchange/models/v6_qwen3_06b_screen_ckpt \
  --output-dir /content/drive/MyDrive/AADP_exchange/models/v6_qwen3_06b_screen \
  --experiment-suffix v6_qwen3_06b_screen \
  --notes 'FE lane: current_v6 screen (v5 + turn exact + lang dominance, no struct line); gate vs v1 anchor 0.770875, must beat v5 0.763387'
```

게이트: fixed 2stage가 **v5의 0.763387 초과 필수**, v1 앵커 0.770875 밴드(±0.005)
진입이 목표. 약클래스(list_directory/read_file/grep_search) 회복 여부 병기.
미달 시: v6 기각 기록, 캐스케이드는 v1 기반 확정(→7번으로 점프).

### 4. v6 0.6B 3-fold OOF (~1.5h) + 판정

스크린 통과 시 fold 0→1→2 순차 체인(각 collect 대기 후 다음 런칭 — 세션
스크래치 `chain_v5_oof.py` 패턴 재사용, serializer/len/suffix만 교체).
pull → `aggregate_oof.py` → `tune_oof_rule_boosts.py`(struct 플래그 포함 여부는
2번 프로브 결과대로). 판정: v1 OOF 2stage 0.758499 / +rules 0.767129 대비.

### 5. 판정별 분기

- **v6 ≥ v1**: v6 0.6B full refit(`--final-model`) → 0.8B로 전환
  (transformers 5.13 재설치) → v6×Qwen3.5 토크나이저 길이 재측정 →
  0.8B v6 3-fold OOF + refit. G4 batch16, fold당 ~1h.
- **v6 < v1 (근소)**: v6는 캐스케이드/타이밍 전용으로 격하 판단 — 품질 v1,
  속도 v6의 혼용은 불가하므로 라우팅 산수(s 요구치)와 품질 델타를 저울질해
  기록 후 사용자 판단 요청.
- ~~depth-cut 카드~~: 프로브 기각으로 폐기 (조기 포화 없음).

### 6. 캐스케이드 팩 조립 (리핏 재료 확보 후, 대부분 로컬)

- script.py에 fp16_state 커널 + **인스턴스 리바인딩** 로더 추가
  (hf_meta.json 옵트인; 커널 코드는 `colab/m8_fp16_deltanet_probe.py`의
  `make_fp16_rule(state_in_fp32=False)` 이식).
- 듀얼 모델 팩: 0.6B 전량 + 저신뢰 r% → 0.8B 재채점. r은 블렌드 OOF에서 확정
  (v1 시뮬 곡선: 15%→엣지 53%, 25%→85%, 50%→107%;
  `experiments/artifacts/20260706_cascade_routing_sim.json`).
- **int8 flat-key rename 필수** (transformers 5.x 저장분): `model.language_model.*`
  → `model.*`, safetensors와 .meta.json quantized 리스트 모두 (research_log
  07-05 서버 프로브 엔트리에 스니펫).
- T4 레플리카 타이밍 리허설(레인 B/C) → ~620-730s권이면 제출
  (제출 자체가 s 측정 겸용; 타임아웃도 하한 정보).

### 7. v6 전멸 시 폴백

v1 캐스케이드 (재료 완비: M7 0.6B refit + `experiments/incoming/models/
m8_qwen35_refit` int8 rename 완료본). fp16 적용 r=15% ≈733s → s≥1.22 도박이므로
r=10%(엣지 41%, ~690s, s≥1.15)와 저울질. 제출 전 사용자 승인 필수.

## 함정 목록 (오늘 실제로 밟은 것들)

1. **transformers 버전**: 새 런타임 기본 4.46.3은 qwen3(0.6B)도 qwen3_5(0.8B)도
   모름. 0.6B=4.51.3, 0.8B=5.13. depth-cut 프로브가 레인 A에 5.13을 깔아놨음.
2. **pip 후 reload 금지**: 같은 프로세스에서 transformers 업그레이드 후
   `importlib.reload` → ImportError. 재-exec 패턴 사용 (m8_depthcut_layer_probe.py 참고).
3. **몽키패치**: DeltaNet 폴백은 모듈 전역 패치 무효 — `model.modules()` 돌며
   `chunk_gated_delta_rule` 인스턴스 속성 교체.
4. **launch 인자**: `cloud_sync.py launch script.py`만 하면 "no run args" 실패 —
   반드시 `-- <args>` 필요 (인자 없는 스크립트도 `-- --dummy` 식으로).
5. **유휴 45분 자동 해제**: collect 후 판정하는 동안 타이머가 돈다.
6. **레인별 번들 분리**: push는 레인 익스체인지별로 각각, cmd로 압축 해제까지
   해야 반영됨 (bootstrap 시점 번들은 낡았을 수 있음).
7. **모델 저장 경로**: 스크린 가중치는 Drive output-dir로 직행 — VM 회수에도 생존.
8. 제출 zip: 이름 ≤30자, `submit` 접두 금지, 루트 3항목, <1GB.

## 오늘의 판정 기준 요약 (독트린)

Public이 결정 지표(10슬롯/일, 남은 슬롯 확인 후 사용), fixed 단독 승급 금지
(±0.005 시드 노이즈), 레시피 판정은 OOF, 단일 Public 델타 <0.02는 방향 증거
아님, sub-0.02 레버는 1급(스택킹 대상), best-of-N은 종반 보류.
