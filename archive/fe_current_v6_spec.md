# current_v6 스펙 (2026-07-06 합의)

근거 문서: `fe_raw_data_review.md`(팀 FE 조사) + 2026-07-06 조건부 MI 실측
+ v5 3-fold OOF 판정(-0.0054, 탐색 클래스 집중)·xmeta 룰 회수 실패(+0.0002).
v5 구현 기준은 `fe_current_v5_spec.md`; v6는 그 위의 증분만 정의한다.

## 판정 근거 요약 (왜 이 스펙인가)

조건부 MI(70k행, turn 조건부): elapsed 계열은 파생을 포함해 전부 사망 —
elapsed_q 0.007 / pace(elapsed/turn) 0.006 / budget_q 0.007 / burn_rate 0.004 /
budget_low 0.003 bits. **v5의 5필드 삭제 자체는 옳았다.** 손실의 범인은:

1. **turn 정확값→구간화**: I(label; turn_exact)=0.1994 vs regime=0.1947,
   특히 turn≤2 내부에서 정확값 MI 0.097 — 세션 오프닝(0/1/2) 구분이 뭉개짐.
   list_directory OOF -0.023과 정합.
2. **lang 비율→이름 축약**: lang 지배도는 유일하게 turn 조건부에서 상승하는
   생존 신호(0.011→0.018 bits).
3. hist_truncated는 (turn>6)과 100% 동치 — turn 정확값 복원 시 불필요.

## v6 = v5 + 아래 증분

```text
current: <v1/v5 동일>
meta: turn=07/mid                       # 정확값/구간 병기 (구간 어휘는 v5 동일)
workspace: dirty=.. ci=.. lang=py!ts open=<v5 동일>   # ! 지배(top1>=0.7) ~ 혼합
actions: <v1/v5 동일>
last_user / args / results: <v1/v5 동일>
struct: cand=few path=basename overlap=last_arg res=found open_n=1
```

- **meta 라인**: `turn=<정확값 2자리, cap 14+>/<v5 구간토큰>`. elapsed/budget/
  tier/lang_pref/loc은 v5와 동일하게 미포함(실측 사망 확인).
- **workspace 라인**: `lang=<top1><sep><top2>`; sep `!` = top1 비율 ≥0.7(지배),
  `~` = 미만(혼합). 나머지 v5 동일.
- **struct 라인: 제외 (2026-07-06 프로브 판정)**. 후보 8종(turn_x/tri/res/
  res_n/cand/path/overlap/open_n)을 v1 OOF 룰 프로브로 검증한 결과 널
  (0.767109 vs 기준 0.767129, struct 룰 1개만 한계 선택 —
  `m7_qwen3_oof_rules_struct_rule_boosts.json`). v1 모델이 원문에서 이미
  추출하는 정보로 판정 — 합의된 승격 규칙(프로브 통과분만)에 따라 v6에서
  제외하고, v6가 v1 동급을 달성하면 v7 후보로 보류. 구현된 플래그는
  `tune_oof_rule_boosts.py --struct-flags`에 보존.

**따라서 v6 최종형 = v5 + turn 정확값 병기 + lang 지배도 마커** (위 두
증분만). 토큰 비용 ~+3, 평균 ~182 (v1 대비 -19%), p100 ~385 → len384 재측정
후 유지 가능성 높음. 기대값: v1 동급 품질 + 토큰 -19% (타이밍 레버) — 품질
상승 베팅이 아님.

## 파생 규칙 (단일 소스 원칙)

result_summary는 강한 템플릿("N matches in", "N files matched", "ok; exit=0",
"error: patch failed", "ok; read <path>", "plan with N steps",
"clarifying question sent")이므로 정규식 파싱이 안정적. 단:

- 파생 함수는 script.py의 공유 함수로 구현하고 train 직렬화·룰 플래그·추론이
  전부 같은 함수를 import한다 (train/infer 불일치 = 치명상).
- 모든 struct 키는 상시 출력, 실패/부재 시 `unknown` 명시 (스키마 안정).
- 서버 test에서 템플릿이 어긋날 가능성에 대비해 파싱 실패는 조용히
  `unknown`으로 — 절대 예외 전파 금지.

## 길이/타이밍 예산

v5 평균 179 + struct ~20-25토큰 → 평균 ~200 (v1 225 대비 -9~-11%).
p100 ~382+25 ≈ 410 → **len416 복귀 예상**(무절단 원칙, 8배수). 정렬 배치
추론 비용은 평균이 지배하므로 캐스케이드 s-요구치 ~1.0-1.05권 유지.

## 검증 체인 (합의된 순서)

1. 룰 vocab 프로브: 위 후보를 `tune_oof_rule_boosts.py --struct-flags`로
   v1 OOF에서 검증 (GPU 불요). 안정 이득 토큰만 직렬화 승격; v1 rules에도
   즉시 +α 편입 가능 (실패해도 본전 구조).
2. v6 직렬화 구현 (train_transformer.py + script.py 공유 함수) → 0.6B fixed
   스크린 (게이트: v1 앵커 0.770875 밴드, v5의 0.7634 초과 필수).
3. 통과 시 3-fold OOF → v1 OOF(0.758499/0.767129)와 판정 → 캐스케이드 재료
   (0.6B/0.8B refit) 재생산.
