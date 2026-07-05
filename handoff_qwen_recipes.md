# Qwen3 / Qwen3.5 디코더 레시피 핸드오프 (2026-07-05 기준)

팀원 공유용. 디코더 분류 라인(Qwen 패밀리)의 검증된 하이퍼파라미터, 환경 요구사항,
실측 성능, 알려진 함정 총정리. 출처는 `experiments/results.csv`(정확한 커맨드),
`research_log.md`(결정 이력), `experiments/artifacts/*.json`(상세 수치).

## 0. 요약 스코어보드

| 모델 | fixed 2stage | OOF raw→2stage→rules | Public | 상태 |
| --- | ---: | --- | ---: | --- |
| Qwen2.5-0.5B ep3 refit (M6) | - | - | 0.770 | 대체됨 |
| **Qwen3-0.6B ep3 len416 (M7)** | 0.770875 | 0.755929→0.758499→**0.767129** | **0.780** | **현 베이스라인 팩** |
| **Qwen3.5-0.8B ep3 len400 (M8)** | **0.780427** | 0.766602→0.767849→**0.774046** | (제출 준비 중) | 품질 green / 추론 타이밍 레인 진행 중 |

M8 약클래스(rules 적용 후): list_directory 0.5124, read_file 0.6104,
grep_search 0.6194, ask_user 0.6600, glob_pattern 0.6650.
M8은 M7 대비 이득이 약클래스에 집중 (스크린 기준 list_directory +0.042,
web_search +0.028, grep_search +0.018).

## 1. 공통 챔피언 레시피 (디코더 분류)

`AutoModelForSequenceClassification` + pad-token 패치 2줄(아래 §4). 학습:

```
--lr 2e-5 --epochs 3 --batch-size 16 (유효배치 16 고정)
--loss focal --focal-gamma 2.0 --class-weight-power 0.5 --label-smoothing 0.02
--serializer current_v1
--replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5
--tune-bias --keep-threshold 0.0 --seed 42
```

- **에폭은 3이 정점** — 두 모델 모두 재확인됨. Qwen3.5에서 ep5는 명백한 과적합
  (fixed 2stage 0.7804(ep3) vs 0.7553(ep5)). 피크 탐지는 `--epochs 5 +
  --epoch-checkpoint-dir` 원런으로, refit은 `--epochs 3`으로 다시.
- max-length는 모델별 무절단 원칙으로 산정 (§2, §3).
- 검증·승급 플로우: fixed screen → 3-fold `--split session_oof` →
  `aggregate_oof.py`(2stage bias) → `tune_oof_rule_boosts.py`(12 rules) →
  FULL-DATA refit `--final-model --final-only` → 패키징 시 bias/rules 주입.

## 2. Qwen3-0.6B (M7, 현 베이스라인)

- base: `Qwen/Qwen3-0.6B` (공식). vocab ~151k.
- **max_length 416** — current_v1 직렬화 토큰 p100=409, 무절단+8배수.
- 환경: **transformers>=4.51,<4.52** (`requirements_qwen3.txt`). 서버
  프리인스톨 4.46.3을 pip 오버라이드 (M7 제출로 검증, 설치 실패는 슬롯 무소모).
- 패키징: fp16 1192MB → int8 598MB (`quantize_checkpoint.py`, int8-rowwise-v1)
  → zip 539MB. **서버 추론 8:50/10:00 — 앙상블 여유 없음.**
- 정확한 커맨드: results.csv `m7_qwen3_06b_len416_focal_ep3_s42`(스크린),
  `m7_qwen3_oof_len416_ep3_fold{0,1,2}`(OOF), `m7_qwen3_refit`(refit).
- 가중치: Drive `AADP_exchange/models/m7_qwen3_refit*`.

## 3. Qwen3.5-0.8B (M8, 진행 중)

- base: **`igorktech/Qwen3.5-0.8B-Base-LM`** — 공식 `Qwen/Qwen3.5-0.8B-Base`
  (멀티모달)에서 비전 제거한 커뮤니티 텍스트 추출본. arch `qwen3_5_text`
  (`Qwen3_5TextForSequenceClassification`), 752M 파라미터, vocab 248,320
  (신규 토크나이저!), tied embeddings, **하이브리드 24층 = Gated DeltaNet
  선형어텐션 18 + 풀어텐션 6**.
- **max_length 400** — 새 토크나이저로 재측정한 무절단 8배수
  (`experiments/artifacts/20260705_qwen35_token_length_coverage.json`).
  384는 5건 절단. Qwen3보다 평균 토큰이 오히려 짧음 (225→216 @4096샘플).
- 환경: **transformers>=5.13,<5.14 필수** (`requirements_qwen35.txt` —
  qwen3_5_text의 SeqCls 오토매핑이 5.3.0+). 학습 VM에서
  `pip install 'transformers>=5.13,<5.14' safetensors==0.8.0` 선행.
