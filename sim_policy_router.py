#!/usr/bin/env python3
"""Leakage-aware trajectory candidates and a selective SIM action router.

The module deliberately keeps the policy model small.  A set of count tables
proposes alternative Weak4 actions from the observed action/event suffix.  A
router then decides between the main prediction and each proposed alternative.
The router never sees an absolute main-logit scale; all main-model features are
computed within each row.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np


CLASSES = [
    "read_file",
    "grep_search",
    "list_directory",
    "glob_pattern",
    "edit_file",
    "write_file",
    "apply_patch",
    "run_bash",
    "run_tests",
    "lint_or_typecheck",
    "ask_user",
    "plan_task",
    "web_search",
    "respond_only",
]
C2I = {label: index for index, label in enumerate(CLASSES)}
WEAK4 = tuple(range(4))
START = "<START>"


def turn_bucket(turn: int) -> str:
    if turn <= 1:
        return "1"
    if turn <= 3:
        return "2-3"
    if turn <= 6:
        return "4-6"
    if turn <= 10:
        return "7-10"
    return "11+"


def support_bucket(support: int) -> str:
    if support < 3:
        return "0-2"
    if support < 5:
        return "3-4"
    if support < 10:
        return "5-9"
    if support < 20:
        return "10-19"
    if support < 50:
        return "20-49"
    if support < 100:
        return "50-99"
    return "100+"


def result_bucket(value: object) -> str:
    text = str(value or "").lower()
    if re.search(r"error|fail|traceback|exception|denied|timeout|conflict", text):
        return "error"
    if re.search(r"no matches?|not found|\b0 (matches?|files?|entries|results?)\b|empty", text):
        return "zero"
    if re.search(r"pass|success|succeeded|\bok\b|clean|patched|found|listed", text):
        return "ok"
    return "other"


def _value_kind(key: str, value: object) -> str:
    text = str(value or "").lower().strip()
    if key in {"path", "scope"}:
        leaf = text.rsplit("/", 1)[-1]
        extension = leaf.rsplit(".", 1)[-1] if "." in leaf else "none"
        return f"{'nested' if '/' in text or '\\' in text else 'base'}:{extension[:8]}"
    if key == "pattern":
        if "*" in text or "?" in text:
            return "glob"
        if re.fullmatch(r"[a-z_][a-z0-9_]*", text):
            return "symbol"
        return "text"
    if key == "cmd":
        if re.search(r"\b(test|pytest|jest|vitest|go test|cargo test)\b", text):
            return "test"
        if re.search(r"\b(lint|eslint|mypy|ruff|tsc)\b", text):
            return "lint"
        if re.search(r"\b(build|compile|package|docker)\b", text):
            return "build"
        return "other"
    return "text"


def arg_schema(args: object) -> str:
    if not isinstance(args, Mapping) or not args:
        return "none"
    return ",".join(
        f"{key}:{_value_kind(str(key), value)}"
        for key, value in sorted(args.items())
    )


@dataclass(frozen=True)
class SimRow:
    sample_id: str
    session: str
    y: int
    turn: int
    actions: tuple[str, ...]
    events: tuple[str, ...]
    last_result: str = "other"
    git_dirty: str = "na"
    ci_status: str = "na"
    open_files: int = 0

    @property
    def turn_bucket(self) -> str:
        return turn_bucket(self.turn)


@dataclass(frozen=True)
class ExpertConfig:
    name: str
    sequence: str
    context: str
    max_depth: int
    min_support: int
    prior_strength: float = 10.0


DEFAULT_EXPERTS = (
    ExpertConfig("action_tb_k2", "action", "turn_bucket", 2, 3),
    ExpertConfig("action_tb_k4", "action", "turn_bucket", 4, 5),
    ExpertConfig("action_turn_k2", "action", "turn", 2, 3),
    ExpertConfig("action_global_k4", "action", "global", 4, 5),
    ExpertConfig("event_tb_k2", "event", "turn_bucket", 2, 3),
)


class SimPolicyTables:
    """Weak4 conditional suffix tables fitted on SIM rows only."""

    def __init__(
        self,
        rows: Sequence[SimRow],
        train_indices: Iterable[int],
        experts: Sequence[ExpertConfig] = DEFAULT_EXPERTS,
    ) -> None:
        self.rows = rows
        self.experts = tuple(experts)
        self.global_counts = np.zeros(4, dtype=np.int64)
        self.base_counts: dict[str, np.ndarray] = defaultdict(
            lambda: np.zeros(4, dtype=np.int64)
        )
        self.tables: dict[str, list[dict[tuple[object, tuple[str, ...]], np.ndarray]]] = {
            config.name: [defaultdict(lambda: np.zeros(4, dtype=np.int64))
                          for _ in range(config.max_depth + 1)]
            for config in self.experts
        }
        for index in train_indices:
            row = rows[int(index)]
            if row.y not in WEAK4:
                continue
            self.global_counts[row.y] += 1
            self.base_counts[row.turn_bucket][row.y] += 1
            for config in self.experts:
                sequence = self._sequence(row, config)
                context = self._context(row, config)
                for depth in range(1, min(config.max_depth, len(sequence)) + 1):
                    key = (context, tuple(sequence[-depth:]))
                    self.tables[config.name][depth][key][row.y] += 1

    @staticmethod
    def _sequence(row: SimRow, config: ExpertConfig) -> tuple[str, ...]:
        sequence = row.actions if config.sequence == "action" else row.events
        return sequence or (START,)

    @staticmethod
    def _context(row: SimRow, config: ExpertConfig) -> object:
        if config.context == "turn_bucket":
            return row.turn_bucket
        if config.context == "turn":
            return row.turn
        if config.context == "global":
            return "*"
        raise ValueError(f"unknown expert context: {config.context}")

    def _base_probability(self, row: SimRow) -> np.ndarray:
        counts = self.base_counts.get(row.turn_bucket)
        if counts is None or not counts.sum():
            counts = self.global_counts
        return (counts.astype(np.float64) + 1.0) / (float(counts.sum()) + 4.0)

    def query_one(self, row: SimRow, config: ExpertConfig) -> dict[str, object]:
        sequence = self._sequence(row, config)
        context = self._context(row, config)
        counts = None
        depth = 0
        for candidate_depth in range(min(config.max_depth, len(sequence)), 0, -1):
            key = (context, tuple(sequence[-candidate_depth:]))
            candidate = self.tables[config.name][candidate_depth].get(key)
            if candidate is not None and int(candidate.sum()) >= config.min_support:
                counts = candidate
                depth = candidate_depth
                break
        base = self._base_probability(row)
        if counts is None:
            probability = base
            support = 0
        else:
            support = int(counts.sum())
            probability = (
                counts.astype(np.float64) + config.prior_strength * base
            ) / (support + config.prior_strength)
        entropy = float(
            -(probability * np.log(np.clip(probability, 1e-12, 1.0))).sum()
            / math.log(4.0)
        )
        order = np.argsort(-probability)
        return {
            "pred": int(order[0]),
            "prob": probability,
            "support": support,
            "depth": depth,
            "entropy": entropy,
            "gap": float(probability[order[0]] - probability[order[1]]),
        }

    def query(self, indices: Sequence[int]) -> dict[str, dict[str, np.ndarray]]:
        output: dict[str, dict[str, np.ndarray]] = {}
        for config in self.experts:
            values = [self.query_one(self.rows[int(index)], config) for index in indices]
            output[config.name] = {
                "pred": np.asarray([value["pred"] for value in values], dtype=np.int64),
                "prob": np.stack([value["prob"] for value in values]),
                "support": np.asarray([value["support"] for value in values], dtype=np.int64),
                "depth": np.asarray([value["depth"] for value in values], dtype=np.int64),
                "entropy": np.asarray([value["entropy"] for value in values], dtype=np.float64),
                "gap": np.asarray([value["gap"] for value in values], dtype=np.float64),
            }
        return output


def row_normalized_main_features(logits: np.ndarray) -> dict[str, np.ndarray]:
    logits = np.asarray(logits, dtype=np.float64)
    centered = logits - logits.mean(axis=1, keepdims=True)
    z = centered / np.maximum(logits.std(axis=1, keepdims=True), 1e-8)
    exp = np.exp(z - z.max(axis=1, keepdims=True))
    probability = exp / exp.sum(axis=1, keepdims=True)
    weak_exp = np.exp(z[:, :4] - z[:, :4].max(axis=1, keepdims=True))
    weak_probability = weak_exp / weak_exp.sum(axis=1, keepdims=True)
    order = np.argsort(-z, axis=1)
    rank = np.empty_like(order)
    rank[np.arange(len(z))[:, None], order] = np.arange(z.shape[1])[None, :]
    entropy = -(
        probability * np.log(np.clip(probability, 1e-12, 1.0))
    ).sum(axis=1) / math.log(logits.shape[1])
    return {
        "z": z,
        "prob": probability,
        "weak_prob": weak_probability,
        "order": order,
        "rank": rank,
        "entropy": entropy,
    }


def _suffix(row: SimRow, length: int) -> str:
    padded = (START,) * length + row.actions
    return ">".join(padded[-length:])


def candidate_examples(
    rows: Sequence[SimRow],
    row_indices: Sequence[int],
    logits: np.ndarray,
    table_outputs: Mapping[str, Mapping[str, np.ndarray]],
    experts: Sequence[ExpertConfig] = DEFAULT_EXPERTS,
    feature_family: str = "full",
) -> dict[str, object]:
    """Build one example per unique table-proposed alternative action."""

    row_indices = np.asarray(row_indices, dtype=np.int64)
    main_features = row_normalized_main_features(np.asarray(logits)[row_indices])
    main = np.asarray(logits)[row_indices].argmax(axis=1)
    example_rows: list[int] = []
    alternatives: list[int] = []
    targets: list[int] = []
    features: list[dict[str, float]] = []
    for local_index, absolute_index in enumerate(row_indices.tolist()):
        main_label = int(main[local_index])
        if main_label not in WEAK4:
            continue
        proposals = sorted({
            int(table_outputs[config.name]["pred"][local_index])
            for config in experts
            if int(table_outputs[config.name]["depth"][local_index]) > 0
            and int(table_outputs[config.name]["pred"][local_index]) != main_label
        })
        if not proposals:
            continue
        row = rows[absolute_index]
        order = main_features["order"][local_index]
        z = main_features["z"][local_index]
        probability = main_features["prob"][local_index]
        weak_probability = main_features["weak_prob"][local_index]
        for alternative in proposals:
            voters = [
                config for config in experts
                if int(table_outputs[config.name]["depth"][local_index]) > 0
                and int(table_outputs[config.name]["pred"][local_index]) == alternative
            ]
            matched = [
                config for config in experts
                if int(table_outputs[config.name]["depth"][local_index]) > 0
            ]
            alt_posts = [
                float(table_outputs[config.name]["prob"][local_index, alternative])
                for config in matched
            ]
            main_posts = [
                float(table_outputs[config.name]["prob"][local_index, main_label])
                for config in matched
            ]
            supports = [
                int(table_outputs[config.name]["support"][local_index])
                for config in voters
            ]
            depths = [
                int(table_outputs[config.name]["depth"][local_index])
                for config in voters
            ]
            entropies = [
                float(table_outputs[config.name]["entropy"][local_index])
                for config in voters
            ]
            feature: dict[str, float] = {
                f"pair={CLASSES[main_label]}>{CLASSES[alternative]}": 1.0,
                f"main={CLASSES[main_label]}": 1.0,
                f"alternative={CLASSES[alternative]}": 1.0,
                f"turn={row.turn_bucket}": 1.0,
                f"last={row.actions[-1] if row.actions else START}": 1.0,
                f"lag2={row.actions[-2] if len(row.actions) >= 2 else START}": 1.0,
                f"suffix2={_suffix(row, 2)}": 1.0,
                f"result={row.last_result}": 1.0,
                f"dirty={row.git_dirty}": 1.0,
                f"ci={row.ci_status}": 1.0,
                f"support={support_bucket(max(supports))}": 1.0,
                f"top2={CLASSES[int(order[1])]}": 1.0,
                "turn_num": min(row.turn, 15) / 15.0,
                "history_len": min(len(row.actions), 15) / 15.0,
                "open_files": min(row.open_files, 5) / 5.0,
                "main_entropy": float(main_features["entropy"][local_index]),
                "main_probability": float(probability[main_label]),
                "main_gap": float(z[main_label] - z[int(order[1])]),
                "weak4_mass": float(probability[:4].sum()),
                "weak_main_probability": float(weak_probability[main_label]),
                "weak_alt_probability": float(weak_probability[alternative]),
                "main_alt_gap": float(z[main_label] - z[alternative]),
                "alternative_rank": float(main_features["rank"][local_index, alternative]) / 13.0,
                "vote_fraction": len(voters) / max(1, len(matched)),
                "matched_experts": len(matched) / max(1, len(experts)),
                "table_alt_mean": float(np.mean(alt_posts)),
                "table_alt_max": float(np.max(alt_posts)),
                "table_main_mean": float(np.mean(main_posts)),
                "table_alt_main_gap": float(np.mean(np.asarray(alt_posts) - np.asarray(main_posts))),
                "table_support_log": math.log1p(max(supports)) / 8.0,
                "table_depth_max": max(depths) / 4.0,
                "table_entropy_min": min(entropies),
            }
            for config in experts:
                values = table_outputs[config.name]
                depth = int(values["depth"][local_index])
                voted = depth > 0 and int(values["pred"][local_index]) == alternative
                feature[f"{config.name}:vote"] = float(voted)
                feature[f"{config.name}:depth"] = depth / max(1, config.max_depth)
                feature[f"{config.name}:alt_post"] = float(values["prob"][local_index, alternative])
            if feature_family == "pair_policy":
                allowed_numeric = {
                    "vote_fraction", "matched_experts", "table_alt_mean", "table_alt_max",
                    "table_main_mean", "table_alt_main_gap", "table_support_log",
                    "table_depth_max", "table_entropy_min",
                }
                feature = {
                    key: value for key, value in feature.items()
                    if key.startswith(("pair=", "turn=", "last=", "support="))
                    or key in allowed_numeric or ":vote" in key or ":depth" in key
                }
            elif feature_family == "pair_main":
                allowed_numeric = {
                    "main_entropy", "main_probability", "main_gap", "weak4_mass",
                    "weak_main_probability", "weak_alt_probability", "main_alt_gap",
                    "alternative_rank", "vote_fraction", "matched_experts",
                    "table_alt_mean", "table_alt_max", "table_main_mean",
                    "table_alt_main_gap", "table_support_log", "table_depth_max",
                    "table_entropy_min",
                }
                feature = {
                    key: value for key, value in feature.items()
                    if key.startswith(("pair=", "turn=", "last=", "support="))
                    or key in allowed_numeric or ":vote" in key or ":depth" in key
                }
            elif feature_family != "full":
                raise ValueError(f"unknown feature family: {feature_family}")
            if row.y == alternative:
                target = 1  # rescue
            elif row.y == main_label:
                target = 2  # harm
            else:
                target = 0  # both predictions are wrong
            example_rows.append(local_index)
            alternatives.append(alternative)
            targets.append(target)
            features.append(feature)
    return {
        "features": features,
        "row_local": np.asarray(example_rows, dtype=np.int64),
        "alternative": np.asarray(alternatives, dtype=np.int64),
        "target": np.asarray(targets, dtype=np.int64),
        "main": main,
    }


def policy_row_examples(
    rows: Sequence[SimRow],
    row_indices: Sequence[int],
    logits: np.ndarray,
    table_outputs: Mapping[str, Mapping[str, np.ndarray]],
    experts: Sequence[ExpertConfig] = DEFAULT_EXPERTS,
    feature_family: str = "pair_main",
) -> dict[str, object]:
    """Build one five-way label-posterior example per main-Weak4 row.

    Label ``4`` collapses every non-Weak4 truth.  For a switch between two
    Weak4 actions this is sufficient: any truth other than main or alternative
    has the same one-row confusion-matrix effect.
    """

    row_indices = np.asarray(row_indices, dtype=np.int64)
    logits_subset = np.asarray(logits)[row_indices]
    main_features = row_normalized_main_features(logits_subset)
    main = logits_subset.argmax(axis=1)
    route_local: list[int] = []
    targets: list[int] = []
    features: list[dict[str, float]] = []
    candidate_rows: list[int] = []
    candidate_alternatives: list[int] = []
    main_prior: list[np.ndarray] = []
    for local_index, absolute_index in enumerate(row_indices.tolist()):
        main_label = int(main[local_index])
        if main_label not in WEAK4:
            continue
        row = rows[absolute_index]
        order = main_features["order"][local_index]
        z = main_features["z"][local_index]
        probability = main_features["prob"][local_index]
        weak_probability = main_features["weak_prob"][local_index]
        feature: dict[str, float] = {
            f"main={CLASSES[main_label]}": 1.0,
            f"turn={row.turn_bucket}": 1.0,
            f"last={row.actions[-1] if row.actions else START}": 1.0,
            f"lag2={row.actions[-2] if len(row.actions) >= 2 else START}": 1.0,
            f"suffix2={_suffix(row, 2)}": 1.0,
            f"result={row.last_result}": 1.0,
            f"dirty={row.git_dirty}": 1.0,
            f"ci={row.ci_status}": 1.0,
            f"top2={CLASSES[int(order[1])]}": 1.0,
            "turn_num": min(row.turn, 15) / 15.0,
            "history_len": min(len(row.actions), 15) / 15.0,
            "open_files": min(row.open_files, 5) / 5.0,
            "main_entropy": float(main_features["entropy"][local_index]),
            "main_gap": float(z[main_label] - z[int(order[1])]),
            "weak4_mass": float(probability[:4].sum()),
        }
        vote_counts = np.zeros(4, dtype=np.float64)
        matched = 0
        for config in experts:
            values = table_outputs[config.name]
            depth = int(values["depth"][local_index])
            support = int(values["support"][local_index])
            prediction = int(values["pred"][local_index])
            if depth > 0:
                matched += 1
                vote_counts[prediction] += 1
            feature[f"{config.name}:pred={CLASSES[prediction]}"] = 1.0
            feature[f"{config.name}:depth"] = depth / max(1, config.max_depth)
            feature[f"{config.name}:support_log"] = math.log1p(support) / 8.0
            feature[f"{config.name}:entropy"] = float(values["entropy"][local_index])
            for label in WEAK4:
                feature[f"{config.name}:p{label}"] = float(values["prob"][local_index, label])
        for label in WEAK4:
            feature[f"main_w4_p{label}"] = float(weak_probability[label])
            feature[f"vote_p{label}"] = vote_counts[label] / max(1, matched)
        feature["matched_experts"] = matched / max(1, len(experts))
        if feature_family == "pair_policy":
            feature = {
                key: value for key, value in feature.items()
                if key.startswith(("main=", "turn=", "last="))
                or key.startswith("vote_p") or key == "matched_experts"
                or ":pred=" in key or ":depth" in key or ":support_log" in key
                or ":entropy" in key or re.search(r":p[0-3]$", key)
            }
        elif feature_family == "pair_main":
            feature = {
                key: value for key, value in feature.items()
                if key.startswith(("main=", "turn=", "last="))
                or key in {"main_entropy", "main_gap", "weak4_mass", "matched_experts"}
                or key.startswith(("main_w4_p", "vote_p"))
                or ":pred=" in key or ":depth" in key or ":support_log" in key
                or ":entropy" in key or re.search(r":p[0-3]$", key)
            }
        elif feature_family == "pair_main_nocount":
            feature = {
                key: value for key, value in feature.items()
                if key.startswith(("main=", "turn=", "last="))
                or key in {"main_entropy", "main_gap", "weak4_mass", "matched_experts"}
                or key.startswith(("main_w4_p", "vote_p"))
                or ":pred=" in key or ":entropy" in key
                or re.search(r":p[0-3]$", key)
            }
        elif feature_family == "main_context":
            feature = {
                key: value for key, value in feature.items()
                if key.startswith((
                    "main=", "turn=", "last=", "lag2=", "suffix2=", "result=",
                    "dirty=", "ci=", "top2=", "main_w4_p",
                ))
                or key in {
                    "turn_num", "history_len", "open_files", "main_entropy",
                    "main_gap", "weak4_mass",
                }
            }
        elif feature_family != "full":
            raise ValueError(f"unknown feature family: {feature_family}")
        feature_index = len(features)
        proposals = sorted({
            int(table_outputs[config.name]["pred"][local_index])
            for config in experts
            if int(table_outputs[config.name]["depth"][local_index]) > 0
            and int(table_outputs[config.name]["pred"][local_index]) != main_label
        })
        for alternative in proposals:
            candidate_rows.append(local_index)
            candidate_alternatives.append(alternative)
        route_local.append(local_index)
        targets.append(row.y if row.y in WEAK4 else 4)
        features.append(feature)
        prior = np.empty(5, dtype=np.float64)
        prior[:4] = probability[:4]
        prior[4] = probability[4:].sum()
        prior /= prior.sum()
        main_prior.append(prior)
    return {
        "features": features,
        "route_local": np.asarray(route_local, dtype=np.int64),
        "target": np.asarray(targets, dtype=np.int64),
        "main": main,
        "main_prior": np.stack(main_prior) if main_prior else np.empty((0, 5)),
        "candidate_row_local": np.asarray(candidate_rows, dtype=np.int64),
        "candidate_alternative": np.asarray(candidate_alternatives, dtype=np.int64),
    }


def label_probabilities_to_candidate_states(
    row_examples: Mapping[str, object],
    label_probability: np.ndarray,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Project five-way row posteriors onto neutral/rescue/harm per candidate."""

    route_local = np.asarray(row_examples["route_local"], dtype=np.int64)
    route_position = {int(row): index for index, row in enumerate(route_local)}
    candidate_rows = np.asarray(row_examples["candidate_row_local"], dtype=np.int64)
    alternatives = np.asarray(row_examples["candidate_alternative"], dtype=np.int64)
    main = np.asarray(row_examples["main"], dtype=np.int64)
    state = np.empty((len(candidate_rows), 3), dtype=np.float64)
    for index, (row, alternative) in enumerate(zip(candidate_rows, alternatives)):
        probability = label_probability[route_position[int(row)]]
        rescue = float(probability[int(alternative)])
        harm = float(probability[int(main[int(row)])])
        state[index] = (max(0.0, 1.0 - rescue - harm), rescue, harm)
    return {
        "row_local": candidate_rows,
        "alternative": alternatives,
    }, state


