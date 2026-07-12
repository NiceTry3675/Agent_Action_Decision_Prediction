"""Core contracts for the live-pair Weak4 residual specialist.

This module intentionally has no packaging or repository side effects.  The
training/evaluation scripts import it directly; a future submission integration
can copy the small inference helpers into ``script.py`` after the offline gates
pass.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter, defaultdict
from typing import Iterable, Mapping, Sequence

import torch
import torch.nn.functional as F
from torch import nn

from script import ALL_CLASSES, safe_text, serialize_transformer_sample


SCHEMA_VERSION = 1
DATASET_KIND = "weak4_live_pair_residual_dataset"
MODEL_SCHEMA = "mbert-live-pair-residual-v1"
FEATURE_SCHEMA = "weak4-main-probabilities-v1"
CLEAN_USAGE_SCOPE = "specialist_clean_cv"
CLEAN_VALIDATION_SCOPE = "clean_fixed_parent_session_cv"
DIAGNOSTIC_USAGE_SCOPE = "construction_diagnostic_only"
DIAGNOSTIC_VALIDATION_PREFIX = "construction_diagnostic_"

# Canonical class ids: read=0, grep=1, list=2, glob=3.
LIVE_WEAK4_PAIRS = ((0, 2), (0, 3), (1, 3), (2, 3))
PAIR_TO_ID = {pair: idx for idx, pair in enumerate(LIVE_WEAK4_PAIRS)}
NUMERIC_FEATURE_DIM = 10

TARGET_PROTECT = 0
TARGET_RESCUE = 1
TARGET_NOISY_ALT = 2
TARGET_OUTSIDE = 3
TARGET_NAMES = ("protect", "rescue", "noisy_alt", "outside")


def validate_dataset_scope(payload: Mapping, allow_diagnostic: bool = False) -> str:
    """Fail closed unless a dataset declares the exact supported scope."""

    usage_scope = payload.get("usage_scope")
    validation_scope = payload.get("validation_scope")
    if (
        usage_scope == CLEAN_USAGE_SCOPE
        and validation_scope == CLEAN_VALIDATION_SCOPE
        and payload.get("parent_split") == "session"
        and int(payload.get("parent_seed", -1)) == 42
        and payload.get("reliability_source") == "none"
    ):
        return "clean"
    if (
        usage_scope == DIAGNOSTIC_USAGE_SCOPE
        and isinstance(validation_scope, str)
        and validation_scope.startswith(DIAGNOSTIC_VALIDATION_PREFIX)
    ):
        if not allow_diagnostic:
            raise ValueError(
                "diagnostic pair dataset is disabled by default; "
                "pass the explicit diagnostic allow flag"
            )
        return "diagnostic"
    raise ValueError(
        "pair dataset has an invalid or ambiguous usage scope: "
        f"usage_scope={usage_scope!r} validation_scope={validation_scope!r}"
    )


def _as_float_logits(logits: torch.Tensor) -> torch.Tensor:
    logits = torch.as_tensor(logits).detach().float().cpu()
    if logits.ndim != 2 or logits.shape[1] != len(ALL_CLASSES):
        raise ValueError(
            f"main logits must have shape [rows, {len(ALL_CLASSES)}], "
            f"got {tuple(logits.shape)}"
        )
    if not torch.isfinite(logits).all():
        raise ValueError("main logits contain non-finite values")
    return logits


def select_live_pair_routes(main_logits: torch.Tensor) -> dict[str, torch.Tensor]:
    """Select rows whose full argmax is Weak4 and Weak4 top-2 is a live pair.

    Pair orientation is canonical (lower class id first). ``top_sides`` is 0
    when the main prediction is the canonical left class and 1 otherwise.
    Stable sorting gives the same lower-id tie break as ``torch.argmax``.
    """

    logits = _as_float_logits(main_logits)
    full_pred = logits.argmax(dim=1)
    weak_order = torch.argsort(logits[:, :4], dim=1, descending=True, stable=True)

    indices: list[int] = []
    pair_ids: list[int] = []
    pair_classes: list[tuple[int, int]] = []
    top_sides: list[int] = []
    base_gaps: list[float] = []
    main_predictions: list[int] = []
    alternative_predictions: list[int] = []
    for row in range(len(logits)):
        main_pred = int(full_pred[row])
        if main_pred >= 4:
            continue
        weak_top = [int(value) for value in weak_order[row, :2]]
        pair = tuple(sorted(weak_top))
        pair_id = PAIR_TO_ID.get(pair)
        if pair_id is None:
            continue
        left, right = pair
        top_side = 0 if main_pred == left else 1
        alt = right if top_side == 0 else left
        indices.append(row)
        pair_ids.append(pair_id)
        pair_classes.append(pair)
        top_sides.append(top_side)
        base_gaps.append(float(logits[row, left] - logits[row, right]))
        main_predictions.append(main_pred)
        alternative_predictions.append(alt)

    return {
        "indices": torch.tensor(indices, dtype=torch.long),
        "pair_ids": torch.tensor(pair_ids, dtype=torch.long),
        "pair_classes": torch.tensor(pair_classes, dtype=torch.long).reshape(-1, 2),
        "top_sides": torch.tensor(top_sides, dtype=torch.long),
        "base_gaps": torch.tensor(base_gaps, dtype=torch.float32),
        "main_predictions": torch.tensor(main_predictions, dtype=torch.long),
        "alternative_predictions": torch.tensor(alternative_predictions, dtype=torch.long),
    }


def main_numeric_features(
    main_logits: torch.Tensor,
    route: Mapping[str, torch.Tensor],
) -> torch.Tensor:
    """Return the ten bounded main-model features used by the expert."""

    logits = _as_float_logits(main_logits)
    indices = torch.as_tensor(route["indices"], dtype=torch.long)
    if not len(indices):
        return torch.empty((0, NUMERIC_FEATURE_DIM), dtype=torch.float32)
    if int(indices.min()) < 0 or int(indices.max()) >= len(logits):
        raise ValueError("route contains an out-of-range row")

    selected = logits[indices]
    p14 = selected.softmax(dim=1)
    p4 = selected[:, :4].softmax(dim=1)
    p14_top2 = p14.topk(2, dim=1).values
    entropy4 = -(p4 * p4.clamp_min(1e-12).log()).sum(dim=1) / math.log(4)
    entropy14 = -(p14 * p14.clamp_min(1e-12).log()).sum(dim=1) / math.log(len(ALL_CLASSES))
    abs_gap = torch.as_tensor(route["base_gaps"], dtype=torch.float32).abs().clamp(max=4.0) / 4.0
    features = torch.cat(
        [
            p4,
            p14[:, :4].sum(dim=1, keepdim=True),
            p14_top2[:, :1],
            (p14_top2[:, 0] - p14_top2[:, 1]).unsqueeze(1),
            entropy4.unsqueeze(1),
            entropy14.unsqueeze(1),
            abs_gap.unsqueeze(1),
        ],
        dim=1,
    )
    if features.shape[1] != NUMERIC_FEATURE_DIM or not torch.isfinite(features).all():
        raise AssertionError(f"invalid numeric features: shape={tuple(features.shape)}")
    return features.float()


def build_pair_targets(
    y_true: Sequence[int] | torch.Tensor,
    route: Mapping[str, torch.Tensor],
    correct_counts: Sequence[int] | torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Build protect/rescue/noisy/outside targets for routed rows."""

    y = torch.as_tensor(y_true, dtype=torch.long).view(-1)
    counts = torch.as_tensor(correct_counts, dtype=torch.long).view(-1)
    indices = torch.as_tensor(route["indices"], dtype=torch.long)
    if len(y) != len(counts):
        raise ValueError("y_true and correct_counts have different lengths")
    if len(indices) and int(indices.max()) >= len(y):
        raise ValueError("route row is outside y_true")

    route_y = y[indices]
    main = torch.as_tensor(route["main_predictions"], dtype=torch.long)
    alt = torch.as_tensor(route["alternative_predictions"], dtype=torch.long)
    pairs = torch.as_tensor(route["pair_classes"], dtype=torch.long)
    route_counts = counts[indices]
    if not (len(route_y) == len(main) == len(alt) == len(pairs)):
        raise ValueError("route tensors have inconsistent lengths")

    kinds = torch.full((len(indices),), TARGET_OUTSIDE, dtype=torch.long)
    kinds[route_y == main] = TARGET_PROTECT
    alt_mask = route_y == alt
    kinds[alt_mask & (route_counts > 0)] = TARGET_RESCUE
    kinds[alt_mask & (route_counts == 0)] = TARGET_NOISY_ALT

    signs = torch.zeros(len(indices), dtype=torch.float32)
    signs[route_y == pairs[:, 0]] = 1.0
    signs[route_y == pairs[:, 1]] = -1.0
    return kinds, signs