- OOF/스크린 커맨드: results.csv `m8_qwen35_08b_screen`,
  `m8_qwen35_oof_len400_ep3_fold{0,1,2}`, (refit: `m8_qwen35_refit` 진행 중).
- 가중치: Drive `AADP_exchange/models/m8_qwen35_08b_ep3_ckpt`(스크린 ep3),
  `m8_qwen35_oof_len400_ep3_fold{0,1,2}_ckpt`, `m8_qwen35_refit*`(진행 중).
  manifest: `experiments/artifacts/m8_qwen35_oof_len400_ep3_weight_manifest.json`.
- int8: 1504.9MB → 755.6MB (50.2%).

## 4. 함정 목록 (돌기 전에 읽을 것)

1. **pad 토큰**: 디코더 SeqCls는 pad_token_id 없으면 batch>1 거부.
   `train_transformer.py`가 자동 처리(config+tokenizer 패치, None 가드) —
   직접 로드할 땐 `model.config.pad_token_id = tokenizer.pad_token_id` 필수.
2. **Qwen3.5 토크나이저는 5.x 전용 클래스**(`TokenizersBackend`). 로컬
   venv(4.46.3)에서 토크나이즈만 필요하면:
   `PreTrainedTokenizerFast(tokenizer_file="tokenizer.json",
   eos_token="<|endoftext|>", pad_token="<|endoftext|>")`.
3. **GPU 메모리 (0.8B 학습)**: batch16 len400은 **A100-40GB에서 OOM**
   (38.5GB 필요). G4(96GB)는 여유. A100에서는 `--batch-size 8
   --grad-accum-steps 2` (유효 16 동일). 실측 처리량:
   - G4 batch16: ~2,140행/분 (OOF fold당 ~80분)
   - A100-40GB batch8×accum2: ~550행/분 (**FULL refit ~7.3h**)
   - A100 batch4×accum4: ~350행/분 — 쓰지 말 것
   0.6B는 batch16이 A100/L4에서도 무난.
4. **Qwen3.5 추론 속도 (중요)**: DeltaNet fast path는 `fla`+`causal_conv1d`
   둘 다 필요한데 **T4(SM75)에서 fla 커널이 컴파일 자체가 안 됨**
   (`PassManager::run failed`, 전 버전) → 항상 느린 torch 폴백.
   실측(T4, 서버복제 스택 torch 2.7.1+cu128/py3.11):
   - eager 폴백: Qwen3-0.6B 대비 **2.63x** (서버 환산 ~23분 — 10분 초과)
   - torch.compile(reduce-overhead)+버킷: **1.73x**, 정합성 100%, 단
     콜드 컴파일 ~25분 → **캐시 동봉 없이 서버 배포 불가**
   - 서버 직접 배포는 compile+캐시 동봉 리허설(진행 중) 결과에 달려 있음.
   **학습·OOF·teacher 용도는 문제없음** (GPU에서 폴백도 학습은 돌아감).
5. **에폭 체크포인트**: `--epoch-checkpoint-dir <drive>/...` 필수 습관 —
   ep3/ep5 비교와 VM 회수 대비를 동시에 해결.
6. **콜랩 무료 T4는 작업 중에도 회수됨** (07-05 하루 2회). 긴 작업은
   Drive에 중간 산출물 미러링을 넣거나 브라우저 세션 사용.
7. 클래스 순서는 config.json id2label == OOF artifact classes 순서로 검증할 것.
   라벨 순서 변경 금지 (제출 계약).

## 5. 서빙/패키징 참고 (0.8B를 직접 서빙하려는 경우)

- script.py에 opt-in compile 경로 추가됨: hf_meta.json에
  `"compile": {"mode": "reduce-overhead", "buckets": [...], "batch_size": N,
  "cache_dir": "hf_model/compile_cache"}` 블록이 있으면 cudagraph+버킷 추론,
  실패 시 eager 폴백. 캐시는 서버와 동일 스택(T4/SM75, torch 2.7.1+cu128,
  **py3.11**, triton 3.3.1)에서 구워서 `hf_model/compile_cache/`에 동봉.
- 서버 스펙 (rule.md): T4 16GB / 3 vCPU / 12GB RAM, python3.11,
  torch 2.7.1+cu128, transformers 4.46.3 프리인스톨(오버라이드 가능),
  build-essential+python3.11-dev 있음(triton 런타임 빌드 가능), pip ≤10분,
  추론 ≤10분, zip ≤1GB. **Public = 최종 점수 100% (private 없음).**

## 6. 다음 사람을 위한 상태 (2026-07-05 저녁)

- M8 refit이 A100에서 진행 중 (내일 새벽 완료 예정) — 완료 시
  `AADP_exchange/models/m8_qwen35_refit`.
- 추론 타이밍 레인(compile+캐시 리허설)은 레인 B에서 진행 중 — 결과에 따라
  0.8B 직접 제출 여부 결정. 실패 시 대안: 0.8B teacher → 0.6B student 증류.
- 문의: 이 문서 기준 최신 결정은 `research_log.md` 하단 참조.
