# v5/v6 피처 엔지니어링 결과와 v7 아이디어

## 1. 배경

이 프로젝트는 코딩 에이전트의 다음 action을 14개 클래스 중 하나로 예측하는
문제입니다. 예시는 `read_file`, `grep_search`, `edit_file`, `run_tests`,
`respond_only` 등이며, 평가지표는 Macro-F1입니다.

입력은 JSON 형태지만, 모델에는 JSON을 바로 넣지 않습니다. `current_prompt`,
`history`, `session_meta`를 사람이 읽을 수 있는 텍스트로 바꾼 뒤 Qwen3-0.6B
classifier에 넣습니다. 이 텍스트 변환 방식을 serializer라고 부르고 있습니다.

현재 최고 기준선은 `current_v1` serializer이고, Public Macro-F1은 `0.780`입니다.
v5/v6는 v1보다 짧고 깔끔한 serializer를 만들어 성능과 추론 속도를 함께 개선하려는
시도였습니다.

## 2. 주요 피처

| 피처 | 의미 | 직관 |
| --- | --- | --- |
| `current_prompt` | 현재 사용자 요청 | action 의도를 직접 담는 가장 강한 신호 |
| history action sequence | 직전에 사용한 도구 흐름 | `grep_search -> read_file`처럼 다음 action 패턴 제공 |
| `args` / `results` | 이전 action의 경로, 패턴, 결과 | “파일을 찾았는지, 더 찾아야 하는지” 판단하는 핵심 |
| `last_user` | 직전 사용자 발화 | 현재 요청만으로 부족한 맥락 보완 |
| `open_files` | 열린 파일 목록 | 사용자의 현재 관심 파일 |
| `git_dirty` | 수정 중인지 여부 | dirty면 edit/test 단계일 가능성 증가 |
| `last_ci_status` | 마지막 CI 상태 | failed면 lint/test/edit 쪽 신호 |
| `turn_index` | 세션의 몇 번째 턴인지 | 초반은 탐색, 중후반은 수정/검증/마무리 경향 |
| `language_mix` | 프로젝트 언어 비율 | Python/TS 등 언어에 따라 test/lint 경향 변화 |

## 3. v5의 설계 직관

v5의 핵심 생각은 다음과 같았습니다.

> 다음 action 선택에 직접 도움이 약한 meta는 줄이고, prompt/history/args/results
> 같은 핵심 신호는 그대로 두는 것입니다.

### 제거한 피처

| 피처 | 제거한 직관 |
| --- | --- |
| `user_tier` | free/pro 요금제는 다음에 grep할지 read할지와 무관. MI 거의 0 |
| `language_pref` | 한국어/영어 사용 여부는 도구 선택과 거의 무관. MI 거의 0 |
| `budget_tokens_remaining` | “예산 부족 -> 빨리 답변” 직관은 가능하지만 데이터에서는 약함. 6자리 숫자라 토큰 낭비 큼 |
| `loc` | 큰 repo면 grep 선호 가능성은 있지만, 세션 안에서는 거의 상수라 다음 행동 변화를 설명하기 어려움 |
| `elapsed_session_sec` | 벽시계 시간은 대부분 “세션 진행 정도” 정보이고, 이는 `turn_index`와 중복 |

### 구간화·치환한 피처

| 피처 | 처리 | 직관 |
| --- | --- | --- |
| `turn_index` | 숫자 그대로 쓰지 않고 `start/early/mid/late/long` 구간으로 변환 | 세션 phase 신호만 짧게 유지 |
| `language_mix` | `py=0.62 js=0.21` 같은 float 나열을 `lang=py+js`로 축약 | 소수점 비율보다 주력 언어 이름이 중요 |

### 그대로 둔 피처

`current_prompt`, history action sequence, `args/results`, `last_user`,
`git_dirty`, `last_ci_status`, `open_files`는 그대로 두었습니다. 이들은 다음 action을
직접 설명하는 핵심 신호라고 보았기 때문입니다.

따라서 v5는 정보를 무작정 버린 버전이 아니라, 낮은 신호의 meta만 줄이고 핵심
원문 신호는 유지한 버전이었습니다.

