# `current_v6e` 스펙 - read/list 보호형 구조 토큰 (2026-07-06)

상태: **GPU fixed screen 완료 — OOF 승격 실패, serializer-compression lane 종료**.

관련 문서: `fe_current_v5_spec.md`, `fe_current_v6_spec.md`,
`fe_raw_data_review.md`. 구현은 `script.py`와 `train_transformer.py`에
`current_v6e`로 등록되어 있다. 이 문서는 v6 fixed screen 이후의 판단을
반영한 다음 serializer 후보 정의다.

## 결론

`current_v6e`는 **v5 + 정확 turn + 탐색 증거 토큰**이다.

2026-07-06 실행 결과: exact-spec batch16 run은 `step=1500`부터 fp16 NaN이
지속되어 중단했다. cache는 fresh rebuild였으므로 stale-cache 문제는 아니었다.
`batch_size=8`, `grad_accum_steps=2` 안정화 재런은 완료됐지만 fixed 2-stage
`0.764074`로 v6 `0.764251`을 넘지 못했고, v1 anchor `0.770875`와도 멀었다.
`read_file`은 `0.5809`까지 일부 회복했지만 목표 `0.59`권에 못 미쳤다. 따라서
OOF로 승격하지 않고 current_v1 cascade/timing으로 복귀한다.

v6의 `lang` 지배도 마커는 제거하고 v5의 top-2 언어 표기로 되돌린다. 대신
`struct:` 라인에 `path`, `overlap`, `cand` 세 토큰만 추가한다. 목적은 점수
상승 베팅이 아니라, v6에서 관측된 `read_file -> list_directory/grep_search`
과잉 전환을 막으면서 v5의 토큰 절감 대부분을 유지하는 것이다.

```text
current: <v1/v5 동일>
meta: turn=07/long
workspace: dirty=.. ci=.. lang=py+ts open=<v5 동일>
actions: <v1/v5 동일>
last_user: <v1/v5 동일>
args: <v1/v5 동일>
results: <v1/v5 동일>
struct: path=basename overlap=last_arg cand=few
```

## 왜 v6e인가

v5 OOF는 v1보다 일관되게 낮았다: raw `0.751161` vs `0.755929`, 2-stage
`0.755129` vs `0.758499`, rules `0.761686` vs `0.767129`. 손실은 주로
탐색 클래스에 집중됐다: `list_directory`, `grep_search`, `read_file`.

xmeta 룰 회수는 실패했다. v5에서 삭제한 `tier/lang_pref/budget/elapsed/loc`
계열을 룰 플래그로 재주입해도 v5 rules는 `+0.0002`에 그쳤고, v1에는 도움이
되지 않았다. 삭제한 raw meta를 복원하는 방향은 닫는다.

v6는 v5에 정확 turn과 언어 지배도만 더했지만 결과가 애매했다. fixed screen
기준으로 v5 대비 2-stage는 `+0.0009`였으나 raw는 `-0.0026`이고, read 계열
손실이 컸다.

| 비교 | 핵심 관측 |
| --- | --- |
| v5 -> v6 | `list_directory` +0.0067, `read_file` -0.0240, `ask_user` -0.0203 |
| v5 -> v6 예측 전환 | `read_file -> list_directory` 268건, `read_file -> grep_search` 198건 |
| v6 단독 판정 | phase prior는 일부 복구하지만, 모호한 "다음에 열어볼지/더 찾을지" 경계를 흔듦 |

따라서 다음 개량은 meta를 더 넣는 방향이 아니라, 모델이 이미 갖고 있는
phase prior를 **작업 증거로 제동**하는 방향이어야 한다. `path/overlap/cand`는
그 목적에 맞춘 최소 구조 토큰이다.

## 피처 판정

