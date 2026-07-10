# FE Raw Data Review — 구조 토큰 조사와 v6 제안 (2026-07-06)

## 목적

`open/data/train.jsonl` 원문을 피처 엔지니어링 관점에서 다시 읽고,
현재 `current_v5`의 meta/workspace 디노이즈 방향이 맞는지, 그리고 그 위에
어떤 구조 토큰을 추가할지 정리한다.

이 문서는 구현 스펙이 아니라 판단 근거 문서다. `current_v5` 구현 세부는
`fe_current_v5_spec.md`가 기준이고, 여기서는 raw 데이터 정성 검토와 다음
serializer/rule 후보를 기록한다.

## 조사 방식

- 로컬 전수 집계: `open/data/train.jsonl` 70,000행과
  `open/data/train_labels.csv`를 조인해 라벨 분포, 세션 구조, categorical MI,
  prompt cue, transition, duplicate/lookup 위험을 측정했다.
- cheap ablation: session split seed 42에서 얕은 SVC로
  `prompt-only`, `structured-only`, `prompt+structured`, `current_v1`,
  `compact_events`를 비교했다.
- 서브에이전트 8개를 병렬로 돌려 raw row를 정성 검토했다.
  - 세션 구조/누수/메타 관점
  - 언어/의도/문체 관점
  - 약한 탐색 클래스 관점
  - serializer/rule vocab 관점
  - raw 탐색 4클래스 160행 검토
  - raw 수정 3클래스 180행 검토
  - raw 실행/검증/마무리 160행 검토
  - raw ask/plan/web/respond 188행 검토

파일 수정은 이 문서 추가 외에는 하지 않았다.

## 데이터가 실제로 시뮬레이션하는 것

각 row는 "현재 사용자 요청을 보고 다음 assistant action 14개 중 하나를
고르는 decision point"다. 자연어 intent classification이라기보다 합성
코딩 에이전트 세션의 다음 tool/action 선택 습관을 맞추는 문제에 가깝다.

핵심 구조:

- row 수: 70,000
- 세션 수: 9,429
- 세션당 row: 평균 7.42, 중앙값 7, p90 12, 최대 18
- `id`는 `sess_...-step_NN` 형식
- `session_meta.turn_index == id step`가 100% 일치
- `history`는 전체 세션이 아니라 최근 최대 6개 이전 user/action 쌍만 보존
- 후반 세션에서는 오래된 문맥이 잘리고, `history_truncated` 자체가 상태
  신호가 된다

주의:

- train 내부에는 later row history가 earlier row의 `current_prompt`와 label을
  그대로 담는 cross-row 구조가 있다. random split, prompt lookup, verbatim
  history lookup은 낙관적이다.
- 기존 Public leak probe가 실패했으므로, 서버 test에 train dump의 cross-row
  구조가 그대로 이전된다고 보면 안 된다.
- 로컬 `open/data/test.jsonl`은 5행 스텁이고 train과 id/prompt가 겹친다.
  검증/lookup/pseudo-label 근거로 쓰면 안 된다.

## 전수 집계의 핵심 수치

라벨 분포:

| label | count | pct |
|---|---:|---:|
| `edit_file` | 11,171 | 15.96% |
| `grep_search` | 9,912 | 14.16% |
| `read_file` | 9,257 | 13.22% |
| `glob_pattern` | 5,284 | 7.55% |
| `respond_only` | 5,178 | 7.40% |
| `run_bash` | 5,068 | 7.24% |
| `apply_patch` | 4,823 | 6.89% |
| `run_tests` | 4,561 | 6.52% |
| `list_directory` | 4,329 | 6.18% |
| `plan_task` | 2,679 | 3.83% |
| `ask_user` | 2,701 | 3.86% |
| `lint_or_typecheck` | 2,283 | 3.26% |
| `write_file` | 1,481 | 2.12% |
| `web_search` | 1,273 | 1.82% |

Categorical MI vs label, train 70k:

| feature | MI | relH |
|---|---:|---:|
| `prompt_intent_set` | 0.56545 | 22.93% |
| `last_action_bigram` | 0.41248 | 16.73% |
| `last_action` | 0.24988 | 10.13% |
| `prev_action` | 0.21238 | 8.61% |
| `last_result_semantic` | 0.14936 | 6.06% |
| `turn_exact` | 0.13865 | 5.62% |
| `turn_regime` | 0.13494 | 5.47% |
| `history_action_count` | 0.13334 | 5.41% |
| `open_files_count` | 0.12901 | 5.23% |
| `prompt_len_bin` | 0.09859 | 4.00% |
| `prompt_has_path` | 0.04235 | 1.72% |
| `top2_lang` | 0.03892 | 1.58% |
| `ci_status` | 0.01510 | 0.61% |
| `budget_bin` | 0.00665 | 0.27% |
| `loc_bin` | 0.00210 | 0.09% |
| `user_tier` | 0.00046 | 0.02% |
| `language_pref` | 0.00045 | 0.02% |
| `prompt_has_hangul` | 0.00031 | 0.01% |

