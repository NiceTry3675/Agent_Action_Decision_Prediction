# Handoff 2026-07-10: HCX-1.5B 학생 스케일업 — 품질 스크린 + 서빙 스파이크

전략 문맥(신선한 에이전트용): 팀 챔피언 Public **0.78962**(condalpha-KD, HCX-0.5B
학생), 1등 **0.797**, 마감 ~07-14. 교사축 격자·직렬화기 세대·R-Drop·캐스케이드/
블렌드·specialist 전부 마감(research_log 07-08~07-10). 남은 +0.008급 구조 레버는
**학생 용량**: 이 repo는 1~2B 학생 밴드를 품질 스크린 없이 건너뛰었고, 그 근거였던
타이밍 벽은 (a) naive HF fp16, (b) int8 저장-전용 코덱, (c) 잘못 구운 컴파일 캐시
(07-06 원인 규명 완료: `(128,192)` 캐시를 `{256,400}` 런타임에 적재)라는 세 가지
공격 가능한 가정 위에 있다.

스케일 근거: 0.6B→0.8B +0.007(OOF), llama-8B 교사 홀드아웃 0.7968 vs 0.5B 학생
스크린 0.7878(+0.009). 1.5B 투영 스크린 +0.004~0.008 — 보장 아님, 그래서 1단계가
스크린이다.

## 대상 모델

`naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-1.5B`
- 1.586B params (0.5B는 566M; 비임베딩 ~1.36B vs ~453M → **연산 ~3.0x**)
- **gated: auto** — HF 계정으로 라이선스 동의 + 런타임에 `HF_TOKEN` 필요
  (gemma-4 teacher 때와 동일 방식; 0.5B는 non-gated였어서 이번에 처음 필요)
- 0.5B와 같은 Llama-family/HCX 토크나이저 계열 예상 — transformers 4.46.3에서
  로드돼야 정상(오버라이드 불필요). 로드 직후 로그로 확인.
- 토크나이저 동일성 preflight 필수: 70k train current_v1 토큰 길이 감사
  (0.5B 기준 mean 200.5 / max 386 @len384). mean이 ±2% 이상 다르면 중단·보고.

## 1단계 — 품질 스크린 (A100 레인, ~7h 추정, 슬롯 0)

챔피언 KD 스크린(`kd_hcx_m8_screen_s42`, 2stage **0.787801**)의 커맨드를 완전
미러, 변경은 base-model 하나. condalpha(weak α0.7)는 로컬 파서 미흡수이므로
앵커와 동일한 균일 α0.5 T3 유지(앵커 비교성 확보).

```
AADP_EXCHANGE_DIR=AADP_exchange_b python colab/cloud_sync.py launch train_transformer.py -- \
  --base-model naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-1.5B \
  --lr 2e-5 --device cuda --split session --serializer current_v1 --max-length 384 \
  --epochs 3 --batch-size 16 --grad-accum-steps 1 --eval-batch-size 64 \
  --gradient-checkpointing --class-weight-power 0.5 --label-smoothing 0.02 \
  --loss focal --focal-gamma 2.0 \
  --replay-mode last1 --max-replay-samples 10000 --replay-sample-weight 0.5 \
  --distill-logits /content/drive/MyDrive/AADP_exchange_b/teacher/m8_qwen35_refit_train70k_fp16.pt \
  --distill-alpha 0.5 --distill-temp 3.0 \
  --tune-bias --keep-threshold 0.0 --tokenize-batch-size 1024 --seed 42 \
  --no-research-log --save-val-model --save-fp16 \
  --epoch-checkpoint-dir /content/drive/MyDrive/AADP_exchange_b/models/kd_hcx15b_m8_screen_ckpt \
  --output-dir /content/drive/MyDrive/AADP_exchange_b/models/kd_hcx15b_m8_screen \
  --experiment-suffix kd_hcx15b_m8_screen_s42 \
  --notes 'student scale-up gate: HCX-1.5B student, anchor kd_hcx_m8_screen_s42 2stage 0.787801, gate >= +0.006'
```

- 레인 C 사용 시 경로의 `_b`를 `_c`로 치환. teacher `.pt`는 **세 레인 모두
  Drive `teacher/`에 스테이징 완료 확인(2026-07-10)** — 재스테이징 불필요.
- preflight: `[bootstrap]` 후 transformers==4.46.3 확인, `HF_TOKEN` env 확인,
  gated 다운로드 성공 확인(첫 수 분 로그).
- A100-40GB fp16 + grad-ckpt batch16이면 1.5B 여유. OOM 시 batch8+accum2로
  내리되 실효 배치 유지, 노트에 기록.

**게이트(사전 등록): 2stage ≥ 0.7938 (+0.006 vs 앵커 0.787801).**
- 미달 시: 학생 스케일업 레인 전체 폐기(서빙 작업 착수 금지). 1.2B급 대체
  후보(EXAONE)는 게이트를 크게 벗어나지 않은 경우(+0.004~0.006)에만 사용자
  판단으로 검토.
- KD 스크린은 full-refit teacher가 val을 본 만큼 경미하게 낙관적이나, 앵커도
  같은 형태라 **동형 비교로 상쇄**됨.
- 약클래스(list_directory/read_file/grep_search/glob_pattern/ask_user/web_search)
  델타를 함께 리포트.

## 2단계 — 서빙 스파이크 (T4 레플리카, 스크린 통과 시에만)

목표: 30k행 len384 추론 **≤ ~510s**(오버헤드 ~90s 가정, 총 ≤10:00).
naive 투영 ~1,100-1,180s(0.5B 실측 392s × 3.0) → **~2.0-2.3x 필요.**

경로(순서대로, 각 단계에서 실측 기록):
1. fp16 + sorted batching(기존 script.py 경로) 실측 — 베이스라인.
2. `torch.compile(mode=reduce-overhead)` + **정확한 버킷/배치 셰이프로 구운**
   inductor/triton 캐시 동봉(07-06 버그의 교정판: 캐시 가드 셰이프 = 런타임
   버킷 {…}, batch=eval-batch와 일치 검증 후 zip에 포함). 기대 1.3-1.5x.
   콜랩 T4는 서버 대비 ≤~2.2x 느림(레플리카 캘리브레이션, 07-05) — 서버
   투영은 이 비율로 환산.
3. 부족분은 eval-batch/버킷 재탐색, fp16 SDPA 확인(HCX는 표준 Llama attention).
4. 그래도 >600s면: 강등 옵션 = 저마진 라우팅 캐스케이드(0.5B 챔피언 전행 +
   1.5B 저마진 20-25%; 인프라 9:08 실측 검증, 기대치는 엣지의 ~절반).

## 3단계 — 패키징 제약 (미리 인지)

- zip 1GB: 1.5B int8 저장 = **1.59GB 초과**. `quantize_checkpoint.py`에
  int4-rowwise 바디(+임베딩 int8) 확장 필요 — 투영 ~0.9GB. int4 argmax
  일치율 검증(관례 512샘플) 필수, 저하 시 민감 텐서 int8 예외 목록.
- 게이트 통과 후: `--final-model --final-only` 전량 리핏 → int4 패키징 →
  T4 리허설 → Public (매치드 비교 대상 kd_m8_refit 0.78913).

## 사용자 액션 (선행 필요)

1. HF에서 HCX-1.5B 라이선스 동의 + 레인 런타임에 `HF_TOKEN` 설정
   (gemma-4 때 방식).
2. 레인 B 또는 C 런타임 어태치([mount]→[bootstrap]→[agent]).
   **lane A는 privileged_mode 작업 중 — 사용 금지.**
