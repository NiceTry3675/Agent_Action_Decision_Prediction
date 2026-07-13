#!/usr/bin/env python3
"""Train one locked diagnostic fold of the SIM recorded-policy decoder.

The primary B-lane recipe is a full end-to-end HCX-0.5B fine-tune.  Fold
outputs are diagnostic because the currently available champion OOF stitch is
not strict nested-main stacking data.  Model artifacts are consequently marked
deployment-ineligible even when explicitly saved.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shlex
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch
from safetensors.torch import save_file
from transformers import AutoModel, AutoTokenizer, get_linear_schedule_with_warmup

from build_sim_policy_decoder_dataset import (
    sha256_file,
    torch_load,
    validate_dataset,
    validate_parent_provenance,
)
from script import ALL_CLASSES, load_jsonl, safe_text
from sim_policy_decoder import (
    DEFAULT_MAX_LENGTH,
    DEFAULT_PROMPT_HEAD_TOKENS,
    DEFAULT_PROMPT_TOKEN_CAP,
    FEATURE_SCHEMA,
    FOLD_FORMAT,
    MODEL_SCHEMA,
    NUMERIC_FEATURE_DIM,
    OUTPUT_NAMES,
    SERIALIZER_NAME,
    SimPolicyDecoderModel,
    apply_specialist_actions,
    encode_sim_policy_sample,
    hierarchical_joint_log_probabilities,
    hierarchical_policy_loss,
    specialist_cross_entropy,
    stable_inner_fold,
    target_class_weights,
)
from train import append_results_csv, f1_metrics


DEFAULT_BASE_MODEL = "naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B"

LOCKED_RECIPE_DEFAULTS = {
    "objective": "direct5",
    "conditional_loss_weight": 0.5,
    "gate_pos_weight": 1.0,
    "epochs": 3,
    "batch_size": 16,
    "eval_batch_size": 64,
    "gradient_accumulation": 1,
    "backbone_lr": 2e-5,
    "head_lr": 5e-4,
    "weight_decay": 0.01,
    "warmup_ratio": 0.06,
    "grad_clip": 1.0,
    "dropout": 0.1,
    "fusion_dim": 256,
    "label_smoothing": 0.02,
    "class_weight_power": 0.0,
    "precision": "fp16",
    "gradient_checkpointing": True,
    "max_length": 128,
    "prompt_token_cap": 192,
    "prompt_head_tokens": 128,
    "seed": 42,
}


def validate_locked_recipe(args: argparse.Namespace) -> dict[str, tuple[Any, Any]]:
    mismatches = {
        key: (getattr(args, key), expected)
        for key, expected in LOCKED_RECIPE_DEFAULTS.items()
        if getattr(args, key) != expected
    }
    if mismatches and not args.allow_recipe_ablation:
        raise ValueError(
            "B-lane recipe differs from the preregistered defaults; "
            f"mismatches={mismatches}. Pass --allow-recipe-ablation only for a "
            "separately named experiment."
        )
    return mismatches


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def resolve_cv_partition(
    dataset: Mapping[str, Any], args: argparse.Namespace
) -> tuple[list[int], list[int], list[int], dict[str, int | str]]:
    """Return train route rows, val route rows, and val full rows."""

    route_parent = torch.as_tensor(dataset["route_fold_ids"], dtype=torch.long)
    full_parent = torch.as_tensor(dataset["full_fold_ids"], dtype=torch.long)
    if args.cv_mode == "stitched_outer3":
        if args.fold_id not in (0, 1, 2):
            raise ValueError("stitched_outer3 requires --fold-id 0, 1, or 2")
        train = [row for row in range(len(route_parent)) if int(route_parent[row]) != args.fold_id]
        val = [row for row in range(len(route_parent)) if int(route_parent[row]) == args.fold_id]
        val_full = [row for row in range(len(full_parent)) if int(full_parent[row]) == args.fold_id]
        return train, val, val_full, {"cv_mode": args.cv_mode, "fold_id": int(args.fold_id)}

    if args.parent_fold not in (0, 1, 2):
        raise ValueError("parent_inner3 requires --parent-fold 0, 1, or 2")
    if args.inner_fold is None or not 0 <= args.inner_fold < args.n_inner_folds:
        raise ValueError(
            f"parent_inner3 requires --inner-fold in [0,{args.n_inner_folds - 1}]"
        )
    full_inner = [
        stable_inner_fold(session, args.parent_fold, args.n_inner_folds, args.inner_seed)
        if int(full_parent[row]) == args.parent_fold
        else -1
        for row, session in enumerate(dataset["session_ids"])
    ]
    route_indices = torch.as_tensor(dataset["route_indices"], dtype=torch.long)
    route_inner = [full_inner[int(full_row)] for full_row in route_indices]
    train = [
        row
        for row in range(len(route_parent))
        if int(route_parent[row]) == args.parent_fold and route_inner[row] != args.inner_fold
    ]
    val = [
        row
        for row in range(len(route_parent))
        if int(route_parent[row]) == args.parent_fold and route_inner[row] == args.inner_fold
    ]
    val_full = [
        row
        for row in range(len(full_parent))
        if int(full_parent[row]) == args.parent_fold and full_inner[row] == args.inner_fold
    ]
    return train, val, val_full, {
        "cv_mode": args.cv_mode,
        "parent_fold": int(args.parent_fold),
        "inner_fold": int(args.inner_fold),
        "n_inner_folds": int(args.n_inner_folds),
        "inner_seed": int(args.inner_seed),
    }


def weak4_macro(metrics: Mapping[str, Any]) -> float:
    return sum(float(metrics["per_class_f1"][name]) for name in ALL_CLASSES[:4]) / 4.0


def _batches(
    rows: Sequence[int], batch_size: int, lengths: Sequence[int], rng: random.Random | None
):
    rows = list(rows)
    if rng is not None:
        rng.shuffle(rows)
    bucket_size = max(batch_size, batch_size * 8)
    for start in range(0, len(rows), bucket_size):
        bucket = rows[start : start + bucket_size]
        bucket.sort(key=lambda row: lengths[row], reverse=True)
        for inner in range(0, len(bucket), batch_size):
            yield bucket[inner : inner + batch_size]


def encode_route_samples(
    samples: Sequence[Mapping[str, Any]],
    route_indices: torch.Tensor,
    tokenizer,
    args: argparse.Namespace,
) -> tuple[list[dict[str, list[int]]], list[int], dict[str, Any]]:
    encoded: list[dict[str, list[int]]] = []
    audits: list[dict[str, Any]] = []
    for full_row in route_indices.tolist():
        row, audit = encode_sim_policy_sample(
            samples[full_row],
            tokenizer,
            max_length=args.max_length,
            prompt_token_cap=args.prompt_token_cap,
            prompt_head_tokens=args.prompt_head_tokens,
        )
        encoded.append(row)
        audits.append(audit)
    lengths = [len(row["input_ids"]) for row in encoded]
    sorted_lengths = sorted(lengths)

    def percentile(fraction: float) -> int:
        if not sorted_lengths:
            return 0
        index = min(len(sorted_lengths) - 1, math.ceil(len(sorted_lengths) * fraction) - 1)
        return int(sorted_lengths[index])

    length_audit = {
        "rows": len(encoded),
        "max_length": int(args.max_length),
        "prompt_token_cap": int(args.prompt_token_cap),
        "prompt_head_tokens": int(args.prompt_head_tokens),
        "mean": sum(lengths) / max(1, len(lengths)),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": max(lengths, default=0),
        "prompt_truncated_rows": sum(bool(row["prompt_truncated"]) for row in audits),
        "prompt_budget_reduced_rows": sum(
            int(row["effective_prompt_cap"]) < int(args.prompt_token_cap) for row in audits
        ),
        "max_action_count": max((int(row["action_count"]) for row in audits), default=0),
        "trajectory_truncated_rows": 0,
    }
    return encoded, lengths, length_audit


def make_batch(
    tokenizer,
    encoded: Sequence[Mapping[str, list[int]]],
    rows: Sequence[int],
    numeric_features: torch.Tensor,
    device: torch.device,
) -> dict[str, torch.Tensor]:
    text = tokenizer.pad(
        [encoded[row] for row in rows], padding=True, return_tensors="pt"
    )
    batch = {key: value.to(device) for key, value in text.items()}
    batch["numeric_features"] = numeric_features[list(rows)].to(device)
    return batch


def autocast_context(device: torch.device, precision: str):
    enabled = device.type == "cuda" and precision in {"fp16", "bf16"}
    dtype = torch.float16 if precision == "fp16" else torch.bfloat16
    return torch.autocast(device_type=device.type, dtype=dtype, enabled=enabled)


@torch.inference_mode()
def predict_raw_outputs(
    model: SimPolicyDecoderModel,
    tokenizer,
    encoded: Sequence[Mapping[str, list[int]]],
    rows: Sequence[int],
    numeric_features: torch.Tensor,
    lengths: Sequence[int],
    device: torch.device,
    precision: str,
    batch_size: int,
) -> torch.Tensor:
    model.eval()
    output = torch.empty((len(rows), len(OUTPUT_NAMES)), dtype=torch.float32)
    output_position = {row: index for index, row in enumerate(rows)}
    for batch_rows in _batches(rows, batch_size, lengths, None):
        batch = make_batch(tokenizer, encoded, batch_rows, numeric_features, device)
        with autocast_context(device, precision):
            logits = model(**batch)
        logits = logits.detach().float().cpu()
        for local, row in enumerate(batch_rows):
            output[output_position[row]] = logits[local]
    return output


def scaler_step_and_maybe_schedule(scaler, optimizer, scheduler) -> bool:
    """Advance the LR schedule only when GradScaler ran optimizer.step()."""

    old_scale = float(scaler.get_scale())
    scaler.step(optimizer)
    scaler.update()
    optimizer_ran = (not scaler.is_enabled()) or float(scaler.get_scale()) >= old_scale
    if optimizer_ran:
        scheduler.step()
    return optimizer_ran


def fold_metrics(
    dataset: Mapping[str, Any],
    val_full_rows: Sequence[int],
    val_route_rows: Sequence[int],
    action_logits: torch.Tensor,
    split_spec: Mapping[str, Any],
) -> dict[str, Any]:
    parent_logits = torch.as_tensor(dataset["parent_logits"], dtype=torch.float32)
    truth = torch.as_tensor(dataset["y_true"], dtype=torch.long)
    route_indices = torch.as_tensor(dataset["route_indices"], dtype=torch.long)
    full_route = route_indices[list(val_route_rows)]
    actions = action_logits.argmax(dim=1)
    prediction = apply_specialist_actions(parent_logits, full_route, actions)
    baseline = parent_logits.argmax(dim=1)
    fold_mask = torch.zeros(len(truth), dtype=torch.bool)
    fold_mask[list(val_full_rows)] = True
    sim_mask = torch.tensor(
        [safe_text(sample_id).startswith("sess_sim_") for sample_id in dataset["ids"]],
        dtype=torch.bool,
    )

    def compare(mask: torch.Tensor) -> dict[str, Any]:
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

    route_truth = truth[full_route]
    route_base = baseline[full_route]
    route_new = prediction[full_route]
    changed = route_base != route_new
    return {
        "split": dict(split_spec),
        "full": compare(fold_mask),
        "sim": compare(fold_mask & sim_mask),
        "route_rows": len(full_route),
        "changed": int(changed.sum()),
        "rescue": int(((route_base != route_truth) & (route_new == route_truth)).sum()),
        "harm": int(((route_base == route_truth) & (route_new != route_truth)).sum()),
        "neutral_change": int(
            (changed & (route_base != route_truth) & (route_new != route_truth)).sum()
        ),
        "action_histogram": {
            name: int((actions == index).sum()) for index, name in enumerate(OUTPUT_NAMES)
        },
    }


def training_recipe(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "model_schema": MODEL_SCHEMA,
        "feature_schema": FEATURE_SCHEMA,
        "serializer_name": SERIALIZER_NAME,
        "base_model": args.base_model,
        "fine_tune_mode": "full_end_to_end",
        "seed": int(args.seed),
        "max_length": int(args.max_length),
        "prompt_token_cap": int(args.prompt_token_cap),
        "prompt_head_tokens": int(args.prompt_head_tokens),
        "epochs": int(args.epochs),
        "batch_size": int(args.batch_size),
        "eval_batch_size": int(args.eval_batch_size),
        "gradient_accumulation": int(args.gradient_accumulation),
        "backbone_lr": float(args.backbone_lr),
        "head_lr": float(args.head_lr),
        "weight_decay": float(args.weight_decay),
        "warmup_ratio": float(args.warmup_ratio),
        "grad_clip": float(args.grad_clip),
        "dropout": float(args.dropout),
        "fusion_dim": int(args.fusion_dim),
        "label_smoothing": float(args.label_smoothing),
        "class_weight_power": float(args.class_weight_power),
        "precision": args.precision,
        "gradient_checkpointing": bool(args.gradient_checkpointing),
        "decision_rule": (
            "factorized_joint_log_probability_argmax"
            if args.objective == "hierarchical"
            else "direct_argmax_no_threshold"
        ),
        "objective": args.objective,
        "conditional_loss_weight": float(args.conditional_loss_weight),
        "gate_pos_weight": float(args.gate_pos_weight),
        "cv_mode": args.cv_mode,
        "n_inner_folds": int(args.n_inner_folds),
        "inner_seed": int(args.inner_seed),
    }


def save_model_artifact(
    model: SimPolicyDecoderModel,
    tokenizer,
    output_dir: Path,
    recipe: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(output_dir)
    model.encoder.config.save_pretrained(output_dir)
    state = {}
    for name, tensor in model.state_dict().items():
        value = tensor.detach().cpu()
        if value.is_floating_point():
            value = value.half()
        state[name] = value.contiguous()
    save_file(state, output_dir / "model.safetensors")
    config = {
        "schema_version": 1,
        "model_schema": MODEL_SCHEMA,
        "recipe": dict(recipe),
        "outputs": list(OUTPUT_NAMES),
        "numeric_feature_dim": NUMERIC_FEATURE_DIM,
        "deployment_eligible": False,
        "deployment_blockers": [
            "diagnostic_stitched_oof_not_strict_nested_main",
            "second_full_hcx_model_not_yet_under_submission_zip_limit",
            "no_packaging_or_t4_cascade_gate",
        ],
        "parent_provenance": dict(provenance),
    }
    (output_dir / "specialist_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="experiments/artifacts/20260713_sim_policy_decoder_dataset.pt",
    )
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument(
        "--objective", choices=("direct5", "hierarchical"), default="direct5"
    )
    parser.add_argument("--conditional-loss-weight", type=float, default=0.5)
    parser.add_argument("--gate-pos-weight", type=float, default=1.0)
    parser.add_argument(
        "--cv-mode",
        choices=("parent_inner3", "stitched_outer3"),
        default="parent_inner3",
    )
    parser.add_argument("--fold-id", type=int, choices=(0, 1, 2), default=None)
    parser.add_argument("--parent-fold", type=int, choices=(0, 1, 2), default=None)
    parser.add_argument("--inner-fold", type=int, default=None)
    parser.add_argument("--n-inner-folds", type=int, default=3)
    parser.add_argument("--inner-seed", type=int, default=42013)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="cuda")
    parser.add_argument("--precision", choices=("fp16", "bf16", "fp32"), default="fp16")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH)
    parser.add_argument("--prompt-token-cap", type=int, default=DEFAULT_PROMPT_TOKEN_CAP)
    parser.add_argument("--prompt-head-tokens", type=int, default=DEFAULT_PROMPT_HEAD_TOKENS)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--eval-batch-size", type=int, default=64)
    parser.add_argument("--gradient-accumulation", type=int, default=1)
    parser.add_argument("--backbone-lr", type=float, default=2e-5)
    parser.add_argument("--head-lr", type=float, default=5e-4)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.06)
    parser.add_argument("--grad-clip", type=float, default=1.0)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--fusion-dim", type=int, default=256)
    parser.add_argument("--label-smoothing", type=float, default=0.02)
    parser.add_argument(
        "--class-weight-power",
        type=float,
        default=0.0,
        help="locked B recipe is unweighted CE; nonzero values are separate ablations",
    )
    parser.add_argument(
        "--gradient-checkpointing",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--experiment-id", default="20260713_sim_policy_decoder_hcx05b")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--save-model", action="store_true")
    parser.add_argument("--overwrite-output", action="store_true")
    parser.add_argument("--logits-dir", default="experiments/logits")
    parser.add_argument("--artifacts-dir", default="experiments/artifacts")
    parser.add_argument("--no-results-csv", action="store_true")
    parser.add_argument("--allow-recipe-ablation", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    recipe_mismatches = validate_locked_recipe(args)
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    if args.precision != "fp32" and args.device == "cpu":
        raise ValueError("CPU training requires --precision fp32")
    device = torch.device(
        args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
    )
    if device.type == "cpu" and args.precision != "fp32":
        raise ValueError("CPU training requires --precision fp32")
    if args.gradient_accumulation < 1:
        raise ValueError("gradient accumulation must be positive")

    dataset_path = Path(args.dataset)
    dataset = validate_dataset(torch_load(dataset_path))
    provenance_eligibility = validate_parent_provenance(
        dataset["parent"], dataset["usage_scope"]
    )
    set_seed(args.seed)
    recipe = training_recipe(args)

    train_rows, val_rows, val_full_rows, split_spec = resolve_cv_partition(dataset, args)
    if not train_rows or not val_rows or not val_full_rows:
        raise ValueError("empty specialist train/validation partition")
    train_sessions = {
        dataset["session_ids"][int(dataset["route_indices"][row])] for row in train_rows
    }
    val_sessions = {
        dataset["session_ids"][int(dataset["route_indices"][row])] for row in val_rows
    }
    if train_sessions & val_sessions:
        raise AssertionError("specialist train and validation sessions overlap")

    samples = load_jsonl(Path(args.data_dir) / "train.jsonl")
    if [safe_text(row.get("id")) for row in samples] != list(dataset["ids"]):
        raise ValueError("train.jsonl row order differs from specialist dataset")
    tokenizer = AutoTokenizer.from_pretrained(
        args.base_model, local_files_only=args.local_files_only
    )
    if tokenizer.pad_token_id is None:
        if tokenizer.eos_token_id is None:
            raise ValueError("tokenizer has neither pad_token nor eos_token")
        tokenizer.pad_token = tokenizer.eos_token
    route_indices = torch.as_tensor(dataset["route_indices"], dtype=torch.long)
    encoded, lengths, length_audit = encode_route_samples(
        samples, route_indices, tokenizer, args
    )
    print(
        "token audit: "
        f"mean={length_audit['mean']:.1f} p95={length_audit['p95']} "
        f"p99={length_audit['p99']} max={length_audit['max']} "
        f"prompt_truncated={length_audit['prompt_truncated_rows']} "
        f"trajectory_truncated=0"
    )

    encoder = AutoModel.from_pretrained(
        args.base_model,
        local_files_only=args.local_files_only,
        torch_dtype=torch.float32,
    )
    if hasattr(encoder.config, "use_cache"):
        encoder.config.use_cache = False
    if hasattr(encoder.config, "pad_token_id"):
        encoder.config.pad_token_id = tokenizer.pad_token_id
    if args.gradient_checkpointing and hasattr(encoder, "gradient_checkpointing_enable"):
        encoder.gradient_checkpointing_enable()
    model = SimPolicyDecoderModel(
        encoder, fusion_dim=args.fusion_dim, dropout=args.dropout
    ).to(device)
    numeric_features = torch.as_tensor(dataset["numeric_features"], dtype=torch.float32)
    targets = torch.as_tensor(dataset["targets"], dtype=torch.long)
    route_main_predictions = torch.as_tensor(
        dataset["route_main_predictions"], dtype=torch.long
    )
    class_weights = target_class_weights(targets[train_rows], power=args.class_weight_power)

    backbone_params = list(model.encoder.parameters())
    head_params = [
        parameter
        for name, parameter in model.named_parameters()
        if not name.startswith("encoder.")
    ]
    optimizer = torch.optim.AdamW(
        [
            {"params": backbone_params, "lr": args.backbone_lr, "weight_decay": args.weight_decay},
            {"params": head_params, "lr": args.head_lr, "weight_decay": args.weight_decay},
        ]
    )
    batches_per_epoch = math.ceil(len(train_rows) / args.batch_size)
    steps_per_epoch = math.ceil(batches_per_epoch / args.gradient_accumulation)
    total_steps = max(1, steps_per_epoch * args.epochs)
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(total_steps * args.warmup_ratio),
        num_training_steps=total_steps,
    )
    scaler = torch.amp.GradScaler(
        "cuda", enabled=(device.type == "cuda" and args.precision == "fp16")
    )
    optimizer.zero_grad(set_to_none=True)
    optimizer_steps = 0
    skipped_optimizer_steps = 0
    for epoch in range(args.epochs):
        model.train()
        losses = []
        batches = list(
            _batches(
                train_rows,
                args.batch_size,
                lengths,
                random.Random(args.seed + epoch * 1009),
            )
        )
        for batch_index, batch_rows in enumerate(batches):
            batch = make_batch(tokenizer, encoded, batch_rows, numeric_features, device)
            with autocast_context(device, args.precision):
                logits = model(**batch)
                if args.objective == "hierarchical":
                    loss, _ = hierarchical_policy_loss(
                        logits,
                        targets[batch_rows].to(device),
                        route_main_predictions[batch_rows].to(device),
                        conditional_loss_weight=args.conditional_loss_weight,
                        gate_pos_weight=args.gate_pos_weight,
                        label_smoothing=args.label_smoothing,
                    )
                else:
                    loss = specialist_cross_entropy(
                        logits,
                        targets[batch_rows].to(device),
                        class_weights,
                        label_smoothing=args.label_smoothing,
                    )
                scaled_loss = loss / args.gradient_accumulation
            scaler.scale(scaled_loss).backward()
            losses.append(float(loss.detach().cpu()))
            final_batch = batch_index + 1 == len(batches)
            if (batch_index + 1) % args.gradient_accumulation == 0 or final_batch:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
                optimizer_ran = scaler_step_and_maybe_schedule(
                    scaler, optimizer, scheduler
                )
                optimizer.zero_grad(set_to_none=True)
                if optimizer_ran:
                    optimizer_steps += 1
                else:
                    skipped_optimizer_steps += 1
        print(
            f"epoch={epoch + 1}/{args.epochs} loss={sum(losses) / max(1, len(losses)):.6f} "
            f"optimizer_steps={optimizer_steps} skipped_overflow={skipped_optimizer_steps}"
        )

    val_raw_outputs = predict_raw_outputs(
        model,
        tokenizer,
        encoded,
        val_rows,
        numeric_features,
        lengths,
        device,
        args.precision,
        args.eval_batch_size,
    )
    if args.objective == "hierarchical":
        val_action_logits = hierarchical_joint_log_probabilities(
            val_raw_outputs,
            route_main_predictions[val_rows],
        )
    else:
        val_action_logits = val_raw_outputs
    metrics = fold_metrics(
        dataset, val_full_rows, val_rows, val_action_logits, split_spec
    )
    if args.cv_mode == "parent_inner3":
        run_suffix = f"pf{args.parent_fold}_if{args.inner_fold}"
    else:
        run_suffix = f"fold{args.fold_id}"
    experiment_id = f"{args.experiment_id}_{run_suffix}"
    logits_dir = Path(args.logits_dir)
    logits_dir.mkdir(parents=True, exist_ok=True)
    fold_path = logits_dir / f"{experiment_id}_val.pt"
    fold_payload = {
        "schema_version": 1,
        "format": FOLD_FORMAT,
        "experiment_id": experiment_id,
        "dataset_sha256": sha256_file(dataset_path),
        "usage_scope": dataset["usage_scope"],
        "parent_provenance": dataset["parent"],
        "promotion_eligible": bool(provenance_eligibility["promotion_eligible"]),
        "split_spec": split_spec,
        "n_folds": 9 if args.cv_mode == "parent_inner3" else 3,
        "val_full_indices": torch.tensor(val_full_rows, dtype=torch.long),
        "route_local_indices": torch.tensor(val_rows, dtype=torch.long),
        "route_ids": [dataset["route_ids"][row] for row in val_rows],
        "action_logits": val_action_logits,
        "actions": val_action_logits.argmax(dim=1),
        "raw_gate_logits": (
            val_raw_outputs[:, 0].clone()
            if args.objective == "hierarchical"
            else None
        ),
        "raw_alt_logits": (
            val_raw_outputs[:, 1:].clone()
            if args.objective == "hierarchical"
            else None
        ),
        "outputs": list(OUTPUT_NAMES),
        "recipe": recipe,
        "recipe_ablation": bool(recipe_mismatches),
        "recipe_mismatches": recipe_mismatches,
        "token_length_audit": length_audit,
        "metrics": metrics,
        "deployment_eligible": False,
    }
    torch.save(fold_payload, fold_path)

    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("experiments/incoming/models") / experiment_id
    )
    if args.save_model:
        if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite_output:
            raise FileExistsError(
                f"output directory is non-empty: {output_dir}; pass --overwrite-output"
            )
        save_model_artifact(model, tokenizer, output_dir, recipe, dataset["parent"])

    report = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "dataset": str(dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "usage_scope": dataset["usage_scope"],
        "promotion_eligible": False,
        "promotion_blockers": [
            "parent_inner3 removes the cross-parent level-2 path only; stitched_outer3 does not",
            "upstream champion parent folds do not exclude full-data KD/consensus label paths",
            "strict nested-main parent provenance is unavailable",
        ],
        "recipe": recipe,
        "recipe_ablation": bool(recipe_mismatches),
        "recipe_mismatches": recipe_mismatches,
        "train_rows": len(train_rows),
        "val_rows": len(val_rows),
        "class_weights": class_weights.tolist(),
        "optimizer_steps": optimizer_steps,
        "skipped_optimizer_steps": skipped_optimizer_steps,
        "token_length_audit": length_audit,
        "metrics": metrics,
        "val_logits_path": str(fold_path),
        "model_dir": str(output_dir) if args.save_model else None,
        "runtime_sec": time.perf_counter() - started,
        "train_command": " ".join(shlex.quote(value) for value in sys.argv),
    }
    artifacts_dir = Path(args.artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifacts_dir / f"{experiment_id}_metrics.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not args.no_results_csv:
        append_results_csv(
            Path("experiments/results.csv"),
            {
                "experiment_id": experiment_id,
                "model_family": "sim_recorded_policy_decoder_diagnostic_fold",
                "base_model": args.base_model,
                "features": "prompt + full action-name trajectory + row-z main posterior 7d",
                "serializer_name": SERIALIZER_NAME,
                "split_type": (
                    "diagnostic_parent_fold_inner3_session_cv"
                    if args.cv_mode == "parent_inner3"
                    else "diagnostic_stitched_outer3_session_cv"
                ),
                "fold_id": run_suffix,
                "seed": args.seed,
                "max_length": args.max_length,
                "epochs": args.epochs,
                "learning_rate": args.backbone_lr,
                "batch_size": args.batch_size,
                "macro_f1_raw": f"{metrics['full']['base_macro_f1']:.6f}",
                "macro_f1": f"{metrics['full']['macro_f1']:.6f}",
                "artifact_path": str(report_path),
                "val_logits_path": str(fold_path),
                "runtime_sec": f"{report['runtime_sec']:.3f}",
                "train_command": report["train_command"],
                "notes": (
                    f"diagnostic only; full_delta={metrics['full']['macro_delta']:+.6f}; "
                    f"sim_delta={metrics['sim']['macro_delta']:+.6f}; "
                    f"rescue={metrics['rescue']} harm={metrics['harm']}"
                ),
                "decision": "aggregate diagnostic folds; never promote without nested-main rerun",
            },
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
