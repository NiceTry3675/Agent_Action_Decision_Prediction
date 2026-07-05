# Qwen3-0.6B → Qwen3.5-0.8B (igorktech/Qwen3.5-0.8B-Base-LM) 교체 플랜

## Context

현 베이스라인(M7)은 Qwen3-0.6B ep3 refit + 3-fold OOF bias/rules, Public 0.780.
디코더 패밀리 교체가 유일하게 검증된 범주적(0.02+) 레버이므로 Qwen3.5-0.8B
승급 시도는 로드맵 M3 "bigger swings"에 부합. `igorktech/Qwen3.5-0.8B-Base-LM`
조사 결과 **코드 수정 없이 교체 가능**. 사용자 결정: quick-val 생략, 학습은
G4(RTX PRO 6000, 96GB)에서, 스크린에서 ep3 vs ep5 비교.

## 조사로 확정된 사실

1. **레포 실체**: 공식 `Qwen/Qwen3.5-0.8B-Base`(멀티모달, apache-2.0)에서 비전
   인코더를 제거한 커뮤니티 텍스트 전용 추출본. `model_type: qwen3_5_text`,
   F32 3009.6 MB(≈752M 파라미터), vocab 248,320(신규 토크나이저), tied
   embeddings, 24층 하이브리드(Gated DeltaNet 선형 어텐션 18층 : 풀 어텐션 6층).
   다운로드 258 / 좋아요 0 — 저신뢰 변환본이므로 스크린이 곧 검증.
2. **SeqCls 지원**: transformers **v5.3.0부터** auto-mapping에
   `qwen3_5_text → Qwen3_5ForSequenceClassification` 등록 확인(v5.13.0도 확인).
   현행 `AutoModelForSequenceClassification` 파이프라인에 그대로 꽂힘.
3. **파이프라인은 모델 불가지론적**: `--base-model` 플래그(train_transformer.py
   L1024), pad-token 패치는 `None` 가드라 범용(L382-385, L525-533). `script.py`
   int8 로드도 `AutoConfig`/`from_config` 기반(L814-828). **코드 수정 0줄.**
4. **버전 점프 필수**: 체크포인트는 transformers 5.3.0 저장본. 신규
   `requirements_qwen35.txt`(5.x 핀) 필요. 서버 preinstall 4.46.3 → pip
   override(M7 검증 경로; 설치 실패는 슬롯 무소모 프로브). 서버 torch 버전
   미상 — 첫 제출이 프로브 겸용.
5. **용량 통과**: F32 3.0 GB → fp16 ~1.5 GB → int8 ~753 MB → zip ~700 MB 추정
   (1 GB 한도 내; 0.6B는 598 MB → zip 539 MB).
6. **결정적 리스크 — 서버 추론 시간**: 0.6B가 이미 8:50/10:00. HF 문서 명시:
   DeltaNet 경로는 `causal_conv1d`+`fla` 없으면 느린 PyTorch 폴백. 서버
   T4(SM75)+오프라인이라 폴백 확정에 가까움. 비임베딩 파라미터 +12% 수준이나
   폴백 상수 비용 미지수 → T4 타이밍 프로브를 학습과 병렬로 수행(아래).
7. **토크나이저 교체** → `current_v1` 길이 분포 재측정 필수(len416은 Qwen3
   p100=409 기준값). 무절단 원칙으로 새 max-length L 산정.

## Phase 0 — 새 max-length L 로컬 측정 (선행, WSL에서 가능한 경량 작업)

- 토크나이저 파일만 다운로드(`tokenizer.json` 20MB + `tokenizer_config.json`) —
  3GB 모델 불필요.
- 주의: `tokenizer_class: TokenizersBackend`는 transformers 5.x 전용이라 로컬
  venv(4.46.3)의 `AutoTokenizer`로는 못 엶. 대신
  `PreTrainedTokenizerFast(tokenizer_file="tokenizer.json",
  eos_token="<|endoftext|>", pad_token="<|endoftext|>")` 직접 로드 — 토크나이즈
  결과는 tokenizer.json이 전부이므로 5.x와 동일. (이 토크나이저는 pad_token이
  이미 정의돼 있어 학습 시 pad 패치도 자연 통과.)
- `script.py`의 `serialize_transformer_sample`(current_v1)로 train 70k + test
  직렬화 → 토큰 길이 분포(p50/p90/p99/p100) 산출 → 무절단 원칙으로 L 결정
  (Qwen3 기준 p100=409 → len416이었음; 8배수 정렬 권장).
