#!/usr/bin/env python3
"""Prevalidate a leakage-safe hidden-state kNN residual on the fixed KD screen.

The datastore is restricted to the cache's fixed training indices and true
Weak4 labels.  Queries are restricted to the fixed validation indices.  The
only JSONL features used for retrieval blocks are source, turn bucket, and
past assistant-action suffixes.  In particular, privileged future-event and
candidate-mode fields in the cache are deliberately never read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F


WEAK4_IDS = np.arange(4, dtype=np.int64)
TURN_BIN_EDGES = (1, 2, 4, 6)
TURN_BIN_NAMES = ("start", "early", "mid", "late", "long")
DEFAULT_K = (3, 5, 9, 15, 31)
DEFAULT_WEIGHTS = (0.05, 0.10, 0.20, 0.40, 0.80, 1.20)
DEFAULT_PURITIES = (0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0)
A5_PURITY_GRID = (0.5, 0.6, 0.7, 0.8)
A5_PAIR_ORDER = (
    # User-prioritized explorer confusions first.
    (3, 2),  # glob -> list
    (2, 3),  # list -> glob
    (2, 0),  # list -> read
    (0, 2),  # read -> list
    (2, 1),  # list -> grep
    (1, 2),  # grep -> list
    (0, 1),  # read -> grep
    (1, 0),  # grep -> read
    # Complete the small directed Weak4 graph so a real utility-bearing glob
    # correction is not silently discarded merely because it was less common
    # in the motivating examples.
    (3, 0),  # glob -> read
    (0, 3),  # read -> glob
    (3, 1),  # glob -> grep
    (1, 3),  # grep -> glob
)

# Keep this list explicit: it is also emitted in the artifact as an audit trail.
CACHE_FIELDS_READ = (
    "ids",
    "y_true",
    "hidden",
    "parent_logits",
    "train_indices",
    "val_indices",
    "classes",
)
FORBIDDEN_CACHE_FIELDS = (
    "candidate_mode_labels",
    "event_recovery_statuses",
    "event_recovered",
    "reconstruction",
)


def torch_load(path: Path):
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def session_id(sample_id: str) -> str:
    return sample_id.rsplit("-step_", 1)[0]


def source_id(sample_id: str) -> str:
    parts = session_id(sample_id).split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else parts[0]


def scenario_group(sample_id: str) -> str:
    """Group AU sibling variants by their shared primary scenario ID."""
    session = session_id(sample_id)
    parts = session.split("_")
    if len(parts) >= 4 and parts[:2] == ["sess", "au"]:
        return "_".join(parts[:3])
    return session


def scenario_fold(sample_id: str, folds: int, seed: int) -> int:
    value = f"{seed}:{scenario_group(sample_id)}".encode("utf-8")
    digest = hashlib.sha256(value).digest()
    return int.from_bytes(digest[:8], "big") % folds


def turn_bucket(value) -> str:
    try:
        turn = int(float(value))
    except (TypeError, ValueError):
        return "na"
    return TURN_BIN_NAMES[sum(turn > edge for edge in TURN_BIN_EDGES)]


def row_features(sample: dict) -> dict:
    sample_id = str(sample.get("id", ""))
    actions = tuple(
        str(event.get("name"))
        for event in (sample.get("history") or [])
        if event.get("role") == "assistant_action" and event.get("name")
    )
    meta = sample.get("session_meta") or {}
    return {
        "id": sample_id,
        "source": source_id(sample_id),
        "scenario": scenario_group(sample_id),
        "turn_bucket": turn_bucket(meta.get("turn_index")),
        "actions": actions,
    }


def load_feature_rows(path: Path, expected_ids: list[str]) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            sample = json.loads(line)
            rows.append(row_features(sample))
            if len(rows) > len(expected_ids):
                raise ValueError(f"JSONL has more rows than cache at line {line_number}")
            if rows[-1]["id"] != expected_ids[len(rows) - 1]:
                raise ValueError(
                    f"cache/JSONL ID mismatch at row {len(rows) - 1}: "
                    f"{rows[-1]['id']} != {expected_ids[len(rows) - 1]}"
                )
    if len(rows) != len(expected_ids):
        raise ValueError(f"JSONL/cache row mismatch: {len(rows)} != {len(expected_ids)}")
    return rows


def block_key(row: dict, suffix_length: int | str):
    if isinstance(suffix_length, int):
        if len(row["actions"]) < suffix_length:
            return None
        return (
            row["source"],
            row["turn_bucket"],
            row["actions"][-suffix_length:],
        )
    if suffix_length == "turn":
        return (row["source"], row["turn_bucket"])
    if suffix_length == "source":
        return (row["source"],)
    raise ValueError(f"unknown block level: {suffix_length}")


def build_block_maps(rows: list[dict], datastore_global: np.ndarray):
    levels = (4, 3, 2, 1, "turn", "source")
    maps = {level: defaultdict(list) for level in levels}
    for local_index, global_index in enumerate(datastore_global.tolist()):
        row = rows[global_index]
        for level in levels:
            key = block_key(row, level)
            if key is not None:
                maps[level][key].append(local_index)
    return {
        level: {key: np.asarray(values, dtype=np.int64) for key, values in table.items()}
        for level, table in maps.items()
    }


def select_query_blocks(
    rows: list[dict],
    query_global: np.ndarray,
    datastore_scenarios: np.ndarray,
    datastore_labels: np.ndarray,
    block_maps: dict,
    min_block_size: int,
):
    selections = []
    level_counts = Counter()
    excluded_total = 0
    for global_index in query_global.tolist():
        row = rows[global_index]
        chosen = None
        for level in (4, 3, 2, 1, "turn", "source"):
            key = block_key(row, level)
            if key is None:
                continue
            candidates = block_maps[level].get(key)
            if candidates is None:
                continue
            keep = datastore_scenarios[candidates] != row["scenario"]
            valid_count = int(keep.sum())
            # Suffix levels are preferred only with enough support.  Turn/source
            # are explicit backoffs; source must always have ample support.
            if valid_count >= min_block_size or level == "source":
                chosen = (level, key, candidates, keep)
                break
        if chosen is None:
            raise RuntimeError(f"no nonempty retrieval block for {row['id']}")
        level, key, candidates, keep = chosen
        valid = candidates[keep]
        if len(valid) < min_block_size:
            raise RuntimeError(
                f"source fallback below min block size for {row['id']}: {len(valid)}"
            )
        counts = np.bincount(datastore_labels[valid], minlength=4).astype(np.float64)
        prior = (counts + 1.0) / (counts.sum() + 4.0)
        selections.append(
            {
                "level": str(level),
                "group": (level, key),
                "candidate_local": candidates,
                "scenario": row["scenario"],
                "valid_count": int(len(valid)),
                "prior": prior,
            }
        )
        level_counts[str(level)] += 1
        excluded_total += int((~keep).sum())
    return selections, level_counts, excluded_total


def exact_cosine_neighbors(
    hidden: torch.Tensor,
    datastore_global: np.ndarray,
    query_global: np.ndarray,
    datastore_scenarios: np.ndarray,
    datastore_labels: np.ndarray,
    selections: list[dict],
    max_k: int,
):
    """Compute exact 1024-dimensional cosine top-k inside each selected block."""
    datastore_hidden = F.normalize(hidden[datastore_global].float(), dim=1)
    query_hidden = F.normalize(hidden[query_global].float(), dim=1)
    groups = defaultdict(list)
    for query_position, selection in enumerate(selections):
        groups[selection["group"]].append(query_position)

    neighbor_labels = np.full((len(query_global), max_k), -1, dtype=np.int64)
    neighbor_cosines = np.full((len(query_global), max_k), -np.inf, dtype=np.float32)
    max_matrix_elements = 0
    for query_positions in groups.values():
        first = selections[query_positions[0]]
        candidate_local = first["candidate_local"]
        query_positions_np = np.asarray(query_positions, dtype=np.int64)
        similarities = query_hidden[query_positions_np] @ datastore_hidden[candidate_local].T
        max_matrix_elements = max(max_matrix_elements, similarities.numel())

        candidate_scenarios = datastore_scenarios[candidate_local]
        query_scenarios = np.asarray(
            [selections[position]["scenario"] for position in query_positions], dtype=object
        )
        same_scenario = query_scenarios[:, None] == candidate_scenarios[None, :]
        if same_scenario.any():
            similarities.masked_fill_(torch.from_numpy(same_scenario), -torch.inf)

        values, local_top = torch.topk(similarities, k=max_k, dim=1, largest=True, sorted=True)
        labels = datastore_labels[candidate_local[local_top.numpy()]]
        neighbor_labels[query_positions_np] = labels
        neighbor_cosines[query_positions_np] = values.numpy()
        del similarities, values, local_top
    return neighbor_labels, neighbor_cosines, len(groups), max_matrix_elements


def f1_vector(y_true: np.ndarray, y_pred: np.ndarray, n_classes: int) -> np.ndarray:
    values = np.zeros(n_classes, dtype=np.float64)
    for class_id in range(n_classes):
        true_positive = np.sum((y_true == class_id) & (y_pred == class_id))
        false_positive = np.sum((y_true != class_id) & (y_pred == class_id))
        false_negative = np.sum((y_true == class_id) & (y_pred != class_id))
        denominator = 2 * true_positive + false_positive + false_negative
        values[class_id] = 0.0 if denominator == 0 else 2 * true_positive / denominator
    return values


def metrics(y_true: np.ndarray, y_pred: np.ndarray, classes: list[str]) -> dict:
    values = f1_vector(y_true, y_pred, len(classes))
    return {
        "rows": int(len(y_true)),
        "macro_f1": float(values.mean()),
        "weak4_macro_f1": float(values[:4].mean()),
        "nonweak_f1_sum": float(values[4:].sum()),
        "per_class_f1": {label: float(values[index]) for index, label in enumerate(classes)},
    }


def subset_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, mask: np.ndarray, classes: list[str]
) -> dict:
    return metrics(y_true[mask], y_pred[mask], classes)


def change_summary(y_true: np.ndarray, base: np.ndarray, selected: np.ndarray) -> dict:
    changed = selected != base
    rescue = changed & (base != y_true) & (selected == y_true)
    harm = changed & (base == y_true) & (selected != y_true)
    wrong_to_wrong = changed & (base != y_true) & (selected != y_true)
    rescue_count = int(rescue.sum())
    harm_count = int(harm.sum())
    return {
        "changed": int(changed.sum()),
        "rescue": rescue_count,
        "harm": harm_count,
        "wrong_to_wrong": int(wrong_to_wrong.sum()),
        "rescue_harm_ratio": (
            float(rescue_count / harm_count) if harm_count else (math.inf if rescue_count else None)
        ),
    }


def make_prediction(
    logits: np.ndarray,
    base_prediction: np.ndarray,
    query_val_positions: np.ndarray,
    top3: np.ndarray,
    neighbor_labels: np.ndarray,
    priors: np.ndarray,
    k: int,
    weight: float,
    min_purity: float,
):
    labels = neighbor_labels[:, :k]
    votes = np.zeros((len(labels), 4), dtype=np.float64)
    for class_id in range(4):
        votes[:, class_id] = np.sum(labels == class_id, axis=1)
    probability = (votes + 2.0 * priors) / (k + 2.0)
    residual = np.log(probability) - np.log(priors)
    purity = votes.max(axis=1) / k
    proposal = probability.argmax(axis=1)
    weak_logits = logits[query_val_positions, :4] + weight * residual
    adjusted = weak_logits.argmax(axis=1)
    # The user gate constrains the action actually selected after residual
    # injection, which can differ from the raw kNN-posterior proposal.
    adjusted_in_top3 = np.any(top3[query_val_positions] == adjusted[:, None], axis=1)
    route = (purity >= min_purity) & adjusted_in_top3
    prediction = base_prediction.copy()
    prediction[query_val_positions[route]] = adjusted[route]
    return prediction, route, purity, proposal


def learn_pairwise_utility_rules(
    logits: np.ndarray,
    base_prediction: np.ndarray,
    query_val_positions: np.ndarray,
    top3: np.ndarray,
    neighbor_labels: np.ndarray,
    priors: np.ndarray,
    k: int,
    weight: float,
    purity_grid: tuple[float, ...],
    tune_positions: np.ndarray,
    tune_y_true: np.ndarray,
    classes: list[str],
    min_tune_changes: int,
    min_tune_rescues: int,
    min_rescue_harm_ratio: float,
):
    """Learn a small directed-pair gate using tune labels and nothing else.

    Pair order and threshold grid are fixed before looking at outcomes.  Each
    directed pair must independently clear support, rescue/harm, and positive
    incremental Macro-F1 constraints against the already accepted tune rules.
    The learned feature-only rules are then applied to every validation row;
    confirm labels are not an input to this function or its selection logic.
    """
    labels = neighbor_labels[:, :k]
    votes = np.zeros((len(labels), 4), dtype=np.float64)
    for class_id in range(4):
        votes[:, class_id] = np.sum(labels == class_id, axis=1)
    probability = (votes + 2.0 * priors) / (k + 2.0)
    residual = np.log(probability) - np.log(priors)
    purity = votes.max(axis=1) / k
    adjusted = (logits[query_val_positions, :4] + weight * residual).argmax(axis=1)
    adjusted_in_top3 = np.any(top3[query_val_positions] == adjusted[:, None], axis=1)

    tune_lookup = np.full(len(base_prediction), -1, dtype=np.int64)
    tune_lookup[tune_positions] = np.arange(len(tune_positions), dtype=np.int64)
    current_prediction = base_prediction.copy()
    selected_rules = []
    pair_audit = []
    for source_id, target_id in A5_PAIR_ORDER:
        pair_mask = (
            adjusted_in_top3
            & (base_prediction[query_val_positions] == source_id)
            & (adjusted == target_id)
        )
        before_tune = metrics(
            tune_y_true, current_prediction[tune_positions], classes
        )
        threshold_rows = []
        valid_candidates = []
        for threshold in purity_grid:
            flip_positions = query_val_positions[pair_mask & (purity >= threshold)]
            tune_flip_positions = flip_positions[tune_lookup[flip_positions] >= 0]
            tune_truth = tune_y_true[tune_lookup[tune_flip_positions]]
            rescue = int(np.sum((tune_truth != source_id) & (tune_truth == target_id)))
            harm = int(np.sum(tune_truth == source_id))
            wrong_to_wrong = int(len(tune_truth) - rescue - harm)
            ratio = float(rescue / harm) if harm else (math.inf if rescue else None)
            trial_prediction = current_prediction.copy()
            trial_prediction[flip_positions] = target_id
            after_tune = metrics(
                tune_y_true, trial_prediction[tune_positions], classes
            )
            macro_gain = after_tune["macro_f1"] - before_tune["macro_f1"]
            ratio_ok = ratio is math.inf or (
                isinstance(ratio, float) and ratio >= min_rescue_harm_ratio
            )
            constraints = {
                "min_tune_changes": len(tune_flip_positions) >= min_tune_changes,
                "min_tune_rescues": rescue >= min_tune_rescues,
                "rescue_harm_ratio": ratio_ok,
                "positive_incremental_macro_f1": macro_gain > 0.0,
            }
            row = {
                "min_raw_vote_purity": threshold,
                "full_feature_matched_rows": int(len(flip_positions)),
                "tune_changes": int(len(tune_flip_positions)),
                "tune_rescue": rescue,
                "tune_harm": harm,
                "tune_wrong_to_wrong": wrong_to_wrong,
                "tune_rescue_harm_ratio": ratio,
                "tune_macro_f1_before": before_tune["macro_f1"],
                "tune_macro_f1_after": after_tune["macro_f1"],
                "tune_incremental_macro_f1": macro_gain,
                "constraints": constraints,
                "eligible": all(constraints.values()),
            }
            threshold_rows.append(row)
            if row["eligible"]:
                valid_candidates.append((row, trial_prediction, after_tune))

        accepted = None
        if valid_candidates:
            accepted, accepted_prediction, accepted_metrics = max(
                valid_candidates,
                key=lambda item: (
                    item[2]["macro_f1"],
                    item[2]["weak4_macro_f1"],
                    item[0]["min_raw_vote_purity"],
                    -item[0]["tune_changes"],
                ),
            )
            current_prediction = accepted_prediction
            selected_rules.append(
                {
                    "source": classes[source_id],
                    "target": classes[target_id],
                    **{key: value for key, value in accepted.items() if key != "constraints"},
                    "constraints": accepted["constraints"],
                }
            )
        pair_audit.append(
            {
                "source": classes[source_id],
                "target": classes[target_id],
                "accepted": accepted is not None,
                "selected_min_raw_vote_purity": (
                    accepted["min_raw_vote_purity"] if accepted is not None else None
                ),
                "thresholds": threshold_rows,
            }
        )
    return current_prediction, selected_rules, pair_audit


def evaluate_candidate(
    config: dict,
    prediction: np.ndarray,
    y_true: np.ndarray,
    base_prediction: np.ndarray,
    tune_mask: np.ndarray,
    confirm_mask: np.ndarray,
    classes: list[str],
) -> dict:
    return {
        "config": config,
        "tune": subset_metrics(y_true, prediction, tune_mask, classes),
        "confirm": subset_metrics(y_true, prediction, confirm_mask, classes),
        "full": metrics(y_true, prediction, classes),
        "changes_tune": change_summary(
            y_true[tune_mask], base_prediction[tune_mask], prediction[tune_mask]
        ),
        "changes_confirm": change_summary(
            y_true[confirm_mask], base_prediction[confirm_mask], prediction[confirm_mask]
        ),
        "changes_full": change_summary(y_true, base_prediction, prediction),
        "prediction": prediction,
    }


def finite_json(value):
    if isinstance(value, dict):
        return {str(key): finite_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [finite_json(item) for item in value]
    if isinstance(value, tuple):
        return [finite_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return "inf" if value > 0 else "-inf"
    if isinstance(value, np.generic):
        return finite_json(value.item())
    return value


def parse_numbers(value: str, converter):
    return tuple(converter(item.strip()) for item in value.split(",") if item.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("experiments/incoming/artifacts/privileged_mode/kd_hcx_m8_screen_cache.pt"),
    )
    parser.add_argument("--train-jsonl", type=Path, default=Path("open/data/train.jsonl"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/artifacts/20260711_hidden_knn_a4_prevalidation.json"),
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--tune-folds", type=int, default=3)
    parser.add_argument("--fold-seed", type=int, default=42)
    parser.add_argument("--min-block-size", type=int, default=32)
    parser.add_argument("--k", default=",".join(map(str, DEFAULT_K)))
    parser.add_argument("--weights", default=",".join(map(str, DEFAULT_WEIGHTS)))
    parser.add_argument("--purities", default=",".join(map(str, DEFAULT_PURITIES)))
    parser.add_argument("--a5-purities", default=",".join(map(str, A5_PURITY_GRID)))
    parser.add_argument("--a5-min-tune-changes", type=int, default=8)
    parser.add_argument("--a5-min-tune-rescues", type=int, default=4)
    parser.add_argument("--a5-min-rescue-harm-ratio", type=float, default=2.0)
    parser.add_argument("--threads", type=int, default=0)
    args = parser.parse_args()
    if args.threads > 0:
        torch.set_num_threads(args.threads)

    started = time.time()
    cache = torch_load(args.cache)
    # Deliberate field-by-field access prevents accidental use of privileged
    # future-event/candidate-mode data carried alongside the permitted tensors.
    ids = list(cache["ids"])
    y_all = cache["y_true"].numpy().astype(np.int64, copy=False)
    hidden = cache["hidden"]
    logits_all = cache["parent_logits"].numpy().astype(np.float64, copy=False)
    train_indices = cache["train_indices"].numpy().astype(np.int64, copy=False)
    val_indices = cache["val_indices"].numpy().astype(np.int64, copy=False)
    classes = list(cache["classes"])

    if hidden.ndim != 2 or hidden.shape[1] != 1024:
        raise ValueError(f"expected full 1024d hidden states, got {tuple(hidden.shape)}")
    if len(classes) != 14:
        raise ValueError(f"expected 14 classes, got {len(classes)}")
    if np.intersect1d(train_indices, val_indices).size:
        raise ValueError("cache train_indices and val_indices overlap")

    rows = load_feature_rows(args.train_jsonl, ids)
    datastore_global = train_indices[y_all[train_indices] < 4]
    datastore_labels = y_all[datastore_global]
    datastore_scenarios = np.asarray(
        [rows[index]["scenario"] for index in datastore_global], dtype=object
    )

    val_logits = logits_all[val_indices]
    y_true = y_all[val_indices]
    # Stable descending order preserves torch/NumPy argmax's lower-class-ID
    # tie break.  Reversing an ascending argsort would silently choose the
    # higher ID for fp16-quantized exact ties and alter the canonical anchor.
    order = np.argsort(-val_logits, axis=1, kind="stable")
    base_prediction = np.argmax(val_logits, axis=1)
    if not np.array_equal(base_prediction, order[:, 0]):
        raise RuntimeError("stable top-class order disagrees with canonical argmax")
    top2_weak = np.all(order[:, :2] < 4, axis=1)
    query_val_positions = np.where(top2_weak)[0]
    query_global = val_indices[query_val_positions]

    block_maps = build_block_maps(rows, datastore_global)
    selections, level_counts, excluded_total = select_query_blocks(
        rows,
        query_global,
        datastore_scenarios,
        datastore_labels,
        block_maps,
        args.min_block_size,
    )
    priors = np.stack([selection["prior"] for selection in selections])
    block_sizes = np.asarray([selection["valid_count"] for selection in selections])

    k_values = parse_numbers(args.k, int)
    weight_values = parse_numbers(args.weights, float)
    purity_values = parse_numbers(args.purities, float)
    a5_purity_values = parse_numbers(args.a5_purities, float)
    if max(k_values) >= args.min_block_size:
        raise ValueError("max k must be smaller than --min-block-size")
    if not a5_purity_values or any(value < 0.0 or value > 1.0 for value in a5_purity_values):
        raise ValueError("--a5-purities must contain values in [0,1]")
    neighbor_labels, neighbor_cosines, retrieval_groups, max_matrix_elements = (
        exact_cosine_neighbors(
            hidden,
            datastore_global,
            query_global,
            datastore_scenarios,
            datastore_labels,
            selections,
            max(k_values),
        )
    )
    if np.any(neighbor_labels < 0) or not np.isfinite(neighbor_cosines).all():
        raise RuntimeError("retrieval returned missing/masked neighbors")

    val_folds = np.asarray(
        [scenario_fold(ids[index], args.folds, args.fold_seed) for index in val_indices],
        dtype=np.int64,
    )
    tune_mask = val_folds < args.tune_folds
    confirm_mask = ~tune_mask
    base = {
        "full": metrics(y_true, base_prediction, classes),
        "tune": subset_metrics(y_true, base_prediction, tune_mask, classes),
        "confirm": subset_metrics(y_true, base_prediction, confirm_mask, classes),
    }

    candidates = []
    for k in k_values:
        for purity in purity_values:
            for weight in weight_values:
                prediction, route, _, _ = make_prediction(
                    val_logits,
                    base_prediction,
                    query_val_positions,
                    order[:, :3],
                    neighbor_labels,
                    priors,
                    k,
                    weight,
                    purity,
                )
                candidates.append(
                    evaluate_candidate(
                        {
                            "k": k,
                            "weight": weight,
                            "min_raw_vote_purity": purity,
                            "routed_before_argmax_change": int(route.sum()),
                        },
                        prediction,
                        y_true,
                        base_prediction,
                        tune_mask,
                        confirm_mask,
                        classes,
                    )
                )
    candidates.append(
        evaluate_candidate(
            {
                "k": 0,
                "weight": 0.0,
                "min_raw_vote_purity": 1.0,
                "routed_before_argmax_change": 0,
                "no_op": True,
            },
            base_prediction.copy(),
            y_true,
            base_prediction,
            tune_mask,
            confirm_mask,
            classes,
        )
    )
    candidates.sort(
        key=lambda row: (
            row["tune"]["macro_f1"],
            row["tune"]["weak4_macro_f1"],
            -row["config"]["weight"],
            -row["changes_tune"]["changed"],
        ),
        reverse=True,
    )
    selected = candidates[0]
    prediction = selected.pop("prediction")
    for row in candidates[1:]:
        row.pop("prediction")

    fold_rows = []
    for fold in range(args.folds):
        mask = val_folds == fold
        before = subset_metrics(y_true, base_prediction, mask, classes)
        after = subset_metrics(y_true, prediction, mask, classes)
        fold_rows.append(
            {
                "fold": fold,
                "role": "tune" if fold < args.tune_folds else "confirm",
                "rows": int(mask.sum()),
                "base_macro_f1": before["macro_f1"],
                "selected_macro_f1": after["macro_f1"],
                "macro_f1_delta": after["macro_f1"] - before["macro_f1"],
                "base_weak4_macro_f1": before["weak4_macro_f1"],
                "selected_weak4_macro_f1": after["weak4_macro_f1"],
                "weak4_macro_f1_delta": (
                    after["weak4_macro_f1"] - before["weak4_macro_f1"]
                ),
                "changes": change_summary(
                    y_true[mask], base_prediction[mask], prediction[mask]
                ),
            }
        )

    full_macro_delta = selected["full"]["macro_f1"] - base["full"]["macro_f1"]
    full_weak_delta = (
        selected["full"]["weak4_macro_f1"] - base["full"]["weak4_macro_f1"]
    )
    confirm_macro_delta = (
        selected["confirm"]["macro_f1"] - base["confirm"]["macro_f1"]
    )
    confirm_weak_delta = (
        selected["confirm"]["weak4_macro_f1"] - base["confirm"]["weak4_macro_f1"]
    )
    nonweak_loss = max(
        0.0, base["full"]["nonweak_f1_sum"] - selected["full"]["nonweak_f1_sum"]
    )
    ratio = selected["changes_full"]["rescue_harm_ratio"]
    positive_folds = sum(row["macro_f1_delta"] > 0 for row in fold_rows)
    ratio_pass = ratio == "inf" or ratio is math.inf or (isinstance(ratio, float) and ratio >= 2.0)
    gates = {
        "positive_macro_folds_at_least_4_of_5": {
            "value": positive_folds,
            "pass": positive_folds >= 4,
        },
        "full_macro_f1_delta_at_least_0.003": {
            "value": full_macro_delta,
            "pass": full_macro_delta >= 0.003,
        },
        "full_weak4_macro_f1_delta_at_least_0.008": {
            "value": full_weak_delta,
            "pass": full_weak_delta >= 0.008,
        },
        "nonweak_f1_sum_loss_at_most_0.002": {
            "value": nonweak_loss,
            "pass": nonweak_loss <= 0.002,
        },
        "full_rescue_harm_ratio_at_least_2": {"value": ratio, "pass": ratio_pass},
        "confirm_macro_f1_positive": {
            "value": confirm_macro_delta,
            "pass": confirm_macro_delta > 0,
        },
        "confirm_weak4_macro_f1_positive": {
            "value": confirm_weak_delta,
            "pass": confirm_weak_delta > 0,
        },
    }
    overall_pass = all(item["pass"] for item in gates.values())

    # A5 fixes the tune-selected A4 k/weight surface, then learns only a small
    # directed-pair purity allowlist.  Pass tune labels as a compact separate
    # array so confirm labels cannot be consulted by the rule learner.
    tune_positions = np.where(tune_mask)[0]
    if selected["config"]["k"] > 0:
        a5_prediction, a5_selected_rules, a5_pair_audit = learn_pairwise_utility_rules(
            val_logits,
            base_prediction,
            query_val_positions,
            order[:, :3],
            neighbor_labels,
            priors,
            int(selected["config"]["k"]),
            float(selected["config"]["weight"]),
            a5_purity_values,
            tune_positions,
            y_true[tune_positions].copy(),
            classes,
            args.a5_min_tune_changes,
            args.a5_min_tune_rescues,
            args.a5_min_rescue_harm_ratio,
        )
    else:
        a5_prediction = base_prediction.copy()
        a5_selected_rules = []
        a5_pair_audit = []

    a5_selected_metrics = {
        "full": metrics(y_true, a5_prediction, classes),
        "tune": subset_metrics(y_true, a5_prediction, tune_mask, classes),
        "confirm": subset_metrics(y_true, a5_prediction, confirm_mask, classes),
    }
    a5_changes = {
        "full": change_summary(y_true, base_prediction, a5_prediction),
        "tune": change_summary(
            y_true[tune_mask], base_prediction[tune_mask], a5_prediction[tune_mask]
        ),
        "confirm": change_summary(
            y_true[confirm_mask], base_prediction[confirm_mask], a5_prediction[confirm_mask]
        ),
    }
    a5_fold_rows = []
    for fold in range(args.folds):
        mask = val_folds == fold
        before = subset_metrics(y_true, base_prediction, mask, classes)
        after = subset_metrics(y_true, a5_prediction, mask, classes)
        a5_fold_rows.append(
            {
                "fold": fold,
                "role": "tune" if fold < args.tune_folds else "confirm",
                "rows": int(mask.sum()),
                "base_macro_f1": before["macro_f1"],
                "selected_macro_f1": after["macro_f1"],
                "macro_f1_delta": after["macro_f1"] - before["macro_f1"],
                "base_weak4_macro_f1": before["weak4_macro_f1"],
                "selected_weak4_macro_f1": after["weak4_macro_f1"],
                "weak4_macro_f1_delta": (
                    after["weak4_macro_f1"] - before["weak4_macro_f1"]
                ),
                "changes": change_summary(
                    y_true[mask], base_prediction[mask], a5_prediction[mask]
                ),
            }
        )

    a5_full_macro_delta = (
        a5_selected_metrics["full"]["macro_f1"] - base["full"]["macro_f1"]
    )
    a5_full_weak_delta = (
        a5_selected_metrics["full"]["weak4_macro_f1"]
        - base["full"]["weak4_macro_f1"]
    )
    a5_confirm_macro_delta = (
        a5_selected_metrics["confirm"]["macro_f1"] - base["confirm"]["macro_f1"]
    )
    a5_confirm_weak_delta = (
        a5_selected_metrics["confirm"]["weak4_macro_f1"]
        - base["confirm"]["weak4_macro_f1"]
    )
    a5_ratio = a5_changes["full"]["rescue_harm_ratio"]
    a5_positive_folds = sum(row["macro_f1_delta"] > 0 for row in a5_fold_rows)
    a5_ratio_pass = a5_ratio == "inf" or a5_ratio is math.inf or (
        isinstance(a5_ratio, float) and a5_ratio >= 2.0
    )
    a5_nonweak_loss = max(
        0.0,
        base["full"]["nonweak_f1_sum"]
        - a5_selected_metrics["full"]["nonweak_f1_sum"],
    )
    a5_gates = {
        "positive_macro_folds_at_least_4_of_5": {
            "value": a5_positive_folds,
            "pass": a5_positive_folds >= 4,
        },
        "full_macro_f1_delta_at_least_0.003": {
            "value": a5_full_macro_delta,
            "pass": a5_full_macro_delta >= 0.003,
        },
        "full_weak4_macro_f1_delta_at_least_0.008": {
            "value": a5_full_weak_delta,
            "pass": a5_full_weak_delta >= 0.008,
        },
        "nonweak_f1_sum_loss_at_most_0.002": {
            "value": a5_nonweak_loss,
            "pass": a5_nonweak_loss <= 0.002,
        },
        "full_rescue_harm_ratio_at_least_2": {
            "value": a5_ratio,
            "pass": a5_ratio_pass,
        },
        "confirm_macro_f1_positive": {
            "value": a5_confirm_macro_delta,
            "pass": a5_confirm_macro_delta > 0,
        },
        "confirm_weak4_macro_f1_positive": {
            "value": a5_confirm_weak_delta,
            "pass": a5_confirm_weak_delta > 0,
        },
    }
    a5_overall_pass = bool(a5_selected_rules) and all(
        item["pass"] for item in a5_gates.values()
    )
    a5 = {
        "status": "pass" if a5_overall_pass else "reject",
        "selection_integrity": (
            "pair rules learned from a separate y_true[tune_positions] array; "
            "confirm labels are not passed to learn_pairwise_utility_rules"
        ),
        "surface": {
            "a4_k": selected["config"]["k"],
            "a4_weight": selected["config"]["weight"],
            "block_and_neighbor_surface": "identical to A4",
        },
        "predeclared_pair_order": [
            {"source": classes[source], "target": classes[target]}
            for source, target in A5_PAIR_ORDER
        ],
        "predeclared_constraints": {
            "raw_vote_purity_grid": list(a5_purity_values),
            "min_tune_changes": args.a5_min_tune_changes,
            "min_tune_rescues": args.a5_min_tune_rescues,
            "min_tune_rescue_harm_ratio": args.a5_min_rescue_harm_ratio,
            "incremental_tune_macro_f1": "> 0",
            "target_gate": "actual residual-adjusted target must be in parent top3",
            "selection_order": "fixed directed-pair order, sequential incremental utility",
        },
        "selected_pair_rules": a5_selected_rules,
        "pair_threshold_audit": a5_pair_audit,
        "selected": a5_selected_metrics,
        "delta_vs_parent": {
            "full_macro_f1": a5_full_macro_delta,
            "full_weak4_macro_f1": a5_full_weak_delta,
            "full_nonweak_f1_sum": (
                a5_selected_metrics["full"]["nonweak_f1_sum"]
                - base["full"]["nonweak_f1_sum"]
            ),
            "confirm_macro_f1": a5_confirm_macro_delta,
            "confirm_weak4_macro_f1": a5_confirm_weak_delta,
        },
        "delta_vs_a4": {
            "full_macro_f1": (
                a5_selected_metrics["full"]["macro_f1"]
                - selected["full"]["macro_f1"]
            ),
            "full_weak4_macro_f1": (
                a5_selected_metrics["full"]["weak4_macro_f1"]
                - selected["full"]["weak4_macro_f1"]
            ),
            "confirm_macro_f1": (
                a5_selected_metrics["confirm"]["macro_f1"]
                - selected["confirm"]["macro_f1"]
            ),
            "confirm_weak4_macro_f1": (
                a5_selected_metrics["confirm"]["weak4_macro_f1"]
                - selected["confirm"]["weak4_macro_f1"]
            ),
        },
        "changes": a5_changes,
        "folds": a5_fold_rows,
        "promotion_gates": a5_gates,
        "overall_gate_pass": a5_overall_pass,
    }

    artifact = {
        "experiment": "A4 hidden kNN memory + A5 pairwise utility prevalidation",
        "status": {
            "a4": "pass" if overall_pass else "reject",
            "a5": "pass" if a5_overall_pass else "reject",
        },
        "method": {
            "cache_fields_read": list(CACHE_FIELDS_READ),
            "forbidden_cache_fields_not_read": list(FORBIDDEN_CACHE_FIELDS),
            "json_fields_read": [
                "id",
                "session_meta.turn_index",
                "history.role",
                "history.name",
            ],
            "datastore": "cache train_indices intersect true Weak4 labels only",
            "queries": "cache val_indices with parent top-2 both Weak4",
            "same_scenario_rule": (
                "remove exact session for SIM; remove all sess_au_<primary> siblings for AU"
            ),
            "retrieval": "exact full-1024 cosine within selected block",
            "block_backoff": [
                "source+turn_bin+action_suffix4",
                "source+turn_bin+action_suffix3",
                "source+turn_bin+action_suffix2",
                "source+turn_bin+last_action",
                "source+turn_bin",
                "source",
            ],
            "block_rule": f"first block with >= {args.min_block_size} rows",
            "block_prior": "Weak4 counts with add-one smoothing",
            "knn_posterior": "(top-k unweighted votes + 2*block_prior)/(k+2)",
            "residual": "log(knn_posterior)-log(block_prior)",
            "family_lock": "only Weak4 logits can change; non-Weak4 prediction impossible",
            "route_gate": (
                "parent top2 Weak4; actual residual-adjusted target in parent top3; "
                "raw-vote purity"
            ),
            "selection": (
                f"pooled scenario folds 0-{args.tune_folds - 1}; configuration applied "
                f"unchanged to confirm folds {args.tune_folds}-{args.folds - 1}"
            ),
        },
        "rows": {
            "all": len(ids),
            "datastore_train_weak4": int(len(datastore_global)),
            "validation": int(len(val_indices)),
            "top2_weak4_queries": int(len(query_global)),
            "same_scenario_datastore_rows_excluded_across_queries": excluded_total,
        },
        "retrieval_diagnostics": {
            "hidden_dimension": int(hidden.shape[1]),
            "selected_block_level_counts": dict(sorted(level_counts.items())),
            "selected_block_size_percentiles": {
                str(percentile): float(np.percentile(block_sizes, percentile))
                for percentile in (0, 10, 25, 50, 75, 90, 100)
            },
            "grouped_matrix_multiplications": retrieval_groups,
            "largest_similarity_matrix_elements": max_matrix_elements,
            "top1_cosine_percentiles": {
                str(percentile): float(np.percentile(neighbor_cosines[:, 0], percentile))
                for percentile in (0, 10, 25, 50, 75, 90, 100)
            },
            "kth_cosine_percentiles": {
                str(percentile): float(np.percentile(neighbor_cosines[:, -1], percentile))
                for percentile in (0, 10, 25, 50, 75, 90, 100)
            },
        },
        "fold_protocol": {
            "folds": args.folds,
            "seed": args.fold_seed,
            "tune_folds": list(range(args.tune_folds)),
            "confirm_folds": list(range(args.tune_folds, args.folds)),
            "fold_rows": [int(np.sum(val_folds == fold)) for fold in range(args.folds)],
        },
        "grid": {
            "k": list(k_values),
            "weights": list(weight_values),
            "min_raw_vote_purity": list(purity_values),
            "candidate_count_including_noop": len(candidates),
        },
        "base": base,
        "selected": selected,
        "delta": {
            "full_macro_f1": full_macro_delta,
            "full_weak4_macro_f1": full_weak_delta,
            "full_nonweak_f1_sum": (
                selected["full"]["nonweak_f1_sum"] - base["full"]["nonweak_f1_sum"]
            ),
            "confirm_macro_f1": confirm_macro_delta,
            "confirm_weak4_macro_f1": confirm_weak_delta,
        },
        "folds": fold_rows,
        "promotion_gates": gates,
        "overall_gate_pass": overall_pass,
        "a5": a5,
        "top_tune_configurations": [
            {
                "config": row["config"],
                "tune_macro_f1": row["tune"]["macro_f1"],
                "tune_weak4_macro_f1": row["tune"]["weak4_macro_f1"],
                "confirm_macro_f1": row["confirm"]["macro_f1"],
                "confirm_weak4_macro_f1": row["confirm"]["weak4_macro_f1"],
                "full_macro_f1": row["full"]["macro_f1"],
                "changes_tune": row["changes_tune"],
                "changes_confirm": row["changes_confirm"],
            }
            for row in candidates[:20]
        ],
        "runtime_seconds": time.time() - started,
        "command_defaults": {
            "cache": str(args.cache),
            "train_jsonl": str(args.train_jsonl),
            "output": str(args.output),
            "min_block_size": args.min_block_size,
            "a5_purities": list(a5_purity_values),
            "a5_min_tune_changes": args.a5_min_tune_changes,
            "a5_min_tune_rescues": args.a5_min_tune_rescues,
            "a5_min_rescue_harm_ratio": args.a5_min_rescue_harm_ratio,
            "threads": args.threads,
        },
    }
    artifact = finite_json(artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": artifact["status"],
        "selected_config": artifact["selected"]["config"],
        "delta": artifact["delta"],
        "promotion_gates": artifact["promotion_gates"],
        "a5": {
            "status": artifact["a5"]["status"],
            "selected_pair_rules": artifact["a5"]["selected_pair_rules"],
            "delta_vs_parent": artifact["a5"]["delta_vs_parent"],
            "changes": artifact["a5"]["changes"],
            "promotion_gates": artifact["a5"]["promotion_gates"],
        },
        "runtime_seconds": artifact["runtime_seconds"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
