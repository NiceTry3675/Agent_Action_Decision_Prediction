있습니다. **같은 A-W4 레시피를 두 seed 더 학습하고 Public 최고 seed를 고른 뒤 threshold까지 바꾸는 순서보다 더 좋은 슬롯 운용**이 있습니다.

그 방식은 처음 제외했던 **seed harvesting / best-of-N 그 자체**이고, 현재 관찰된 main-swap 변동폭 `±0.0006`을 Public에서 최대값 선택하는 구조라 실제 레시피 개선과 instance luck이 다시 섞입니다. A-W4는 이미 강한 구조적 신호가 있으므로, 먼저 **Weak4 이득이 기존 aux ensemble에 희석되지 않도록 통합하는 카드**에 슬롯을 써야 합니다.

## 권장 제출 순서

|    순위 | 카드                                  | 추가 학습 | 핵심 목적                                |
| ----: | ----------------------------------- | ----: | ------------------------------------ |
|     1 | A-W4 s42 main swap, 기존 mgn1.25      |    없음 | 기본 통합 효과 확인                          |
| **2** | **Weak4 geometry lock**             |    없음 | aux가 A-W4의 intra-Weak4 경계를 희석하는 것 방지 |
| **3** | **Weak4만 threshold 1.0, 나머지는 1.25** |    없음 | specialist 구간만 aux 사용 축소             |
|     4 | 고정 A-W4 다중-seed ensemble            |  1~2회 | seed 선택이 아니라 variance averaging      |
|   마지막 | 전역 threshold 1.0                    |    없음 | 작은 저비용 probe                         |

남은 슬롯이 2개라면 **2번과 3번**, 한 개라면 **2번**이 최선입니다.

---

# 1. 먼저 제출할 기준 카드

현재 최고 pack을 기준으로:

```text
model/    ← A-W4 seed42 clean INT8
model_b/  ← 기존 seed909 INT4
model_c/  ← 기존 seed7070 INT4
script    ← 기존 threshold 1.25와 rule stack
```

이 카드가 Public에서도 양성이면 다음 슬롯으로 넘어갑니다.

단, rule stack은 제출 전에 무료로 한 번만 검사해야 합니다. 회수된 A-W4 validation에서:

```text
기존 ten-rule
vs
ten-rule + R1i + sequence-exec
```

의 **추가분**만 확인하세요.

* pooled 양수이고 pseudo-fold 2/3 이상 양수: 12개 유지
* pooled `≤-0.0002`이고 pseudo-fold 2/3 이상 음수: R1i와 sequence-exec 제거
* 애매하면 현재 12개 유지

새 rule이나 bias는 만들지 않습니다.

---

# 2. 가장 기대값 높은 무학습 카드: Weak4 geometry lock

A-W4의 top-3 teacher negatives는 사실상 거의 모두 Weak4 내부입니다. 즉 이번 레버가 잘 학습한 것은 주로:

```text
read_file
grep_search
list_directory
glob_pattern
```

사이의 **상대 순위와 margin**입니다.

그런데 현재 ensemble은 old-recipe seed909/7070 logits까지 동일 평균하므로, A-W4가 만든 Weak4 내부 기하를 다시 희석할 수 있습니다.

이를 막되 기존 ensemble의 cross-family 판단은 보존하는 방식이 좋습니다.

## 적용 방식

현재처럼 먼저 centered-logit ensemble을 만듭니다.

```python
def center(z):
    return z - z.mean(dim=-1, keepdim=True)

mixed = (
    center(main_logits)
    + center(model_b_logits)
    + center(model_c_logits)
) / 3.0
```

그다음 **main의 raw top-2가 모두 Weak4인 routed row**에서만:

* ensemble이 결정한 Weak4 블록 평균은 유지
* Weak4 네 클래스 내부 상대 logits는 A-W4 main 것으로 복원

합니다.

```python
WEAK4 = torch.tensor([0, 1, 2, 3], device=main_logits.device)

main_centered = center(main_logits)
main_top2 = main_logits.topk(2, dim=-1).indices
weak4_internal = (main_top2[..., None] == WEAK4).any(-1).all(-1)

route = raw_main_margin < 1.25
lock_mask = route & weak4_internal

mixed_w = mixed[:, WEAK4]
main_w = main_centered[:, WEAK4]

# Ensemble의 Weak4-vs-나머지 block mass는 보존
block_mean = mixed_w.mean(dim=-1, keepdim=True)

# A-W4가 학습한 Weak4 내부 순위/margin은 보존
main_w_relative = main_w - main_w.mean(dim=-1, keepdim=True)
locked_w = block_mean + main_w_relative

mixed[:, WEAK4] = torch.where(
    lock_mask[:, None],
    locked_w,
    mixed_w,
)
```

## 이 카드가 threshold 1.0보다 우선인 이유

이 방식은 aux가 잘할 수 있는:

```text
Weak4 블록 vs ask/plan/edit/run 계열
```

판단은 그대로 둡니다.

오직 A-W4가 실제 양성을 보인:

```text
Weak4 내부 클래스 선택
```

만 main에 맡깁니다.

