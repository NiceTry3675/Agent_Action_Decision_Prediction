# DACON 236694 팀 도구 키트 핸드오프 (07-08 기준 전체)

노진산 레인의 학습·KD·검증·분석·자동화 도구 전부. 폴더별 카탈로그 + 표준 워크플로우 + 실측 규칙집.
현 챔피언: **m8-KD Public 0.78913** (HCX-0.5B 학생 × Qwen3.5-0.8B 교사, α0.5/T3.0, seed42).

---

## 0. 환경 2종 (혼용 금지 — 중요)

| venv | 스택 | 용도 |
|---|---|---|
| **학생용** | py3.11 + torch 2.x + **transformers 4.51.3** (평가서버 매칭) | 학생 학습·저장·게이트·패키징 전부 |
| **교사용** | transformers **5.13** + peft 0.19.1 (+ Qwen3.5면 FLA 0.5.1 + triton 3.7.1) | 교사 학습·로짓 추출 전용 |

**왜 분리**: tf 5.13에서 학생을 저장하면 RoPE가 신형식(`rope_parameters`)으로만 기록되고
제출 서버(tf 4.51)가 이를 무시 → rope_theta 10000으로 로드 → Public 0.70급 붕괴 (준현 실사고).
교사는 로짓(숫자)만 넘기므로 무관.

셋업 참고: `setup_venv52_4070.sh` (교사용 venv 구축 — torch 2.5.1+cu121 조합, triton 충돌 해법 포함).

## 1. 01_pipeline/ — 학습 본체

- `train_transformer.py` — **마스터**. 주요 옵션:
  `--distill-logits <pack> --distill-alpha 0.5 --distill-temp 3.0` (KD),
  `--lora-r 16` (LoRA 교사), `--model-class {auto,qwen35text,gemma4custom}`,
  `--split session_oof --n-folds K --fold-id F` (세션 fold), `--resume-from` (재개),
  `--epoch-checkpoint-dir` (에폭 ckpt — **매 에폭 덮어씀**, 스냅샷 필수), `--optim adamw8bit`.
  캐시 키에 seed·fold 포함(오염 픽스 완료판) — 구버전 쓰지 말 것.
- `script.py` — 직렬화(current_v1 등)·ALL_CLASSES·추론 진입점 (제출 zip에 그대로 들어가는 파일)
- `train.py` — 라벨 로드·f1_metrics
- `requirements_qwen3.txt` — 제출 zip용 requirements

## 2. 02_teacher_tools/ — 교사 로짓

- `export_teacher_logits.py` — 학습 완료 hf_model → train 70k 로짓 팩
  {ids, logits fp16 70k×14, classes, labels, y_true, metadata}. 마지막 줄 train acc가 핵심 지표.
- `export_ckpt_logits.py` — **에폭 어댑터 ckpt에서 직접 추출** (base+LoRA merge). 스윗스팟 후퇴용
- `gemma4_seqcls.py` — Gemma-4 unified 백본 seq-cls 커스텀 헤드
- `merge_oof_pack.py` — fold val-logits 병합 (블렌드 가중치 튜닝·검증용. **KD 교사용으론 쓰지 말 것** — 실측 -0.0045)
- `make_blend2_pack.py` — 교사 확률평균 블렌드 팩 (겹침 분석 출력 포함)
- `ckpt_snapshot_daemon.sh` — 에폭 ckpt 자동 스냅샷 (덮어쓰기 대비, 120초 폴링)

## 3. 03_eval_gates/ — 제출 전 게이트

- `eval_pseudo_holdout.py` — 세션 second-to-last 페어 20k macro-F1.
  **병리 탐지 전용** (건강 0.70~0.75 / 병리 <0.4). 순위 예측 무효 (실측: 게이트 0.721이 Public 0.7807).
  **반드시 fp16 refit 디렉토리로** — int8 팩을 넣으면 코덱 포맷 불일치로 가짜 병리(0.018) 발생
- `gate_pack.sh` — 게이트 원클릭 러너 (`bash gate_pack.sh models/<refit_dir> <tag>`)
- `eval_int8_pack.py` / `eval_int8_oof08.py` — int8 팩을 서버 동일 로더(`.__scale__`)로 검증 (경로 수정해서 사용)
- `logit_stats.py` / `logit_stats2.py` — 교사 로짓 통계 (train acc, 엔트로피, T-타깃, wrong share)

## 4. 04_packaging/ — 제출물