def macro_f1(y_true: np.ndarray, prediction: np.ndarray, n_classes: int = 14) -> float:
    y_true = np.asarray(y_true, dtype=np.int64)
    prediction = np.asarray(prediction, dtype=np.int64)
    scores = []
    for label in range(n_classes):
        tp = int(np.sum((y_true == label) & (prediction == label)))
        fp = int(np.sum((y_true != label) & (prediction == label)))
        fn = int(np.sum((y_true == label) & (prediction != label)))
        denominator = 2 * tp + fp + fn
        scores.append(0.0 if denominator == 0 else 2 * tp / denominator)
    return float(np.mean(scores))


def state_utility_table(
    y_true: np.ndarray,
    main_prediction: np.ndarray,
    n_classes: int = 14,
) -> np.ndarray:
    """Exact one-row Macro-F1 changes under the baseline confusion counts.

    Axis 2 is ``neutral, rescue, harm``.  Values are multiplied by the number
    of rows so thresholds remain numerically convenient across folds.
    """

    y_true = np.asarray(y_true, dtype=np.int64)
    main_prediction = np.asarray(main_prediction, dtype=np.int64)
    tp = np.asarray([
        np.sum((y_true == label) & (main_prediction == label))
        for label in range(n_classes)
    ], dtype=np.int64)
    fp = np.asarray([
        np.sum((y_true != label) & (main_prediction == label))
        for label in range(n_classes)
    ], dtype=np.int64)
    fn = np.asarray([
        np.sum((y_true == label) & (main_prediction != label))
        for label in range(n_classes)
    ], dtype=np.int64)

    def score(tp_value: np.ndarray, fp_value: np.ndarray, fn_value: np.ndarray) -> float:
        denominator = 2 * tp_value + fp_value + fn_value
        values = np.divide(
            2 * tp_value,
            denominator,
            out=np.zeros_like(denominator, dtype=np.float64),
            where=denominator != 0,
        )
        return float(values.mean())

    baseline = score(tp, fp, fn)
    utility = np.zeros((n_classes, n_classes, 3), dtype=np.float64)
    for main_label in range(n_classes):
        for alternative in range(n_classes):
            if main_label == alternative:
                continue
            # Neutral: true label is neither prediction; only the false positive moves.
            ntp, nfp, nfn = tp.copy(), fp.copy(), fn.copy()
            nfp[main_label] -= 1
            nfp[alternative] += 1
            utility[main_label, alternative, 0] = score(ntp, nfp, nfn) - baseline

            # Rescue: y=alternative, main was a false positive and alternative a FN.
            rtp, rfp, rfn = tp.copy(), fp.copy(), fn.copy()
            rfp[main_label] -= 1
            rtp[alternative] += 1
            rfn[alternative] -= 1
            utility[main_label, alternative, 1] = score(rtp, rfp, rfn) - baseline

            # Harm: y=main, main was correct and alternative becomes a false positive.
            htp, hfp, hfn = tp.copy(), fp.copy(), fn.copy()
            htp[main_label] -= 1
            hfn[main_label] += 1
            hfp[alternative] += 1
            utility[main_label, alternative, 2] = score(htp, hfp, hfn) - baseline
    return utility * len(y_true)