## 4. 결과 요약

핵심 점수만 보면 다음과 같습니다.

| 버전 | OOF+rules | 해석 |
| --- | ---: | --- |
| v1 | `0.767129` | 현재 기준선. Public `0.780` |
| v5 | `0.761686` | v1보다 약 `0.0054` 낮음 |
| v6 | `0.762935` | v5보다 조금 회복, v1보다 낮음 |
| v6e | OOF 미진행 | fixed에서 v6보다 낮아 중단 |

손실은 주로 탐색 계열 action에서 났습니다.

- `read_file`
- `list_directory`
- `grep_search`
- `glob_pattern`

## 5. 왜 v1보다 낮았다고 보는가

“중요한 정보를 지워서 낮아졌다”는 설명은 약해 보입니다.

- 제거한 필드들의 MI가 낮았습니다.
- 삭제한 meta를 rule feature로 다시 넣어도 회복이 거의 없었습니다.
- Qwen tokenizer 기준으로 v1도 길이 절단 문제가 거의 없었습니다.

현재는 다음 두 해석이 더 그럴듯해 보입니다.

- **v1의 노이즈 토큰이 완충재 역할을 했을 가능성**: 정보량은 낮아도 decoder
  classifier의 계산 과정에서는 안정성을 줬을 수 있습니다.
- **v6의 phase prior가 너무 선명했을 가능성**: exact turn이 눈에 잘 띄면서,
  모델이 `args/results/current_prompt`보다 “이 시점이면 보통 list/grep” 같은
  prior에 더 의존했을 수 있습니다.

## 6. 그래도 v5/v6가 의미 있는 이유

v5/v6는 단독으로는 낮지만, v1과 다르게 틀립니다.

- v6는 v1과 예측이 약 `10.4%` 달랐습니다.
- v1 + v6 blend + rules: `0.770680`
- v1 + v5 + v6 blend + rules: `0.772394`
- m7(v1) + m8(v1) + v6 blend + rules: `0.778175`

즉 v5/v6는 v1을 대체하지는 못했지만, 다른 판단 경계를 가진 보조 모델로는 의미가
있었습니다.

## 7. 그래서 제가 풀고 싶은 문제

직관적으로는 v1보다 나은 serializer가 반드시 있을 것 같습니다. v1은 raw meta를
많이 넣는 단순한 형태라서, 불필요한 토큰과 중복 정보가 섞여 있습니다. 그런데
v5/v6처럼 깔끔하게 줄이면 오히려 성능이 떨어졌습니다.

현재 문제는 “serializer 개선이 불가능하다”가 아니라, **무엇을 줄이고 무엇을
강조해야 하는지 아직 찾지 못했다**는 점이라고 생각합니다.

제가 찾고 싶은 형태는 다음과 같습니다.

- v1처럼 `prompt/history/args/results`의 원문 의미는 충분히 보존합니다.
- v5처럼 명백히 약한 meta는 줄입니다.
- v6처럼 phase 정보를 너무 강하게 드러내지는 않습니다.
- `read_file`, `list_directory`, `grep_search` 경계를 더 잘 가르는 증거를 남깁니다.
- 단순히 짧은 serializer가 아니라, 모델이 올바른 증거에 attention을 쓰게 만드는
  serializer를 만들고 싶습니다.

교수님께는 이 막힌 지점 자체에 대해 조언을 구하고 싶습니다.

질문은 다음 정도로 정리할 수 있습니다.

1. v1보다 나은 serializer가 있을 것이라는 직관은 타당한가요?
2. 낮은 MI meta를 줄였는데 성능이 떨어진 현상을 어떻게 해석해야 할까요?
3. decoder classifier에서 정보량이 낮은 토큰도 attention 분산, 계산 안정성,
   salience 조절 같은 역할을 할 수 있을까요?
4. `turn_index`처럼 유용하지만 위험한 phase 정보는 어떤 방식으로 표현하는 것이
   좋을까요?
5. `read_file`, `list_directory`, `grep_search`처럼 가까운 action 경계를 구분하려면
   어떤 종류의 증거를 serializer에 남겨야 할까요?