- `quantize_checkpoint.py` — int8 row-wise 양자화 (`.__scale__` 코덱, 실측 무손실: fp16 0.7208 = int8 0.7210)
- `quantize_int4.py` — int4-group128 (argmax 94.5%라 미사용, 참고용)
- `package_submission.py` — 제출 zip 빌더 (+smoke 테스트 내장)
- `verify_zip.py` — zip 구조·스모크 검증

## 5. 05_analysis/ — 로짓 분석 (이론 검증용)

- `blend2_theory_eval.sh` / `coder_theory_eval.sh` — 교사 2~3개 비교 분석 셸(내장 python):
  이견 겹침 분해(both/only-A/only-B), 동일-대안율, p(true) 캘리브레이션, 클래스별 이견 분포, 에폭 간 암기량.
  경로만 바꿔 어떤 교사 조합에도 재사용 가능

## 6. 06_chains_daemons/ — 자동화 템플릿 (자기 장비에 맞게 경로 수정)

- 교사 학습 러너: `teacher_gemma_a100.sh`(gemma4custom LoRA), `teacher_hcx15_wsl.sh`(4070 16GB LoRA 템플릿), `teacher_oof08_wsl.sh`(fold 학습)
- KD 러너: `kd_m8_refit_wsl.sh`(**챔피언 레시피 원본**), `kd_coder_refit_wsl.sh`(교사 팩 인자화 버전 — 신규 교사는 이걸 복사)
- 체인: `coder_kd_chain.sh`(로짓 회수→통계→**acc≥0.85 자동 에폭후퇴**→KD→게이트), `oof08_chain_wsl.sh`, `blend2_chain_wsl.sh`
- 베이비시터: `occupy_chain_daemon.sh`(OOM 래더 babysit 함수 원형), `ax_chain_daemon.sh`, `gemma_chain_daemon.sh`(중복발진 가드 패턴)
- Windows: `coder_watch.bat`(원격 완료 감지→회수→체인, schtasks용)

## 7. 표준 워크플로우 (신규 교사 1개 추가 시)

```
1) 교사 학습 (교사용 venv, LoRA r16, lr 1e-4, 3ep, seed42, 에폭 ckpt 스냅샷)
2) 로짓 추출 → train acc 확인
   - 0.80~0.82: 채택 (스윗스팟)  - ≥0.85: 전 에폭 ckpt에서 재추출 후 비교  - 0.9+: 폐기
3) (선택) 05_analysis로 기존 교사와 이견 겹침 분석 — 신규 정보량(only-신규 %) 확인
4) KD refit (학생용 venv, kd_coder_refit_wsl.sh 복사, α0.5/T3.0 seed42 고정 — 비율 축은 실측 종결)
5) 게이트 (fp16 refit 디렉토리, ≥0.70만 제출) → int8 팩 → zip → Public
```

## 8. 실측 규칙집 (07-06~08 확립, 재실험 금지 목록 포함)

- **교사 가치 = 이견의 양 × 이견의 질 × 계열 이질성**
  - 양: train acc 0.80~0.82가 스윗스팟 (m8 0.811→+0.0039). 0.9+는 암기라 이득 0 (m9 0.943→+0.0001)
  - 질: in-sample 로짓만. OOF 로짓은 추정오차라 역효과 (실측 -0.0045). 재시도 금지
  - 이질성: 학생과 다른 계열만 (HCX교사×HCX학생 -0.0147 / 같은 교사를 Qwen 학생에 주면 +0.0067, 태연 교차검증)
- **상관 교사 블렌드 금지**: q35 이견의 91%가 m8과 중복 → blend 0.7872 (양쪽 부모에 패배).
  블렌드는 이견 집합이 disjoint한 타계열끼리만
- **α/T 탐색 금지**: (0.5,3.0)=(0.7,2.0) 완전 동점
- 학생 = HCX-0.5B 고정 (0.5B가 T4 10분 하드캡, 학생 축 종결)
- 에폭 ckpt는 덮어쓰기 — 스냅샷 데몬 켜고 시작할 것 (coder ep1 유실 교훈)

## 9. 교사 로짓 현황 (공유 완료분)

| 팩 | acc | KD Public |
|---|---|---|
| m8 (Qwen3.5-0.8B) | 0.811 | **0.7891 챔피언** |
| coder ep2 (Qwen2.5-Coder-7B) | 0.7997 | 게이트 중 (오늘 밤) |
| q35 (4B) / coder ep3 / m9 / exa / hcx15 | 0.870 / 0.869 / 0.943 / 0.928 / 0.891 | 0.7885 / — / 0.7853 / 미투입 / 0.7744 |
| A.X-Light → Gemma-4-12B | 학습 중 → 대기 | 내일~모레 |

— 노진산 레인, 07-08 심야
