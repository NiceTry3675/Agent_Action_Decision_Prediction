#!/usr/bin/env python3
"""Aggregate Weak4 full-residual folds and enforce the clean OOF gate."""

from __future__ import annotations

import argparse
import glob
import json
import math
import shlex
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch

from build_weak4_full_dataset import DATASET_FORMAT, sha256_file, torch_load
from script import ALL_CLASSES
from train import append_results_csv, f1_metrics
from train_weak4_full_residual import FOLD_FORMAT, validate_dataset, weak4_macro
from weak4_full_residual import WEAK4_CLASSES, apply_residual_actions


def residual_f1(y_true: Sequence[int], y_pred: Sequence[int]) -> dict[str, Any]:
    counts = {idx: {"tp": 0, "fp": 0, "fn": 0} for idx in range(5)}
    for truth, pred in zip(y_true, y_pred):
        if truth == pred:
            counts[truth]["tp"] += 1
        else:
            counts[truth]["fn"] += 1
            counts[pred]["fp"] += 1
    per_class = {}
    for idx, values in counts.items():
        denom = 2 * values["tp"] + values["fp"] + values["fn"]
        per_class[str(idx)] = 0.0 if not denom else 2 * values["tp"] / denom
    return {
        "macro_f1": sum(per_class.values()) / 5.0,
        "per_class_f1": per_class,
    }


def outcome_counts(
    truth: torch.Tensor,
    baseline: torch.Tensor,
    candidate: torch.Tensor,
    indices: torch.Tensor,
) -> dict[str, Any]:
    base = baseline[indices]
    final = candidate[indices]
    labels = truth[indices]
    rescue = int(((base != labels) & (final == labels)).sum())
    harm = int(((base == labels) & (final != labels)).sum())
    changed = int((base != final).sum())
    return {
        "rows": len(indices),
        "changed": changed,
        "rescue": rescue,
        "harm": harm,
        "net": rescue - harm,
        "rescue_harm_ratio": None if harm == 0 else rescue / harm,
    }


def load_folds(pattern: str) -> list[tuple[Path, dict[str, Any]]]:
    paths = [Path(value) for value in sorted(glob.glob(pattern))]
    if not paths:
        raise FileNotFoundError(f"no fold payload matched {pattern!r}")
    payloads = []
    for path in paths:
        payload = torch_load(path)
        if not isinstance(payload, dict) or payload.get("format") != FOLD_FORMAT:
            raise ValueError(f"{path} does not have format {FOLD_FORMAT!r}")
        payloads.append((path, payload))
    return payloads


