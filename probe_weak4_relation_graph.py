#!/usr/bin/env python3
"""Leak-safe CPU probe for deterministic Weak4 relation features.

This is deliberately a prevalidation harness, not deployment code.  It keeps
the clean Card-B parent logits, route, and KEEP/SWITCH targets fixed and changes
only the observable feature representation:

R0  parent pair gap
R1  R0 + the existing ten main-model numeric features
R2  R1 + the already-screened coarse route/state abstraction
R3  R1 + full-history, action-attributed exact relation features
R4  secondary no-op-safe residual use of the same relation block

The primary surface removes AU scenarios that the fixed parent saw through a
sibling variant and groups the remaining rows by AU primary scenario (ordinary
session elsewhere).  The original Card-B session folds are retained as a
secondary comparability surface.  c_clean, consensus, decoder hidden states,
and packaging are intentionally out of scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shlex
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import torch
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import StandardScaler

from build_weak4_pair_dataset import sha256_file, torch_load
from script import ALL_CLASSES, load_jsonl, safe_text
from train import append_results_csv, f1_metrics
from train_weak4_pair_residual import validate_dataset, weak4_macro
from weak4_pair_residual import (
    LIVE_WEAK4_PAIRS,
    TARGET_OUTSIDE,
    TARGET_PROTECT,
    TARGET_RESCUE,
)
from weak4_relation_graph import (
    extract_coarse_features,
    extract_relation_features,
    scenario_group,
    vectorize_feature_dicts,
)


SCHEMA_VERSION = 1
ARTIFACT_KIND = "weak4_relation_graph_probe"
DEFAULT_DATASET = "experiments/artifacts/20260712_weak4_pair_fixed_parent_dataset.pt"
DEFAULT_OUTPUT = "experiments/artifacts/20260713_weak4_relation_graph_v1_probe.json"
DEFAULT_SCORES = "experiments/logits/20260713_weak4_relation_graph_v1_probe.pt"
SURFACE_ORDER = (
    "r0_gap",
    "r1_main",
    "r2_coarse",
    "r3_relation",
    "r4_relation_residual",
)


def session_id(sample_id: str) -> str:
    return safe_text(sample_id).split("-step_")[0]


def source_name(sample_id: str) -> str:
    sample_id = safe_text(sample_id)
    if sample_id.startswith("sess_au_"):
        return "au"
    if sample_id.startswith("sess_sim_"):
        return "sim"
    return "unknown"


def turn_bin(value: object) -> str:
    try:
        turn = int(float(value))
    except (TypeError, ValueError):
        return "unknown"
    if turn <= 1:
        return "start"
    if turn <= 2:
        return "early"
    if turn <= 4:
        return "mid"
    if turn <= 6:
        return "late"
    return "long"


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def safe_auc(labels: Sequence[int] | np.ndarray, scores: Sequence[float] | np.ndarray):
    labels = np.asarray(labels, dtype=np.int64)
    scores = np.asarray(scores, dtype=np.float64)
    if len(labels) == 0 or len(np.unique(labels)) < 2:
        return None
    return float(roc_auc_score(labels, scores))


def json_safe(value):
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def stratified_group_fold_ids(
    strata: Sequence[int] | np.ndarray,
    groups: Sequence[str] | np.ndarray,
    *,
    n_folds: int,
    seed: int,
) -> np.ndarray:
    """Assign every group to one deterministic stratified fold."""

    strata = np.asarray(strata, dtype=np.int64)
    groups = np.asarray(groups, dtype=object)
    if len(strata) != len(groups) or not len(strata):
        raise ValueError("strata and groups must be nonempty and aligned")
    if len(set(groups.tolist())) < n_folds:
        raise ValueError("fewer groups than requested folds")
    splitter = StratifiedGroupKFold(
        n_splits=n_folds, shuffle=True, random_state=int(seed)
    )
    folds = np.full(len(strata), -1, dtype=np.int64)
    for fold_id, (_, val_idx) in enumerate(
        splitter.split(np.zeros((len(strata), 1)), strata, groups)
    ):
        folds[val_idx] = fold_id
    if np.any(folds < 0):
        raise AssertionError("group fold assignment left unassigned rows")
    group_folds: dict[str, set[int]] = defaultdict(set)
    for group, fold in zip(groups.tolist(), folds.tolist()):
        group_folds[str(group)].add(int(fold))
    leaked = [group for group, values in group_folds.items() if len(values) != 1]
    if leaked:
        raise AssertionError(f"groups cross folds: {leaked[:5]}")
    return folds


def _balanced_binary_weights(labels: np.ndarray, directions: np.ndarray) -> np.ndarray:
    """Equalize directed-pair groups inside switch and identity targets."""

    labels = np.asarray(labels, dtype=np.int64)
    directions = np.asarray(directions, dtype=np.int64)
    weights = np.zeros(len(labels), dtype=np.float64)
    for target in (0, 1):
        target_rows = np.flatnonzero(labels == target)
        if not len(target_rows):
            continue
        groups = sorted(set(directions[target_rows].tolist()))
        for group in groups:
            rows = target_rows[directions[target_rows] == group]
            weights[rows] = 1.0 / (len(groups) * len(rows))
    if weights.sum() <= 0:
        raise ValueError("binary weighting produced zero mass")
    return weights * (len(weights) / weights.sum())


def fit_predict_logistic(
    train_x: np.ndarray,
    train_y: np.ndarray,
    train_directions: np.ndarray,
    eval_x: np.ndarray,
    *,
    c_value: float,
    seed: int,
) -> np.ndarray:
    train_x = np.asarray(train_x, dtype=np.float64)
    eval_x = np.asarray(eval_x, dtype=np.float64)
    train_y = np.asarray(train_y, dtype=np.int64)
    if train_x.ndim != 2 or eval_x.ndim != 2 or train_x.shape[1] != eval_x.shape[1]:
        raise ValueError("train/eval feature matrices are incompatible")
    if len(np.unique(train_y)) != 2:
        raise ValueError("logistic fit requires both switch and identity targets")
    if not np.isfinite(train_x).all() or not np.isfinite(eval_x).all():
        raise ValueError("feature matrix contains non-finite values")
    scaler = StandardScaler()
    scaled_train = scaler.fit_transform(train_x)
    scaled_eval = scaler.transform(eval_x)
    model = LogisticRegression(
        C=float(c_value),
        solver="liblinear",
        max_iter=2000,
        random_state=int(seed),
    )
    weights = _balanced_binary_weights(train_y, train_directions)
    model.fit(scaled_train, train_y, sample_weight=weights)
    return model.predict_proba(scaled_eval)[:, 1].astype(np.float64)


def choose_direction_thresholds(
    scores: np.ndarray,
    labels: np.ndarray,
    directions: np.ndarray,
    *,
    min_support: int,
    min_positive: int,
) -> tuple[dict[int, float], dict]:
    """Tune conservative switch thresholds on inner OOF scores only.

    The objective is true switches minus every false switch.  A threshold above
    one is always present, so calibration becomes an exact no-op when no
    positive-net selector exists.
    """

    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels, dtype=np.int64)
    directions = np.asarray(directions, dtype=np.int64)
    if not (len(scores) == len(labels) == len(directions)):
        raise ValueError("threshold inputs are not aligned")
    fixed = np.linspace(0.10, 0.99, 90)
    quantiles = np.quantile(scores, np.linspace(0.05, 0.95, 19)) if len(scores) else []
    grid = sorted({1.000001, *fixed.tolist(), *np.asarray(quantiles).tolist()})

    def select(rows: np.ndarray) -> tuple[float, dict]:
        best = None
        for threshold in grid:
            switched = rows & (scores >= threshold)
            rescue = int(np.sum(switched & (labels == 1)))
            false_switch = int(np.sum(switched & (labels == 0)))
            net = rescue - false_switch
            changed = rescue + false_switch
            precision = rescue / changed if changed else 1.0
            candidate = (net, precision, -false_switch, threshold, rescue)
            if best is None or candidate > best:
                best = candidate
        assert best is not None
        return float(best[3]), {
            "support": int(rows.sum()),
            "positive": int(np.sum(rows & (labels == 1))),
            "threshold": float(best[3]),
            "net": int(best[0]),
            "precision": float(best[1]),
            "false_switch": int(-best[2]),
            "rescue": int(best[4]),
        }

    all_rows = np.ones(len(scores), dtype=bool)
    global_threshold, global_detail = select(all_rows)
    thresholds: dict[int, float] = {}
    details = {}
    for direction in sorted(set(directions.tolist())):
        rows = directions == direction
        positive = int(np.sum(rows & (labels == 1)))
        negative = int(np.sum(rows & (labels == 0)))
        if int(rows.sum()) < min_support or positive < min_positive or negative < min_positive:
            thresholds[int(direction)] = global_threshold
            details[str(direction)] = {
                "support": int(rows.sum()),
                "positive": positive,
                "negative": negative,
                "threshold": global_threshold,
                "fallback": True,
            }
        else:
            threshold, detail = select(rows)
            thresholds[int(direction)] = threshold
            detail.update(negative=negative, fallback=False)
            details[str(direction)] = detail
    return thresholds, {
        "objective": "rescue_minus_all_false_switches",
        "global": global_detail,
        "directions": details,
        "min_support": int(min_support),
        "min_positive": int(min_positive),
    }


def _inner_oof_scores(
    x: np.ndarray,
    labels: np.ndarray,
    directions: np.ndarray,
    groups: np.ndarray,
    strata: np.ndarray,
    *,
    n_folds: int,
    c_value: float,
    seed: int,
) -> np.ndarray:
    inner_folds = stratified_group_fold_ids(
        strata, groups, n_folds=n_folds, seed=seed
    )
    scores = np.full(len(labels), np.nan, dtype=np.float64)
    for fold_id in range(n_folds):
        fit_rows = inner_folds != fold_id
        val_rows = inner_folds == fold_id
        scores[val_rows] = fit_predict_logistic(
            x[fit_rows],
            labels[fit_rows],
            directions[fit_rows],
            x[val_rows],
            c_value=c_value,
            seed=seed + fold_id,
        )
    if not np.isfinite(scores).all():
        raise AssertionError("inner OOF scoring left non-finite rows")
    return scores


def fuse_residual_scores(
    baseline_scores: np.ndarray,
    relation_scores: np.ndarray,
    alpha: float,
) -> np.ndarray:
    """Add a bounded relation log-odds residual; alpha=0 is exact identity."""

    baseline = np.clip(np.asarray(baseline_scores, dtype=np.float64), 1e-6, 1 - 1e-6)
    relation = np.clip(np.asarray(relation_scores, dtype=np.float64), 1e-6, 1 - 1e-6)
    if baseline.shape != relation.shape:
        raise ValueError("baseline/relation score shapes differ")
    if alpha < 0:
        raise ValueError("relation residual alpha must be nonnegative")
    base_logit = np.log(baseline / (1.0 - baseline))
    relation_logit = np.log(relation / (1.0 - relation))
    fused_logit = base_logit + float(alpha) * relation_logit
    return 1.0 / (1.0 + np.exp(-np.clip(fused_logit, -30.0, 30.0)))


def choose_residual_alpha(
    baseline_scores: np.ndarray,
    relation_scores: np.ndarray,
    labels: np.ndarray,
    *,
    min_auc_gain: float = 0.005,
) -> tuple[float, dict]:
    """Choose relation strength on inner OOF only, with an explicit no-op."""

    grid = tuple(index / 10 for index in range(11))
    base_auc = safe_auc(labels, baseline_scores)
    if base_auc is None:
        raise ValueError("inner residual-alpha calibration has undefined baseline AUC")
    rows = []
    for alpha in grid:
        scores = fuse_residual_scores(baseline_scores, relation_scores, alpha)
        auc = safe_auc(labels, scores)
        rows.append({"alpha": alpha, "auc": auc})
    best = max(rows, key=lambda row: (float(row["auc"]), -float(row["alpha"])))
    gain = float(best["auc"]) - base_auc
    selected = float(best["alpha"]) if gain >= min_auc_gain else 0.0
    return selected, {
        "grid": rows,
        "baseline_auc": base_auc,
        "best_alpha": float(best["alpha"]),
        "best_auc": float(best["auc"]),
        "best_gain": gain,
        "min_auc_gain": float(min_auc_gain),
        "selected_alpha": selected,
        "fell_back_to_noop": selected == 0.0,
    }


def session_block_permute(
    matrix: np.ndarray,
    groups: Sequence[str] | np.ndarray,
    strata_levels: Sequence[Sequence[str] | np.ndarray],
    *,
    seed: int,
) -> tuple[np.ndarray, dict]:
    """Break row alignment with deterministic cross-session donor blocks.

    Exact pair/direction/source/turn/candidate strata are attempted first, then
    progressively coarser levels supplied by the caller.  A row is never
    donated from its own group.  Remaining singleton strata are zeroed rather
    than silently retaining their true relation.
    """

    matrix = np.asarray(matrix, dtype=np.float64)
    groups = np.asarray(groups, dtype=object)
    levels = [np.asarray(level, dtype=object) for level in strata_levels]
    if matrix.ndim != 2 or len(matrix) != len(groups):
        raise ValueError("permutation matrix/groups are not aligned")
    if any(len(level) != len(matrix) for level in levels):
        raise ValueError("permutation strata are not aligned")
    rng = random.Random(int(seed))
    output = np.zeros_like(matrix)
    assigned = np.zeros(len(matrix), dtype=bool)
    level_counts = Counter()

    for level_index, strata in enumerate(levels):
        by_stratum: dict[str, list[int]] = defaultdict(list)
        for row in np.flatnonzero(~assigned):
            by_stratum[str(strata[row])].append(int(row))
        for stratum in sorted(by_stratum):
            rows = by_stratum[stratum]
            by_group: dict[str, list[int]] = defaultdict(list)
            for row in rows:
                by_group[str(groups[row])].append(row)
            donor_groups = sorted(by_group)
            if len(donor_groups) < 2:
                continue
            rng.shuffle(donor_groups)
            offset = 1 + rng.randrange(len(donor_groups) - 1)
            donors = donor_groups[offset:] + donor_groups[:offset]
            for target_group, donor_group in zip(donor_groups, donors):
                if target_group == donor_group:
                    raise AssertionError("session permutation selected a self donor")
                target_rows = sorted(by_group[target_group])
                donor_rows = sorted(by_group[donor_group])
                for position, target_row in enumerate(target_rows):
                    output[target_row] = matrix[donor_rows[position % len(donor_rows)]]
                    assigned[target_row] = True
                    level_counts[str(level_index)] += 1
    zeroed = int((~assigned).sum())
    return output, {
        "rows": len(matrix),
        "assigned_cross_session": int(assigned.sum()),
        "zeroed_singleton": zeroed,
        "level_rows": dict(level_counts),
    }


def _structural_features(payload: Mapping) -> tuple[np.ndarray, list[str]]:
    pair_ids = torch.as_tensor(payload["pair_ids"], dtype=torch.long).numpy()
    main = torch.as_tensor(payload["main_predictions"], dtype=torch.long).numpy()
    alt = torch.as_tensor(payload["alternative_predictions"], dtype=torch.long).numpy()
    columns = []
    names = []
    for pair_id in range(len(LIVE_WEAK4_PAIRS)):
        columns.append((pair_ids == pair_id).astype(np.float64))
        names.append(f"pair_{pair_id}")
    for class_id, label in enumerate(ALL_CLASSES[:4]):
        columns.append((main == class_id).astype(np.float64))
        names.append(f"main_{label}")
        columns.append((alt == class_id).astype(np.float64))
        names.append(f"alt_{label}")
    return np.column_stack(columns), names


def _combine(named_matrices: Sequence[tuple[np.ndarray, Sequence[str]]]):
    matrices = [np.asarray(matrix, dtype=np.float64) for matrix, _ in named_matrices]
    names = [name for _, part_names in named_matrices for name in part_names]
    if len(names) != len(set(names)):
        duplicates = [name for name, count in Counter(names).items() if count > 1]
        raise ValueError(f"duplicate feature names: {duplicates[:5]}")
    return np.column_stack(matrices), names


def _feature_coverage(matrix: np.ndarray, names: Sequence[str]) -> dict:
    matrix = np.asarray(matrix)
    counts = np.sum(np.abs(matrix) > 1e-12, axis=0)
    ranked = sorted(
        (
            {"feature": str(name), "nonzero_rows": int(count), "rate": float(count / len(matrix))}
            for name, count in zip(names, counts)
        ),
        key=lambda row: (-row["nonzero_rows"], row["feature"]),
    )
    return {
        "rows": len(matrix),
        "features": len(names),
        "nonzero_features": int(np.sum(counts > 0)),
        "feature_names": list(names),
        "nonzero_coverage": ranked,
    }


def _metadata_summary(metadata: Sequence[Mapping]) -> dict:
    values: dict[str, Counter] = defaultdict(Counter)
    for row in metadata:
        for key, value in row.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                values[str(key)][str(value)] += 1
    return {key: dict(counter.most_common()) for key, counter in sorted(values.items())}


def build_feature_surfaces(payload: Mapping, samples: Sequence[Mapping]) -> dict:
    train_indices = torch.as_tensor(payload["train_indices"], dtype=torch.long)
    route_indices = torch.as_tensor(payload["route_indices"], dtype=torch.long)
    pair_ids = torch.as_tensor(payload["pair_ids"], dtype=torch.long)
    main = torch.as_tensor(payload["main_predictions"], dtype=torch.long)
    route_samples = []
    coarse_rows = []
    relation_rows = []
    relation_metadata = []
    coarse_metadata = []
    for local_row, full_row in enumerate(route_indices.tolist()):
        sample = samples[int(train_indices[full_row])]
        expected_id = safe_text(payload["route_ids"][local_row])
        if safe_text(sample.get("id")) != expected_id:
            raise ValueError(
                f"route/sample id mismatch at {local_row}: "
                f"{sample.get('id')!r} != {expected_id!r}"
            )
        route_samples.append(sample)
        coarse, coarse_meta = extract_coarse_features(
            sample, int(pair_ids[local_row]), int(main[local_row])
        )
        relation, relation_meta = extract_relation_features(
            sample, int(pair_ids[local_row]), int(main[local_row])
        )
        coarse_rows.append(coarse)
        relation_rows.append(relation)
        coarse_metadata.append(coarse_meta)
        relation_metadata.append(relation_meta)

    structural, structural_names = _structural_features(payload)
    gap = torch.as_tensor(payload["base_gaps"], dtype=torch.float32).abs().numpy()[:, None]
    numeric = torch.as_tensor(payload["numeric_features"], dtype=torch.float32).numpy()
    coarse, coarse_names = vectorize_feature_dicts(coarse_rows)
    relation, relation_names = vectorize_feature_dicts(relation_rows)
    r0 = _combine(((structural, structural_names), (gap, ("abs_pair_gap",))))
    r1 = _combine(
        (
            (structural, structural_names),
            (gap, ("abs_pair_gap",)),
            (numeric, tuple(f"main_numeric_{index}" for index in range(numeric.shape[1]))),
        )
    )
    r2 = _combine((r1, (coarse, tuple(f"coarse.{name}" for name in coarse_names))))
    r3 = _combine((r1, (relation, tuple(f"relation.{name}" for name in relation_names))))
    return {
        "route_samples": route_samples,
        "matrices": {
            "r0_gap": r0,
            "r1_main": r1,
            "r2_coarse": r2,
            "r3_relation": r3,
        },
        "relation_matrix": relation,
        "relation_names": relation_names,
        "relation_metadata": relation_metadata,
        "coarse_metadata": coarse_metadata,
        "coverage": {
            "coarse": _feature_coverage(coarse, coarse_names),
            "relation": _feature_coverage(relation, relation_names),
            "coarse_metadata": _metadata_summary(coarse_metadata),
            "relation_metadata": _metadata_summary(relation_metadata),
        },
    }


def build_surface_contracts(payload: Mapping, samples: Sequence[Mapping], n_folds: int, seed: int):
    ids = [safe_text(value) for value in payload["ids"]]
    if len(ids) != len(set(ids)):
        raise ValueError("parent surface contains duplicate ids")
    route_indices = torch.as_tensor(payload["route_indices"], dtype=torch.long).numpy()
    y_true = torch.as_tensor(payload["y_true"], dtype=torch.long).numpy()
    pair_ids = torch.as_tensor(payload["pair_ids"], dtype=torch.long).numpy()
    target_kinds = torch.as_tensor(payload["target_kinds"], dtype=torch.long).numpy()
    full_strata = y_true.copy()
    full_strata[route_indices] = 100 + pair_ids * 4 + target_kinds
    sample_ids = {safe_text(sample.get("id")) for sample in samples}
    if not set(ids).issubset(sample_ids):
        raise ValueError("parent ids are not a subset of train.jsonl ids")

    val_ids = set(ids)
    parent_train_au_scenarios = {
        scenario_group(safe_text(sample.get("id")))
        for sample in samples
        if safe_text(sample.get("id")) not in val_ids
        and safe_text(sample.get("id")).startswith("sess_au_")
    }
    exposed = np.asarray(
        [
            sample_id.startswith("sess_au_")
            and scenario_group(sample_id) in parent_train_au_scenarios
            for sample_id in ids
        ],
        dtype=bool,
    )
    strict_allowed = ~exposed
    strict_groups = np.asarray([scenario_group(sample_id) for sample_id in ids], dtype=object)
    strict_full_folds = np.full(len(ids), -1, dtype=np.int64)
    strict_full_folds[strict_allowed] = stratified_group_fold_ids(
        full_strata[strict_allowed],
        strict_groups[strict_allowed],
        n_folds=n_folds,
        seed=seed,
    )
    legacy_full_folds = torch.as_tensor(payload["full_fold_ids"], dtype=torch.long).numpy()
    if set(np.unique(legacy_full_folds).tolist()) != set(range(n_folds)):
        raise ValueError("legacy dataset fold ids do not match requested n_folds")

    return {
        "strict_scenario": {
            "description": (
                "primary: exclude fixed-parent AU scenario siblings and group outer/inner "
                "folds by AU primary scenario or ordinary session"
            ),
            "full_allowed": strict_allowed,
            "route_allowed": strict_allowed[route_indices],
            "full_folds": strict_full_folds,
            "route_folds": strict_full_folds[route_indices],
            "route_groups": strict_groups[route_indices],
            "excluded_full_rows": int(exposed.sum()),
            "excluded_route_rows": int(exposed[route_indices].sum()),
            "excluded_ids_sha256": hashlib.sha256(
                "\n".join(sorted(np.asarray(ids, dtype=object)[exposed].tolist())).encode()
            ).hexdigest(),
        },
        "legacy_session": {
            "description": "secondary: original Card-B fixed-parent session folds",
            "full_allowed": np.ones(len(ids), dtype=bool),
            "route_allowed": np.ones(len(route_indices), dtype=bool),
            "full_folds": legacy_full_folds,
            "route_folds": torch.as_tensor(payload["route_fold_ids"], dtype=torch.long).numpy(),
            "route_groups": np.asarray([session_id(payload["route_ids"][row]) for row in range(len(route_indices))], dtype=object),
            "excluded_full_rows": 0,
            "excluded_route_rows": 0,
            "excluded_ids_sha256": hashlib.sha256(b"").hexdigest(),
        },
    }


def _direction_cells(
    labels: np.ndarray,
    scores: np.ndarray,
    mask: np.ndarray,
    pair_ids: np.ndarray,
    top_sides: np.ndarray,
) -> dict:
    report = {}
    for pair_id, pair in enumerate(LIVE_WEAK4_PAIRS):
        for top_side in (0, 1):
            rows = mask & (pair_ids == pair_id) & (top_sides == top_side)
            main = pair[top_side]
            alt = pair[1 - top_side]
            key = f"{ALL_CLASSES[main]}->{ALL_CLASSES[alt]}"
            report[key] = {
                "rows": int(rows.sum()),
                "positive": int(np.sum(labels[rows] == 1)),
                "auc": safe_auc(labels[rows], scores[rows]),
            }
    return report


def score_report(
    labels: np.ndarray,
    scores: np.ndarray,
    mask: np.ndarray,
    target_kinds: np.ndarray,
    pair_ids: np.ndarray,
    top_sides: np.ndarray,
) -> dict:
    pr_mask = mask & np.isin(target_kinds, [TARGET_PROTECT, TARGET_RESCUE])
    return {
        "rows": int(mask.sum()),
        "positive": int(np.sum(labels[mask] == 1)),
        "rescue_vs_all_identity_auc": safe_auc(labels[mask], scores[mask]),
        "rescue_vs_protect_auc": safe_auc(labels[pr_mask], scores[pr_mask]),
        "direction_cells": _direction_cells(
            labels, scores, mask, pair_ids, top_sides
        ),
    }


def evaluate_predictions(
    payload: Mapping,
    contract: Mapping,
    scores: np.ndarray,
    switched: np.ndarray,
) -> dict:
    y_true = torch.as_tensor(payload["y_true"], dtype=torch.long)
    parent_logits = torch.as_tensor(payload["parent_logits"], dtype=torch.float32)
    base_pred = parent_logits.argmax(dim=1)
    prediction = base_pred.clone()
    route_indices = torch.as_tensor(payload["route_indices"], dtype=torch.long)
    alternatives = torch.as_tensor(payload["alternative_predictions"], dtype=torch.long)
    allowed = np.asarray(contract["route_allowed"], dtype=bool)
    effective_switch = np.asarray(switched, dtype=bool) & allowed
    local_switch = torch.from_numpy(np.flatnonzero(effective_switch)).long()
    prediction[route_indices[local_switch]] = alternatives[local_switch]
    full_allowed = torch.from_numpy(np.asarray(contract["full_allowed"], dtype=bool))
    base_metrics = f1_metrics(y_true[full_allowed].tolist(), base_pred[full_allowed].tolist())
    metrics = f1_metrics(y_true[full_allowed].tolist(), prediction[full_allowed].tolist())

    route_allowed = torch.from_numpy(allowed)
    routed = route_indices[route_allowed]
    route_y = y_true[routed]
    route_base = base_pred[routed]
    route_new = prediction[routed]
    changed = route_base != route_new
    rescue = int(((route_base != route_y) & (route_new == route_y)).sum())
    harm = int(((route_base == route_y) & (route_new != route_y)).sum())
    neutral = int((changed & (route_base != route_y) & (route_new != route_y)).sum())
    false_switch = int((changed & (route_new != route_y)).sum())
    per_class_delta = {
        label: metrics["per_class_f1"][label] - base_metrics["per_class_f1"][label]
        for label in ALL_CLASSES[:4]
    }
    fold_metrics = []
    full_folds = np.asarray(contract["full_folds"], dtype=np.int64)
    for fold_id in sorted(set(full_folds[full_folds >= 0].tolist())):
        mask = torch.from_numpy((full_folds == fold_id) & np.asarray(contract["full_allowed"]))
        base_fold = f1_metrics(y_true[mask].tolist(), base_pred[mask].tolist())
        new_fold = f1_metrics(y_true[mask].tolist(), prediction[mask].tolist())
        fold_metrics.append(
            {
                "fold_id": int(fold_id),
                "rows": int(mask.sum()),
                "base_macro_f1": base_fold["macro_f1"],
                "macro_f1": new_fold["macro_f1"],
                "macro_delta": new_fold["macro_f1"] - base_fold["macro_f1"],
                "base_weak4_macro_f1": weak4_macro(base_fold),
                "weak4_macro_f1": weak4_macro(new_fold),
                "weak4_delta": weak4_macro(new_fold) - weak4_macro(base_fold),
            }
        )
    return {
        "base_metrics": base_metrics,
        "metrics": metrics,
        "macro_delta": metrics["macro_f1"] - base_metrics["macro_f1"],
        "base_weak4_macro_f1": weak4_macro(base_metrics),
        "weak4_macro_f1": weak4_macro(metrics),
        "weak4_delta": weak4_macro(metrics) - weak4_macro(base_metrics),
        "per_class_delta": per_class_delta,
        "fold_metrics": fold_metrics,
        "changed": int(changed.sum()),
        "rescue": rescue,
        "harm": harm,
        "neutral_changes": neutral,
        "false_switch": false_switch,
        "rescue_harm_ratio": rescue / harm if harm else None,
        "switch_precision": rescue / int(changed.sum()) if int(changed.sum()) else None,
        "predictions": prediction,
    }


def run_surface(
    name: str,
    matrix: np.ndarray,
    payload: Mapping,
    contract: Mapping,
    *,
    n_folds: int,
    inner_folds: int,
    c_value: float,
    seed: int,
    min_threshold_support: int,
    min_threshold_positive: int,
) -> tuple[dict, dict]:
    route_allowed = np.asarray(contract["route_allowed"], dtype=bool)
    outer_folds = np.asarray(contract["route_folds"], dtype=np.int64)
    groups = np.asarray(contract["route_groups"], dtype=object)
    pair_ids = torch.as_tensor(payload["pair_ids"], dtype=torch.long).numpy()
    top_sides = torch.as_tensor(payload["top_sides"], dtype=torch.long).numpy()
    directions = pair_ids * 2 + top_sides
    target_kinds = torch.as_tensor(payload["target_kinds"], dtype=torch.long).numpy()
    switch_labels = (target_kinds == TARGET_RESCUE).astype(np.int64)
    inner_strata = directions * 4 + target_kinds
    scores = np.full(len(route_allowed), np.nan, dtype=np.float64)
    switched = np.zeros(len(route_allowed), dtype=bool)
    fold_reports = []
    threshold_reports = []
    for fold_id in range(n_folds):
        train_rows = route_allowed & (outer_folds != fold_id)
        val_rows = route_allowed & (outer_folds == fold_id)
        if not train_rows.any() or not val_rows.any():
            raise ValueError(f"{name}: empty outer fold {fold_id}")
        train_idx = np.flatnonzero(train_rows)
        val_idx = np.flatnonzero(val_rows)
        inner_scores = _inner_oof_scores(
            matrix[train_idx],
            switch_labels[train_idx],
            directions[train_idx],
            groups[train_idx],
            inner_strata[train_idx],
            n_folds=inner_folds,
            c_value=c_value,
            seed=seed + fold_id * 100,
        )
        thresholds, threshold_detail = choose_direction_thresholds(
            inner_scores,
            switch_labels[train_idx],
            directions[train_idx],
            min_support=min_threshold_support,
            min_positive=min_threshold_positive,
        )
        fold_scores = fit_predict_logistic(
            matrix[train_idx],
            switch_labels[train_idx],
            directions[train_idx],
            matrix[val_idx],
            c_value=c_value,
            seed=seed + fold_id,
        )
        scores[val_idx] = fold_scores
        cutoffs = np.asarray(
            [thresholds[int(direction)] for direction in directions[val_idx]],
            dtype=np.float64,
        )
        switched[val_idx] = fold_scores >= cutoffs
        fold_score = score_report(
            switch_labels,
            scores,
            val_rows,
            target_kinds,
            pair_ids,
            top_sides,
        )
        fold_score["fold_id"] = fold_id
        fold_reports.append(fold_score)
        threshold_detail["fold_id"] = fold_id
        threshold_reports.append(threshold_detail)
    if not np.isfinite(scores[route_allowed]).all():
        raise AssertionError(f"{name}: outer OOF scores are incomplete")
    score_metrics = score_report(
        switch_labels,
        scores,
        route_allowed,
        target_kinds,
        pair_ids,
        top_sides,
    )
    prediction_metrics = evaluate_predictions(payload, contract, scores, switched)
    prediction_tensor = prediction_metrics.pop("predictions")
    report = {
        "surface": name,
        "feature_count": int(matrix.shape[1]),
        "score_metrics": score_metrics,
        "fold_score_metrics": fold_reports,
        "threshold_calibration": threshold_reports,
        "prediction_metrics": prediction_metrics,
    }
    tensors = {
        "scores": torch.from_numpy(scores).float(),
        "switched": torch.from_numpy(switched),
        "predictions": prediction_tensor,
    }
    return report, tensors


def run_relation_residual_surface(
    baseline_matrix: np.ndarray,
    relation_matrix: np.ndarray,
    payload: Mapping,
    contract: Mapping,
    *,
    n_folds: int,
    inner_folds: int,
    c_value: float,
    seed: int,
    min_threshold_support: int,
    min_threshold_positive: int,
) -> tuple[dict, dict]:
    """Cross-fit a no-op-safe relation residual on top of the R1 selector."""

    route_allowed = np.asarray(contract["route_allowed"], dtype=bool)
    outer_folds = np.asarray(contract["route_folds"], dtype=np.int64)
    groups = np.asarray(contract["route_groups"], dtype=object)
    pair_ids = torch.as_tensor(payload["pair_ids"], dtype=torch.long).numpy()
    top_sides = torch.as_tensor(payload["top_sides"], dtype=torch.long).numpy()
    directions = pair_ids * 2 + top_sides
    target_kinds = torch.as_tensor(payload["target_kinds"], dtype=torch.long).numpy()
    switch_labels = (target_kinds == TARGET_RESCUE).astype(np.int64)
    inner_strata = directions * 4 + target_kinds
    scores = np.full(len(route_allowed), np.nan, dtype=np.float64)
    ungated_scores = np.full(len(route_allowed), np.nan, dtype=np.float64)
    switched = np.zeros(len(route_allowed), dtype=bool)
    fold_reports = []
    ungated_fold_reports = []
    threshold_reports = []
    alpha_reports = []
    for fold_id in range(n_folds):
        train_rows = route_allowed & (outer_folds != fold_id)
        val_rows = route_allowed & (outer_folds == fold_id)
        train_idx = np.flatnonzero(train_rows)
        val_idx = np.flatnonzero(val_rows)
        inner_base = _inner_oof_scores(
            baseline_matrix[train_idx],
            switch_labels[train_idx],
            directions[train_idx],
            groups[train_idx],
            inner_strata[train_idx],
            n_folds=inner_folds,
            c_value=c_value,
            seed=seed + fold_id * 100,
        )
        inner_relation = _inner_oof_scores(
            relation_matrix[train_idx],
            switch_labels[train_idx],
            directions[train_idx],
            groups[train_idx],
            inner_strata[train_idx],
            n_folds=inner_folds,
            c_value=c_value,
            seed=seed + fold_id * 100 + 50,
        )
        alpha, alpha_detail = choose_residual_alpha(
            inner_base, inner_relation, switch_labels[train_idx]
        )
        inner_scores = fuse_residual_scores(inner_base, inner_relation, alpha)
        thresholds, threshold_detail = choose_direction_thresholds(
            inner_scores,
            switch_labels[train_idx],
            directions[train_idx],
            min_support=min_threshold_support,
            min_positive=min_threshold_positive,
        )
        outer_base = fit_predict_logistic(
            baseline_matrix[train_idx],
            switch_labels[train_idx],
            directions[train_idx],
            baseline_matrix[val_idx],
            c_value=c_value,
            seed=seed + fold_id,
        )
        best_alpha = float(alpha_detail["best_alpha"])
        if alpha == 0.0 and best_alpha == 0.0:
            outer_relation = None
        else:
            outer_relation = fit_predict_logistic(
                relation_matrix[train_idx],
                switch_labels[train_idx],
                directions[train_idx],
                relation_matrix[val_idx],
                c_value=c_value,
                seed=seed + fold_id + 50,
            )
        if alpha == 0.0:
            outer_scores = outer_base
        else:
            assert outer_relation is not None
            outer_scores = fuse_residual_scores(outer_base, outer_relation, alpha)
        ungated_outer = (
            outer_base
            if best_alpha == 0.0
            else fuse_residual_scores(outer_base, outer_relation, best_alpha)
        )
        scores[val_idx] = outer_scores
        ungated_scores[val_idx] = ungated_outer
        cutoffs = np.asarray(
            [thresholds[int(direction)] for direction in directions[val_idx]],
            dtype=np.float64,
        )
        switched[val_idx] = outer_scores >= cutoffs
        fold_score = score_report(
            switch_labels,
            scores,
            val_rows,
            target_kinds,
            pair_ids,
            top_sides,
        )
        fold_score["fold_id"] = fold_id
        fold_reports.append(fold_score)
        ungated_fold = score_report(
            switch_labels,
            ungated_scores,
            val_rows,
            target_kinds,
            pair_ids,
            top_sides,
        )
        ungated_fold["fold_id"] = fold_id
        ungated_fold_reports.append(ungated_fold)
        threshold_detail["fold_id"] = fold_id
        threshold_reports.append(threshold_detail)
        alpha_detail["fold_id"] = fold_id
        alpha_reports.append(alpha_detail)
    if not np.isfinite(scores[route_allowed]).all():
        raise AssertionError("r3_relation: outer OOF scores are incomplete")
    if not np.isfinite(ungated_scores[route_allowed]).all():
        raise AssertionError("r3_relation: ungated diagnostic scores are incomplete")
    score_metrics = score_report(
        switch_labels,
        scores,
        route_allowed,
        target_kinds,
        pair_ids,
        top_sides,
    )
    prediction_metrics = evaluate_predictions(payload, contract, scores, switched)
    prediction_tensor = prediction_metrics.pop("predictions")
    ungated_score_metrics = score_report(
        switch_labels,
        ungated_scores,
        route_allowed,
        target_kinds,
        pair_ids,
        top_sides,
    )
    return {
        "surface": "r4_relation_residual",
        "contract": "R1 baseline log-odds plus inner-OOF-selected relation residual",
        "feature_count": int(baseline_matrix.shape[1] + relation_matrix.shape[1]),
        "score_metrics": score_metrics,
        "fold_score_metrics": fold_reports,
        "ungated_score_metrics": ungated_score_metrics,
        "ungated_fold_score_metrics": ungated_fold_reports,
        "alpha_calibration": alpha_reports,
        "threshold_calibration": threshold_reports,
        "prediction_metrics": prediction_metrics,
    }, {
        "scores": torch.from_numpy(scores).float(),
        "ungated_scores": torch.from_numpy(ungated_scores).float(),
        "switched": torch.from_numpy(switched),
        "predictions": prediction_tensor,
    }


def permutation_null(
    baseline: np.ndarray,
    relation: np.ndarray,
    payload: Mapping,
    contract: Mapping,
    route_samples: Sequence[Mapping],
    metadata: Sequence[Mapping],
    *,
    repeats: int,
    c_value: float,
    seed: int,
) -> dict:
    if repeats <= 0:
        return {"repeats": 0}
    route_allowed = np.asarray(contract["route_allowed"], dtype=bool)
    outer_folds = np.asarray(contract["route_folds"], dtype=np.int64)
    groups = np.asarray(contract["route_groups"], dtype=object)
    pair_ids = torch.as_tensor(payload["pair_ids"], dtype=torch.long).numpy()
    top_sides = torch.as_tensor(payload["top_sides"], dtype=torch.long).numpy()
    directions = pair_ids * 2 + top_sides
    target_kinds = torch.as_tensor(payload["target_kinds"], dtype=torch.long).numpy()
    labels = (target_kinds == TARGET_RESCUE).astype(np.int64)

    source = np.asarray([source_name(sample.get("id")) for sample in route_samples], dtype=object)
    turns = np.asarray(
        [
            safe_text(meta.get("turn_bin")) or turn_bin((sample.get("session_meta") or {}).get("turn_index"))
            for sample, meta in zip(route_samples, metadata)
        ],
        dtype=object,
    )
    candidate = np.asarray(
        [safe_text(meta.get("candidate_count_bin") or "unknown") for meta in metadata],
        dtype=object,
    )
    exact = np.asarray(
        [
            f"{pair_ids[row]}:{top_sides[row]}:{source[row]}:{turns[row]}:{candidate[row]}"
            for row in range(len(labels))
        ],
        dtype=object,
    )
    middle = np.asarray(
        [
            f"{pair_ids[row]}:{top_sides[row]}:{source[row]}:{turns[row]}"
            for row in range(len(labels))
        ],
        dtype=object,
    )
    broad = np.asarray(
        [
            f"{pair_ids[row]}:{top_sides[row]}:{source[row]}"
            for row in range(len(labels))
        ],
        dtype=object,
    )
    auc_values = []
    fold_auc_values = []
    permutation_audits = []
    for repeat in range(repeats):
        scores = np.full(len(labels), np.nan, dtype=np.float64)
        repeat_audit = {"repeat": repeat, "partitions": []}
        for fold_id in sorted(set(outer_folds[route_allowed].tolist())):
            train_idx = np.flatnonzero(route_allowed & (outer_folds != fold_id))
            val_idx = np.flatnonzero(route_allowed & (outer_folds == fold_id))
            perm_train, train_audit = session_block_permute(
                relation[train_idx],
                groups[train_idx],
                (exact[train_idx], middle[train_idx], broad[train_idx]),
                seed=seed + repeat * 1000 + fold_id * 2,
            )
            perm_val, val_audit = session_block_permute(
                relation[val_idx],
                groups[val_idx],
                (exact[val_idx], middle[val_idx], broad[val_idx]),
                seed=seed + repeat * 1000 + fold_id * 2 + 1,
            )
            scores[val_idx] = fit_predict_logistic(
                np.column_stack((baseline[train_idx], perm_train)),
                labels[train_idx],
                directions[train_idx],
                np.column_stack((baseline[val_idx], perm_val)),
                c_value=c_value,
                seed=seed + repeat * 100 + fold_id,
            )
            repeat_audit["partitions"].append(
                {"fold_id": fold_id, "train": train_audit, "validation": val_audit}
            )
        pooled = safe_auc(labels[route_allowed], scores[route_allowed])
        folds = [
            safe_auc(labels[route_allowed & (outer_folds == fold_id)], scores[route_allowed & (outer_folds == fold_id)])
            for fold_id in sorted(set(outer_folds[route_allowed].tolist()))
        ]
        auc_values.append(pooled)
        fold_auc_values.append(folds)
        permutation_audits.append(repeat_audit)
    finite = np.asarray([value for value in auc_values if value is not None], dtype=np.float64)
    return {
        "repeats": repeats,
        "pooled_auc": auc_values,
        "fold_auc": fold_auc_values,
        "mean": float(finite.mean()) if len(finite) else None,
        "p95": float(np.quantile(finite, 0.95)) if len(finite) else None,
        "max": float(finite.max()) if len(finite) else None,
        "permutation_audits": permutation_audits,
    }


def promotion_gate(strict_report: Mapping, permutation: Mapping) -> dict:
    main = strict_report["surfaces"]["r1_main"]
    relation = strict_report["surfaces"]["r3_relation"]
    main_folds = main["fold_score_metrics"]
    relation_folds = relation["fold_score_metrics"]
    incremental = []
    for base, candidate in zip(main_folds, relation_folds):
        base_auc = base["rescue_vs_all_identity_auc"]
        candidate_auc = candidate["rescue_vs_all_identity_auc"]
        incremental.append(
            None if base_auc is None or candidate_auc is None else candidate_auc - base_auc
        )
    prediction = relation["prediction_metrics"]
    per_class = prediction["per_class_delta"]
    relation_auc = relation["score_metrics"]["rescue_vs_all_identity_auc"]
    null_p95 = permutation.get("p95")
    checks = {
        "all_fold_incremental_auc_positive": all(
            value is not None and value > 0 for value in incremental
        ),
        "macro_delta_at_least_0.002": prediction["macro_delta"] >= 0.002,
        "all_macro_folds_nonnegative": all(
            fold["macro_delta"] >= 0 for fold in prediction["fold_metrics"]
        ),
        "rescue_harm_ratio_at_least_1.5": (
            prediction["rescue"] > 0
            if prediction["harm"] == 0
            else prediction["rescue"] / prediction["harm"] >= 1.5
        ),
        "at_least_30_changes": prediction["changed"] >= 30,
        "no_weak4_drop_below_minus_0.003": all(
            value >= -0.003 for value in per_class.values()
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "fold_incremental_auc_r3_minus_r1": incremental,
        "r3_auc": relation_auc,
        "permutation_p95": null_p95,
        "permutation_diagnostic": {
            "hard_gate": False,
            "reason": "negative control only; finite repeats do not define promotion",
            "actual_minus_null_mean": (
                None
                if relation_auc is None or permutation.get("mean") is None
                else relation_auc - permutation["mean"]
            ),
            "actual_beats_null_p95": (
                None
                if relation_auc is None or null_p95 is None
                else relation_auc > null_p95
            ),
        },
        "next_step_if_passed": (
            "export a matching clean non-KD HCX hidden cache and run frozen-decoder R5"
        ),
        "next_step_if_failed": (
            "close deterministic relation-graph selector; do not train decoder or c_clean"
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--surface", choices=["both", "strict", "legacy"], default="both")
    parser.add_argument("--n-folds", type=int, default=3)
    parser.add_argument("--inner-folds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--logistic-c", type=float, default=0.1)
    parser.add_argument(
        "--permutations",
        type=int,
        default=200,
        help="diagnostic session-block feature permutations; not a hard gate",
    )
    parser.add_argument("--min-threshold-support", type=int, default=40)
    parser.add_argument("--min-threshold-positive", type=int, default=5)
    parser.add_argument("--experiment-id", default="20260713_weak4_relation_graph_v1")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--scores-output", default=DEFAULT_SCORES)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--append-results", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    if args.n_folds != 3:
        raise ValueError("the preregistered outer contract requires exactly 3 folds")
    if args.inner_folds < 2:
        raise ValueError("--inner-folds must be at least 2")
    if args.logistic_c <= 0:
        raise ValueError("--logistic-c must be positive")
    output_path = Path(args.output)
    scores_path = Path(args.scores_output)
    for path in (output_path, scores_path):
        if path.exists() and not args.overwrite:
            raise FileExistsError(f"refusing to overwrite {path}; pass --overwrite")

    dataset_path = Path(args.dataset)
    payload = torch_load(dataset_path)
    scope = validate_dataset(payload, allow_diagnostic=False)
    if scope != "clean":
        raise ValueError("relation probe accepts only the clean Card-B parent surface")
    samples = load_jsonl(Path(args.data_dir) / "train.jsonl")
    feature_payload = build_feature_surfaces(payload, samples)
    contracts = build_surface_contracts(payload, samples, args.n_folds, args.seed)

    selected_contracts = (
        ("strict_scenario",)
        if args.surface == "strict"
        else ("legacy_session",)
        if args.surface == "legacy"
        else ("strict_scenario", "legacy_session")
    )
    surface_reports = {}
    score_payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": ARTIFACT_KIND + "_scores",
        "experiment_id": args.experiment_id,
        "dataset_sha256": sha256_file(dataset_path),
        "contracts": {},
    }
    for contract_name in selected_contracts:
        contract = contracts[contract_name]
        reports = {}
        tensors = {}
        for surface_name in SURFACE_ORDER:
            if surface_name == "r4_relation_residual":
                baseline, baseline_names = feature_payload["matrices"]["r1_main"]
                relation = feature_payload["relation_matrix"]
                names = [
                    *baseline_names,
                    *(f"relation.{name}" for name in feature_payload["relation_names"]),
                ]
                report, tensor_payload = run_relation_residual_surface(
                    baseline,
                    relation,
                    payload,
                    contract,
                    n_folds=args.n_folds,
                    inner_folds=args.inner_folds,
                    c_value=args.logistic_c,
                    seed=args.seed,
                    min_threshold_support=args.min_threshold_support,
                    min_threshold_positive=args.min_threshold_positive,
                )
                report["surface"] = surface_name
            else:
                matrix, names = feature_payload["matrices"][surface_name]
                report, tensor_payload = run_surface(
                    surface_name,
                    matrix,
                    payload,
                    contract,
                    n_folds=args.n_folds,
                    inner_folds=args.inner_folds,
                    c_value=args.logistic_c,
                    seed=args.seed,
                    min_threshold_support=args.min_threshold_support,
                    min_threshold_positive=args.min_threshold_positive,
                )
            report["feature_names"] = list(names)
            reports[surface_name] = report
            tensors[surface_name] = tensor_payload
            print(
                f"{contract_name}/{surface_name}: "
                f"auc={report['score_metrics']['rescue_vs_all_identity_auc']} "
                f"macro_delta={report['prediction_metrics']['macro_delta']:+.6f}"
            )

        permutation = {"repeats": 0}
        if contract_name == "strict_scenario" and args.permutations:
            baseline, _ = feature_payload["matrices"]["r1_main"]
            permutation = permutation_null(
                baseline,
                feature_payload["relation_matrix"],
                payload,
                contract,
                feature_payload["route_samples"],
                feature_payload["relation_metadata"],
                repeats=args.permutations,
                c_value=args.logistic_c,
                seed=args.seed + 10000,
            )
        contract_report = {
            key: value
            for key, value in contract.items()
            if key not in {"full_allowed", "route_allowed", "full_folds", "route_folds", "route_groups"}
        }
        contract_report.update(
            full_rows=int(np.sum(contract["full_allowed"])),
            route_rows=int(np.sum(contract["route_allowed"])),
            surfaces=reports,
            relation_permutation_null=permutation,
        )
        surface_reports[contract_name] = contract_report
        score_payload["contracts"][contract_name] = {
            "full_allowed": torch.from_numpy(np.asarray(contract["full_allowed"])),
            "route_allowed": torch.from_numpy(np.asarray(contract["route_allowed"])),
            "full_folds": torch.from_numpy(np.asarray(contract["full_folds"])),
            "route_folds": torch.from_numpy(np.asarray(contract["route_folds"])),
            "surfaces": tensors,
        }

    gate = None
    if "strict_scenario" in surface_reports:
        gate = promotion_gate(
            surface_reports["strict_scenario"],
            surface_reports["strict_scenario"]["relation_permutation_null"],
        )
    runtime = time.perf_counter() - started
    command = shlex.join(sys.argv)
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": ARTIFACT_KIND,
        "experiment_id": args.experiment_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "hypothesis": (
                "deterministic exact entity/candidate relations add selector signal "
                "beyond the fixed parent gap and existing numeric state"
            ),
            "unchanged": [
                "clean non-KD HCX fixed parent raw logits",
                "live pair route",
                "all-rescue protect/outside targets",
                "14-class parent predictions outside selected switches",
            ],
            "excluded": [
                "c_clean and full-data consensus",
                "mBERT or decoder training",
                "mismatched KD hidden cache",
                "script.py/package/Public integration",
            ],
            "model": {
                "type": "fold-local standardized L2 logistic regression",
                "c": args.logistic_c,
                "balanced": "switch/identity x directed pair",
                "outer_folds": args.n_folds,
                "inner_folds": args.inner_folds,
                "threshold_objective": "rescue minus every false switch",
            },
        },
        "dataset": {
            "path": str(dataset_path),
            "sha256": sha256_file(dataset_path),
            "usage_scope": payload.get("usage_scope"),
            "validation_scope": payload.get("validation_scope"),
            "rows": len(payload["ids"]),
            "route_rows": len(payload["route_ids"]),
            "train_jsonl": str(Path(args.data_dir) / "train.jsonl"),
            "train_jsonl_sha256": sha256_file(Path(args.data_dir) / "train.jsonl"),
        },
        "feature_coverage": feature_payload["coverage"],
        "surfaces": surface_reports,
        "promotion_gate": gate,
        "runtime_sec": runtime,
        "train_command": command,
        "git_sha": git_sha(),
        "scores_path": str(scores_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scores_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(score_payload, scores_path)
    report["scores_sha256"] = sha256_file(scores_path)
    output_path.write_text(
        json.dumps(json_safe(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.append_results:
        if "strict_scenario" not in surface_reports:
            raise ValueError("--append-results requires the strict primary surface")
        strict = surface_reports["strict_scenario"]
        relation = strict["surfaces"]["r3_relation"]
        decision = "pass relation gate" if gate and gate["passed"] else "reject relation gate"
        append_results_csv(
            Path("experiments/results.csv"),
            {
                "experiment_id": args.experiment_id,
                "model_family": "sklearn_weak4_relation_graph_probe",
                "base_model": payload.get("parent_base_model"),
                "features": "main numeric + deterministic exact full-history relation graph",
                "serializer_name": "weak4_relation_graph_v1",
                "split_type": "clean_fixed_parent_strict_scenario_cv",
                "seed": args.seed,
                "fold_id": "cv",
                "macro_f1_raw": relation["prediction_metrics"]["base_metrics"]["macro_f1"],
                "macro_f1": relation["prediction_metrics"]["metrics"]["macro_f1"],
                "artifact_path": str(output_path),
                "val_logits_path": str(scores_path),
                "runtime_sec": runtime,
                "train_command": command,
                "notes": (
                    f"relation_auc={relation['score_metrics']['rescue_vs_all_identity_auc']}; "
                    f"permutation_p95={strict['relation_permutation_null'].get('p95')}; "
                    f"macro_delta={relation['prediction_metrics']['macro_delta']:+.6f}; "
                    f"strict_route_rows={strict['route_rows']}"
                ),
                "decision": decision,
            },
        )
    print(json.dumps(json_safe({"output": str(output_path), "gate": gate}), indent=2))


if __name__ == "__main__":
    main()