| 판정 | 피처 | 이유 |
| --- | --- | --- |
| 제거 | `elapsed`, `pace`, `budget`, `burn_rate`, `budget_low` | turn 조건부 MI가 사실상 사망. 토큰 낭비. |
| 제거 | `tier`, `lang_pref`, `loc` | xmeta 룰 회수 실패. v5 손실 원인이 아님. |
| 제거 | `hist_truncated` | `turn>6`과 동치. 정확 turn 복원 후 중복. |
| 제거 | v6 `lang=py!ts` / `py~ts` 지배도 | v6 fixed에서 순효과 불명확. read/list 문제와 직접 연결되지 않음. |
| 구간화+정확값 | `turn=07/long` | v5 구간화는 좋지만 0/1/2 오프닝 구분을 뭉갰다. 정확값과 구간을 병기. |
| 유지 | v5 `lang=py+ts` top-2 | lang float 대신 이름만 보존. 지배도 마커보다 보수적. |
| 유지 | `actions`, `args`, `results`, `open_files` | v1/v5 공통 핵심 증거. 라인 순서와 원자료 유지. |
| 추가 | `path` | 현재 요청이 exact path, basename, glob, pronoun, symbol, dir 중 무엇인지 명시. |
| 추가 | `overlap` | 현재 요청의 path/symbol 단서가 open file, last action args, result와 이어지는지 명시. |
| 추가 | `cand` | 직전 탐색 결과가 none/single/few/many 후보 상태인지 명시. |
| 보류 | `last_trigram`, `res`, `open_n` | v1 OOF 룰 프로브에서 null. 이번 버전에는 넣지 않음. |

## 구조 토큰 정의

`struct:` 라인은 항상 마지막에 둔다. 디코더 sequence-classification에서
마지막 토큰 근처의 명시 증거가 phase prior를 보정하기 쉽다는 가정이다.

```text
struct: path=<path> overlap=<overlap> cand=<cand>
```

`path` 값:

| 값 | 의미 |
| --- | --- |
| `glob` | `*`, `glob`, `pattern`, `matching` 등 패턴 탐색 요청 |
| `exact` | slash가 있는 구체 경로 |
| `basename` | `foo.py`, `README.md` 같은 파일명 |
| `pronoun` | "that file", "open it", "그 파일", "방금" 등 직전 맥락 지시 |
| `symbol` | snake/camel/call 형태의 코드 심볼 단서 |
| `dir` | `src`, `docs`, `components` 등 흔한 코드 디렉터리명 |
| `none` | 위 단서 없음 |

`overlap` 값:

| 값 | 의미 |
| --- | --- |
| `open` | 현재 요청 단서가 open file 목록과 겹침 |
| `last_arg` | 현재 요청 단서가 직전 action args와 겹침 |
| `result` | 현재 요청 단서가 직전 result summary와 겹침 |
| 조합 | `open+last_arg`, `last_arg+result` 등 복수 hit |
| `none` | 겹침 없음 |

`cand` 값:

| 값 | 의미 |
| --- | --- |
| `none` | 직전 탐색 결과가 0개/no match 계열이거나 history 없음 |
| `single` | 직전 결과가 단일 후보로 해석됨. `read_file` 직후도 `single` |
| `few` | 2-3개 후보 |
| `many` | 4개 이상 후보 |
| `unknown` | 파싱 실패, 에러, 비탐색 action |

파싱은 보수적이어야 한다. train/inference가 같은 `script.py` 함수를 사용하고,
템플릿이 어긋나면 예외를 던지지 않고 `unknown` 또는 `none`으로 빠진다.

## 길이 예산

Qwen3-0.6B tokenizer, train 70k 기준:

| serializer | mean | p90 | p95 | p99 | max | trunc@384 | trunc@400 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `current_v6e` | `193.786` | `280` | `294` | `319` | `399` | `4/70000` | `0/70000` |

판정: **v6e screen은 `--max-length 400`으로 한다.** len384는 4행만 잘리지만
serializer 효과와 length 효과가 섞인다. v6e 자체를 판단할 때는 무절단
조건을 유지한다.