def balanced_target_weights(
    target_kinds: Sequence[int] | torch.Tensor,
    pair_ids: Sequence[int] | torch.Tensor,
    top_sides: Sequence[int] | torch.Tensor,
    kind_mass: Mapping[int, float] | None = None,
) -> torch.Tensor:
    """Equalize directed-pair groups inside each active target kind.

    Total pre-normalization mass is 1 for rescue, 1 for protect, and .25 for
    outside. Noisy c=0 alternatives have exactly zero weight.
    """

    kinds = torch.as_tensor(target_kinds, dtype=torch.long).view(-1)
    pairs = torch.as_tensor(pair_ids, dtype=torch.long).view(-1)
    sides = torch.as_tensor(top_sides, dtype=torch.long).view(-1)
    if not (len(kinds) == len(pairs) == len(sides)):
        raise ValueError("target/group tensors have different lengths")
    masses = dict(kind_mass or {
        TARGET_PROTECT: 1.0,
        TARGET_RESCUE: 1.0,
        TARGET_OUTSIDE: 0.25,
    })
    weights = torch.zeros(len(kinds), dtype=torch.float32)
    for kind, total_mass in masses.items():
        members = (kinds == int(kind)).nonzero(as_tuple=False).view(-1).tolist()
        if not members or total_mass <= 0:
            continue
        groups: dict[tuple[int, int], list[int]] = defaultdict(list)
        for idx in members:
            groups[(int(pairs[idx]), int(sides[idx]))].append(idx)
        per_group_mass = float(total_mass) / len(groups)
        for group in groups.values():
            per_row = per_group_mass / len(group)
            weights[group] = per_row
    active = weights > 0
    if active.any():
        weights *= float(active.sum()) / float(weights.sum())
    return weights


