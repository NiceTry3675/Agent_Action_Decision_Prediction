#!/usr/bin/env python3
"""Aggregate SIM policy decoder fold outputs with fail-closed provenance."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch

from build_sim_policy_decoder_dataset import (
    sha256_file,
    torch_load,
    validate_dataset,
    validate_parent_provenance,
)
from script import ALL_CLASSES, safe_text
from sim_policy_decoder import (
    DIAGNOSTIC_USAGE_SCOPE,
    FOLD_FORMAT,
    OUTPUT_NAMES,
    apply_specialist_actions,
    project_predictions_to_logits,
    stable_inner_fold,
)
from train import append_results_csv, f1_metrics


def weak4_macro(metrics: Mapping[str, Any]) -> float:
    return sum(float(metrics["per_class_f1"][name]) for name in ALL_CLASSES[:4]) / 4.0


def metric_delta(
    truth: torch.Tensor,
    baseline: torch.Tensor,
    prediction: torch.Tensor,
    mask: torch.Tensor,
) -> dict[str, Any]:
    base = f1_metrics(truth[mask].tolist(), baseline[mask].tolist())
    candidate = f1_metrics(truth[mask].tolist(), prediction[mask].tolist())
    return {
        "rows": int(mask.sum()),
        "base_macro_f1": base["macro_f1"],
        "macro_f1": candidate["macro_f1"],
        "macro_delta": candidate["macro_f1"] - base["macro_f1"],
        "base_weak4_macro_f1": weak4_macro(base),
        "weak4_macro_f1": weak4_macro(candidate),
        "weak4_delta": weak4_macro(candidate) - weak4_macro(base),
        "per_class_f1": candidate["per_class_f1"],
    }


def expected_splits(cv_mode: str, n_inner_folds: int = 3) -> set[tuple[int, ...]]:
    if cv_mode == "parent_inner3":
        return {
            (parent, inner) for parent in range(3) for inner in range(n_inner_folds)
        }
    if cv_mode == "stitched_outer3":
        return {(fold,) for fold in range(3)}
    raise ValueError(f"unsupported cv_mode: {cv_mode!r}")


def split_key(spec: Mapping[str, Any]) -> tuple[int, ...]:
    if spec.get("cv_mode") == "parent_inner3":
        return int(spec["parent_fold"]), int(spec["inner_fold"])
    if spec.get("cv_mode") == "stitched_outer3":
        return (int(spec["fold_id"]),)
    raise ValueError(f"invalid split spec: {spec}")


def aggregate_fold_payloads(
    dataset: Mapping[str, Any],
    folds: Sequence[Mapping[str, Any]],
    *,
    dataset_sha256: str = "",
) -> dict[str, Any]:
    dataset = validate_dataset(dataset)
    parent_eligibility = validate_parent_provenance(
        dataset["parent"], dataset["usage_scope"]
    )
    if not folds:
        raise ValueError("no specialist folds supplied")
    first_recipe = dict(folds[0].get("recipe") or {})
    cv_mode = first_recipe.get("cv_mode")
    n_inner = int(first_recipe.get("n_inner_folds", 3))
    expected = expected_splits(safe_text(cv_mode), n_inner)
    if len(folds) != len(expected):
        raise ValueError(f"expected {len(expected)} {cv_mode} folds, got {len(folds)}")

    route_count = len(dataset["route_ids"])
    full_count = len(dataset["ids"])
    action_logits = torch.empty((route_count, len(OUTPUT_NAMES)), dtype=torch.float32)
    route_seen = torch.zeros(route_count, dtype=torch.bool)
    full_seen = torch.zeros(full_count, dtype=torch.bool)
    seen_splits: set[tuple[int, ...]] = set()
    fold_reports = []
    sources = []
    for payload in folds:
        if payload.get("format") != FOLD_FORMAT:
            raise ValueError("unexpected specialist fold format")
        if payload.get("usage_scope") != dataset["usage_scope"]:
            raise ValueError("fold usage scope differs from dataset")
        if dataset_sha256 and payload.get("dataset_sha256") != dataset_sha256:
            raise ValueError("fold was produced from a different dataset")
        if tuple(payload.get("outputs") or ()) != OUTPUT_NAMES:
            raise ValueError("fold output order is invalid")
        if dict(payload.get("recipe") or {}) != first_recipe:
            raise ValueError("fold recipes differ; post-outer recipe selection is forbidden")
        if payload.get("promotion_eligible") is not bool(parent_eligibility["promotion_eligible"]):
            raise ValueError("fold promotion eligibility disagrees with parent provenance")
        spec = dict(payload.get("split_spec") or {})
        key = split_key(spec)
        if key not in expected or key in seen_splits:
            raise ValueError(f"duplicate or unexpected split: {spec}")
        seen_splits.add(key)

        route_rows = torch.as_tensor(payload["route_local_indices"], dtype=torch.long).view(-1)
        full_rows = torch.as_tensor(payload["val_full_indices"], dtype=torch.long).view(-1)
        values = torch.as_tensor(payload["action_logits"], dtype=torch.float32)
        actions = torch.as_tensor(payload["actions"], dtype=torch.long).view(-1)
        if values.shape != (len(route_rows), len(OUTPUT_NAMES)):
            raise ValueError(f"fold action_logits shape is invalid for {spec}")
        if not torch.isfinite(values).all():
            raise ValueError(f"fold action_logits contains non-finite values for {spec}")
        if len(actions) != len(route_rows) or not torch.equal(actions, values.argmax(dim=1)):
            raise ValueError(f"fold actions do not match logits for {spec}")
        if len(route_rows) and (
            int(route_rows.min()) < 0 or int(route_rows.max()) >= route_count
        ):
            raise ValueError(f"fold route index out of range for {spec}")
        if len(full_rows) and (int(full_rows.min()) < 0 or int(full_rows.max()) >= full_count):
            raise ValueError(f"fold full index out of range for {spec}")
        if bool(route_seen[route_rows].any()) or bool(full_seen[full_rows].any()):
            raise ValueError(f"fold validation coverage overlaps for {spec}")
        expected_ids = [dataset["route_ids"][row] for row in route_rows.tolist()]
        if expected_ids != list(payload.get("route_ids") or []):
            raise ValueError(f"fold route IDs are misaligned for {spec}")
        route_full = torch.as_tensor(dataset["route_indices"], dtype=torch.long)[route_rows]
        if not set(route_full.tolist()).issubset(set(full_rows.tolist())):
            raise ValueError(f"fold route rows are not inside its full validation surface: {spec}")
        if spec.get("cv_mode") == "parent_inner3":
            parent = int(spec["parent_fold"])
            inner = int(spec["inner_fold"])
            expected_full = [
                row
                for row, (session, parent_id) in enumerate(
                    zip(dataset["session_ids"], dataset["full_fold_ids"])
                )
                if int(parent_id) == parent
                and stable_inner_fold(
                    session,
                    parent,
                    int(spec["n_inner_folds"]),
                    int(spec["inner_seed"]),
                )
                == inner
            ]
            if full_rows.tolist() != expected_full:
                raise ValueError(f"fold full validation rows violate parent_inner3 hash: {spec}")
            expected_full_set = set(expected_full)
            expected_route = [
                row
                for row, full_row in enumerate(dataset["route_indices"])
                if int(full_row) in expected_full_set
            ]
            if route_rows.tolist() != expected_route:
                raise ValueError(f"fold route rows violate parent_inner3 hash: {spec}")
        action_logits[route_rows] = values
        route_seen[route_rows] = True
        full_seen[full_rows] = True
        fold_reports.append(payload.get("metrics"))
        sources.append(payload.get("experiment_id"))
    if seen_splits != expected:
        raise ValueError(f"missing split outputs: {sorted(expected - seen_splits)}")
    if not bool(route_seen.all()) or not bool(full_seen.all()):
        raise ValueError(
            "folds do not cover each row exactly once: "
            f"route_missing={int((~route_seen).sum())} full_missing={int((~full_seen).sum())}"
        )

    parent_logits = torch.as_tensor(dataset["parent_logits"], dtype=torch.float32)
    truth = torch.as_tensor(dataset["y_true"], dtype=torch.long)
    baseline = parent_logits.argmax(dim=1)
    route_indices = torch.as_tensor(dataset["route_indices"], dtype=torch.long)
    actions = action_logits.argmax(dim=1)
    prediction = apply_specialist_actions(parent_logits, route_indices, actions)
    adjusted_logits = project_predictions_to_logits(parent_logits, prediction)
    all_mask = torch.ones(full_count, dtype=torch.bool)
    sim_mask = torch.tensor(
        [safe_text(sample_id).startswith("sess_sim_") for sample_id in dataset["ids"]],
        dtype=torch.bool,
    )
    full_result = metric_delta(truth, baseline, prediction, all_mask)
    sim_result = metric_delta(truth, baseline, prediction, sim_mask)
    parent_results = []
    parent_fold_ids = torch.as_tensor(dataset["full_fold_ids"], dtype=torch.long)
    for parent in range(3):
        result = metric_delta(truth, baseline, prediction, parent_fold_ids == parent)
        result["parent_fold"] = parent
        parent_results.append(result)

    route_truth = truth[route_indices]
    route_base = baseline[route_indices]
    route_new = prediction[route_indices]
    changed = route_base != route_new
    rescue = int(((route_base != route_truth) & (route_new == route_truth)).sum())
    harm = int(((route_base == route_truth) & (route_new != route_truth)).sum())
    neutral = int((changed & (route_base != route_truth) & (route_new != route_truth)).sum())
    quality_checks = {
        "full_macro_delta_ge_0.001": full_result["macro_delta"] >= 0.001,
        "all_three_parent_folds_positive": all(
            result["macro_delta"] > 0 for result in parent_results
        ),
        "sim_macro_positive": sim_result["macro_delta"] > 0,
        "sim_weak4_positive": sim_result["weak4_delta"] > 0,
        "rescue_gt_harm": rescue > harm,
    }
    provenance_check = bool(parent_eligibility["promotion_eligible"])
    gate = {
        "quality_screen_passed": all(quality_checks.values()),
        "quality_checks": quality_checks,
        "provenance_promotion_eligible": provenance_check,
        "passed": all(quality_checks.values()) and provenance_check,
        "fail_closed_reason": (
            None
            if provenance_check
            else "diagnostic stitched parent OOF is not strict nested-main evidence"
        ),
    }
    if dataset["usage_scope"] == DIAGNOSTIC_USAGE_SCOPE and gate["passed"]:
        raise AssertionError("diagnostic stitched OOF must never pass promotion gate")
    return {
        "usage_scope": dataset["usage_scope"],
        "cv_mode": cv_mode,
        "recipe": first_recipe,
        "fold_sources": sources,
        "fold_reports": fold_reports,
        "rows": full_count,
        "route_rows": route_count,
        "full": full_result,
        "sim": sim_result,
        "parent_folds": parent_results,
        "changed": int(changed.sum()),
        "rescue": rescue,
        "harm": harm,
        "neutral_change": neutral,
        "action_histogram": {
            name: int((actions == index).sum()) for index, name in enumerate(OUTPUT_NAMES)
        },
        "gate": gate,
        "promotion_eligible": bool(gate["passed"]),
        "deployment_eligible": False,
        "action_logits": action_logits,
        "actions": actions,
        "predictions": prediction,
        "adjusted_logits": adjusted_logits,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="experiments/artifacts/20260713_sim_policy_decoder_dataset.pt",
    )
    parser.add_argument("--fold-pattern", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-logits", default="")
    parser.add_argument("--no-results-csv", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_path = Path(args.dataset)
    dataset_sha = sha256_file(dataset_path)
    dataset = torch_load(dataset_path)
    paths = sorted(Path(value) for value in glob.glob(args.fold_pattern))
    if not paths:
        raise FileNotFoundError(f"no specialist folds match {args.fold_pattern!r}")
    folds = [torch_load(path) for path in paths]
    result = aggregate_fold_payloads(dataset, folds, dataset_sha256=dataset_sha)
    tensor_keys = {"action_logits", "actions", "predictions", "adjusted_logits"}
    report = {key: value for key, value in result.items() if key not in tensor_keys}
    report.update(
        schema_version=1,
        experiment_id=args.experiment_id,
        dataset=str(dataset_path),
        dataset_sha256=dataset_sha,
        fold_paths=[str(path) for path in paths],
    )
    output_json = (
        Path(args.output_json)
        if args.output_json
        else Path("experiments/artifacts") / f"{args.experiment_id}_metrics.json"
    )
    output_logits = (
        Path(args.output_logits)
        if args.output_logits
        else Path("experiments/logits") / f"{args.experiment_id}_adjusted_logits.pt"
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_logits.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    torch.save(
        {
            "schema_version": 1,
            "experiment_id": args.experiment_id,
            "usage_scope": result["usage_scope"],
            "promotion_eligible": bool(result["promotion_eligible"]),
            "classes": ALL_CLASSES,
            "ids": dataset["ids"],
            "y_true": dataset["y_true"],
            "logits": result["adjusted_logits"],
            "predictions": result["predictions"],
            "route_action_logits": result["action_logits"],
            "route_actions": result["actions"],
            "dataset_sha256": dataset_sha,
        },
        output_logits,
    )
    if not args.no_results_csv:
        append_results_csv(
            Path("experiments/results.csv"),
            {
                "experiment_id": args.experiment_id,
                "model_family": "sim_recorded_policy_decoder_diagnostic_oof",
                "base_model": result["recipe"]["base_model"],
                "features": "prompt + full action-name trajectory + row-z main posterior 7d",
                "serializer_name": result["recipe"]["serializer_name"],
                "split_type": f"diagnostic_{result['cv_mode']}",
                "fold_id": "cv",
                "seed": result["recipe"]["seed"],
                "max_length": result["recipe"]["max_length"],
                "epochs": result["recipe"]["epochs"],
                "learning_rate": result["recipe"]["backbone_lr"],
                "batch_size": result["recipe"]["batch_size"],
                "macro_f1_raw": f"{result['full']['base_macro_f1']:.6f}",
                "macro_f1": f"{result['full']['macro_f1']:.6f}",
                "artifact_path": str(output_json),
                "val_logits_path": str(output_logits),
                "notes": (
                    f"diagnostic only; full_delta={result['full']['macro_delta']:+.6f}; "
                    f"sim_delta={result['sim']['macro_delta']:+.6f}; "
                    f"rescue={result['rescue']} harm={result['harm']}"
                ),
                "decision": (
                    "quality screen positive but promotion blocked pending strict nested-main"
                    if result["gate"]["quality_screen_passed"]
                    else "reject diagnostic quality screen"
                ),
            },
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