길이는 v5/v6보다 길지만 v1보다는 짧은 중간안이다. v5의 핵심 장점인 토큰
절감은 일부 유지하고, v6의 read/list 흔들림을 줄이는 쪽에 비용을 쓴다.

측정 artifact:
`experiments/artifacts/20260706_current_v6e_qwen3_06b_token_lengths.json`.

## 구현 상태

- `script.py`
  - `serialize_transformer_sample_current_v6e()`
  - `path_specificity_token()`
  - `struct_overlap_token()`
  - `candidate_state_token()`
  - `explorer_struct_line()`
- `train_transformer.py`
  - `--serializer current_v6e` 등록
- 스모크
  - `python -m py_compile script.py train_transformer.py` 통과
  - serializer help 등록 확인
  - train/test 샘플 직렬화 눈검사 통과
  - 70k 토큰 길이 측정 완료

## 검증 계획

첫 판정은 Qwen3-0.6B fixed screen 하나로 한다. 변수는 serializer와
`max_length=400`만 허용한다.

```bash
.venv/bin/python train_transformer.py \
  --base-model Qwen/Qwen3-0.6B \
  --lr 2e-5 \
  --device cuda \
  --split session \
  --serializer current_v6e \
  --max-length 400 \
  --epochs 3 \
  --batch-size 16 \
  --eval-batch-size 64 \
  --class-weight-power 0.5 \
  --label-smoothing 0.02 \
  --loss focal \
  --focal-gamma 2.0 \
  --replay-mode last1 \
  --max-replay-samples 10000 \
  --replay-sample-weight 0.5 \
  --tune-bias \
  --keep-threshold 0.0 \
  --tokenize-batch-size 1024 \
  --no-research-log \
  --seed 42 \
  --save-val-model \
  --save-fp16 \
  --epoch-checkpoint-dir /content/drive/MyDrive/AADP_exchange/models/v6e_qwen3_06b_screen_ckpt \
  --output-dir /content/drive/MyDrive/AADP_exchange/models/v6e_qwen3_06b_screen \
  --experiment-suffix v6e_qwen3_06b_screen \
  --notes 'FE lane: current_v6e screen (v5 + exact turn + path/overlap/cand; lang top2; len400 zero truncation)'
```

게이트:

- 1차: v6 fixed 2-stage `0.764251`을 실질적으로 넘어야 한다.
- 2차: v1 fixed 앵커 `0.770875` 밴드에 접근해야 한다. 단일 fixed split에서
  `0.005` 이하는 seed/분할 노이즈 가능성이 크므로 per-class를 같이 본다.
- 필수 per-class 조건: `read_file`이 v6의 `0.5744`에서 회복되어야 한다.
  목표는 최소 `0.59`권. `list_directory`는 `0.47`권을 유지하고, `ask_user`는
  v6의 `0.6422`보다 더 무너지면 안 된다.
- 통과 시에만 3-fold OOF를 돌린다. OOF 판정 기준은 v1 OOF 2-stage
  `0.758499`, rules `0.767129`다.

## 실패 시 정리

v6e fixed가 v6를 넘지 못하면 serializer 압축 라인은 닫는다. 그 경우 결론은:

- v5의 삭제는 타당했지만, Qwen3-0.6B 현 레시피에서는 v1 자연어 표현이
  탐색 클래스 경계를 더 잘 보존한다.
- v6의 정확 turn은 phase prior를 복원하지만 read/list/grep 경계에는
  충분하지 않다.
- 구조 토큰은 룰 프로브 null + 학습 screen 실패로 정리하고, 다음 주력은
  current_v1 기반 cascade/timing 최적화로 되돌린다.

v6e가 v6를 넘지만 v1 밴드에는 못 미치면 OOF 여부는 read/list/ask의 방향으로
판단한다. aggregate가 애매해도 `read_file` 회복과 `list_directory` 유지가
동시에 보이면 OOF 한 벌은 가치가 있다. 반대로 read만 회복하고 list/ask가
크게 무너지면 구조 토큰이 다른 경계를 밀어낸 것이므로 중단한다.