def bounded_delta(raw_delta: torch.Tensor, delta_max: float = 2.0) -> torch.Tensor:
    if delta_max <= 0:
        raise ValueError("delta_max must be positive")
    return float(delta_max) * torch.tanh(torch.as_tensor(raw_delta).float())


def pair_residual_loss(
    raw_delta: torch.Tensor,
    base_gaps: torch.Tensor,
    target_signs: torch.Tensor,
    target_kinds: torch.Tensor,
    sample_weights: torch.Tensor,
    delta_max: float = 2.0,
    identity_beta: float = 0.25,
) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
    """Selective binary ranking loss plus explicit identity protection."""

    raw = raw_delta.float().view(-1)
    gap = base_gaps.float().view(-1).to(raw.device)
    signs = target_signs.float().view(-1).to(raw.device)
    kinds = target_kinds.long().view(-1).to(raw.device)
    weights = sample_weights.float().view(-1).to(raw.device)
    if not (len(raw) == len(gap) == len(signs) == len(kinds) == len(weights)):
        raise ValueError("loss inputs have different lengths")
    delta = bounded_delta(raw, delta_max=delta_max)
    pair = F.softplus(-signs * (gap + delta))
    identity = F.smooth_l1_loss(
        delta,
        torch.zeros_like(delta),
        beta=identity_beta,
        reduction="none",
    )
    values = torch.zeros_like(delta)
    rescue = kinds == TARGET_RESCUE
    protect = kinds == TARGET_PROTECT
    outside = kinds == TARGET_OUTSIDE
    values[rescue] = pair[rescue]
    values[protect] = pair[protect] + identity[protect]
    values[outside] = identity[outside]
    denom = weights.sum()
    # A batch containing only masked c=0 rows remains differentiable and no-op.
    loss = (values * weights).sum() / denom.clamp_min(1e-12)
    return loss, {"delta": delta, "pair": pair, "identity": identity, "values": values}