즉 aux를 통째로 빼는 것보다 훨씬 보수적입니다. 기존 screen에서도 centered Weak4 graft가 전체 `+0.001204`, pseudo-session 세 구간 모두 양수였으므로 방향성 근거도 있습니다. [A-W4 screen manifest](sandbox:/mnt/data/repo_f3c9/Agent_Action_Decision_Prediction/experiments/manifests/20260715_lane_a_action_margin_weak4_screen.json)

이것이 **남은 슬롯 하나만 있을 때의 선택**입니다.

---

# 3. threshold는 전역 1.0이 아니라 Weak4에만 1.0

그다음 카드로 threshold를 바꾼다면 다음처럼 해야 합니다.

```python
route_threshold = torch.where(
    weak4_internal,
    torch.tensor(1.0, device=margin.device),
    torch.tensor(1.25, device=margin.device),
)
route = raw_main_margin < route_threshold
```

즉:

```text
main top-2가 모두 Weak4:
    aux route threshold = 1.0

그 외:
    aux route threshold = 1.25
```

이유는 분명합니다.

A-W4의 implied non-Weak4 평균 변화는 약 `-0.000785`였습니다. 따라서 non-Weak4에서는 기존 generalist aux가 오히려 collateral damage를 복구할 수 있습니다. **전역 threshold를 1.0으로 낮추면 aux가 필요한 non-Weak4 중간-margin 행까지 main solo로 남깁니다.**

반면 Weak4 내부에서는 A-W4가 강하게 좋아졌으므로 aux가 개입하는 범위를 줄이는 것이 맞습니다.

따라서 두 번째 script 카드의 권장 조합은:

```text
Weak4 geometry lock
+
Weak4-internal threshold 1.0
+
기타 threshold 1.25
```

입니다.

전역 `1.0`은 이것보다 뒤입니다.

---

# 4. 추가 seed를 만들 거라면 “최고 seed 선택”이 아니라 고정 ensemble

GPU가 남아 두 seed를 더 만들 수 있더라도 각각 Public에 올려 최고점을 고르지 않는 편이 좋습니다.

대신 제출 전 다음 한 구성을 고정하세요.

```text
model/    = A-W4 s42 INT8
model_b/  = A-W4 s202 INT4
model_c/  = A-W4 s909 INT4
routing   = margin < 1.0
rules     = 사전 rule-transfer audit에서 확정한 stack
```

이것은 seed harvesting이 아니라 **동일한 양성 recipe의 variance reduction**입니다. 세 모델을 각각 제출하지 않고, 고정된 하나의 ensemble만 제출합니다.

다만 이 카드는 script-only 두 카드보다 우선하지 않습니다. A-W4의 non-Weak4 부작용이 systematic이면 세 seed 모두 같은 방향으로 틀릴 수 있기 때문입니다.

조금 더 보수적인 고정 hybrid는:

```text
model/    = A-W4 s42
model_b/  = 기존 M8-recipe s909
model_c/  = A-W4 s202
```

입니다. 기존 seed909는 2-member ensemble 단계부터 독립적인 Public 근거가 있었으므로 generalist 하나를 남길 수 있습니다. 새 seed를 하나만 학습한다면 **s202를 택하고, 기존 s909를 보존하는 hybrid**가 가장 합리적입니다. 기존 s909와 A-W4 s909를 함께 넣는 것은 같은 seed의 상관이 커질 수 있어 덜 좋습니다.

---

# 추천 슬롯 배분

## 직접 A-W4 main swap 이후 2슬롯

```text
슬롯 1:
A-W4 main
+ mgn1.25
+ Weak4 geometry lock

슬롯 2:
위 카드
+ Weak4-internal만 mgn1.0
+ 나머지 mgn1.25
```

## 3슬롯

세 번째는:

```text
고정 A-W4 multi-seed 또는 hybrid ensemble
```

에 씁니다.

## 4슬롯 이상

그때야 마지막으로:

```text
A-W4 direct pack의 전역 mgn1.0
```

을 봅니다.

---

# 하지 않을 순서

다음 방식은 권하지 않습니다.

```text
A-W4 s42 제출
A-W4 s202 제출
A-W4 s909 제출
Public 최고 seed 선택
그 최고 seed에 mgn1.0 제출
```

이 순서는 recipe 효과보다 main-instance noise를 고르는 데 슬롯 대부분을 씁니다. 현재 main swap만으로도 solo 중립인 trioT-s42가 ensemble에서는 `+0.000557` 움직였으므로, Public 최고 seed가 반드시 가장 좋은 정책이라는 보장이 없습니다. [현재 champion 기록](sandbox:/mnt/data/repo_f3c9/Agent_Action_Decision_Prediction/final_summary.md)

## 최종 선택

가장 좋은 순서는 다음입니다.

> **A-W4 s42 direct main swap → Weak4 geometry lock → Weak4에만 routing 1.0 → 그래도 슬롯이 남으면 고정 multi-seed ensemble.**

전역 `1.25 → 1.0`은 마지막 카드이고, 같은 recipe seed 두 개를 개별 제출해 최고점을 고르는 것보다 **A-W4의 Weak4 내부 기하를 기존 ensemble로부터 보호하는 카드가 더 직접적이고 기대값도 높습니다.**