- 아티팩트: `experiments/artifacts/<날짜>_qwen35_token_length_coverage.json`
  (20260704 커버리지 아티팩트와 같은 형식). Qwen3 대비 분포 요약을 함께 기록.

## Phase 1 — 스크린 (G4, 챔피언 레시피, ep3/ep5 원런 비교)

세션 첫 셀(수 분): transformers `>=5.13,<5.14` 설치 → 모델 로드 확인:
`AutoModelForSequenceClassification.from_pretrained(..., num_labels=14)`
+ pad-token 확인. max-length는 Phase 0에서 측정한 L 사용.

**fixed session split, `--epochs 5` + per-epoch 체크포인트 한 런**으로 ep3 vs
ep5 비교(두 런 불필요 — M6/M7의 피크 에폭 탐지 방식 그대로). results.csv
116행 명령 기준, 변경은 base-model / max-length / epochs / 배치 크기 유지:

```
train_transformer.py --base-model igorktech/Qwen3.5-0.8B-Base-LM --lr 2e-5 \
  --device cuda --split session --serializer current_v1 --max-length <L> \
  --epochs 5 --batch-size 16 --eval-batch-size 64 --class-weight-power 0.5 \
  --label-smoothing 0.02 --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 \
  --no-research-log --seed 42 --save-val-model --save-fp16 \
  --epoch-checkpoint-dir <drive>/m8_qwen35_08b_ckpt \
  --output-dir <drive>/m8_qwen35_08b_screen --experiment-suffix m8_qwen35_08b_screen
```

- G4 96GB라 grad-accum 불필요, champion batch 16 유지.
- 게이트: 피크 에폭의 fixed 2stage가 Qwen3-0.6B 밴드(~0.771) 이상. 붕괴 시
  기각·기록. fixed 단독 승급 판단 금지(±0.005 시드 노이즈) — 통과는 진입 조건.
- 승자 에폭 E(3 vs 5, 델타 ±0.005 이내면 싼 쪽=3 유지)를 이후 OOF/refit에 고정.
- 주의: 5ep 스케줄의 ep3 체크포인트는 3ep 스케줄과 lr 스케줄이 다름 — M7과
  동일하게 "피크 탐지는 멀티에폭 런, refit은 `--epochs E`"로 처리.

**병렬 트랙 — T4 타이밍 프로브(학습 불요, 무료 T4 ~30분)**: 서버 test 크기가
미상이고 로컬 test.jsonl은 5행 폴백이므로 **비율법**을 쓴다 — 동일 워크로드
(train에서 수천 행 샘플, script.py의 길이정렬 배치 추론 경로 재현)로
Qwen3-0.6B(len416)와 Qwen3.5-0.8B 랜덤 헤드(len L)를 같은 T4에서 각각 측정,
`(0.8B 시간 / 0.6B 시간) × 8:50`으로 서버 시간을 환산. **게이트: 환산
≤ ~8:30. 늦어도 refit/패키징 전에는 반드시 통과 확인.** 초과 시 eval batch
64 / L 하향 → 그래도 >10:00 전망이면 레인 사망, 학습 중단·기록.

**실행 메모(2026-07-05)**: T4 비율 프로브 완료. 동일 4,096-row train 샘플,
`current_v1`, sorted-batch fp16 추론 기준 Qwen3-0.6B len416 batch64 `76.42s`,
Qwen3.5-0.8B len400 batch64 `200.78s` → `2.63x`, 서버 환산 약 `23.2min`.
batch/max-length rescue grid의 최선도 Qwen3.5 len336 batch64 `182.24s`
(`2.38x`, 약 `21.1min`)라 로컬 타이밍 게이트는 red. 다만 사용자 결정으로
G4 fixed screen은 중단하지 않고 끝까지 유지하며, 실제 패키징/Public probe 전까지
최종 사망 판정은 보류. 상세: `experiments/artifacts/m8_qwen35_t4_timing_probe.json`,
`experiments/artifacts/m8_qwen35_t4_batch_grid.json`.

**실행 메모(품질, 2026-07-05)**: Phase 0 길이 측정 결과 len400으로 확정
(`experiments/artifacts/20260705_qwen35_token_length_coverage.json`). G4 fixed
screen은 ep5 raw `0.746547` / old-bias `0.753819` / 2stage `0.755271`로
과적합, 보존 ep3 체크포인트 평가는 raw `0.769643` / old-bias `0.779071` /
2stage `0.780427`로 품질 게이트 통과. 이후 에폭 `E=3` 고정.

