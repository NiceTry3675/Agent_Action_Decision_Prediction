#!/usr/bin/env python3
"""Leak-free diagnostics for the Card-B live-pair residual specialist.

The audit consumes only held-out fold ``raw_delta`` payloads. It never loads a
trained checkpoint and never fits a selector on the evaluated labels. Curves
are diagnostic surfaces, not tuned deployment parameters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import torch

from script import ALL_CLASSES
from train import f1_metrics
from weak4_pair_residual import (
    CLEAN_USAGE_SCOPE,
    CLEAN_VALIDATION_SCOPE,
    DATASET_KIND,
    LIVE_WEAK4_PAIRS,
    TARGET_NAMES,
    TARGET_NOISY_ALT,
    TARGET_OUTSIDE,
    TARGET_PROTECT,
    TARGET_RESCUE,
    apply_pair_residual,
    bounded_delta,
    pair_residual_loss,
    validate_dataset_scope,
)


AUDIT_SCHEMA_VERSION = 1
AUDIT_KIND = "weak4_pair_residual_leak_free_diagnostics"
DEFAULT_DATASET = "experiments/artifacts/20260712_weak4_pair_fixed_parent_dataset.pt"
DEFAULT_BASE_FOLDS = [
    f"experiments/logits/20260712_weak4_pair_hcx05b_fold{fold}_val_pair_delta.pt"
    for fold in range(3)
]
DEFAULT_HEAD_FOLDS = [
    f"experiments/logits/20260712_weak4_pair_hcx05b_head1e3_fold{fold}_val_pair_delta.pt"
    for fold in range(3)
]
DEFAULT_OUTPUT = (
    "experiments/artifacts/"
    "20260713_weak4_pair_residual_diagnostics.json"
)
DEFAULT_SCALES = (
    -128.0, -64.0, -32.0, -16.0, -8.0, -4.0, -2.0, -1.0, -0.5, -0.25,
    0.0,
    0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0,
)
RECIPE_KEYS = {
    "model_schema", "feature_schema", "base_model", "serializer_name",
    "seed", "max_length", "epochs", "encoder_lr", "head_lr",
    "batch_size", "eval_batch_size", "weight_decay", "warmup_ratio",
    "grad_clip", "delta_max", "fusion_dim", "dropout",
    "gradient_checkpointing",
}


def torch_load(path: str | Path):
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def validate_clean_dataset(dataset: Mapping) -> None:
    if dataset.get("kind") != DATASET_KIND:
        raise ValueError(f"unexpected dataset kind: {dataset.get('kind')!r}")
    if list(dataset.get("classes") or []) != ALL_CLASSES:
        raise ValueError("dataset classes do not match ALL_CLASSES")
    if [tuple(pair) for pair in dataset.get("live_pairs") or []] != list(LIVE_WEAK4_PAIRS):
        raise ValueError("dataset live-pair order is invalid")
    if validate_dataset_scope(dataset) != "clean":
        raise ValueError("audit requires the clean fixed-parent scope")
    if dataset.get("usage_scope") != CLEAN_USAGE_SCOPE:
        raise ValueError("audit refuses a non-clean usage_scope")
    if dataset.get("validation_scope") != CLEAN_VALIDATION_SCOPE:
        raise ValueError("audit refuses a non-clean validation_scope")
    correct_counts = torch.as_tensor(dataset.get("correct_counts"), dtype=torch.long)
    target_kinds = torch.as_tensor(dataset.get("target_kinds"), dtype=torch.long)
    if dataset.get("reliability_source") != "none":
        raise ValueError("clean audit forbids consensus/c reliability conditioning")
    if not len(correct_counts) or not bool((correct_counts == 1).all()):
        raise ValueError("clean dataset correct_counts must be the constant placeholder 1")
    if bool((target_kinds == TARGET_NOISY_ALT).any()):
        raise ValueError("clean dataset must contain zero noisy_alt rows")


def route_subset(dataset: Mapping, local_rows: torch.Tensor) -> dict[str, torch.Tensor]:
    rows = torch.as_tensor(local_rows, dtype=torch.long).view(-1)
    return {
        "indices": torch.as_tensor(dataset["route_indices"], dtype=torch.long)[rows],
        "pair_ids": torch.as_tensor(dataset["pair_ids"], dtype=torch.long)[rows],
        "pair_classes": torch.as_tensor(dataset["pair_classes"], dtype=torch.long)[rows],
        "top_sides": torch.as_tensor(dataset["top_sides"], dtype=torch.long)[rows],
        "base_gaps": torch.as_tensor(dataset["base_gaps"], dtype=torch.float32)[rows],
        "main_predictions": torch.as_tensor(dataset["main_predictions"], dtype=torch.long)[rows],
        "alternative_predictions": torch.as_tensor(
            dataset["alternative_predictions"], dtype=torch.long
        )[rows],
    }


def load_delta_surface(
    dataset: Mapping,
    fold_paths: Sequence[str | Path],
    *,
    dataset_sha256: str,
    expected_folds: Sequence[int],
    name: str,
) -> dict:
    """ID/fold/provenance join of held-out residual predictions."""

    validate_clean_dataset(dataset)
    expected_fold_set = {int(fold) for fold in expected_folds}
    if not expected_fold_set:
        raise ValueError("expected_folds cannot be empty")
    route_fold_ids = torch.as_tensor(dataset["route_fold_ids"], dtype=torch.long)
    expected_rows = (
        torch.stack([route_fold_ids == fold for fold in sorted(expected_fold_set)])
        .any(dim=0)
        .nonzero(as_tuple=False)
        .view(-1)
    )
    seen = torch.zeros(len(route_fold_ids), dtype=torch.bool)
    raw_by_row = torch.empty(len(route_fold_ids), dtype=torch.float32)
    fold_by_row = torch.full((len(route_fold_ids),), -1, dtype=torch.long)
    canonical_recipe = None
    source_rows = []
    seen_folds = set()
    for raw_path in fold_paths:
        path = Path(raw_path)
        payload = torch_load(path)
        if payload.get("kind") != "weak4_pair_residual_fold_delta":
            raise ValueError(f"{path}: unexpected fold payload kind")
        if payload.get("dataset_sha256") != dataset_sha256:
            raise ValueError(f"{path}: dataset SHA mismatch")
        if payload.get("usage_scope") != dataset.get("usage_scope") or payload.get(
            "validation_scope"
        ) != dataset.get("validation_scope"):
            raise ValueError(f"{path}: fold scope differs from dataset")
        fold_id = int(payload.get("fold_id", -1))
        if fold_id not in expected_fold_set or fold_id in seen_folds:
            raise ValueError(f"{path}: duplicate or unexpected fold {fold_id}")
        seen_folds.add(fold_id)
        recipe = payload.get("recipe")
        if not isinstance(recipe, dict) or set(recipe) != RECIPE_KEYS:
            raise ValueError(f"{path}: missing or invalid canonical recipe")
        if canonical_recipe is None:
            canonical_recipe = dict(recipe)
        elif recipe != canonical_recipe:
            raise ValueError(f"{path}: recipe differs across the surface")
        rows = torch.as_tensor(payload.get("route_local_indices"), dtype=torch.long).view(-1)
        raw_delta = torch.as_tensor(payload.get("raw_delta"), dtype=torch.float32).view(-1)
        stored_delta = torch.as_tensor(payload.get("delta"), dtype=torch.float32).view(-1)
        ids = list(payload.get("ids") or [])
        if not (len(rows) == len(raw_delta) == len(stored_delta) == len(ids)):
            raise ValueError(f"{path}: row/raw/delta/id length mismatch")
        if len(rows) and (int(rows.min()) < 0 or int(rows.max()) >= len(route_fold_ids)):
            raise ValueError(f"{path}: route row out of range")
        if bool(seen[rows].any()):
            raise ValueError(f"{path}: overlapping route rows")
        if not bool((route_fold_ids[rows] == fold_id).all()):
            raise ValueError(f"{path}: row crosses its declared held-out fold")
        expected_ids = [dataset["route_ids"][row] for row in rows.tolist()]
        if ids != expected_ids:
            raise ValueError(f"{path}: IDs are not aligned to route_local_indices")
        recomputed = bounded_delta(raw_delta, float(recipe["delta_max"])).cpu()
        if not torch.allclose(recomputed, stored_delta, atol=1e-6, rtol=1e-6):
            raise ValueError(f"{path}: stored bounded delta does not match raw_delta")
        seen[rows] = True
        raw_by_row[rows] = raw_delta
        fold_by_row[rows] = fold_id
        source_rows.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "fold_id": fold_id,
                "rows": len(rows),
                "experiment_id": payload.get("experiment_id"),
            }
        )
    actual_rows = seen.nonzero(as_tuple=False).view(-1)
    if seen_folds != expected_fold_set:
        raise ValueError(f"missing folds: {sorted(expected_fold_set - seen_folds)}")
    if not torch.equal(actual_rows, expected_rows):
        missing = sorted(set(expected_rows.tolist()) - set(actual_rows.tolist()))[:5]
        extra = sorted(set(actual_rows.tolist()) - set(expected_rows.tolist()))[:5]
        raise ValueError(f"surface coverage mismatch: missing={missing} extra={extra}")
    rows = actual_rows
    raw = raw_by_row[rows]
    delta = bounded_delta(raw, float(canonical_recipe["delta_max"])).cpu()
    return {
        "name": name,
        "route_local_indices": rows,
        "raw_delta": raw,
        "delta": delta,
        "fold_ids": fold_by_row[rows],
        "expected_folds": sorted(expected_fold_set),
        "recipe": canonical_recipe,
        "sources": source_rows,
    }


def average_ranks(scores: Sequence[float] | torch.Tensor) -> torch.Tensor:
    values = torch.as_tensor(scores, dtype=torch.float64).view(-1)
    if not torch.isfinite(values).all():
        raise ValueError("AUC scores contain non-finite values")
    order = sorted(range(len(values)), key=lambda idx: (float(values[idx]), idx))
    ranks = torch.empty(len(values), dtype=torch.float64)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        value = float(values[order[cursor]])
        while end < len(order) and float(values[order[end]]) == value:
            end += 1
        average = ((cursor + 1) + end) / 2.0
        for position in range(cursor, end):
            ranks[order[position]] = average
        cursor = end
    return ranks


def auc_from_ranks(labels: Sequence[int] | torch.Tensor, ranks: torch.Tensor) -> float | None:
    y = torch.as_tensor(labels, dtype=torch.long).view(-1)
    if len(y) != len(ranks) or not bool(((y == 0) | (y == 1)).all()):
        raise ValueError("AUC labels must be a binary vector aligned to ranks")
    positives = y == 1
    n_pos = int(positives.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    rank_sum = float(ranks[positives].sum())
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def binary_auc(labels: Sequence[int] | torch.Tensor, scores: Sequence[float] | torch.Tensor) -> float | None:
    return auc_from_ranks(labels, average_ranks(scores))


def auc_cell(labels: torch.Tensor, scores: torch.Tensor, mask: torch.Tensor) -> dict:
    mask = torch.as_tensor(mask, dtype=torch.bool)
    selected_labels = labels[mask]
    selected_scores = scores[mask]
    positives = int((selected_labels == 1).sum())
    negatives = int((selected_labels == 0).sum())
    return {
        "rows": len(selected_labels),
        "rescue": positives,
        "protect": negatives,
        "auc": binary_auc(selected_labels, selected_scores),
    }


def auc_comparison_cell(
    labels: torch.Tensor,
    baseline_scores: torch.Tensor,
    adjusted_scores: torch.Tensor,
    mask: torch.Tensor,
) -> dict:
    baseline = auc_cell(labels, baseline_scores, mask)
    adjusted = auc_cell(labels, adjusted_scores, mask)
    baseline_auc, adjusted_auc = baseline["auc"], adjusted["auc"]
    return {
        "rows": baseline["rows"],
        "rescue": baseline["rescue"],
        "protect": baseline["protect"],
        "baseline_gap_auc": baseline_auc,
        "gap_plus_delta_auc": adjusted_auc,
        "auc_delta": (
            adjusted_auc - baseline_auc
            if baseline_auc is not None and adjusted_auc is not None
            else None
        ),
    }


def grouped_cells(
    labels: torch.Tensor,
    scores: torch.Tensor,
    eligible: torch.Tensor,
    group_values: Sequence[str],
) -> dict[str, dict]:
    groups = sorted(set(group_values))
    return {
        group: auc_cell(
            labels,
            scores,
            eligible & torch.tensor([value == group for value in group_values]),
        )
        for group in groups
    }


def grouped_comparisons(
    labels: torch.Tensor,
    baseline_scores: torch.Tensor,
    adjusted_scores: torch.Tensor,
    eligible: torch.Tensor,
    group_values: Sequence[str],
) -> dict[str, dict]:
    groups = sorted(set(group_values))
    return {
        group: auc_comparison_cell(
            labels,
            baseline_scores,
            adjusted_scores,
            eligible & torch.tensor([value == group for value in group_values]),
        )
        for group in groups
    }


def conditional_auc_cell(
    labels: torch.Tensor,
    scores: torch.Tensor,
    eligible: torch.Tensor,
    group_values: Sequence[str],
) -> dict:
    """Pair-count-weighted within-stratum U-AUC, excluding one-class strata."""

    numerator = 0.0
    denominator = 0
    used = 0
    skipped = 0
    for group in sorted(set(group_values)):
        mask = eligible & torch.tensor([value == group for value in group_values])
        cell = auc_cell(labels, scores, mask)
        pair_count = cell["rescue"] * cell["protect"]
        if cell["auc"] is None or pair_count == 0:
            skipped += 1
            continue
        numerator += cell["auc"] * pair_count
        denominator += pair_count
        used += 1
    return {
        "auc": numerator / denominator if denominator else None,
        "comparable_rescue_protect_pairs": denominator,
        "used_strata": used,
        "skipped_one_class_strata": skipped,
    }


def conditional_auc_comparison(
    labels: torch.Tensor,
    baseline_scores: torch.Tensor,
    adjusted_scores: torch.Tensor,
    eligible: torch.Tensor,
    group_values: Sequence[str],
) -> dict:
    baseline = conditional_auc_cell(labels, baseline_scores, eligible, group_values)
    adjusted = conditional_auc_cell(labels, adjusted_scores, eligible, group_values)
    return {
        "baseline_gap_auc": baseline["auc"],
        "gap_plus_delta_auc": adjusted["auc"],
        "auc_delta": (
            adjusted["auc"] - baseline["auc"]
            if baseline["auc"] is not None and adjusted["auc"] is not None
            else None
        ),
        "comparable_rescue_protect_pairs": baseline["comparable_rescue_protect_pairs"],
        "used_strata": baseline["used_strata"],
        "skipped_one_class_strata": baseline["skipped_one_class_strata"],
    }


def surface_arrays(dataset: Mapping, surface: Mapping) -> dict:
    local = torch.as_tensor(surface["route_local_indices"], dtype=torch.long)
    pair_ids = torch.as_tensor(dataset["pair_ids"], dtype=torch.long)[local]
    pairs = torch.as_tensor(dataset["pair_classes"], dtype=torch.long)[local]
    main = torch.as_tensor(dataset["main_predictions"], dtype=torch.long)[local]
    alt = torch.as_tensor(dataset["alternative_predictions"], dtype=torch.long)[local]
    top_sides = torch.as_tensor(dataset["top_sides"], dtype=torch.long)[local]
    base_gaps = torch.as_tensor(dataset["base_gaps"], dtype=torch.float32)[local]
    target_kinds = torch.as_tensor(dataset["target_kinds"], dtype=torch.long)[local]
    fold_ids = torch.as_tensor(dataset["route_fold_ids"], dtype=torch.long)[local]
    raw = torch.as_tensor(surface["raw_delta"], dtype=torch.float32)
    delta = torch.as_tensor(surface["delta"], dtype=torch.float32)
    if not (len(local) == len(raw) == len(delta)):
        raise ValueError("surface route rows and residuals are misaligned")
    expected_main = torch.where(top_sides == 0, pairs[:, 0], pairs[:, 1])
    if not torch.equal(main, expected_main):
        raise ValueError("top_sides disagree with main_predictions")
    if bool(((top_sides == 0) & (base_gaps < -1e-6)).any()) or bool(
        ((top_sides == 1) & (base_gaps > 1e-6)).any()
    ):
        raise ValueError("base gap sign disagrees with main pair direction")
    correction_factor = torch.where(
        top_sides == 0,
        torch.full_like(base_gaps, -1.0),
        torch.ones_like(base_gaps),
    )
    pair_names = [
        f"{ALL_CLASSES[int(left)]}<>{ALL_CLASSES[int(right)]}"
        for left, right in pairs.tolist()
    ]
    direction_names = [
        f"{ALL_CLASSES[int(source)]}->{ALL_CLASSES[int(target)]}"
        for source, target in zip(main.tolist(), alt.tolist())
    ]
    fold_names = [f"fold{int(fold)}" for fold in fold_ids.tolist()]
    pair_direction = [
        f"{pair}|{direction}"
        for pair, direction in zip(pair_names, direction_names)
    ]
    combined = [
        f"{pair}|{direction}|{fold}"
        for pair, direction, fold in zip(pair_names, direction_names, fold_names)
    ]
    labels = torch.full((len(local),), -1, dtype=torch.long)
    labels[target_kinds == TARGET_PROTECT] = 0
    labels[target_kinds == TARGET_RESCUE] = 1
    eligible = labels >= 0
    correction_raw = raw * correction_factor
    correction_delta = delta * correction_factor
    baseline_score = -base_gaps.abs()
    adjusted_score = baseline_score + correction_delta
    return {
        "local": local,
        "pair_ids": pair_ids,
        "pairs": pairs,
        "main": main,
        "alt": alt,
        "top_sides": top_sides,
        "base_gaps": base_gaps,
        "target_kinds": target_kinds,
        "fold_ids": fold_ids,
        "raw_delta": raw,
        "delta": delta,
        "correction_factor": correction_factor,
        "correction_raw": correction_raw,
        "correction_delta": correction_delta,
        "labels": labels,
        "eligible": eligible,
        "baseline_score": baseline_score,
        "adjusted_score": adjusted_score,
        "pair_names": pair_names,
        "direction_names": direction_names,
        "fold_names": fold_names,
        "pair_direction_names": pair_direction,
        "combined_names": combined,
    }


def raw_delta_auc_report(arrays: Mapping) -> dict:
    labels, scores, eligible = arrays["labels"], arrays["correction_raw"], arrays["eligible"]
    return {
        "score_definition": (
            "raw_delta * (-1 when main is canonical left, +1 when main is canonical right); "
            "higher means movement from main toward the routed alternative"
        ),
        "outside_excluded": True,
        "overall": auc_cell(labels, scores, eligible),
        "by_pair": grouped_cells(labels, scores, eligible, arrays["pair_names"]),
        "by_direction": grouped_cells(labels, scores, eligible, arrays["direction_names"]),
        "by_fold": grouped_cells(labels, scores, eligible, arrays["fold_names"]),
        "by_pair_direction_fold": grouped_cells(
            labels, scores, eligible, arrays["combined_names"]
        ),
        "conditional_by_pair_direction": conditional_auc_cell(
            labels, scores, eligible, arrays["pair_direction_names"]
        ),
        "conditional_by_fold_pair_direction": conditional_auc_cell(
            labels, scores, eligible, arrays["combined_names"]
        ),
    }


def gap_auc_report(arrays: Mapping) -> dict:
    labels, eligible = arrays["labels"], arrays["eligible"]
    baseline, adjusted = arrays["baseline_score"], arrays["adjusted_score"]
    return {
        "baseline_score_definition": "-|canonical pair gap|; higher means closer to switching",
        "adjusted_score_definition": (
            "-|gap| + direction_corrected bounded delta, equivalent to negative final "
            "margin in favor of the original main pair class"
        ),
        "overall": auc_comparison_cell(labels, baseline, adjusted, eligible),
        "by_pair": grouped_comparisons(
            labels, baseline, adjusted, eligible, arrays["pair_names"]
        ),
        "by_direction": grouped_comparisons(
            labels, baseline, adjusted, eligible, arrays["direction_names"]
        ),
        "by_fold": grouped_comparisons(
            labels, baseline, adjusted, eligible, arrays["fold_names"]
        ),
        "conditional_by_pair_direction": conditional_auc_comparison(
            labels, baseline, adjusted, eligible, arrays["pair_direction_names"]
        ),
        "conditional_by_fold_pair_direction": conditional_auc_comparison(
            labels, baseline, adjusted, eligible, arrays["combined_names"]
        ),
    }


def scaled_gap_auc_curve(arrays: Mapping, scales: Sequence[float], delta_max: float) -> dict:
    labels, eligible = arrays["labels"], arrays["eligible"]
    baseline = arrays["baseline_score"]
    baseline_auc = auc_cell(labels, baseline, eligible)["auc"]
    cells = []
    for scale in scales:
        scale = float(scale)
        scaled_delta = bounded_delta(arrays["raw_delta"] * scale, delta_max)
        adjusted = baseline + scaled_delta * arrays["correction_factor"]
        adjusted_auc = auc_cell(labels, adjusted, eligible)["auc"]
        cells.append(
            {
                "scale": scale,
                "baseline_gap_auc": baseline_auc,
                "scaled_gap_plus_delta_auc": adjusted_auc,
                "auc_delta": (
                    adjusted_auc - baseline_auc
                    if adjusted_auc is not None and baseline_auc is not None
                    else None
                ),
            }
        )
    return {
        "definition": (
            "score=-|gap| + correction_factor * delta_max*tanh(scale*raw_delta); "
            "negative scales are sign-inversion diagnostics"
        ),
        "post_hoc_diagnostic_only": True,
        "cells": cells,
    }


def sign_orientation_contract_audit(dataset: Mapping, surface: Mapping) -> dict:
    """Prove the canonical gap/loss/apply/correction signs on synthetic and real rows."""

    arrays = surface_arrays(dataset, surface)
    delta_max = float(surface["recipe"]["delta_max"])
    synthetic = []
    cases = [
        ("left_rescue", 1.0, -1.0, TARGET_RESCUE, -1.0),
        ("left_protect", 1.0, 1.0, TARGET_PROTECT, -1.0),
        ("right_rescue", -1.0, 1.0, TARGET_RESCUE, 1.0),
        ("right_protect", -1.0, -1.0, TARGET_PROTECT, 1.0),
    ]
    for name, gap, target_sign, target_kind, correction_factor in cases:
        raw = torch.zeros(1, requires_grad=True)
        loss, _ = pair_residual_loss(
            raw,
            torch.tensor([gap]),
            torch.tensor([target_sign]),
            torch.tensor([target_kind]),
            torch.ones(1),
            delta_max=delta_max,
        )
        loss.backward()
        gradient = float(raw.grad)
        descent_direction = -1.0 if gradient > 0 else 1.0 if gradient < 0 else 0.0
        correction_oriented_step = descent_direction * correction_factor
        expected_positive = target_kind == TARGET_RESCUE
        passed = correction_oriented_step > 0 if expected_positive else correction_oriented_step < 0
        probe_raw = torch.tensor([0.1 * correction_factor])
        probe_delta = float(bounded_delta(probe_raw, delta_max)[0])
        original_main_margin = abs(gap)
        corrected_main_margin = original_main_margin - correction_factor * probe_delta
        synthetic.append(
            {
                "case": name,
                "base_gap_left_minus_right": gap,
                "target_sign": target_sign,
                "target_kind": TARGET_NAMES[target_kind],
                "raw_gradient_at_zero": gradient,
                "gradient_descent_delta_direction": descent_direction,
                "correction_factor": correction_factor,
                "correction_oriented_step": correction_oriented_step,
                "expected": "toward_alternative" if expected_positive else "toward_main",
                "passed": passed,
                "probe_correction_raw": float(probe_raw[0]),
                "probe_main_margin_before": original_main_margin,
                "probe_main_margin_after": corrected_main_margin,
                "probe_reduces_main_margin": corrected_main_margin < original_main_margin,
            }
        )

    local = arrays["local"]
    full_indices = torch.as_tensor(dataset["route_indices"], dtype=torch.long)[local]
    pairs = arrays["pairs"]
    parent_logits = torch.as_tensor(dataset["parent_logits"], dtype=torch.float32)
    recomputed_gap = (
        parent_logits[full_indices, pairs[:, 0]]
        - parent_logits[full_indices, pairs[:, 1]]
    )
    gap_error = (recomputed_gap - arrays["base_gaps"]).abs()
    y = torch.as_tensor(dataset["y_true"], dtype=torch.long)[full_indices]
    stored_sign = torch.as_tensor(dataset["target_signs"], dtype=torch.float32)[local]
    expected_sign = torch.zeros_like(stored_sign)
    expected_sign[y == pairs[:, 0]] = 1.0
    expected_sign[y == pairs[:, 1]] = -1.0
    expected_kind = torch.full_like(arrays["target_kinds"], TARGET_OUTSIDE)
    expected_kind[y == arrays["main"]] = TARGET_PROTECT
    expected_kind[y == arrays["alt"]] = TARGET_RESCUE
    analytic_gradient_delta = -stored_sign * torch.sigmoid(
        -stored_sign * arrays["base_gaps"]
    )
    descent_delta_direction = -torch.sign(analytic_gradient_delta)
    descent_correction_orientation = (
        descent_delta_direction * arrays["correction_factor"]
    )
    rescue = arrays["target_kinds"] == TARGET_RESCUE
    protect = arrays["target_kinds"] == TARGET_PROTECT
    _, applied_pred, _ = apply_pair_residual(
        parent_logits,
        route_subset(dataset, local),
        arrays["raw_delta"],
        delta_max=delta_max,
    )
    adjusted_logits, _, _ = apply_pair_residual(
        parent_logits,
        route_subset(dataset, local),
        arrays["raw_delta"],
        delta_max=delta_max,
    )
    applied_gap = (
        adjusted_logits[full_indices, pairs[:, 0]]
        - adjusted_logits[full_indices, pairs[:, 1]]
    )
    expected_applied_gap = arrays["base_gaps"] + arrays["delta"]
    learned = {}
    for mask, name in ((rescue, "rescue"), (protect, "protect")):
        learned[name] = {
            "rows": int(mask.sum()),
            "correction_oriented_raw_positive_fraction": (
                float((arrays["correction_raw"][mask] > 0).float().mean())
                if bool(mask.any()) else None
            ),
            "correction_oriented_raw_mean": (
                float(arrays["correction_raw"][mask].mean())
                if bool(mask.any()) else None
            ),
        }
    return {
        "canonical_contract": {
            "base_gap": "main_logits[left] - main_logits[right]",
            "target_sign": "+1 when official y is left, -1 when y is right, 0 outside pair",
            "pair_loss": "softplus(-target_sign * (base_gap + bounded_delta))",
            "correction_factor": "-1 for left-main, +1 for right-main",
            "correction_score": "raw_delta * correction_factor; positive means toward alternative",
        },
        "synthetic_cases": synthetic,
        "synthetic_all_passed": all(case["passed"] and case["probe_reduces_main_margin"] for case in synthetic),
        "real_rows": {
            "rows": len(local),
            "max_abs_stored_vs_recomputed_gap": float(gap_error.max()) if len(gap_error) else 0.0,
            "target_sign_mismatch_rows": int((stored_sign != expected_sign).sum()),
            "target_kind_mismatch_rows": int((arrays["target_kinds"] != expected_kind).sum()),
            "rescue_loss_descent_wrong_orientation_rows": int(
                (rescue & (descent_correction_orientation <= 0)).sum()
            ),
            "protect_loss_descent_wrong_orientation_rows": int(
                (protect & (descent_correction_orientation >= 0)).sum()
            ),
            "max_abs_apply_gap_error": float(
                (applied_gap - expected_applied_gap).abs().max()
            ) if len(applied_gap) else 0.0,
            "apply_prediction_vs_adjusted_argmax_mismatch_rows": int(
                (applied_pred != adjusted_logits.argmax(dim=1)).sum()
            ),
            "learned_output_orientation": learned,
        },
    }


def quantile_summary(values: torch.Tensor) -> dict | None:
    values = torch.as_tensor(values, dtype=torch.float64).view(-1)
    if not len(values):
        return None
    return {
        "p10": float(torch.quantile(values, 0.10)),
        "p50": float(torch.quantile(values, 0.50)),
        "p90": float(torch.quantile(values, 0.90)),
        "mean": float(values.mean()),
        "min": float(values.min()),
        "max": float(values.max()),
    }


def target_delta_quantiles(arrays: Mapping) -> dict:
    output = {}
    kinds = arrays["target_kinds"]
    for kind, name in enumerate(TARGET_NAMES):
        mask = kinds == kind
        output[name] = {
            "rows": int(mask.sum()),
            "raw_delta": quantile_summary(arrays["raw_delta"][mask]),
            "bounded_delta": quantile_summary(arrays["delta"][mask]),
            "direction_corrected_raw_delta": quantile_summary(arrays["correction_raw"][mask]),
            "direction_corrected_bounded_delta": quantile_summary(
                arrays["correction_delta"][mask]
            ),
        }
    return output


def weak4_macro(metrics: Mapping) -> float:
    return sum(metrics["per_class_f1"][label] for label in ALL_CLASSES[:4]) / 4.0


def evaluation_mask(dataset: Mapping, folds: Sequence[int]) -> torch.Tensor:
    full_folds = torch.as_tensor(dataset["full_fold_ids"], dtype=torch.long)
    return torch.stack([full_folds == int(fold) for fold in folds]).any(dim=0)


def bounded_reachable_oracle(
    dataset: Mapping,
    surface: Mapping,
    bounds: Sequence[float] = (1.0, 2.0, 3.0, 4.0),
) -> dict:
    arrays = surface_arrays(dataset, surface)
    full_y = torch.as_tensor(dataset["y_true"], dtype=torch.long)
    parent_logits = torch.as_tensor(dataset["parent_logits"], dtype=torch.float32)
    parent_pred = parent_logits.argmax(dim=1)
    eval_mask = evaluation_mask(dataset, surface["expected_folds"])
    base_metrics = f1_metrics(full_y[eval_mask].tolist(), parent_pred[eval_mask].tolist())
    full_route_indices = torch.as_tensor(dataset["route_indices"], dtype=torch.long)[arrays["local"]]
    output = {}
    for bound in bounds:
        bound = float(bound)
        nominal_abs_reachable = (
            (arrays["target_kinds"] == TARGET_RESCUE)
            & (arrays["base_gaps"].abs() <= bound)
        )
        # apply_pair_residual chooses canonical left at final_gap == 0.
        # Therefore a left-main -> right rescue is strict at +bound, while a
        # right-main -> left rescue is inclusive at -bound.
        reachable = (
            (arrays["target_kinds"] == TARGET_RESCUE)
            & (
                ((arrays["top_sides"] == 0) & (arrays["base_gaps"] < bound))
                | ((arrays["top_sides"] == 1) & (-arrays["base_gaps"] <= bound))
            )
        )
        oracle_pred = parent_pred.clone()
        oracle_pred[full_route_indices[reachable]] = arrays["alt"][reachable]
        metrics = f1_metrics(full_y[eval_mask].tolist(), oracle_pred[eval_mask].tolist())
        by_pair = {}
        for pair_id, (left, right) in enumerate(LIVE_WEAK4_PAIRS):
            pair_mask = arrays["pair_ids"] == pair_id
            pair_reachable = reachable & pair_mask
            pair_pred = parent_pred.clone()
            pair_pred[full_route_indices[pair_reachable]] = arrays["alt"][pair_reachable]
            pair_metrics = f1_metrics(
                full_y[eval_mask].tolist(), pair_pred[eval_mask].tolist()
            )
            rescue_count = int(
                (pair_mask & (arrays["target_kinds"] == TARGET_RESCUE)).sum()
            )
            by_pair[f"{ALL_CLASSES[left]}<>{ALL_CLASSES[right]}"] = {
                "route_rows": int(pair_mask.sum()),
                "rescue_rows": rescue_count,
                "nominal_abs_gap_le_bound": int((nominal_abs_reachable & pair_mask).sum()),
                "reachable_corrections_upper": int(pair_reachable.sum()),
                "rescue_reach_rate": (
                    int(pair_reachable.sum()) / rescue_count if rescue_count else None
                ),
                "macro_f1_upper_if_only_pair": pair_metrics["macro_f1"],
                "macro_delta_upper_if_only_pair": (
                    pair_metrics["macro_f1"] - base_metrics["macro_f1"]
                ),
            }
        output[f"le_{bound:g}"] = {
            "gap_bound": bound,
            "rescue_rows": int((arrays["target_kinds"] == TARGET_RESCUE).sum()),
            "nominal_abs_gap_le_bound": int(nominal_abs_reachable.sum()),
            "reachable_corrections_upper": int(reachable.sum()),
            "endpoint_tie_excluded_rows": int((nominal_abs_reachable & ~reachable).sum()),
            "unreachable_rescue_rows": int(
                ((arrays["target_kinds"] == TARGET_RESCUE) & ~reachable).sum()
            ),
            "macro_f1_upper": metrics["macro_f1"],
            "macro_delta_upper": metrics["macro_f1"] - base_metrics["macro_f1"],
            "weak4_macro_f1_upper": weak4_macro(metrics),
            "weak4_delta_upper": weak4_macro(metrics) - weak4_macro(base_metrics),
            "by_pair": by_pair,
        }
    deployed = float(surface["recipe"]["delta_max"])
    deployed_key = f"le_{deployed:g}"
    return {
        "reachability_definition": (
            "report nominal rescue rows with |canonical pair gap|<=bound, then apply the implementation "
            "tie rule final_gap>=0=>left: left-main->right requires gap<bound; right-main->left "
            "allows -gap<=bound. Protect stays main; outside cannot be corrected by pair switching"
        ),
        "evaluation_rows": int(eval_mask.sum()),
        "base_macro_f1": base_metrics["macro_f1"],
        "base_weak4_macro_f1": weak4_macro(base_metrics),
        "deployed_delta_max": deployed,
        "deployed_bound_key": deployed_key if deployed_key in output else None,
        "bounds": output,
    }


def curve_thresholds(correction_raw: torch.Tensor) -> list[dict]:
    values = torch.as_tensor(correction_raw, dtype=torch.float64)
    entries = [{"name": "no_gate", "value": None}, {"name": "zero", "value": 0.0}]
    for quantile in (0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99):
        entries.append(
            {
                "name": f"q{int(quantile * 100):02d}",
                "value": float(torch.quantile(values, quantile)),
            }
        )
    return entries


def threshold_scale_curve(
    dataset: Mapping,
    surface: Mapping,
    scales: Sequence[float] = DEFAULT_SCALES,
) -> dict:
    arrays = surface_arrays(dataset, surface)
    parent_logits = torch.as_tensor(dataset["parent_logits"], dtype=torch.float32)
    full_y = torch.as_tensor(dataset["y_true"], dtype=torch.long)
    base_pred = parent_logits.argmax(dim=1)
    eval_mask = evaluation_mask(dataset, surface["expected_folds"])
    base_metrics = f1_metrics(full_y[eval_mask].tolist(), base_pred[eval_mask].tolist())
    route = route_subset(dataset, arrays["local"])
    route_y = full_y[route["indices"]]
    base_route = base_pred[route["indices"]]
    thresholds = curve_thresholds(arrays["correction_raw"])
    cells = []
    for scale in scales:
        scale = float(scale)
        for threshold in thresholds:
            value = threshold["value"]
            tails = ("all",) if value is None else ("high", "low")
            for tail in tails:
                if tail == "all":
                    active = torch.ones(len(arrays["raw_delta"]), dtype=torch.bool)
                elif tail == "high":
                    active = arrays["correction_raw"] >= float(value)
                else:
                    active = arrays["correction_raw"] <= float(value)
                scaled_raw = arrays["raw_delta"] * scale
                scaled_raw = torch.where(active, scaled_raw, torch.zeros_like(scaled_raw))
                _, pred, _ = apply_pair_residual(
                    parent_logits,
                    route,
                    scaled_raw,
                    delta_max=float(surface["recipe"]["delta_max"]),
                )
                metrics = f1_metrics(full_y[eval_mask].tolist(), pred[eval_mask].tolist())
                new_route = pred[route["indices"]]
                changed = base_route != new_route
                rescue = int(((base_route != route_y) & (new_route == route_y)).sum())
                harm = int(((base_route == route_y) & (new_route != route_y)).sum())
                neutral = int((changed & (base_route != route_y) & (new_route != route_y)).sum())
                kinds = arrays["target_kinds"]
                cells.append(
                    {
                        "scale": scale,
                        "threshold_name": threshold["name"],
                        "threshold": value,
                        "tail": tail,
                        "gate_rows": int(active.sum()),
                        "changed": int(changed.sum()),
                        "rescue": rescue,
                        "harm": harm,
                        "neutral_wrong_to_wrong": neutral,
                        "net_rescue_minus_harm": rescue - harm,
                        "rescue_harm_ratio": rescue / harm if harm else None,
                        "protect_changed": int((changed & (kinds == TARGET_PROTECT)).sum()),
                        "rescue_changed": int((changed & (kinds == TARGET_RESCUE)).sum()),
                        "outside_changed": int((changed & (kinds == TARGET_OUTSIDE)).sum()),
                        "macro_f1": metrics["macro_f1"],
                        "macro_delta": metrics["macro_f1"] - base_metrics["macro_f1"],
                        "weak4_macro_f1": weak4_macro(metrics),
                        "weak4_delta": weak4_macro(metrics) - weak4_macro(base_metrics),
                    }
                )
    return {
        "scale_definition": "bounded_delta = delta_max * tanh(scale * raw_delta)",
        "threshold_definition": (
            "gate on unscaled direction-corrected raw_delta; high-tail keeps score>=threshold, "
            "low-tail keeps score<=threshold, and inactive rows receive raw_delta=0"
        ),
        "diagnostic_only_no_selection": True,
        "scales": [float(scale) for scale in scales],
        "thresholds": thresholds,
        "cells": cells,
    }


def session_block_permutation_auc_null(
    ids: Sequence[str],
    labels: Sequence[int] | torch.Tensor,
    scores: Sequence[float] | torch.Tensor,
    *,
    session_strata: Sequence[str] | None = None,
    permutations: int = 1000,
    seed: int = 20260713,
) -> dict:
    """Move complete same-length label vectors between sessions."""

    if permutations <= 0:
        raise ValueError("permutations must be positive")
    y = torch.as_tensor(labels, dtype=torch.long).view(-1)
    score_tensor = torch.as_tensor(scores, dtype=torch.float64).view(-1)
    if not (len(ids) == len(y) == len(score_tensor)):
        raise ValueError("session null ids/labels/scores lengths differ")
    if session_strata is not None and len(session_strata) != len(ids):
        raise ValueError("session null strata length differs from rows")
    if not bool(((y == 0) | (y == 1)).all()):
        raise ValueError("session null requires binary labels")
    ranks = average_ranks(score_tensor)
    observed = auc_from_ranks(y, ranks)
    if observed is None:
        raise ValueError("session null requires both rescue and protect rows")
    session_rows: dict[str, list[int]] = defaultdict(list)
    for row, sample_id in enumerate(ids):
        session_rows[str(sample_id).split("-step_")[0]].append(row)
    buckets: dict[tuple[str, int], list[str]] = defaultdict(list)
    for session, rows in session_rows.items():
        if session_strata is None:
            stratum = "all"
        else:
            values = {str(session_strata[row]) for row in rows}
            if len(values) != 1:
                raise ValueError(f"session {session!r} crosses permutation strata: {values}")
            stratum = next(iter(values))
        buckets[(stratum, len(rows))].append(session)
    rng = random.Random(seed)
    null_values = []
    for _ in range(permutations):
        permuted = torch.empty_like(y)
        for _, sessions in sorted(buckets.items()):
            donors = list(sessions)
            rng.shuffle(donors)
            for receiver, donor in zip(sessions, donors):
                receiver_rows = session_rows[receiver]
                donor_rows = session_rows[donor]
                permuted[receiver_rows] = y[donor_rows]
        null_auc = auc_from_ranks(permuted, ranks)
        if null_auc is None:
            raise AssertionError("session permutation changed global class support")
        null_values.append(null_auc)
    null = torch.tensor(null_values, dtype=torch.float64)
    mean = float(null.mean())
    std = float(null.std(unbiased=False))
    return {
        "method": (
            "permute complete rescue/protect label vectors between sessions within held-out-fold and "
            "equal eligible-row-count buckets; scores and within-session row order stay fixed"
        ),
        "permutations": permutations,
        "seed": seed,
        "rows": len(y),
        "sessions": len(session_rows),
        "permutable_sessions": sum(len(items) for items in buckets.values() if len(items) > 1),
        "fixed_singleton_sessions": sum(len(items) for items in buckets.values() if len(items) == 1),
        "session_size_buckets": {
            f"{stratum}|n={size}": len(items)
            for (stratum, size), items in sorted(buckets.items())
        },
        "observed_auc": observed,
        "null_mean": mean,
        "null_std": std,
        "null_p05": float(torch.quantile(null, 0.05)),
        "null_p50": float(torch.quantile(null, 0.50)),
        "null_p95": float(torch.quantile(null, 0.95)),
        "observed_minus_null_mean": observed - mean,
        "z_score": (observed - mean) / std if std > 0 else None,
        "empirical_p_null_ge_observed": (
            1 + int((null >= observed).sum())
        ) / (permutations + 1),
    }


def _permutation_stat_summary(values: list[float], observed: float) -> dict:
    if not values:
        raise ValueError("permutation null produced no valid statistics")
    tensor = torch.tensor(values, dtype=torch.float64)
    mean = float(tensor.mean())
    std = float(tensor.std(unbiased=False))
    return {
        "observed": observed,
        "valid_permutations": len(values),
        "null_mean": mean,
        "null_std": std,
        "null_p05": float(torch.quantile(tensor, 0.05)),
        "null_p50": float(torch.quantile(tensor, 0.50)),
        "null_p95": float(torch.quantile(tensor, 0.95)),
        "observed_minus_null_mean": observed - mean,
        "z_score": (observed - mean) / std if std > 0 else None,
        "empirical_p_null_ge_observed": (
            1 + sum(value >= observed for value in values)
        ) / (len(values) + 1),
    }


def full_session_label_permutation_null(
    dataset: Mapping,
    surface: Mapping,
    arrays: Mapping,
    *,
    permutations: int = 1000,
    seed: int = 20260713,
) -> dict:
    """Permute complete official-label session sequences within fold/length."""

    if permutations <= 0:
        raise ValueError("permutations must be positive")
    ids = list(dataset["ids"])
    full_y = torch.as_tensor(dataset["y_true"], dtype=torch.long)
    full_folds = torch.as_tensor(dataset["full_fold_ids"], dtype=torch.long)
    eval_mask = evaluation_mask(dataset, surface["expected_folds"])
    session_rows: dict[str, list[int]] = defaultdict(list)
    for row, sample_id in enumerate(ids):
        if bool(eval_mask[row]):
            session_rows[str(sample_id).split("-step_")[0]].append(row)

    def step_key(row: int):
        match = re.search(r"-step_(\d+)$", str(ids[row]))
        return (int(match.group(1)) if match else 10**9, str(ids[row]))

    buckets: dict[tuple[int, int], list[str]] = defaultdict(list)
    for session, rows in session_rows.items():
        rows.sort(key=step_key)
        fold_values = {int(full_folds[row]) for row in rows}
        if len(fold_values) != 1:
            raise ValueError(f"session {session!r} crosses fixed outer folds")
        buckets[(next(iter(fold_values)), len(rows))].append(session)

    route_full = torch.as_tensor(dataset["route_indices"], dtype=torch.long)[arrays["local"]]
    main = arrays["main"]
    alt = arrays["alt"]

    def derive_labels(labels_full: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        route_y = labels_full[route_full]
        labels = torch.full((len(route_full),), -1, dtype=torch.long)
        labels[route_y == main] = 0
        labels[route_y == alt] = 1
        return labels, labels >= 0

    observed_labels, observed_eligible = derive_labels(full_y)
    if not torch.equal(observed_labels, arrays["labels"]):
        raise ValueError("rederived clean target labels differ from dataset targets")
    pooled_observed = binary_auc(
        observed_labels[observed_eligible],
        arrays["correction_raw"][observed_eligible],
    )
    conditional_observed = conditional_auc_cell(
        observed_labels,
        arrays["correction_raw"],
        observed_eligible,
        arrays["combined_names"],
    )["auc"]
    if pooled_observed is None or conditional_observed is None:
        raise ValueError("observed session-null statistic lacks class support")

    rng = random.Random(seed)
    pooled_null: list[float] = []
    conditional_null: list[float] = []
    rescue_counts = []
    protect_counts = []
    for _ in range(permutations):
        permuted_y = full_y.clone()
        for _, sessions in sorted(buckets.items()):
            donors = list(sessions)
            rng.shuffle(donors)
            for receiver, donor in zip(sessions, donors):
                permuted_y[session_rows[receiver]] = full_y[session_rows[donor]]
        labels, eligible = derive_labels(permuted_y)
        rescue_counts.append(int((labels == 1).sum()))
        protect_counts.append(int((labels == 0).sum()))
        pooled = binary_auc(labels[eligible], arrays["correction_raw"][eligible])
        conditional = conditional_auc_cell(
            labels,
            arrays["correction_raw"],
            eligible,
            arrays["combined_names"],
        )["auc"]
        if pooled is not None:
            pooled_null.append(pooled)
        if conditional is not None:
            conditional_null.append(conditional)
    return {
        "method": (
            "permute complete official-action sequences between sessions within fixed outer-fold and "
            "full-session-length buckets; preserve numeric step order; keep route/logits/raw_delta fixed; "
            "then rederive protect/rescue/outside from permuted official y"
        ),
        "interpretation": (
            "fixed-OOF-output association null, not an exact model-retraining permutation test"
        ),
        "permutations": permutations,
        "seed": seed,
        "full_rows": int(eval_mask.sum()),
        "route_rows": len(route_full),
        "sessions": len(session_rows),
        "permutable_sessions": sum(
            len(sessions) for sessions in buckets.values() if len(sessions) > 1
        ),
        "fixed_singleton_sessions": sum(
            len(sessions) for sessions in buckets.values() if len(sessions) == 1
        ),
        "fold_session_length_buckets": {
            f"fold{fold}|n={length}": len(sessions)
            for (fold, length), sessions in sorted(buckets.items())
        },
        "observed_target_rows": {
            "rescue": int((observed_labels == 1).sum()),
            "protect": int((observed_labels == 0).sum()),
            "outside": int((observed_labels < 0).sum()),
        },
        "permuted_target_count_ranges": {
            "rescue_min": min(rescue_counts),
            "rescue_max": max(rescue_counts),
            "protect_min": min(protect_counts),
            "protect_max": max(protect_counts),
        },
        "pooled_auc": _permutation_stat_summary(pooled_null, pooled_observed),
        "conditional_fold_pair_direction_auc": _permutation_stat_summary(
            conditional_null, conditional_observed
        ),
    }


def target_semantics(dataset: Mapping) -> dict:
    correct_counts = torch.as_tensor(dataset["correct_counts"], dtype=torch.long)
    kinds = torch.as_tensor(dataset["target_kinds"], dtype=torch.long)
    return {
        "reliability_source": dataset.get("reliability_source"),
        "c_conditioning": "disabled",
        "correct_counts_unique": sorted(set(correct_counts.tolist())),
        "correct_counts_meaning": (
            "constant placeholder 1 only; no OOF consensus c bin is used on this clean fixed-parent surface"
        ),
        "protect": "official y equals routed main prediction",
        "rescue": (
            "official y equals the routed in-pair alternative; every such row is RESCUE because c is disabled"
        ),
        "noisy_alt": "absent on the clean surface; only an explicit consensus diagnostic could create c=0 noisy_alt",
        "outside": (
            "official y is outside the routed pair; family-locked switching cannot correct it and training uses identity-only"
        ),
        "target_histogram": {
            name: int((kinds == kind).sum()) for kind, name in enumerate(TARGET_NAMES)
        },
    }


def analyze_surface(
    dataset: Mapping,
    surface: Mapping,
    *,
    scales: Sequence[float],
    permutations: int,
    permutation_seed: int,
) -> dict:
    arrays = surface_arrays(dataset, surface)
    eligible = arrays["eligible"]
    curve = threshold_scale_curve(dataset, surface, scales=scales)
    scaled_gap = scaled_gap_auc_curve(
        arrays, scales, float(surface["recipe"]["delta_max"])
    )
    negative_no_gate = [
        cell for cell in curve["cells"]
        if cell["scale"] < 0 and cell["tail"] == "all"
    ]
    positive_low_tail = [
        cell for cell in curve["cells"]
        if cell["scale"] > 0 and cell["tail"] == "low"
    ]
    negative_gap = [cell for cell in scaled_gap["cells"] if cell["scale"] < 0]
    return {
        "name": surface["name"],
        "recipe": surface["recipe"],
        "expected_folds": surface["expected_folds"],
        "route_rows": len(arrays["local"]),
        "evaluation_rows": int(evaluation_mask(dataset, surface["expected_folds"]).sum()),
        "sources": surface["sources"],
        "surface_target_histogram": {
            name: int((arrays["target_kinds"] == kind).sum())
            for kind, name in enumerate(TARGET_NAMES)
        },
        "bounded_reachable_oracle": bounded_reachable_oracle(dataset, surface),
        "sign_orientation_contract_audit": sign_orientation_contract_audit(
            dataset, surface
        ),
        "rescue_vs_protect_direction_corrected_raw_delta_auc": raw_delta_auc_report(arrays),
        "baseline_gap_vs_gap_plus_delta_auc": gap_auc_report(arrays),
        "scaled_gap_auc_curve": scaled_gap,
        "target_delta_quantiles": target_delta_quantiles(arrays),
        "threshold_residual_scale_curve": curve,
        "post_hoc_inversion_diagnostic": {
            "diagnostic_only_not_promotion_tuning": True,
            "best_negative_scale_no_gate_by_macro": (
                max(negative_no_gate, key=lambda cell: cell["macro_delta"])
                if negative_no_gate else None
            ),
            "best_negative_scale_by_gap_auc": (
                max(negative_gap, key=lambda cell: cell["auc_delta"])
                if negative_gap else None
            ),
            "best_positive_scale_low_tail_by_macro": (
                max(positive_low_tail, key=lambda cell: cell["macro_delta"])
                if positive_low_tail else None
            ),
        },
        "session_label_permutation_null": full_session_label_permutation_null(
            dataset,
            surface,
            arrays,
            permutations=permutations,
            seed=permutation_seed,
        ),
    }


def parse_scales(value: str) -> list[float]:
    scales = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not scales or any(not math.isfinite(scale) for scale in scales):
        raise ValueError("scales must be finite comma-separated numbers")
    return scales


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--base-folds", nargs="+", default=DEFAULT_BASE_FOLDS)
    parser.add_argument("--head-folds", nargs="+", default=DEFAULT_HEAD_FOLDS)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--permutations", type=int, default=1000)
    parser.add_argument("--permutation-seed", type=int, default=20260713)
    parser.add_argument(
        "--scales",
        default=",".join(f"{value:g}" for value in DEFAULT_SCALES),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    dataset_path = Path(args.dataset)
    dataset = torch_load(dataset_path)
    validate_clean_dataset(dataset)
    dataset_sha = sha256_file(dataset_path)
    scales = parse_scales(args.scales)
    base = load_delta_surface(
        dataset,
        args.base_folds,
        dataset_sha256=dataset_sha,
        expected_folds=range(int(dataset["n_folds"])),
        name="base_head_lr_1e-4_3fold",
    )
    head = load_delta_surface(
        dataset,
        args.head_folds,
        dataset_sha256=dataset_sha,
        expected_folds=range(int(dataset["n_folds"])),
        name="head_lr_1e-3_3fold",
    )
    base_report = analyze_surface(
        dataset,
        base,
        scales=scales,
        permutations=args.permutations,
        permutation_seed=args.permutation_seed,
    )
    head_report = analyze_surface(
        dataset,
        head,
        scales=scales,
        permutations=args.permutations,
        permutation_seed=args.permutation_seed + 1,
    )
    base_fold0_raw = base_report[
        "rescue_vs_protect_direction_corrected_raw_delta_auc"
    ]["by_fold"]["fold0"]["auc"]
    head_raw = head_report[
        "rescue_vs_protect_direction_corrected_raw_delta_auc"
    ]["by_fold"]["fold0"]["auc"]
    base_fold0_gap = base_report["baseline_gap_vs_gap_plus_delta_auc"]["by_fold"]["fold0"]
    head_gap = head_report["baseline_gap_vs_gap_plus_delta_auc"]["by_fold"]["fold0"]
    report = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "kind": AUDIT_KIND,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "leakage_contract": (
            "clean fixed parent only; every residual is joined to its held-out session fold; "
            "no train/checkpoint forward pass and no row-level label permutation"
        ),
        "dataset": {
            "path": str(dataset_path),
            "sha256": dataset_sha,
            "usage_scope": dataset["usage_scope"],
            "validation_scope": dataset["validation_scope"],
            "rows": len(dataset["ids"]),
            "route_rows": len(dataset["route_ids"]),
        },
        "target_semantics": target_semantics(dataset),
        "surfaces": {
            "base_head_lr_1e-4_3fold": base_report,
            "head_lr_1e-3_3fold": head_report,
        },
        "head_lr_1e-3_vs_base_fold0": {
            "direction_corrected_raw_delta_auc_base_fold0": base_fold0_raw,
            "direction_corrected_raw_delta_auc_head1e3_fold0": head_raw,
            "auc_delta": (
                head_raw - base_fold0_raw
                if head_raw is not None and base_fold0_raw is not None
                else None
            ),
            "baseline_gap_auc": head_gap["baseline_gap_auc"],
            "base_fold0_gap_plus_delta_auc": base_fold0_gap["gap_plus_delta_auc"],
            "head1e3_gap_plus_delta_auc": head_gap["gap_plus_delta_auc"],
            "head_minus_base_adjusted_gap_auc": (
                head_gap["gap_plus_delta_auc"] - base_fold0_gap["gap_plus_delta_auc"]
                if head_gap["gap_plus_delta_auc"] is not None
                and base_fold0_gap["gap_plus_delta_auc"] is not None
                else None
            ),
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved {output}")
    for name, surface_report in report["surfaces"].items():
        raw_auc = surface_report[
            "rescue_vs_protect_direction_corrected_raw_delta_auc"
        ]["overall"]["auc"]
        gap = surface_report["baseline_gap_vs_gap_plus_delta_auc"]["overall"]
        null = surface_report["session_label_permutation_null"]
        conditional_null = null["conditional_fold_pair_direction_auc"]
        bound = surface_report["bounded_reachable_oracle"]["bounds"]["le_2"]
        print(
            f"{name}: raw_auc={raw_auc} gap_auc={gap['baseline_gap_auc']} "
            f"gap+delta_auc={gap['gap_plus_delta_auc']} "
            f"conditional_null_mean={conditional_null['null_mean']} "
            f"reachable@2={bound['reachable_corrections_upper']} "
            f"macro_oracle_delta@2={bound['macro_delta_upper']:+.6f}"
        )


if __name__ == "__main__":
    main()