def aggregate(
    dataset_path: str | Path,
    pattern: str,
    *,
    min_macro_delta: float = 0.003,
    min_weak4_delta: float = 0.0105,
    min_rescue_harm: float = 1.3,
    max_class_drop: float = 0.005,
    adjusted_logits_output: str | Path | None = None,
) -> dict[str, Any]:
    dataset_path = Path(dataset_path)
    dataset = validate_dataset(torch_load(dataset_path))
    if dataset.get("format") != DATASET_FORMAT:
        raise ValueError("unexpected dataset format")
    dataset_sha = sha256_file(dataset_path)
    folds = load_folds(pattern)
    n_folds_values = {int(payload["n_folds"]) for _, payload in folds}
    seed_values = {int(payload["seed"]) for _, payload in folds}
    fold_ids = [int(payload["fold_id"]) for _, payload in folds]
    if len(n_folds_values) != 1 or len(seed_values) != 1:
        raise ValueError("fold payloads disagree on n_folds or seed")
    n_folds = next(iter(n_folds_values))
    seed = next(iter(seed_values))
    if sorted(fold_ids) != list(range(n_folds)):
        raise ValueError(f"expected exactly folds 0..{n_folds - 1}, got {sorted(fold_ids)}")

    parent_logits = torch.as_tensor(dataset["parent_logits"]).float()
    truth = torch.as_tensor(dataset["y_true"]).long()
    baseline = parent_logits.argmax(dim=1)
    candidate = baseline.clone()
    all_val_full: list[int] = []
    all_val_routes: list[int] = []
    fold_reports = []
    model_configs: set[str] = set()
    parameter_counts: set[int] = set()
    route_row_by_full = {
        int(row["full_index"]): row for row in dataset["route_rows"]
    }
    for path, payload in sorted(folds, key=lambda item: int(item[1]["fold_id"])):
        if payload.get("dataset_sha256") != dataset_sha:
            raise ValueError(f"{path} was produced from a different dataset file")
        if payload.get("dataset_ids_sha256") != dataset["ids_sha256"]:
            raise ValueError(f"{path} dataset ID digest mismatch")
        if list(payload.get("classes") or []) != ALL_CLASSES:
            raise ValueError(f"{path} class order mismatch")
        val_full = torch.as_tensor(payload["val_full_indices"], dtype=torch.long)
        val_routes = torch.as_tensor(payload["val_route_full_indices"], dtype=torch.long)
        probabilities = torch.as_tensor(payload["action_probs"], dtype=torch.float32)
        thresholds = torch.as_tensor(payload["thresholds"], dtype=torch.float32)
        if probabilities.shape != (len(val_routes), 5):
            raise ValueError(f"{path} action probability shape mismatch")
        if thresholds.shape != (4, 4) or not bool(torch.isfinite(thresholds).all()):
            raise ValueError(f"{path} threshold matrix is invalid")
        if not bool(torch.allclose(thresholds.diag(), torch.ones(4))):
            raise ValueError(f"{path} threshold diagonal must be one")
        if not set(val_routes.tolist()).issubset(set(val_full.tolist())):
            raise ValueError(f"{path} routed validation rows are not inside the full fold")
        expected_ids = [dataset["ids"][idx] for idx in val_routes.tolist()]
        if list(payload["ids"]) != expected_ids:
            raise ValueError(f"{path} routed ID order mismatch")
        expected_sessions = {dataset["session_ids"][idx] for idx in val_full.tolist()}
        val_sessions = set(payload.get("val_sessions") or [])
        calibration_sessions = set(payload.get("calibration_sessions") or [])
        if val_sessions != expected_sessions:
            raise ValueError(f"{path} validation session provenance mismatch")
        if val_sessions & calibration_sessions:
            raise ValueError(f"{path} leaked outer validation sessions into threshold calibration")
        model_configs.add(json.dumps(payload.get("model_config") or {}, sort_keys=True))
        parameter_counts.add(int(payload.get("parameter_count", -1)))
        fold_candidate = apply_residual_actions(
            parent_logits, val_routes, probabilities, thresholds
        )
        candidate[val_routes] = fold_candidate[val_routes]
        all_val_full.extend(val_full.tolist())
        all_val_routes.extend(val_routes.tolist())
        base_metrics = f1_metrics(truth[val_full].tolist(), baseline[val_full].tolist())
        final_metrics = f1_metrics(truth[val_full].tolist(), candidate[val_full].tolist())
        outcomes = outcome_counts(truth, baseline, candidate, val_routes)
        recomputed = {
            "fold_id": int(payload["fold_id"]),
            "path": str(path),
            "full_rows": len(val_full),
            "route_rows": len(val_routes),
            "baseline_macro_f1": base_metrics["macro_f1"],
            "candidate_macro_f1": final_metrics["macro_f1"],
            "macro_delta": final_metrics["macro_f1"] - base_metrics["macro_f1"],
            "baseline_weak4_macro_f1": weak4_macro(base_metrics),
            "candidate_weak4_macro_f1": weak4_macro(final_metrics),
            "weak4_delta": weak4_macro(final_metrics) - weak4_macro(base_metrics),
            "outcomes": outcomes,
        }
        saved_delta = float((payload.get("metrics") or {}).get("macro_delta", math.nan))
        if not math.isclose(saved_delta, recomputed["macro_delta"], rel_tol=0, abs_tol=1e-12):
            raise ValueError(f"{path} saved/recomputed fold metric mismatch")
        fold_reports.append(recomputed)

    if len(all_val_full) != len(dataset["ids"]) or len(set(all_val_full)) != len(all_val_full):
        raise ValueError("outer full folds do not cover every dataset row exactly once")
    if len(model_configs) != 1 or len(parameter_counts) != 1 or min(parameter_counts) <= 0:
        raise ValueError("fold model configs or parameter counts are inconsistent")
    model_config = json.loads(next(iter(model_configs)))
    parameter_count = next(iter(parameter_counts))
    expected_routes = torch.as_tensor(dataset["route_indices"], dtype=torch.long).tolist()
    if sorted(all_val_routes) != sorted(expected_routes) or len(set(all_val_routes)) != len(all_val_routes):
        raise ValueError("outer folds do not cover every routed row exactly once")
    route_set = set(expected_routes)
    nonroute = torch.tensor(
        [idx for idx in range(len(baseline)) if idx not in route_set], dtype=torch.long
    )
    nonroute_identity = bool(torch.equal(candidate[nonroute], baseline[nonroute]))
    if not nonroute_identity:
        raise AssertionError("nonrouted predictions changed")
    if bool((candidate < 0).any()) or bool((candidate >= len(ALL_CLASSES)).any()):
        raise AssertionError("candidate predictions left the canonical class range")

    baseline_metrics = f1_metrics(truth.tolist(), baseline.tolist())
    candidate_metrics = f1_metrics(truth.tolist(), candidate.tolist())
    weak_deltas = {
        label: candidate_metrics["per_class_f1"][label] - baseline_metrics["per_class_f1"][label]
        for label in WEAK4_CLASSES
    }
    global_outcomes = outcome_counts(
        truth, baseline, candidate, torch.tensor(expected_routes, dtype=torch.long)
    )
    route_targets = [int(route_row_by_full[idx]["target_action"]) for idx in expected_routes]
    residual_predictions = [
        0 if int(candidate[idx]) == int(baseline[idx]) else int(candidate[idx]) + 1
        for idx in expected_routes
    ]
    residual_metrics = residual_f1(route_targets, residual_predictions)
    true_nonweak_keep_indices = torch.tensor(
        [idx for idx in expected_routes if int(truth[idx]) >= 4], dtype=torch.long
    )
    true_nonweak_keep = {
        "rows": len(true_nonweak_keep_indices),
        "changed": int(
            (candidate[true_nonweak_keep_indices] != baseline[true_nonweak_keep_indices]).sum()
        ) if len(true_nonweak_keep_indices) else 0,
        "true_label_histogram": dict(
            sorted(
                Counter(
                    ALL_CLASSES[int(truth[idx])] for idx in true_nonweak_keep_indices.tolist()
                ).items()
            )
        ),
    }

    direction_report: dict[str, Any] = {}
    for source in range(4):
        for target in range(4):
            if source == target:
                continue
            indices = torch.tensor(
                [
                    idx for idx in expected_routes
                    if int(baseline[idx]) == source and int(candidate[idx]) == target
                ],
                dtype=torch.long,
            )
            if not len(indices):
                continue
            direction_report[f"{WEAK4_CLASSES[source]}->{WEAK4_CLASSES[target]}"] = outcome_counts(
                truth, baseline, candidate, indices
            )

    macro_delta = candidate_metrics["macro_f1"] - baseline_metrics["macro_f1"]
    weak_delta = weak4_macro(candidate_metrics) - weak4_macro(baseline_metrics)
    ratio = global_outcomes["rescue_harm_ratio"]
    adjusted_logits_path = None
    adjusted_logits_sha256 = None
    if adjusted_logits_output is not None:
        adjusted_logits_path = Path(adjusted_logits_output)
        adjusted_logits_path.parent.mkdir(parents=True, exist_ok=True)
        adjusted_logits = parent_logits.clone()
        changed_rows = (candidate != baseline).nonzero(as_tuple=False).squeeze(1)
        if len(changed_rows):
            row_max = adjusted_logits[changed_rows].max(dim=1).values
            promoted = torch.nextafter(row_max, torch.full_like(row_max, float("inf")))
            adjusted_logits[changed_rows, candidate[changed_rows]] = promoted
        if not torch.equal(adjusted_logits.argmax(dim=1), candidate):
            raise AssertionError("adjusted logits do not reproduce OOF candidate predictions")
        torch.save(
            {
                "format": "weak4-full-residual-adjusted-logits-v1",
                "classes": ALL_CLASSES,
                "ids": dataset["ids"],
                "y_true": truth,
                "logits": adjusted_logits,
                "predictions": candidate,
                "split": "clean_fixed_parent_session_cv",
                "seed": seed,
                "n_folds": n_folds,
                "model_config": model_config,
                "parameter_count": parameter_count,
                "parent": dataset.get("parent"),
                "dataset_sha256": dataset_sha,
                "fold_prediction_paths": [str(path) for path, _ in folds],
                "adjustment_contract": "parent logits unchanged except nextafter target promotion on OOF-selected switches",
                "class_bias_applied": False,
            },
            adjusted_logits_path,
        )
        adjusted_logits_sha256 = sha256_file(adjusted_logits_path)

    gate_checks = {
        "full_macro_delta": macro_delta >= min_macro_delta,
        "weak4_macro_delta": weak_delta >= min_weak4_delta,
        "all_fold_deltas_positive": all(row["macro_delta"] > 0.0 for row in fold_reports),
        "rescue_harm_ratio": global_outcomes["harm"] == 0
        and global_outcomes["rescue"] > 0
        or ratio is not None and ratio >= min_rescue_harm,
        "no_weak4_class_drop": all(value >= -max_class_drop for value in weak_deltas.values()),
        "nonroute_identity": nonroute_identity,
    }
    report = {
        "format": "weak4-full-residual-cv-report-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "dataset_sha256": dataset_sha,
        "parent": dataset.get("parent"),
        "fold_prediction_paths": [str(path) for path, _ in folds],
        "adjusted_logits_path": str(adjusted_logits_path) if adjusted_logits_path else None,
        "adjusted_logits_sha256": adjusted_logits_sha256,
        "classes": ALL_CLASSES,
        "rows": len(dataset["ids"]),
        "routed_rows": len(expected_routes),
        "n_folds": n_folds,
        "seed": seed,
        "model_config": model_config,
        "parameter_count": parameter_count,
        "folds": fold_reports,
        "baseline": {
            "macro_f1": baseline_metrics["macro_f1"],
            "weak4_macro_f1": weak4_macro(baseline_metrics),
            "per_class_f1": baseline_metrics["per_class_f1"],
        },
        "candidate": {
            "macro_f1": candidate_metrics["macro_f1"],
            "macro_delta": macro_delta,
            "weak4_macro_f1": weak4_macro(candidate_metrics),
            "weak4_delta": weak_delta,
            "weak4_per_class_delta": weak_deltas,
            "per_class_f1": candidate_metrics["per_class_f1"],
            "prediction_distribution": candidate_metrics["prediction_distribution"],
            "top_confusions": candidate_metrics["top_confusions"],
        },
        "residual_target_metrics": residual_metrics,
        "true_nonweak_keep": true_nonweak_keep,
        "outcomes": global_outcomes,
        "directions": direction_report,
        "gate": {
            "thresholds": {
                "min_macro_delta": min_macro_delta,
                "min_weak4_delta": min_weak4_delta,
                "min_rescue_harm": min_rescue_harm,
                "max_class_drop": max_class_drop,
            },
            "checks": gate_checks,
            "passed": all(gate_checks.values()),
        },
    }
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--adjusted-logits-output", default="")
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--min-macro-delta", type=float, default=0.003)
    parser.add_argument("--min-weak4-delta", type=float, default=0.0105)
    parser.add_argument("--min-rescue-harm", type=float, default=1.3)
    parser.add_argument("--max-class-drop", type=float, default=0.005)
    parser.add_argument("--append-results", action="store_true")
    parser.add_argument("--results-csv", default="experiments/results.csv")
    parser.add_argument("--notes", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    output = Path(args.output)
    adjusted_logits_output = (
        Path(args.adjusted_logits_output)
        if args.adjusted_logits_output
        else output.with_name(f"{output.stem}_adjusted_logits.pt")
    )
    report = aggregate(
        args.dataset,
        args.pattern,
        min_macro_delta=args.min_macro_delta,
        min_weak4_delta=args.min_weak4_delta,
        min_rescue_harm=args.min_rescue_harm,
        max_class_drop=args.max_class_drop,
        adjusted_logits_output=adjusted_logits_output,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    experiment_id = args.experiment_id or output.stem
    if args.append_results:
        weakest = sorted(
            report["candidate"]["per_class_f1"].items(), key=lambda item: item[1]
        )[:5]
        append_results_csv(
            Path(args.results_csv),
            {
                "experiment_id": experiment_id,
                "model_family": "torch_weak4_full_residual_oof",
                "base_model": (report.get("parent") or {}).get("metadata", {}).get("base_model", ""),
                "features": "typed prompt/event/workspace/main-logit residual inputs",
                "serializer_name": "weak4_full_v1",
                "split_type": "clean_fixed_parent_session_cv",
                "fold_id": "oof",
                "seed": report["seed"],
                "macro_f1_raw": f"{report['baseline']['macro_f1']:.6f}",
                "macro_f1": f"{report['candidate']['macro_f1']:.6f}",
                "weakest_classes": ";".join(f"{name}:{score:.4f}" for name, score in weakest),
                "top_confusions": json.dumps(report["candidate"]["top_confusions"][:8], ensure_ascii=False),
                "prediction_distribution": json.dumps(report["candidate"]["prediction_distribution"], ensure_ascii=False, sort_keys=True),
                "artifact_path": str(output),
                "val_logits_path": str(adjusted_logits_output),
                "train_command": shlex.join(sys.argv),
                "notes": (
                    f"{args.notes}; fold_prediction_pattern={args.pattern}"
                    if args.notes else f"fold_prediction_pattern={args.pattern}"
                ),
                "decision": "promote" if report["gate"]["passed"] else "reject",
            },
        )
    print(
        f"saved {output} macro={report['candidate']['macro_f1']:.6f} "
        f"delta={report['candidate']['macro_delta']:+.6f} "
        f"weak4_delta={report['candidate']['weak4_delta']:+.6f} "
        f"gate={'PASS' if report['gate']['passed'] else 'FAIL'}"
    )


if __name__ == "__main__":
    main()