## Phase 2 — OOF + refit + Public (M7 플로우 복제)

1. 3-fold `--split session_oof --n-folds 3 --fold-id {0,1,2}` (에폭 E, results.csv
   117-119행 레시피) → `cloud_sync.py pull` → `aggregate_oof.py` → 2-stage bias
   → `tune_oof_rule_boosts.py`. 비교 기준: OOF 2stage 0.758499 / +rules 0.767129.
2. FULL-DATA refit: `--epochs E --final-model --final-only`(122행 레시피).
3. `quantize_checkpoint.py quantize` → int8 (+`verify`는 로컬 transformers 5.x
   PYTHONPATH 오버레이 — M7의 4.51 오버레이 패턴 재사용).
4. **신규 `requirements_qwen35.txt`**: `transformers>=5.13,<5.14` /
   `safetensors==0.8.0` / `scikit-learn==1.8.0` / `joblib==1.5.3`.
   (서버 pip 실패 시 무료 — 5.3.0까지 핀 하향 여지.)
5. `package_submission.py --no-sparse --requirements requirements_qwen35.txt`,
   zip명 예: `m8_qwen35_refit.zip`(≤30자).
6. 재빌드 zip 클린 추출 + `data/` 추가 후 오프라인 스모크(5.x 오버레이,
   `TRANSFORMERS_OFFLINE=1`); CPU 스모크는 0.75B라 "가능하면". 체크리스트:
   `id,action` 컬럼·ID 순서·14 라벨·zip<1GB·루트 3항목.
7. 제출 → `leaderboard_calibration.md` 기록. **Public > 0.780일 때만**
   `final_summary.md`/베이스라인 갱신.

**실행 메모(OOF, 2026-07-05)**: 1번 OOF 단계 완료. Fold별 2stage는 fold0
`0.770064`, fold1 `0.769374`, fold2 `0.768901`. Aggregate OOF는 raw
`0.766602` → old-bias `0.767393` → 2stage `0.767849`; rule tuning은
`0.774046`. 약클래스(after rules): `list_directory 0.5124`, `read_file 0.6104`,
`grep_search 0.6194`, `ask_user 0.6600`, `glob_pattern 0.6650`. 상세:
`experiments/artifacts/m8_qwen35_oof_len400_ep3_oof_metrics.json`,
`experiments/artifacts/m8_qwen35_oof_len400_ep3_rules_rule_boosts.json`.

**현재 상태(2026-07-05)**: Qwen3.5는 모델 품질 신호가 확인됐지만, 현재 승인된
T4 추론 경로에서는 서버 10분 예산을 넘는다. 따라서 2-7번 refit/quantize/package/
Public 제출은 추론 경로가 별도로 green 판정되기 전까지 보류한다. OOF fold
체크포인트 3개는 Drive `AADP_exchange/models/`에 저장되어 있고, 각 디렉터리는
`model.safetensors`, `config.json`, tokenizer 파일, `checkpoint_state.json`을
포함한다. 로컬 manifest:
`experiments/artifacts/m8_qwen35_oof_len400_ep3_weight_manifest.json`.
사용자 결정: 레인 C 추론 가속 작업은 다른 에이전트 소유이므로 이 플랜 실행자는
건드리지 않는다. 증류 경로도 아직 착수하지 않는다.

## 변경 파일

- 신규: `requirements_qwen35.txt` (유일한 리포 코드/설정 변경)
- 산출물: 토큰 커버리지·타이밍 아티팩트(`experiments/artifacts/`), results.csv
  행(cloud_sync pull 경유), research_log 결정 기록
- **수정 없음(이 비-C 학습/OOF 실행 범위)**: `train_transformer.py`,
  `package_submission.py`, `quantize_checkpoint.py`. `script.py` 추론 가속
  변경은 별도 레인 C 범위로 취급한다.

## 잔여 리스크

- 서버 torch ↔ transformers 5.13 호환(첫 제출이 무료 설치 프로브; 실패 시 핀 하향)
- 커뮤니티 추출본 충실도(스크린 붕괴가 검출 신호)
- 하이브리드 폴백의 fp16 수치 안정성·마스킹(quantize verify와 스모크가 검출)
- 이 팩도 앙상블 여유 없음(0.6B에서 이미 8:50) — M7과 같은 encoder-only 구조
- G4(Blackwell)에서 torch/transformers 5.x CUDA 호환은 최신 cu128+ 빌드 필요 —
  세션 첫 셀 로드 확인에서 즉시 드러남
