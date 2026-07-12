#!/usr/bin/env python3
"""Train one clean session-CV fold of the live-pair residual expert."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shlex
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import torch
from safetensors.torch import save_file
from transformers import AutoModel, AutoTokenizer, get_linear_schedule_with_warmup

from build_weak4_pair_dataset import sha256_file, torch_load
from script import ALL_CLASSES, load_jsonl
from train import append_results_csv, f1_metrics
from weak4_pair_residual import (
    DATASET_KIND,
    FEATURE_SCHEMA,
    LIVE_WEAK4_PAIRS,
    MODEL_SCHEMA,
    NUMERIC_FEATURE_DIM,
    Weak4PairResidualModel,
    apply_pair_residual,
    balanced_target_weights,
    bounded_delta,
    pair_residual_loss,
    serialize_pair_sample,
    target_histogram,
    validate_dataset_scope,
)


DEFAULT_MAX_LENGTH = 384


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def validate_dataset(payload: dict, allow_diagnostic: bool = False) -> str:
    if payload.get("kind") != DATASET_KIND:
        raise ValueError(f"unexpected dataset kind: {payload.get('kind')!r}")
    if list(payload.get("classes") or []) != ALL_CLASSES:
        raise ValueError("dataset class order does not match ALL_CLASSES")
    if [tuple(pair) for pair in payload.get("live_pairs") or []] != list(LIVE_WEAK4_PAIRS):
        raise ValueError("dataset live-pair order is invalid")
    if payload.get("feature_schema") != FEATURE_SCHEMA:
        raise ValueError("dataset numeric feature schema is invalid")
    route_rows = len(payload.get("route_ids") or [])
    required_route_tensors = (
        "route_indices", "pair_ids", "pair_classes", "top_sides", "base_gaps",
        "main_predictions", "alternative_predictions", "numeric_features",
        "target_kinds", "target_signs", "route_fold_ids",
    )
    for key in required_route_tensors:
        if len(payload[key]) != route_rows:
            raise ValueError(f"dataset {key} rows {len(payload[key])} != {route_rows}")
    if tuple(torch.as_tensor(payload["numeric_features"]).shape) != (
        route_rows,
        NUMERIC_FEATURE_DIM,
    ):
        raise ValueError("dataset numeric_features shape is invalid")
    return validate_dataset_scope(payload, allow_diagnostic=allow_diagnostic)


def _route_subset(payload: dict, local_rows: list[int]) -> dict[str, torch.Tensor]:
    idx = torch.tensor(local_rows, dtype=torch.long)
    return {
        "indices": torch.as_tensor(payload["route_indices"], dtype=torch.long)[idx],
        "pair_ids": torch.as_tensor(payload["pair_ids"], dtype=torch.long)[idx],
        "pair_classes": torch.as_tensor(payload["pair_classes"], dtype=torch.long)[idx],
        "top_sides": torch.as_tensor(payload["top_sides"], dtype=torch.long)[idx],
        "base_gaps": torch.as_tensor(payload["base_gaps"], dtype=torch.float32)[idx],
        "main_predictions": torch.as_tensor(payload["main_predictions"], dtype=torch.long)[idx],
        "alternative_predictions": torch.as_tensor(
            payload["alternative_predictions"], dtype=torch.long
        )[idx],
    }


def token_length_report(full_lengths: list[int], max_length: int) -> dict:
    if max_length <= 0:
        raise ValueError("max_length must be positive")
    if not full_lengths:
        return {
            "rows": 0,
            "max_length": int(max_length),
            "mean": 0.0,
            "p95": 0,
            "p99": 0,
            "max": 0,
            "truncated_rows": 0,
            "truncation_rate": 0.0,
        }
    lengths = torch.tensor(full_lengths, dtype=torch.float32)
    truncated = sum(int(length > max_length) for length in full_lengths)
    return {
        "rows": len(full_lengths),
        "max_length": int(max_length),
        "mean": float(lengths.mean()),
        "p95": int(torch.quantile(lengths, 0.95, interpolation="higher")),
        "p99": int(torch.quantile(lengths, 0.99, interpolation="higher")),
        "max": int(lengths.max()),
        "truncated_rows": truncated,
        "truncation_rate": truncated / len(full_lengths),
    }


def _tokenize_route_texts(
    tokenizer, texts: list[str], max_length: int
) -> tuple[list[dict], list[int], list[int]]:
    full_encoded = tokenizer(texts, padding=False, truncation=False)
    full_lengths = [len(row) for row in full_encoded["input_ids"]]
    encoded = tokenizer(texts, padding=False, truncation=True, max_length=max_length)
    keys = list(encoded)
    features = [
        {key: encoded[key][row] for key in keys}
        for row in range(len(texts))
    ]
    lengths = [len(row["input_ids"]) for row in features]
    return features, lengths, full_lengths


def _batches(rows: list[int], batch_size: int, rng: random.Random | None, lengths: list[int]):
    rows = list(rows)
    if rng is not None:
        rng.shuffle(rows)
    bucket_size = max(batch_size, batch_size * 8)
    for start in range(0, len(rows), bucket_size):
        bucket = rows[start:start + bucket_size]
        bucket.sort(key=lambda row: lengths[row], reverse=True)
        for inner in range(0, len(bucket), batch_size):
            yield bucket[inner:inner + batch_size]


def _model_batch(
    tokenizer,
    encoded: list[dict],
    rows: list[int],
    payload: dict,
    device: torch.device,
) -> dict:
    text_batch = tokenizer.pad([encoded[row] for row in rows], padding=True, return_tensors="pt")
    batch = {key: value.to(device) for key, value in text_batch.items()}
    batch.update(
        numeric_features=torch.as_tensor(payload["numeric_features"], dtype=torch.float32)[rows].to(device),
        pair_ids=torch.as_tensor(payload["pair_ids"], dtype=torch.long)[rows].to(device),
        top_sides=torch.as_tensor(payload["top_sides"], dtype=torch.long)[rows].to(device),
    )
    return batch


def infer_raw_delta(
    model,
    tokenizer,
    encoded,
    rows,
    payload,
    device,
    batch_size,
    lengths,
) -> torch.Tensor:
    model.eval()
    out = torch.empty(len(rows), dtype=torch.float32)
    row_to_output = {row: pos for pos, row in enumerate(rows)}
    with torch.inference_mode():
        for batch_rows in _batches(rows, batch_size, None, lengths):
            batch = _model_batch(tokenizer, encoded, batch_rows, payload, device)
            raw = model(**batch).detach().float().cpu()
            for pos, row in enumerate(batch_rows):
                out[row_to_output[row]] = raw[pos]
    return out


def weak4_macro(metrics: dict) -> float:
    return sum(metrics["per_class_f1"][name] for name in ALL_CLASSES[:4]) / 4.0


def fold_report(payload: dict, fold_id: int, val_rows: list[int], raw_delta: torch.Tensor, delta_max: float):
    parent_logits = torch.as_tensor(payload["parent_logits"], dtype=torch.float32)
    y_true = torch.as_tensor(payload["y_true"], dtype=torch.long)
    route = _route_subset(payload, val_rows)
    _, new_pred, delta = apply_pair_residual(parent_logits, route, raw_delta, delta_max=delta_max)
    base_pred = parent_logits.argmax(dim=1)
    full_fold = torch.as_tensor(payload["full_fold_ids"], dtype=torch.long) == fold_id
    fold_y = y_true[full_fold].tolist()
    base_metrics = f1_metrics(fold_y, base_pred[full_fold].tolist())
    new_metrics = f1_metrics(fold_y, new_pred[full_fold].tolist())
    routed_full = route["indices"]
    base_route = base_pred[routed_full]
    new_route = new_pred[routed_full]
    route_y = y_true[routed_full]
    rescue = int(((base_route != route_y) & (new_route == route_y)).sum())
    harm = int(((base_route == route_y) & (new_route != route_y)).sum())
    changed = int((base_route != new_route).sum())
    return {
        "fold_id": fold_id,
        "full_rows": int(full_fold.sum()),
        "route_rows": len(val_rows),
        "base_macro_f1": base_metrics["macro_f1"],
        "macro_f1": new_metrics["macro_f1"],
        "macro_delta": new_metrics["macro_f1"] - base_metrics["macro_f1"],
        "base_weak4_macro_f1": weak4_macro(base_metrics),
        "weak4_macro_f1": weak4_macro(new_metrics),
        "weak4_delta": weak4_macro(new_metrics) - weak4_macro(base_metrics),
        "rescue": rescue,
        "harm": harm,
        "changed": changed,
        "delta_abs_mean": float(delta.abs().mean()) if len(delta) else 0.0,
        "delta_abs_max": float(delta.abs().max()) if len(delta) else 0.0,
        "per_class_f1": new_metrics["per_class_f1"],
    }, new_pred


def save_model_artifact(model, tokenizer, output_dir: Path, args, dataset_path: Path, train_rows: list[int]):
    output_dir.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(output_dir)
    model.encoder.config.save_pretrained(output_dir)
    state = {
        key: tensor.detach().cpu().contiguous()
        for key, tensor in model.state_dict().items()
    }
    save_file(state, output_dir / "model.safetensors")
    config = {
        "schema_version": 1,
        "model_schema": MODEL_SCHEMA,
        "feature_schema": FEATURE_SCHEMA,
        "base_model": args.base_model,
        "serializer_name": args.serializer,
        "numeric_dim": NUMERIC_FEATURE_DIM,
        "live_pairs": [list(pair) for pair in LIVE_WEAK4_PAIRS],
        "delta_max": args.delta_max,
        "max_length": args.max_length,
        "inference_batch_size": args.eval_batch_size,
        "fusion_dim": args.fusion_dim,
        "dropout": args.dropout,
    }
    (output_dir / "residual_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    provenance = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "train_command": " ".join(shlex.quote(value) for value in sys.argv),
        "dataset_path": str(dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "train_route_rows": len(train_rows),
        "fold_id": args.fold_id,
        "seed": args.seed,
        "torch": torch.__version__,
    }
    (output_dir / "training_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def resolve_run_identity(
    serializer_name: str,
    fold_id: int,
    experiment_prefix: str = "",
    output_dir: str = "",
) -> tuple[str, Path]:
    """Give every fold/final run a collision-resistant default identity."""

    run_suffix = "final" if int(fold_id) == -1 else f"fold{int(fold_id)}"
    prefix = experiment_prefix or f"weak4_pair_{serializer_name}"
    experiment_id = f"{prefix}_{run_suffix}"
    resolved_output = (
        Path(output_dir)
        if output_dir
        else Path("experiments/incoming/models") / experiment_id
    )
    return experiment_id, resolved_output


def training_recipe(args) -> dict:
    """Canonical fold recipe; aggregate requires exact equality."""

    return {
        "model_schema": MODEL_SCHEMA,
        "feature_schema": FEATURE_SCHEMA,
        "base_model": args.base_model,
        "serializer_name": args.serializer,
        "seed": int(args.seed),
        "max_length": int(args.max_length),
        "epochs": int(args.epochs),
        "encoder_lr": float(args.encoder_lr),
        "head_lr": float(args.head_lr),
        "batch_size": int(args.batch_size),
        "eval_batch_size": int(args.eval_batch_size),
        "weight_decay": float(args.weight_decay),
        "warmup_ratio": float(args.warmup_ratio),
        "grad_clip": float(args.grad_clip),
        "delta_max": float(args.delta_max),
        "fusion_dim": int(args.fusion_dim),
        "dropout": float(args.dropout),
        "gradient_checkpointing": bool(args.gradient_checkpointing),
    }


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="experiments/artifacts/20260712_weak4_pair_fixed_parent_dataset.pt",
    )
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--base-model", default="bert-base-multilingual-cased")
    parser.add_argument("--serializer", choices=["current_v1", "weak_policy_pair_v1"], default="weak_policy_pair_v1")
    parser.add_argument("--fold-id", type=int, default=0, help="0..n-1; -1 trains the final all-route refit")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="cuda")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--eval-batch-size", type=int, default=64)
    parser.add_argument("--encoder-lr", type=float, default=2e-5)
    parser.add_argument("--head-lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.06)
    parser.add_argument("--grad-clip", type=float, default=1.0)
    parser.add_argument("--delta-max", type=float, default=2.0)
    parser.add_argument("--fusion-dim", type=int, default=256)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output-dir",
        default="",
        help="default: experiments/incoming/models/<experiment-id>",
    )
    parser.add_argument(
        "--overwrite-output",
        action="store_true",
        help="replace a non-empty explicit/default output directory",
    )
    parser.add_argument("--logits-dir", default="experiments/logits")
    parser.add_argument("--artifacts-dir", default="experiments/artifacts")
    parser.add_argument("--experiment-id", default="")
    parser.add_argument(
        "--allow-diagnostic-dataset",
        action="store_true",
        help="allow construction-only nonfixed/consensus dataset scopes",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    started = time.perf_counter()
    dataset_path = Path(args.dataset)
    payload = torch_load(dataset_path)
    scope_mode = validate_dataset(
        payload, allow_diagnostic=args.allow_diagnostic_dataset
    )
    n_folds = int(payload["n_folds"])
    if args.fold_id < -1 or args.fold_id >= n_folds:
        raise ValueError(f"fold_id must be -1 or in [0,{n_folds - 1}]")
    experiment_id, output_dir = resolve_run_identity(
        args.serializer,
        args.fold_id,
        experiment_prefix=args.experiment_id,
        output_dir=args.output_dir,
    )
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite_output:
        raise FileExistsError(
            f"output directory is non-empty: {output_dir}; "
            "use a fold-specific path or pass --overwrite-output"
        )
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    device = torch.device(args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
    set_seed(args.seed)
    recipe = training_recipe(args)

    route_folds = torch.as_tensor(payload["route_fold_ids"], dtype=torch.long)
    all_rows = list(range(len(route_folds)))
    if args.fold_id == -1:
        train_rows, val_rows = all_rows, []
    else:
        train_rows = [row for row in all_rows if int(route_folds[row]) != args.fold_id]
        val_rows = [row for row in all_rows if int(route_folds[row]) == args.fold_id]
    if not train_rows or (args.fold_id >= 0 and not val_rows):
        raise ValueError("empty train or validation route split")

    samples = load_jsonl(Path(args.data_dir) / "train.jsonl")
    train_indices = torch.as_tensor(payload["train_indices"], dtype=torch.long)
    route_indices = torch.as_tensor(payload["route_indices"], dtype=torch.long)
    route_samples = [samples[int(train_indices[int(full_idx)])] for full_idx in route_indices]
    pair_ids = torch.as_tensor(payload["pair_ids"], dtype=torch.long)
    main_pred = torch.as_tensor(payload["main_predictions"], dtype=torch.long)
    texts = [
        serialize_pair_sample(route_samples[row], int(pair_ids[row]), int(main_pred[row]), args.serializer)
        for row in all_rows
    ]

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, local_files_only=args.local_files_only)
    encoded, lengths, full_lengths = _tokenize_route_texts(tokenizer, texts, args.max_length)
    length_audit = token_length_report(full_lengths, args.max_length)
    print(
        "token lengths: "
        f"mean={length_audit['mean']:.1f} p95={length_audit['p95']} "
        f"p99={length_audit['p99']} max={length_audit['max']} "
        f"truncated={length_audit['truncated_rows']}/{length_audit['rows']} "
        f"({length_audit['truncation_rate']:.2%})"
    )
    encoder = AutoModel.from_pretrained(args.base_model, local_files_only=args.local_files_only)
    if args.gradient_checkpointing and hasattr(encoder, "gradient_checkpointing_enable"):
        encoder.gradient_checkpointing_enable()
    model = Weak4PairResidualModel(
        encoder,
        numeric_dim=NUMERIC_FEATURE_DIM,
        fusion_dim=args.fusion_dim,
        dropout=args.dropout,
    ).to(device)

    train_kinds = torch.as_tensor(payload["target_kinds"], dtype=torch.long)[train_rows]
    train_pairs = pair_ids[train_rows]
    train_sides = torch.as_tensor(payload["top_sides"], dtype=torch.long)[train_rows]
    local_weights = balanced_target_weights(train_kinds, train_pairs, train_sides)
    weights = torch.zeros(len(all_rows), dtype=torch.float32)
    weights[train_rows] = local_weights
    encoder_params = list(model.encoder.parameters())
    head_params = [param for name, param in model.named_parameters() if not name.startswith("encoder.")]
    optimizer = torch.optim.AdamW(
        [
            {"params": encoder_params, "lr": args.encoder_lr, "weight_decay": args.weight_decay},
            {"params": head_params, "lr": args.head_lr, "weight_decay": args.weight_decay},
        ]
    )
    steps_per_epoch = math.ceil(len(train_rows) / args.batch_size)
    total_steps = max(1, args.epochs * steps_per_epoch)
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(total_steps * args.warmup_ratio),
        num_training_steps=total_steps,
    )
    base_gaps = torch.as_tensor(payload["base_gaps"], dtype=torch.float32)
    target_signs = torch.as_tensor(payload["target_signs"], dtype=torch.float32)
    target_kinds = torch.as_tensor(payload["target_kinds"], dtype=torch.long)
    for epoch in range(args.epochs):
        model.train()
        losses = []
        rng = random.Random(args.seed + epoch * 1009)
        for batch_rows in _batches(train_rows, args.batch_size, rng, lengths):
            optimizer.zero_grad(set_to_none=True)
            batch = _model_batch(tokenizer, encoded, batch_rows, payload, device)
            raw = model(**batch)
            loss, _ = pair_residual_loss(
                raw,
                base_gaps[batch_rows].to(device),
                target_signs[batch_rows].to(device),
                target_kinds[batch_rows].to(device),
                weights[batch_rows].to(device),
                delta_max=args.delta_max,
            )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
            optimizer.step()
            scheduler.step()
            losses.append(float(loss.detach()))
        print(f"epoch={epoch + 1}/{args.epochs} loss={sum(losses) / max(1, len(losses)):.6f}")

    if output_dir.exists() and any(output_dir.iterdir()):
        # Non-overwrite collisions failed before model/tokenizer load. Reaching
        # here with a non-empty directory therefore means explicit overwrite.
        shutil.rmtree(output_dir)
    save_model_artifact(model, tokenizer, output_dir, args, dataset_path, train_rows)
    report = {
        "experiment_id": experiment_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "usage_scope": payload["usage_scope"],
        "validation_scope": payload["validation_scope"],
        "scope_mode": scope_mode,
        "recipe": recipe,
        "serializer_name": args.serializer,
        "base_model": args.base_model,
        "fold_id": args.fold_id,
        "train_route_rows": len(train_rows),
        "val_route_rows": len(val_rows),
        "train_target_histogram": target_histogram(target_kinds[train_rows]),
        "token_length_audit": length_audit,
        "runtime_sec": time.perf_counter() - started,
        "model_dir": str(output_dir),
    }
    if val_rows:
        raw_delta = infer_raw_delta(
            model, tokenizer, encoded, val_rows, payload, device,
            args.eval_batch_size, lengths,
        )
        fold_metrics, _ = fold_report(payload, args.fold_id, val_rows, raw_delta, args.delta_max)
        report["fold_metrics"] = fold_metrics
        logits_dir = Path(args.logits_dir)
        logits_dir.mkdir(parents=True, exist_ok=True)
        delta_path = logits_dir / f"{experiment_id}_val_pair_delta.pt"
        torch.save(
            {
                "schema_version": 1,
                "kind": "weak4_pair_residual_fold_delta",
                "experiment_id": experiment_id,
                "classes": ALL_CLASSES,
                "live_pairs": [list(pair) for pair in LIVE_WEAK4_PAIRS],
                "dataset_sha256": sha256_file(dataset_path),
                "usage_scope": payload["usage_scope"],
                "validation_scope": payload["validation_scope"],
                "scope_mode": scope_mode,
                "recipe": recipe,
                "base_model": args.base_model,
                "seed": args.seed,
                "max_length": args.max_length,
                "epochs": args.epochs,
                "encoder_lr": args.encoder_lr,
                "head_lr": args.head_lr,
                "batch_size": args.batch_size,
                "eval_batch_size": args.eval_batch_size,
                "fusion_dim": args.fusion_dim,
                "fold_id": args.fold_id,
                "n_folds": n_folds,
                "route_local_indices": torch.tensor(val_rows, dtype=torch.long),
                "ids": [payload["route_ids"][row] for row in val_rows],
                "raw_delta": raw_delta,
                "delta": bounded_delta(raw_delta, args.delta_max),
                "delta_max": args.delta_max,
                "serializer_name": args.serializer,
                "token_length_audit": length_audit,
                "model_dir": str(output_dir),
            },
            delta_path,
        )
        report["val_delta_path"] = str(delta_path)
        print(json.dumps(fold_metrics, ensure_ascii=False, indent=2))
    report["runtime_sec"] = time.perf_counter() - started
    artifacts_dir = Path(args.artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifacts_dir / f"{experiment_id}_metrics.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fold_metrics = report.get("fold_metrics") or {}
    append_results_csv(
        Path("experiments/results.csv"),
        {
            "experiment_id": experiment_id,
            "model_family": "weak4_live_pair_residual_fold" if args.fold_id >= 0 else "weak4_live_pair_residual_final",
            "base_model": args.base_model,
            "features": "policy serializer + main numeric features + live pair",
            "serializer_name": args.serializer,
            "split_type": (
                "clean_fixed_parent_session_cv"
                if scope_mode == "clean" and args.fold_id >= 0
                else "clean_fixed_parent_route_refit"
                if scope_mode == "clean"
                else "construction_diagnostic_session_cv"
                if args.fold_id >= 0
                else "construction_diagnostic_route_refit"
            ),
            "fold_id": args.fold_id if args.fold_id >= 0 else "final",
            "seed": args.seed,
            "max_length": args.max_length,
            "epochs": args.epochs,
            "learning_rate": args.encoder_lr,
            "batch_size": args.batch_size,
            "macro_f1_raw": (
                f"{fold_metrics['base_macro_f1']:.6f}" if fold_metrics else ""
            ),
            "macro_f1": f"{fold_metrics['macro_f1']:.6f}" if fold_metrics else "",
            "artifact_path": str(output_dir),
            "val_logits_path": report.get("val_delta_path", ""),
            "runtime_sec": f"{report['runtime_sec']:.3f}",
            "train_command": " ".join(shlex.quote(value) for value in sys.argv),
            "notes": (
                f"macro_delta={fold_metrics['macro_delta']:+.6f}; "
                f"weak4_delta={fold_metrics['weak4_delta']:+.6f}; "
                f"truncation={length_audit['truncation_rate']:.4%}"
                if fold_metrics else (
                    "final refit on all fixed-parent live-pair routes; "
                    f"truncation={length_audit['truncation_rate']:.4%}"
                )
            ),
            "decision": "fold complete; aggregate all folds" if fold_metrics else "final pair residual refit complete",
        },
    )
    print(f"saved model={output_dir} report={report_path}")


if __name__ == "__main__":
    main()