def apply_pair_residual(
    main_logits: torch.Tensor,
    route: Mapping[str, torch.Tensor],
    raw_delta: torch.Tensor,
    delta_max: float = 2.0,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Adjust only a routed pair and emit family-locked predictions."""

    logits = _as_float_logits(main_logits)
    indices = torch.as_tensor(route["indices"], dtype=torch.long)
    pairs = torch.as_tensor(route["pair_classes"], dtype=torch.long)
    if len(indices) != len(pairs):
        raise ValueError("route indices/pairs have different lengths")
    raw = torch.as_tensor(raw_delta, dtype=torch.float32).view(-1)
    if len(raw) != len(indices):
        raise ValueError(f"raw_delta rows {len(raw)} != routed rows {len(indices)}")
    delta = bounded_delta(raw, delta_max=delta_max).cpu()
    adjusted = logits.clone()
    pred = logits.argmax(dim=1)
    if len(indices):
        left, right = pairs[:, 0], pairs[:, 1]
        adjusted[indices, left] += delta / 2.0
        adjusted[indices, right] -= delta / 2.0
        final_gap = torch.as_tensor(route["base_gaps"], dtype=torch.float32) + delta
        pred[indices] = torch.where(final_gap >= 0, left, right)

        # The pair choice is family-locked even when a non-Weak4 runner-up is
        # above the switched alternative after the bounded gap adjustment.
        # Project that discrete contract back into logit space with a *common*
        # offset on the two pair logits. This preserves their adjusted gap and
        # leaves all twelve non-pair logits untouched. Exact delta==0 rows are
        # deliberately skipped so the zero-init path stays bit-identical.
        all_classes = set(range(logits.shape[1]))
        for local, row in enumerate(indices.tolist()):
            if float(delta[local]) == 0.0:
                continue
            pair_set = {int(pairs[local, 0]), int(pairs[local, 1])}
            nonpair = sorted(all_classes - pair_set)
            nonpair_max = adjusted[row, nonpair].max()
            selected = int(pred[row])
            selected_logit = adjusted[row, selected]
            if selected_logit <= nonpair_max:
                # A scale-aware positive margin avoids an equality after the
                # subtraction/addition round trip in float32.
                margin = torch.finfo(adjusted.dtype).eps * nonpair_max.abs().clamp_min(1.0) * 4.0
                shift = nonpair_max + margin - selected_logit
                adjusted[row, pairs[local, 0]] += shift
                adjusted[row, pairs[local, 1]] += shift

    routed_mask = torch.zeros(len(logits), dtype=torch.bool)
    routed_mask[indices] = True
    original_pred = logits.argmax(dim=1)
    if not torch.equal(pred[~routed_mask], original_pred[~routed_mask]):
        raise AssertionError("pair residual changed a non-routed prediction")
    if len(indices):
        valid = (pred[indices] == pairs[:, 0]) | (pred[indices] == pairs[:, 1])
        if not bool(valid.all()):
            raise AssertionError("pair residual escaped its routed pair")
    if not torch.equal(adjusted.argmax(dim=1), pred):
        raise AssertionError("family-locked predictions disagree with adjusted-logit argmax")
    return adjusted, pred, delta


def _inline(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", safe_text(value)).strip()
    return text[:limit]


def _result_bucket(result: object) -> str:
    lower = safe_text(result).lower()
    if not lower:
        return "na"
    if re.search(r"\b(error|failed|failure|traceback|exception|timeout|conflict)\b", lower):
        return "error"
    if re.search(r"\b(no matches?|not found|0 matches?|0 files?|empty|no results?)\b", lower):
        return "zero"
    match = re.search(r"\b(\d+)\s+(?:matches?|files?|entries|results?)\b", lower)
    if match:
        count = int(match.group(1))
        return "zero" if count == 0 else "one" if count == 1 else "few" if count <= 5 else "many"
    if re.search(r"\b(pass(?:ed)?|success(?:ful)?|succeeded|ok|clean|read)\b", lower):
        return "ok"
    return "na"


def serialize_weak_policy_pair_v1(sample: Mapping, pair_id: int, main_pred: int) -> str:
    """Policy-oriented text without hand-authored semantic intent tags."""

    pair_id = int(pair_id)
    if pair_id < 0 or pair_id >= len(LIVE_WEAK4_PAIRS):
        raise ValueError(f"invalid pair_id: {pair_id}")
    left, right = LIVE_WEAK4_PAIRS[pair_id]
    if int(main_pred) not in (left, right):
        raise ValueError(f"main prediction {main_pred} is outside pair {(left, right)}")
    history = sample.get("history") or []
    current_user = ""
    events: list[tuple[str, Mapping]] = []
    action_names: list[str] = []
    for event in history:
        if event.get("role") == "user":
            current_user = safe_text(event.get("content"))
        elif event.get("role") == "assistant_action":
            events.append((current_user, event))
            action_names.append(safe_text(event.get("name")))
    repeat = 0
    if action_names:
        for name in reversed(action_names):
            if name != action_names[-1]:
                break
            repeat += 1
    sm = sample.get("session_meta") or {}
    workspace = sm.get("workspace") or {}
    parts = [
        f"pair: {ALL_CLASSES[left]} <> {ALL_CLASSES[right]} main={ALL_CLASSES[int(main_pred)]}",
        f"current: {safe_text(sample.get('current_prompt'))}",
        f"state: turn={safe_text(sm.get('turn_index'))} actions={len(action_names)} repeat={repeat}",
    ]
    for offset, (user_text, event) in enumerate(reversed(events[-3:]), 1):
        args = event.get("args") or {}
        try:
            args_text = json.dumps(
                args,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError):
            args_text = safe_text(args)
        result = safe_text(event.get("result_summary"))
        parts.append(
            f"event_-{offset}: user={_inline(user_text, 240)} | "
            f"action={safe_text(event.get('name'))} | args={_inline(args_text, 320)} | "
            f"result_bucket={_result_bucket(result)} | result={_inline(result, 240)}"
        )
    open_files = workspace.get("open_files") or []
    parts.append(
        f"workspace: dirty={safe_text(workspace.get('git_dirty'))} "
        f"ci={safe_text(workspace.get('last_ci_status'))} "
        f"open={' | '.join(_inline(value, 100) for value in open_files[:6])}"
    )
    return "\n".join(parts)


def serialize_pair_sample(
    sample: Mapping,
    pair_id: int,
    main_pred: int,
    serializer_name: str,
) -> str:
    if serializer_name == "weak_policy_pair_v1":
        return serialize_weak_policy_pair_v1(sample, pair_id, main_pred)
    if serializer_name == "current_v1":
        return serialize_transformer_sample(sample, "current_v1")
    raise ValueError(f"unknown pair serializer: {serializer_name}")


def assign_session_folds(
    ids: Sequence[str],
    strata: Sequence[int] | torch.Tensor,
    n_folds: int = 3,
    seed: int = 42,
) -> torch.Tensor:
    """Greedily balance strata while keeping every session in one fold."""

    if n_folds < 2:
        raise ValueError("n_folds must be at least 2")
    strata_list = [int(value) for value in torch.as_tensor(strata).view(-1).tolist()]
    if len(ids) != len(strata_list):
        raise ValueError("ids and strata have different lengths")
    groups: dict[str, list[int]] = defaultdict(list)
    for idx, sample_id in enumerate(ids):
        groups[safe_text(sample_id).split("-step_")[0]].append(idx)
    rng = random.Random(seed)
    group_ids = list(groups)
    rng.shuffle(group_ids)
    group_ids.sort(key=lambda group_id: len(groups[group_id]), reverse=True)
    total_counts = Counter(strata_list)
    target_counts = {key: value / n_folds for key, value in total_counts.items()}
    target_size = len(ids) / n_folds
    fold_counts = [Counter() for _ in range(n_folds)]
    fold_sizes = [0] * n_folds
    assignments: dict[str, int] = {}
    for group_id in group_ids:
        rows = groups[group_id]
        group_counts = Counter(strata_list[idx] for idx in rows)
        best_fold, best_score = None, None
        for fold in range(n_folds):
            size_before = fold_sizes[fold]
            size_after = size_before + len(rows)
            score = 0.2 * (
                ((size_after - target_size) ** 2)
                - ((size_before - target_size) ** 2)
            ) / max(1.0, target_size)
            for key, target in target_counts.items():
                before = fold_counts[fold][key]
                after = fold_counts[fold][key] + group_counts[key]
                score += (
                    ((after - target) ** 2) - ((before - target) ** 2)
                ) / max(1.0, target)
            if best_score is None or score < best_score:
                best_fold, best_score = fold, score
        assignments[group_id] = int(best_fold)
        fold_sizes[best_fold] += len(rows)
        fold_counts[best_fold].update(group_counts)
    return torch.tensor(
        [assignments[safe_text(sample_id).split("-step_")[0]] for sample_id in ids],
        dtype=torch.long,
    )


class Weak4PairResidualModel(nn.Module):
    """Pretrained multilingual text encoder plus numeric/pair residual head."""

    def __init__(
        self,
        encoder: nn.Module,
        numeric_dim: int = NUMERIC_FEATURE_DIM,
        fusion_dim: int = 256,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.encoder = encoder
        hidden_size = int(getattr(encoder.config, "hidden_size"))
        self.numeric_mlp = nn.Sequential(
            nn.Linear(numeric_dim, 64),
            nn.GELU(),
            nn.LayerNorm(64),
        )
        self.pair_embedding = nn.Embedding(len(LIVE_WEAK4_PAIRS), 16)
        self.top_side_embedding = nn.Embedding(2, 8)
        self.fusion = nn.Sequential(
            nn.Linear(hidden_size + 64 + 16 + 8, fusion_dim),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.output = nn.Linear(fusion_dim, 1)
        nn.init.zeros_(self.output.weight)
        nn.init.zeros_(self.output.bias)

    def forward(
        self,
        *,
        numeric_features: torch.Tensor,
        pair_ids: torch.Tensor,
        top_sides: torch.Tensor,
        **encoder_inputs: torch.Tensor,
    ) -> torch.Tensor:
        outputs = self.encoder(**encoder_inputs, return_dict=True)
        hidden = outputs.last_hidden_state[:, 0]
        numeric = self.numeric_mlp(numeric_features.float())
        fused = torch.cat(
            [
                hidden,
                numeric,
                self.pair_embedding(pair_ids.long()),
                self.top_side_embedding(top_sides.long()),
            ],
            dim=1,
        )
        return self.output(self.fusion(fused)).squeeze(1)


def target_histogram(kinds: Iterable[int]) -> dict[str, int]:
    counts = Counter(int(value) for value in kinds)
    return {name: counts.get(idx, 0) for idx, name in enumerate(TARGET_NAMES)}
