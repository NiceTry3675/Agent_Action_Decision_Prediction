#!/usr/bin/env python3
"""Aggregate clean fixed-parent CV deltas for the live-pair specialist."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import torch

from build_weak4_pair_dataset import sha256_file, torch_load
from script import ALL_CLASSES
from train import append_results_csv, f1_metrics
from weak4_pair_residual import (
    DATASET_KIND,
    LIVE_WEAK4_PAIRS,
    apply_pair_residual,
    target_histogram,
    validate_dataset_scope,
)


def weak4_macro(metrics: dict) -> float:
    return sum(metrics["per_class_f1"][name] for name in ALL_CLASSES[:4]) / 4.0


def _full_route(payload: dict) -> dict[str, torch.Tensor]:
    return {
        "indices": torch.as_tensor(payload["route_indices"], dtype=torch.long),
        "pair_ids": torch.as_tensor(payload["pair_ids"], dtype=torch.long),
        "pair_classes": torch.as_tensor(payload["pair_classes"], dtype=torch.long),
        "top_sides": torch.as_tensor(payload["top_sides"], dtype=torch.long),
        "base_gaps": torch.as_tensor(payload["base_gaps"], dtype=torch.float32),
        "main_predictions": torch.as_tensor(payload["main_predictions"], dtype=torch.long),
        "alternative_predictions": torch.as_tensor(payload["alternative_predictions"], dtype=torch.long),
    }


def aggregate_fold_payloads(
    dataset: dict,
    folds: list[dict],
    dataset_sha256: str = "",
    allow_diagnostic: bool = False,
) -> dict:
    if dataset.get("kind") != DATASET_KIND:
        raise ValueError("unexpected pair dataset kind")
    scope_mode = validate_dataset_scope(
        dataset, allow_diagnostic=allow_diagnostic
    )
    n_folds = int(dataset["n_folds"])
    if len(folds) != n_folds:
        raise ValueError(f"expected {n_folds} fold payloads, got {len(folds)}")
    route_rows = len(dataset["route_ids"])
    raw_delta = torch.empty(route_rows, dtype=torch.float32)
    seen = torch.zeros(route_rows, dtype=torch.bool)
    delta_max_values = set()
    serializers = set()
    fold_sources = []
    token_audits = []
    canonical_recipe = None
    seen_fold_ids = set()
    for payload in folds:
        if payload.get("kind") != "weak4_pair_residual_fold_delta":
            raise ValueError("unexpected fold delta kind")
        fold_id = int(payload["fold_id"])
        if fold_id in seen_fold_ids or fold_id < 0 or fold_id >= n_folds:
            raise ValueError(f"duplicate or invalid fold id: {fold_id}")
        seen_fold_ids.add(fold_id)
        if int(payload.get("n_folds", -1)) != n_folds:
            raise ValueError("fold n_folds differs from dataset")
        if payload.get("usage_scope") != dataset.get("usage_scope") or payload.get(
            "validation_scope"
        ) != dataset.get("validation_scope"):
            raise ValueError("fold scope differs from dataset scope")
        recipe = payload.get("recipe")
        if not isinstance(recipe, dict):
            raise ValueError("fold payload is missing canonical recipe provenance")
        required_recipe = {
            "model_schema", "feature_schema", "base_model", "serializer_name",
            "seed", "max_length", "epochs", "encoder_lr", "head_lr",
            "batch_size", "eval_batch_size", "weight_decay", "warmup_ratio",
            "grad_clip", "delta_max", "fusion_dim", "dropout",
            "gradient_checkpointing",
        }
        if set(recipe) != required_recipe:
            raise ValueError(
                "fold recipe keys differ from the canonical contract: "
                f"missing={sorted(required_recipe - set(recipe))} "
                f"extra={sorted(set(recipe) - required_recipe)}"
            )
        if canonical_recipe is None:
            canonical_recipe = recipe
        elif recipe != canonical_recipe:
            raise ValueError("fold recipe provenance differs across folds")
        top_level_recipe = {
            "base_model": payload.get("base_model"),
            "seed": payload.get("seed"),
            "max_length": payload.get("max_length"),
            "epochs": payload.get("epochs"),
            "encoder_lr": payload.get("encoder_lr"),
            "head_lr": payload.get("head_lr"),
            "batch_size": payload.get("batch_size"),
            "eval_batch_size": payload.get("eval_batch_size"),
            "delta_max": payload.get("delta_max"),
            "fusion_dim": payload.get("fusion_dim"),
            "serializer_name": payload.get("serializer_name"),
        }
        mismatched = {
            key: (value, recipe.get(key))
            for key, value in top_level_recipe.items()
            if value != recipe.get(key)
        }
        if mismatched:
            raise ValueError(f"fold top-level recipe fields disagree: {mismatched}")
        if dataset_sha256 and payload.get("dataset_sha256") != dataset_sha256:
            raise ValueError("fold was produced from a different dataset artifact")
        rows = torch.as_tensor(payload["route_local_indices"], dtype=torch.long)
        values = torch.as_tensor(payload["raw_delta"], dtype=torch.float32).view(-1)
        if len(rows) != len(values) or len(rows) != len(payload.get("ids") or []):
            raise ValueError("fold route rows/deltas/ids lengths differ")
        if len(rows) and (int(rows.min()) < 0 or int(rows.max()) >= route_rows):
            raise ValueError("fold contains an out-of-range route row")
        if bool(seen[rows].any()):
            raise ValueError("fold route rows overlap")
        expected_ids = [dataset["route_ids"][row] for row in rows.tolist()]
        if expected_ids != list(payload["ids"]):
            raise ValueError("fold ids do not match dataset route rows")
        expected_fold = torch.as_tensor(dataset["route_fold_ids"], dtype=torch.long)[rows]
        if not bool((expected_fold == fold_id).all()):
            raise ValueError("fold payload contains rows assigned to another session fold")
        raw_delta[rows] = values
        seen[rows] = True
        delta_max_values.add(float(payload["delta_max"]))
        serializers.add(payload.get("serializer_name"))
        fold_sources.append(payload.get("experiment_id"))
        token_audits.append(payload.get("token_length_audit"))
    if not bool(seen.all()):
        raise ValueError(f"fold deltas do not cover all routes: missing={int((~seen).sum())}")
    if seen_fold_ids != set(range(n_folds)):
        raise ValueError(f"missing folds: {sorted(set(range(n_folds)) - seen_fold_ids)}")
    if len(delta_max_values) != 1 or len(serializers) != 1:
        raise ValueError("folds disagree on delta_max or serializer")

    delta_max = next(iter(delta_max_values))
    parent_logits = torch.as_tensor(dataset["parent_logits"], dtype=torch.float32)
    y_true = torch.as_tensor(dataset["y_true"], dtype=torch.long)
    route = _full_route(dataset)
    adjusted_logits, predictions, delta = apply_pair_residual(
        parent_logits, route, raw_delta, delta_max=delta_max
    )
    base_predictions = parent_logits.argmax(dim=1)
    base_metrics = f1_metrics(y_true.tolist(), base_predictions.tolist())
    metrics = f1_metrics(y_true.tolist(), predictions.tolist())

    routed = route["indices"]
    route_y = y_true[routed]
    base_route = base_predictions[routed]
    new_route = predictions[routed]
    changed_mask = base_route != new_route
    rescue = int(((base_route != route_y) & (new_route == route_y)).sum())
    harm = int(((base_route == route_y) & (new_route != route_y)).sum())
    neutral = int((changed_mask & (base_route != route_y) & (new_route != route_y)).sum())
    fold_metrics = []
    full_folds = torch.as_tensor(dataset["full_fold_ids"], dtype=torch.long)
    for fold_id in range(n_folds):
        mask = full_folds == fold_id
        base_fold = f1_metrics(y_true[mask].tolist(), base_predictions[mask].tolist())
        new_fold = f1_metrics(y_true[mask].tolist(), predictions[mask].tolist())
        fold_metrics.append(
            {
                "fold_id": fold_id,
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
        "classes": ALL_CLASSES,
        "live_pairs": [list(pair) for pair in LIVE_WEAK4_PAIRS],
        "serializer_name": next(iter(serializers)),
        "usage_scope": dataset["usage_scope"],
        "validation_scope": dataset["validation_scope"],
        "scope_mode": scope_mode,
        "recipe": canonical_recipe,
        "base_model": canonical_recipe["base_model"],
        "seed": canonical_recipe["seed"],
        "delta_max": delta_max,
        "fold_sources": fold_sources,
        "fold_token_length_audits": token_audits,
        "rows": len(y_true),
        "routed_rows": len(routed),
        "target_histogram": target_histogram(dataset["target_kinds"]),
        "base_metrics": base_metrics,
        "metrics": metrics,
        "macro_delta": metrics["macro_f1"] - base_metrics["macro_f1"],
        "base_weak4_macro_f1": weak4_macro(base_metrics),
        "weak4_macro_f1": weak4_macro(metrics),
        "weak4_delta": weak4_macro(metrics) - weak4_macro(base_metrics),
        "fold_metrics": fold_metrics,
        "rescue": rescue,
        "harm": harm,
        "neutral_changes": neutral,
        "changed": int(changed_mask.sum()),
        "rescue_harm_ratio": (rescue / harm) if harm else None,
        "raw_delta": raw_delta,
        "delta": delta,
        "adjusted_logits": adjusted_logits,
        "predictions": predictions,
    }


def promotion_gate(result: dict, macro_min: float, weak4_min: float, ratio_min: float, max_drop: float):
    per_class_delta = {
        label: result["metrics"]["per_class_f1"][label]
        - result["base_metrics"]["per_class_f1"][label]
        for label in ALL_CLASSES[:4]
    }
    checks = {
        "macro_delta": result["macro_delta"] >= macro_min,
        "weak4_delta": result["weak4_delta"] >= weak4_min,
        "all_folds_positive": all(fold["macro_delta"] > 0 for fold in result["fold_metrics"]),
        "rescue_harm_ratio": (
            result["rescue"] > 0 if result["harm"] == 0
            else result["rescue"] / result["harm"] >= ratio_min
        ),
        "no_weak4_drop": all(delta >= -max_drop for delta in per_class_delta.values()),
    }
    return {"passed": all(checks.values()), "checks": checks, "per_class_delta": per_class_delta}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="experiments/artifacts/20260712_weak4_pair_fixed_parent_dataset.pt",
    )
    parser.add_argument("--fold-pattern", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-logits", default="")
    parser.add_argument("--macro-min", type=float, default=0.003)
    parser.add_argument("--weak4-min", type=float, default=0.0105)
    parser.add_argument("--ratio-min", type=float, default=1.3)
    parser.add_argument("--max-weak4-drop", type=float, default=0.005)
    parser.add_argument("--no-results-csv", action="store_true")
    parser.add_argument(
        "--allow-diagnostic-dataset",
        action="store_true",
        help="allow construction-only nonfixed/consensus dataset scopes",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    dataset_path = Path(args.dataset)
    dataset_sha = sha256_file(dataset_path)
    dataset = torch_load(dataset_path)
    fold_paths = sorted(Path(path) for path in glob.glob(args.fold_pattern))
    if not fold_paths:
        raise FileNotFoundError(f"no fold deltas match {args.fold_pattern!r}")
    folds = [torch_load(path) for path in fold_paths]
    result = aggregate_fold_payloads(
        dataset,
        folds,
        dataset_sha256=dataset_sha,
        allow_diagnostic=args.allow_diagnostic_dataset,
    )
    gate = promotion_gate(
        result, args.macro_min, args.weak4_min, args.ratio_min, args.max_weak4_drop
    )
    output_json = Path(args.output_json) if args.output_json else Path("experiments/artifacts") / f"{args.experiment_id}_metrics.json"
    output_logits = Path(args.output_logits) if args.output_logits else Path("experiments/logits") / f"{args.experiment_id}_fold_all_pair_residual.pt"
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_logits.parent.mkdir(parents=True, exist_ok=True)
    tensor_keys = {"raw_delta", "delta", "adjusted_logits", "predictions"}
    report = {key: value for key, value in result.items() if key not in tensor_keys}
    report.update(
        experiment_id=args.experiment_id,
        dataset=str(dataset_path),
        dataset_sha256=dataset_sha,
        fold_paths=[str(path) for path in fold_paths],
        gate=gate,
    )
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    torch.save(
        {
            "experiment_id": args.experiment_id,
            "classes": ALL_CLASSES,
            "ids": dataset["ids"],
            "y_true": dataset["y_true"],
            "logits": result["adjusted_logits"],
            "predictions": result["predictions"],
            "raw_delta": result["raw_delta"],
            "delta": result["delta"],
            "dataset_sha256": dataset_sha,
        },
        output_logits,
    )
    if not args.no_results_csv:
        append_results_csv(
            Path("experiments/results.csv"),
            {
                "experiment_id": args.experiment_id,
                "model_family": "weak4_live_pair_residual",
                "base_model": result["base_model"],
                "features": "policy serializer + main numeric features + live pair",
                "serializer_name": result["serializer_name"],
                "split_type": (
                    "clean_fixed_parent_session_cv"
                    if result["scope_mode"] == "clean"
                    else "construction_diagnostic_session_cv"
                ),
                "fold_id": "cv",
                "seed": result["seed"],
                "macro_f1_raw": f"{result['base_metrics']['macro_f1']:.6f}",
                "macro_f1": f"{result['metrics']['macro_f1']:.6f}",
                "weakest_classes": ";".join(
                    f"{label}:{result['metrics']['per_class_f1'][label]:.4f}"
                    for label in ALL_CLASSES[:4]
                ),
                "artifact_path": str(output_json),
                "val_logits_path": str(output_logits),
                "notes": f"macro_delta={result['macro_delta']:+.6f}; weak4_delta={result['weak4_delta']:+.6f}",
                "decision": "pass offline gate" if gate["passed"] else "reject offline gate",
            },
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"saved {output_json} and {output_logits}")


if __name__ == "__main__":
    main()