해석:

- `user_tier`, `language_pref`, Hangul 여부는 거의 노이즈다.
- `turn`, `recent action`, `result status`, `open_files_count`, `top2_lang`는
  모델이 매번 자연어에서 추론하게 두기보다 짧은 구조 토큰으로 주는 편이
  합리적이다.

## Cheap ablation

Session split seed 42, 얕은 선형 SVC 기준:

| input | Macro-F1 |
|---|---:|
| global majority | 0.019741 |
| majority by last_action | 0.137955 |
| majority by last_action_bigram | 0.167802 |
| structured categorical only | 0.235889 |
| prompt word 1-2gram only | 0.439369 |
| prompt + structured tokens | 0.649322 |
| `compact_events_v1` | 0.617036 |
| `current_v1` text | 0.563872 |

해석:

- prompt만으로는 한계가 크다.
- 구조 피처만으로도 약간 설명되지만 단독 결정 피처는 아니다.
- prompt 원문을 보존하고 구조 토큰을 명시 추가할 때 가장 크게 오른다.
- token soup 재구성(`compact_events`)보다 원문 prompt 보존 + 짧은 상태 토큰이
  더 좋다.

## 정성 조사 결론

### 1. 표면어는 자주 배신한다

`open/read/show/봐/찾아/run/test/lint/build`는 라벨을 바로 결정하지 못한다.
한국어의 "봐줘", "한번 보자", "찾아봐"는 완곡 표현으로 여러 tool에 퍼진다.

예:

- `찾아봐`는 로컬 `grep_search`, `glob_pattern`, `list_directory`, 외부
  `web_search`에 모두 나온다.
- `test/spec`는 파일을 열라는 뜻이면 `read_file`, 위치를 찾으면
  `glob_pattern`/`grep_search`, 실행이면 `run_tests`, 정적 검증이면
  `lint_or_typecheck`가 된다.
- `build/compile`은 `run_bash`, `run_tests`, `lint_or_typecheck`에 모두 섞인다.

따라서 keyword hard rule은 위험하다. OOF logits 위에서 조건부 soft boost로
검증해야 한다.

### 2. 탐색 4클래스의 핵심은 candidate state다

`read_file`, `grep_search`, `list_directory`, `glob_pattern`은 현재 약한
클러스터다. raw row를 읽어보면 구분축은 자연어 동사가 아니라 "파일 후보가
이미 좁혀졌는가"다.

| 상황 | 유력 label |
|---|---|
| 세션 초반, 후보 없음, repo/folder orientation | `list_directory` |
| 직전 list/grep/glob 결과로 단일 파일 후보가 생김 | `read_file` |
| 심볼/호출처/참조/설정 키를 찾음 | `grep_search` |
| 파일군, 확장자, 위치 불명 basename, wildcard | `glob_pattern` |

필요한 구조 토큰:

- `candidate_state=single|few|many|none|unknown`
- `path_specificity=exact_path|basename_ext|dir_only|glob|symbol_only|pronoun|none`
- `prompt_mentions_open_file=true|false`
- `prompt_mentions_last_arg=true|false`
- `last_result_count=zero|one|few|many|unknown`

### 3. 수정 3클래스는 "새 파일 vs 국소 수정 vs 누적 패치"다

`write_file`:

- 파일 단위 생성/전면 재작성
- turn 1-3에 강함
- `새 파일`, `scaffold`, `from scratch`, `rewrite whole file`, `통째로`가 고정밀

`edit_file`:

- 직전 `read_file`/`grep_search`가 준 단일 path/symbol을 이어받는 국소 수정
- "그 함수", "거기", "한 줄", "handler" 같은 짧은 지시가 많음
- prompt 표면보다 `last_args.path`, `defines:`, `target_symbol` 연속성이 중요

`apply_patch`:

- 늦은 turn, 다파일, 연쇄 보정, patch/edit 실패 후 재시도, plan 수락 후 실행
- literal `patch` 단어는 낮은 coverage
- `두 파일`, `둘 다`, `한꺼번에`, `both`, `across`, `callsites`, `imports`가 중요

필요한 구조 토큰:

- `new_file_cue`
- `whole_rewrite_cue`
- `multi_file_cue`
- `localized_edit_cue`
- `prev_plan_accept`
- `recent_edit_failure`
- `path_continuity`
- `recent_edit_counts`

### 4. 실행/검증/마무리는 respond_only만 깨끗하다

`respond_only`:

- 일반 Q&A보다 세션 마무리/요약 클래스에 가깝다.
- `마무리`, `정리`, `요약`, `wrap`, `recap`, `done`, `good enough`가 강하다.
- CI failed여도 사용자가 멈추고 요약하라면 `respond_only`다.

`run_bash`:

- 테스트가 아닌 일반 명령 실행
- build/dev server/install/migrate/docker/npm/pip/terraform/dbt/cargo/go/log 확인

`run_tests`:

- 수정 후 관련 suite/spec/happy path/regression/green 확인
- 직전 `edit_file`/`apply_patch` 뒤에 강하다.

`lint_or_typecheck`:

- static/lint/typecheck/tsc/ruff/mypy/pyright/import graph/unused import
- 하지만 `lint/type/test/build` keyword 단독으로는 세 실행 라벨이 모두 섞인다.

필요한 구조 토큰:

- `end_intent_cue`
- `test_target_cue`
- `static_check_cue`
- `generic_command_cue`
- `last_action=edit_file|apply_patch|run_bash`
- `ci_status`
- `result_semantic`

### 5. ask_user / plan_task / web_search는 경계 노이즈가 크다

`ask_user`:

- 진짜 패턴은 사용자 선택/요구사항 부족이다.
- 예: 이름/인자/범위/선호/적용 강도를 물어봐야 하는 상황.
- 다만 외부 조사 cue가 있는데 `ask_user`로 찍힌 row도 있다.

`plan_task`:

- 코딩 전 단계화, 큰 변경의 영향 범위/순서, "before coding", "단계부터".
- 초기 turn 비중이 크다.
- 다만 공식 문서/검색 cue가 섞인 row도 있다.

`web_search`:

- 로컬 코드만으로 답이 안 나는 외부 지식, 버전/호환성/공식 문서/최신 권장 방식.
- median turn이 늦고, 이미 로컬을 본 뒤 외부 확인으로 넘어가는 흐름이 많다.
- `web/docs/latest` 단어만으로는 coverage가 낮다.

필요한 구조 토큰:

- `external_knowledge_cue`
- `missing_requirement_cue`
- `planning_cue`
- `local_context_present`
- `after_local_probe`

단, 이 그룹은 hard boost 금지다. 경계 row가 많으므로 작은 soft boost 후보로만
다뤄야 한다.

## current_v5 해석

`current_v5`의 직관은 맞다. `user_tier`, `language_pref`, raw budget, raw
elapsed, raw loc 같은 토큰은 대부분 노이즈이거나 `turn`과 중복된다.

다만 pure v5는 다음 리스크가 있다.

- 삭제한 raw meta 중 일부가 약하지만 실제 상태 prior로 쓰였을 수 있다.
- 특히 `budget/elapsed/loc`는 단독 MI는 낮아도 generation regime이나 session
  phase의 proxy일 수 있다.
- v5 fixed screen이 v1보다 낮게 나온 정황은 "삭제 방향이 틀렸다"보다
  "필요한 상태 신호까지 같이 버렸다"로 보는 편이 타당하다.

따라서 다음 방향은 pure v5 강화가 아니라:

> `current_v6 = current_v5 + 짧은 구조 토큰`

이다.

## current_v6 제안

목표:

- v1의 noisy meta는 줄인다.
- v5가 잃은 상태 신호는 모델이 쉽게 쓰는 구조 토큰으로 회수한다.
- 자연어를 더 늘리지 않는다.
- v5의 토큰 절감분 일부만 가장 강한 상태 토큰에 재투자한다.

초안:

```text
current: <v1/v5와 동일 prompt>
struct: turn=mid hist=trunc last=grep_search tri=read_file>grep_search>grep_search res=found open_n=1 path=basename overlap=last_arg cand=few ci=failed lang=py+ts
workspace: dirty=true open=<v5와 동일 open list 또는 compact open>
actions: <v1/v5와 동일 최근 actions>
last_user: <v1과 동일>
args: <v1과 동일 또는 compact 유지>
results: <v1과 동일 또는 result head 유지>
```

최소 토큰 세트:

| token | 목적 |
|---|---|
| `turn=start|early|mid|late|long` | phase prior |
| `hist=full|trunc` | history cutoff 상태 |
| `last=<action>` | transition |
| `tri=<a>b>c` | high-signal action ngram |
| `res=pass|fail|found|none|listed|read|unknown` | last result semantic |
| `open_n=0|1|2|many` | workspace state |
| `path=exact|basename|dir|glob|symbol|pronoun|none` | prompt path specificity |
| `overlap=open|last_arg|both|none` | path/symbol continuity |
| `cand=single|few|many|none|unknown` | candidate narrowedness |
| `ci=passed|failed|none` | execution state |
| `lang=<top1+top2>` | workspace language pair |

선택 후보:

- `new_file=true`
- `whole_rewrite=true`
- `multi_file=true`
- `end_intent=true`
- `external=true`
- `static_check=true`
- `test_target=true`

선택 후보는 과하면 keyword soup가 되므로, 먼저 rule vocab에서 검증한 뒤
serializer에 넣는 편이 낫다.

## Rule-tuner vocab 후보

OOF logits가 이미 있는 라인에서는 학습 없이 rule-tuner vocab만 확장해 신호를
볼 수 있다. 우선순위는 다음과 같다.

1. `last_trigram`
2. `turn_regime`
3. `hist_truncated`
4. `path_specificity`
5. `candidate_state`
6. `prompt_mentions_open_file`
7. `prompt_mentions_last_arg`
8. `result_count/semantic`
9. `end_intent_cue`
10. `new_file/whole_rewrite/multi_file`

적용 원칙:

- hard override 금지
- OOF logits 위에서 class별 logit boost로만 검증
- weak class top-2/top-3가 관련 클러스터 안에 있을 때만 조건부 적용하는
  gated boost 검토

## 실험 순서 제안

1. 현재 진행 중인 `current_v5` OOF 판정은 끝까지 본다.
   - v5가 명확히 낮으면 timing-only lever로 격하.
   - v5가 동급이면 v6의 base로 유지.

2. GPU-free rule vocab probe:
   - 기존 M7/M8 OOF logits 위에서 `last_trigram`, `path_specificity`,
     `candidate_state`, `hist_truncated`, `end_intent` 조건을 추가해 greedy
     boost 재튜닝.
   - 목표는 전체 Macro-F1보다 약한 클래스 per-class 개선과 조건 안정성 확인.

3. `current_v6` serializer fixed screen:
   - Qwen3-0.6B, champion recipe, v5와 동일 길이/epoch 조건.
   - 변수는 serializer 하나.
   - pure v5 대비 개선, v1 fixed anchor 대비 손실 폭, 약한 클래스 변화를 같이 본다.

4. v6가 fixed에서 v1 대비 크게 밀리지 않으면 3-fold OOF:
   - serializer 변경은 기존 rules/bias 이식 불가.
   - OOF logits, 2-stage bias, rule boosts를 한 벌로 다시 만든다.

5. timing 목적의 v5/v6 선택:
   - v6가 v5보다 조금 길어도 v1보다 충분히 짧으면 cascade/timing 경로에는 가치가 있다.
   - 품질이 v1보다 낮으면 final baseline 교체가 아니라 cascade/timing 전용으로만 사용한다.

## 리스크

- raw row에 합성 템플릿 노이즈와 모순 사례가 꽤 있다. 사람이 보기엔 맞는 룰도
  Public에서 깨질 수 있다.
- prompt/path identity 기반 lookup은 Public leak probe 실패와 같은 이유로 위험하다.
  normalized path/type token만 쓴다.
- high-cardinality full action sequence는 과적합 위험이 있다. `last_trigram`까지만
  우선 쓰고 support threshold를 둔다.
- `web_search/plan_task/ask_user` 경계는 특히 noisy하다. 이 그룹의 rule은 작은
  soft boost만 허용한다.
- serializer 변경은 항상 체인 전체 재생산 비용이 든다. v6를 만들면 OOF/rules/refit
  모두 새로 해야 한다.

## 결론

메타 삭제/변경은 직관적으로도, 집계상으로도 타당하다. 하지만 pure v5는
상태 신호 일부를 같이 버렸을 가능성이 있다. 다음으로 가장 타당한 가설은
`current_v5 + 구조 토큰`, 즉 `current_v6`다.

정확한 방향은 "자연어를 더 넣기"가 아니라 "모델이 매번 추론하던 세션 상태를
짧은 토큰으로 명시하기"다. 특히 `turn`, `history_truncated`, `last_trigram`,
`result_semantic`, `open_n`, `path_specificity`, `candidate_state`, `overlap`은
raw 데이터와 전수 집계 양쪽에서 반복 확인된 신호다.