def aligned_probabilities(model, matrix) -> np.ndarray:
    raw = model.predict_proba(matrix)
    output = np.zeros((raw.shape[0], 3), dtype=np.float64)
    for column, label in enumerate(model.classes_):
        output[:, int(label)] = raw[:, column]
    return output


def routed_predictions(
    main_prediction: np.ndarray,
    examples: Mapping[str, object],
    state_probability: np.ndarray,
    utility: np.ndarray,
    threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Choose at most one positive-utility alternative for each row."""

    main_prediction = np.asarray(main_prediction, dtype=np.int64)
    row_local = np.asarray(examples["row_local"], dtype=np.int64)
    alternative = np.asarray(examples["alternative"], dtype=np.int64)
    scores = np.asarray([
        float(state_probability[index] @ utility[main_prediction[row], alt])
        for index, (row, alt) in enumerate(zip(row_local, alternative))
    ])
    best: dict[int, tuple[float, int, int]] = {}
    for example_index, (row, alt, score) in enumerate(zip(row_local, alternative, scores)):
        previous = best.get(int(row))
        candidate = (float(score), -int(alt), int(example_index))
        if previous is None or candidate > previous:
            best[int(row)] = candidate
    prediction = main_prediction.copy()
    selected_examples = []
    for row, (score, _negative_alt, example_index) in best.items():
        if score >= threshold:
            prediction[row] = alternative[example_index]
            selected_examples.append(example_index)
    return prediction, np.asarray(selected_examples, dtype=np.int64), scores
