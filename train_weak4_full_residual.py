#!/usr/bin/env python3
"""Train one clean session-OOF fold of the full Weak4 residual specialist."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import shlex
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch

from build_weak4_full_dataset import DATASET_FORMAT, sha256_file, torch_load
from script import ALL_CLASSES
from train import append_results_csv, f1_metrics
from weak4_full_residual import (
    FeatureStats,
    Weak4FullResidualNet,
    Weak4ModelConfig,
    action_probabilities,
    apply_residual_actions,
    collate_weak4_full,
    direction_weights,
    fit_direction_thresholds,
    fit_feature_stats,
    split_fit_calibration_sessions,
    stratified_group_fold_ids,
    tensor_dict_to_device,
    weak4_full_loss,
)


FOLD_FORMAT = "weak4-full-residual-fold-v1"


def validate_dataset(
    payload: Any, *, allow_construction_only: bool = False
) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("format") != DATASET_FORMAT:
        raise ValueError(f"dataset must have format {DATASET_FORMAT!r}")
    if list(payload.get("classes") or []) != ALL_CLASSES:
        raise ValueError("dataset class order is not canonical")
    usage_scope = payload.get("usage_scope")
    if usage_scope != "clean_fixed_parent_session_cv":
        if not (allow_construction_only and usage_scope == "construction_only_no_promotion"):
            raise ValueError(
                "training requires a clean_fixed_parent_session_cv dataset; "
                f"got usage_scope={usage_scope!r}"
            )
    parent = payload.get("parent") or {}
    if parent.get("score_contract") != "payload_logits_unmodified_raw_argmax":
        raise ValueError("dataset parent score contract is not raw/unmodified")
    if parent.get("class_bias_applied") is not False:
        raise ValueError("dataset parent must have class_bias_applied=false")
    if usage_scope == "clean_fixed_parent_session_cv":
        raw_macro = parent.get("raw_argmax_macro_f1")
        if raw_macro is None or not math.isfinite(float(raw_macro)):
            raise ValueError("clean fixed parent raw argmax metric provenance is missing")
        contract = payload.get("clean_fixed_contract") or {}
        if contract.get("split") != "session" or int(contract.get("seed", -1)) != 42:
            raise ValueError("clean fixed dataset split provenance is invalid")
        if int(contract.get("expected_rows", -1)) != len(payload.get("ids") or []):
            raise ValueError("clean fixed dataset expected row count mismatch")
        if contract.get("expected_ids_sha256") != payload.get("ids_sha256"):
            raise ValueError("clean fixed dataset expected ID digest mismatch")
    required = {
        "ids", "session_ids", "y_true", "parent_logits", "parent_pred",
        "route_indices", "route_rows", "ids_sha256",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"dataset is missing keys: {missing}")
    rows = payload["route_rows"]
    full_count = len(payload["ids"])
    if len(payload["session_ids"]) != full_count:
        raise ValueError("dataset session_ids row mismatch")
    if tuple(torch.as_tensor(payload["parent_logits"]).shape) != (full_count, len(ALL_CLASSES)):
        raise ValueError("dataset parent_logits shape mismatch")
    if len(rows) != len(payload["route_indices"]):
        raise ValueError("dataset route row mismatch")
    route_from_rows = [int(row["full_index"]) for row in rows]
    if route_from_rows != torch.as_tensor(payload["route_indices"]).tolist():
        raise ValueError("route_rows are not aligned with route_indices")
    if len(route_from_rows) != len(set(route_from_rows)):
        raise ValueError("duplicate full_index in route rows")
    return payload


def full_fold_ids(dataset: Mapping[str, Any], n_folds: int, seed: int) -> list[int]:
    route_targets = {
        int(row["full_index"]): int(row["target_action"])
        for row in dataset["route_rows"]
    }
    # Label 5 is the no-route identity stratum.  It balances total fold size
    # without conflating an untouched row with residual KEEP.
    labels = [route_targets.get(index, 5) for index in range(len(dataset["ids"]))]
    return stratified_group_fold_ids(dataset["session_ids"], labels, n_folds, seed)


def batch_order(indices: Sequence[int], batch_size: int, seed: int | None = None) -> list[list[int]]:
    values = list(indices)
    if seed is not None:
        random.Random(seed).shuffle(values)
    return [values[start:start + batch_size] for start in range(0, len(values), batch_size)]


@torch.no_grad()
def predict_rows(
    model: Weak4FullResidualNet,
    rows: Sequence[Mapping[str, Any]],
    indices: Sequence[int],
    stats: FeatureStats,
    batch_size: int,
    device: torch.device,
    pair_weights: torch.Tensor,
    pos_weight: float,
) -> tuple[torch.Tensor, float]:
    model.eval()
    probabilities: list[torch.Tensor] = []
    loss_sum = 0.0
    row_count = 0
    for selected in batch_order(indices, batch_size):
        host_batch = collate_weak4_full([rows[idx] for idx in selected], stats)
        batch = tensor_dict_to_device(host_batch, device)
        outputs = model(batch)
        loss, _ = weak4_full_loss(
            outputs, batch, pair_weights, pos_weight=pos_weight
        )
        probabilities.append(action_probabilities(outputs, batch["main_pred"]).cpu())
        loss_sum += float(loss) * len(selected)
        row_count += len(selected)
    return torch.cat(probabilities, dim=0), loss_sum / max(row_count, 1)


def weak4_macro(metrics: Mapping[str, Any]) -> float:
    return sum(float(metrics["per_class_f1"][name]) for name in ALL_CLASSES[:4]) / 4.0


def fold_metrics(
    dataset: Mapping[str, Any],
    val_full_indices: Sequence[int],
    val_route_full_indices: Sequence[int],
    probabilities: torch.Tensor,
    thresholds: torch.Tensor,
) -> dict[str, Any]:
    parent_logits = torch.as_tensor(dataset["parent_logits"]).float()
    baseline = parent_logits.argmax(dim=1)
    candidate = apply_residual_actions(
        parent_logits, val_route_full_indices, probabilities, thresholds
    )
    truth = torch.as_tensor(dataset["y_true"]).long()
    selected = torch.as_tensor(val_full_indices, dtype=torch.long)
    base_metrics = f1_metrics(truth[selected].tolist(), baseline[selected].tolist())
    candidate_metrics = f1_metrics(truth[selected].tolist(), candidate[selected].tolist())
    routed = torch.as_tensor(val_route_full_indices, dtype=torch.long)
    changed = baseline[routed] != candidate[routed]
    rescue = int(((baseline[routed] != truth[routed]) & (candidate[routed] == truth[routed])).sum())
    harm = int(((baseline[routed] == truth[routed]) & (candidate[routed] != truth[routed])).sum())
    return {
        "baseline_macro_f1": base_metrics["macro_f1"],
        "candidate_macro_f1": candidate_metrics["macro_f1"],
        "macro_delta": candidate_metrics["macro_f1"] - base_metrics["macro_f1"],
        "baseline_weak4_macro_f1": weak4_macro(base_metrics),
        "candidate_weak4_macro_f1": weak4_macro(candidate_metrics),
        "weak4_delta": weak4_macro(candidate_metrics) - weak4_macro(base_metrics),
        "candidate_per_class_f1": candidate_metrics["per_class_f1"],
        "top_confusions": candidate_metrics["top_confusions"],
        "prediction_distribution": candidate_metrics["prediction_distribution"],
        "routed": len(routed),
        "changed": int(changed.sum()),
        "rescue": rescue,
        "harm": harm,
    }


def train_fold(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset)
    dataset = validate_dataset(
        torch_load(dataset_path),
        allow_construction_only=args.allow_construction_only_dataset,
    )
    rows: list[Mapping[str, Any]] = dataset["route_rows"]
    folds = full_fold_ids(dataset, args.n_folds, args.seed)
    val_full_indices = [idx for idx, fold in enumerate(folds) if fold == args.fold_id]
    train_full_set = {idx for idx, fold in enumerate(folds) if fold != args.fold_id}
    val_route_positions = [
        idx for idx, row in enumerate(rows) if int(row["full_index"]) not in train_full_set
    ]
    train_route_positions = [
        idx for idx, row in enumerate(rows) if int(row["full_index"]) in train_full_set
    ]
    if not val_route_positions or not train_route_positions:
        raise ValueError("outer fold produced an empty route partition")
    fit_positions, calibration_positions = split_fit_calibration_sessions(
        rows,
        train_route_positions,
        args.inner_calibration_fraction,
        args.seed + 1000 + args.fold_id,
    )
    fit_sessions = {str(rows[idx]["session_id"]) for idx in fit_positions}
    calibration_sessions = {str(rows[idx]["session_id"]) for idx in calibration_positions}
    val_sessions = {str(dataset["session_ids"][idx]) for idx in val_full_indices}
    if fit_sessions & calibration_sessions or calibration_sessions & val_sessions or fit_sessions & val_sessions:
        raise AssertionError("session leakage across fit/calibration/outer validation")

    torch.manual_seed(args.seed + args.fold_id)
    random.seed(args.seed + args.fold_id)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed + args.fold_id)
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)

    stats = fit_feature_stats([rows[idx] for idx in fit_positions])
    pair_weights = direction_weights([rows[idx] for idx in fit_positions])
    positives = sum(int(rows[idx]["change_target"]) for idx in fit_positions)
    negatives = len(fit_positions) - positives
    if positives == 0 or negatives == 0:
        raise ValueError("fit split must contain both KEEP and SWITCH targets")
    pos_weight = negatives / positives
    config = Weak4ModelConfig(
        byte_embed_dim=args.byte_embed_dim,
        byte_hidden_dim=args.byte_hidden_dim,
        byte_conv_layers=args.byte_conv_layers,
        event_dim=args.event_dim,
        event_layers=args.event_layers,
        event_heads=args.event_heads,
        fusion_dim=args.fusion_dim,
        dropout=args.dropout,
    )
    model = Weak4FullResidualNet(config).to(device)
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    trainable_parameter_count = sum(
        parameter.numel() for parameter in model.parameters() if parameter.requires_grad
    )
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay
    )
    best_state = None
    best_epoch = 0
    best_calibration_loss = math.inf
    stale = 0
    history = []
    for epoch in range(1, args.epochs + 1):
        model.train()
        train_loss = 0.0
        train_count = 0
        for selected in batch_order(
            fit_positions, args.batch_size, args.seed + args.fold_id * 1000 + epoch
        ):
            host_batch = collate_weak4_full([rows[idx] for idx in selected], stats)
            batch = tensor_dict_to_device(host_batch, device)
            optimizer.zero_grad(set_to_none=True)
            outputs = model(batch)
            loss, _ = weak4_full_loss(
                outputs, batch, pair_weights, pos_weight=pos_weight
            )
            if not bool(torch.isfinite(loss)):
                raise FloatingPointError(f"nonfinite training loss at epoch {epoch}")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.max_grad_norm)
            optimizer.step()
            train_loss += float(loss.detach()) * len(selected)
            train_count += len(selected)
        _, calibration_loss = predict_rows(
            model,
            rows,
            calibration_positions,
            stats,
            args.eval_batch_size,
            device,
            pair_weights,
            pos_weight,
        )
        epoch_report = {
            "epoch": epoch,
            "train_loss": train_loss / max(train_count, 1),
            "calibration_loss": calibration_loss,
        }
        history.append(epoch_report)
        print(
            f"fold={args.fold_id} epoch={epoch} "
            f"train_loss={epoch_report['train_loss']:.6f} "
            f"cal_loss={calibration_loss:.6f}"
        )
        if calibration_loss < best_calibration_loss - 1e-6:
            best_calibration_loss = calibration_loss
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            stale = 0
        else:
            stale += 1
            if stale >= args.patience:
                break
    if best_state is None:
        raise AssertionError("training did not produce a best checkpoint")
    model.load_state_dict(best_state)

    calibration_probs, _ = predict_rows(
        model, rows, calibration_positions, stats, args.eval_batch_size,
        device, pair_weights, pos_weight,
    )
    calibration_main = [int(rows[idx]["typed"]["main_pred"]) for idx in calibration_positions]
    calibration_truth = [int(rows[idx]["y_true"]) for idx in calibration_positions]
    thresholds, threshold_meta = fit_direction_thresholds(
        calibration_main,
        calibration_truth,
        calibration_probs,
        global_threshold=args.global_threshold,
        min_support=args.threshold_min_support,
    )
    val_probs, val_loss = predict_rows(
        model, rows, val_route_positions, stats, args.eval_batch_size,
        device, pair_weights, pos_weight,
    )
    val_route_full_indices = [int(rows[idx]["full_index"]) for idx in val_route_positions]
    metrics = fold_metrics(
        dataset, val_full_indices, val_route_full_indices, val_probs, thresholds
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_sha = sha256_file(dataset_path)
    checkpoint = {
        "format": "weak4-full-residual-checkpoint-v1",
        "classes": ALL_CLASSES,
        "model_config": asdict(config),
        "parameter_count": parameter_count,
        "trainable_parameter_count": trainable_parameter_count,
        "model_state": {key: value.detach().cpu() for key, value in model.state_dict().items()},
        "feature_stats": asdict(stats),
        "thresholds": thresholds,
        "threshold_metadata": threshold_meta,
        "dataset_sha256": dataset_sha,
        "dataset_usage_scope": dataset["usage_scope"],
        "fold_id": args.fold_id,
        "n_folds": args.n_folds,
        "seed": args.seed,
        "best_epoch": best_epoch,
    }
    model_path = output_dir / "model.pt"
    prediction_path = output_dir / "val_predictions.pt"
    torch.save(checkpoint, model_path)
    prediction_payload = {
        "format": FOLD_FORMAT,
        "classes": ALL_CLASSES,
        "dataset_path": str(dataset_path),
        "dataset_sha256": dataset_sha,
        "dataset_ids_sha256": dataset["ids_sha256"],
        "dataset_usage_scope": dataset["usage_scope"],
        "fold_id": args.fold_id,
        "n_folds": args.n_folds,
        "seed": args.seed,
        "val_full_indices": torch.tensor(val_full_indices, dtype=torch.int64),
        "val_route_positions": torch.tensor(val_route_positions, dtype=torch.int64),
        "val_route_full_indices": torch.tensor(val_route_full_indices, dtype=torch.int64),
        "ids": [rows[idx]["sample_id"] for idx in val_route_positions],
        "action_probs": val_probs,
        "thresholds": thresholds,
        "threshold_metadata": threshold_meta,
        "val_sessions": sorted(val_sessions),
        "calibration_sessions": sorted(calibration_sessions),
        "calibration_ids": [rows[idx]["sample_id"] for idx in calibration_positions],
        "fit_sessions_sha256": hashlib.sha256(
            "\n".join(sorted(fit_sessions)).encode("utf-8")
        ).hexdigest(),
        "feature_stats": asdict(stats),
        "model_config": asdict(config),
        "parameter_count": parameter_count,
        "trainable_parameter_count": trainable_parameter_count,
        "best_epoch": best_epoch,
        "val_loss": val_loss,
        "metrics": metrics,
        "history": history,
    }
    torch.save(prediction_payload, prediction_path)
    (output_dir / "metrics.json").write_text(
        json.dumps(
            {
                "fold_id": args.fold_id,
                "n_folds": args.n_folds,
                "seed": args.seed,
                "fit_rows": len(fit_positions),
                "calibration_rows": len(calibration_positions),
                "validation_route_rows": len(val_route_positions),
                "best_epoch": best_epoch,
                "parameter_count": parameter_count,
                "trainable_parameter_count": trainable_parameter_count,
                "metrics": metrics,
                "threshold_metadata": threshold_meta,
                "history": history,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if args.append_results:
        weakest = sorted(
            metrics["candidate_per_class_f1"].items(), key=lambda item: item[1]
        )[:5]
        parent_meta = (dataset.get("parent") or {}).get("metadata") or {}
        experiment_prefix = args.experiment_id or output_dir.name
        experiment_id = f"{experiment_prefix}_fold{args.fold_id}"
        clean_scope = dataset["usage_scope"] == "clean_fixed_parent_session_cv"
        append_results_csv(
            Path(args.results_csv),
            {
                "experiment_id": experiment_id,
                "model_family": "torch_weak4_full_residual",
                "base_model": parent_meta.get("base_model", ""),
                "features": "typed prompt/event/workspace/main-logit residual inputs",
                "serializer_name": "weak4_full_v1",
                "split_type": (
                    "clean_fixed_parent_session_cv"
                    if clean_scope else "construction_only_oof_cv_untrusted"
                ),
                "seed": args.seed,
                "fold_id": f"{args.fold_id}/{args.n_folds}",
                "epochs": best_epoch,
                "learning_rate": args.lr,
                "batch_size": args.batch_size,
                "macro_f1_raw": f"{metrics['baseline_macro_f1']:.6f}",
                "macro_f1": f"{metrics['candidate_macro_f1']:.6f}",
                "weakest_classes": ";".join(f"{name}:{score:.4f}" for name, score in weakest),
                "top_confusions": json.dumps(metrics["top_confusions"][:8], ensure_ascii=False),
                "prediction_distribution": json.dumps(metrics["prediction_distribution"], ensure_ascii=False, sort_keys=True),
                "artifact_path": str(model_path),
                "val_logits_path": str(prediction_path),
                "train_command": shlex.join(sys.argv),
                "notes": args.notes,
                "decision": (
                    "fold complete; aggregate before promotion"
                    if clean_scope else "construction only; not promotion evidence"
                ),
            },
        )
    print(
        f"saved {output_dir} fold={args.fold_id}/{args.n_folds} "
        f"macro_delta={metrics['macro_delta']:+.6f} rescue={metrics['rescue']} harm={metrics['harm']}"
    )
    return prediction_payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--split", choices=["session_oof"], default="session_oof")
    parser.add_argument("--n-folds", type=int, default=3)
    parser.add_argument("--fold-id", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--inner-calibration-fraction", type=float, default=0.15)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--patience", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--eval-batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--max-grad-norm", type=float, default=1.0)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--global-threshold", type=float, default=0.5)
    parser.add_argument("--threshold-min-support", type=int, default=40)
    parser.add_argument("--byte-embed-dim", type=int, default=96)
    parser.add_argument("--byte-hidden-dim", type=int, default=256)
    parser.add_argument("--byte-conv-layers", type=int, default=6)
    parser.add_argument("--event-dim", type=int, default=384)
    parser.add_argument("--event-layers", type=int, default=4)
    parser.add_argument("--event-heads", type=int, default=8)
    parser.add_argument("--fusion-dim", type=int, default=512)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--results-csv", default="experiments/results.csv")
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--append-results", dest="append_results", action="store_true", default=True
    )
    parser.add_argument("--no-append-results", dest="append_results", action="store_false")
    parser.add_argument("--allow-construction-only-dataset", action="store_true")
    args = parser.parse_args(argv)
    if args.n_folds < 2 or not 0 <= args.fold_id < args.n_folds:
        parser.error("--fold-id must be in [0, n-folds)")
    if args.epochs < 1 or args.patience < 1:
        parser.error("--epochs and --patience must be positive")
    if args.batch_size < 1 or args.eval_batch_size < 1:
        parser.error("batch sizes must be positive")
    if args.event_dim % args.event_heads:
        parser.error("--event-dim must be divisible by --event-heads")
    return args


def main(argv: list[str] | None = None) -> None:
    train_fold(parse_args(argv))


if __name__ == "__main__":
    main()
