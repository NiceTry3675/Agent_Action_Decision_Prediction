#!/usr/bin/env python3
"""Rejection-only frozen-head probe for a soft Macro-F1 auxiliary.

This deliberately optimistic CPU proxy trains a zero-initialized additive
classifier-head residual over cached HCX hidden states.  The paired control and
candidate use the same original rows, data order, base loss, optimizer, and
scheduler.  Epoch 1 is bit-identical base-loss warmup; in epoch 2 only, the
candidate adds a preregistered lambda=0.05 soft Macro-F1 loss computed over
large (normally 1024-row) class-complete windows.

It is a rejection gate, not promotion evidence: the frozen backbone, missing
replay rows, large macro windows, and fixed-val parent/teacher optimism do not
match a full batch-16 champion refit.  Outer validation is evaluated raw only
and is never used to tune lambda or any other parameter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shlex
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from transformers import get_linear_schedule_with_warmup

from script import ALL_CLASSES
from train import append_results_csv, f1_metrics
from train_transformer import classification_loss_values, class_weights


REPORT_FORMAT = "soft-macro-f1-frozen-head-prevalidation-v1"
CACHE_FORMAT = "selective-privileged-mode-cache-v1"
ARMS = ("control_base", "candidate_base_plus_soft_macro_f1")
WEAK4 = ("list_directory", "read_file", "grep_search", "glob_pattern")
PRIORITY5 = (*WEAK4, "web_search")
MONITORED = (*PRIORITY5, "lint_or_typecheck", "run_tests", "run_bash")


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def id_digest(ids: list[str]) -> str:
    digest = hashlib.sha256()
    for sample_id in ids:
        digest.update(sample_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def load_pt(path: Path):
    return torch.load(path, map_location="cpu", weights_only=False)


def validate_cache(cache: dict) -> None:
    if cache.get("format") != CACHE_FORMAT:
        raise ValueError(f"unexpected cache format: {cache.get('format')}")
    if cache.get("rows") != "original_only_no_replay":
        raise ValueError(f"cache must contain originals only: {cache.get('rows')}")
    if cache.get("split") != "session" or int(cache.get("seed", -1)) != 42:
        raise ValueError("cache must use the canonical session split seed42")
    if list(cache.get("classes") or []) != ALL_CLASSES:
        raise ValueError("cache class order mismatch")
    ids = [str(value) for value in cache.get("ids") or []]
    if len(ids) != 70000 or len(ids) != len(set(ids)):
        raise ValueError("cache ids must contain 70k unique originals")
    y = cache.get("y_true")
    hidden = cache.get("hidden")
    logits = cache.get("parent_logits")
    if not torch.is_tensor(y) or tuple(y.shape) != (len(ids),):
        raise ValueError("invalid cache labels")
    if not torch.is_tensor(hidden) or hidden.ndim != 2 or hidden.shape[0] != len(ids):
        raise ValueError("invalid cached hidden states")
    if not torch.is_tensor(logits) or tuple(logits.shape) != (len(ids), len(ALL_CLASSES)):
        raise ValueError("invalid cached parent logits")
    if bool(((y < 0) | (y >= len(ALL_CLASSES))).any()):
        raise ValueError("cache labels out of range")
    if not bool(torch.isfinite(logits.float()).all()):
        raise ValueError("non-finite parent logits")
    train = cache["train_indices"].to(torch.long)
    val = cache["val_indices"].to(torch.long)
    train_list = train.tolist()
    val_list = val.tolist()
    train_set = set(train_list)
    val_set = set(val_list)
    if (
        len(train_list) != len(train_set)
        or len(val_list) != len(val_set)
        or train_set & val_set
        or train_set | val_set != set(range(len(ids)))
    ):
        raise ValueError("cache train/validation partition is invalid")
    train_sessions = {str(cache["session_ids"][i]) for i in train.tolist()}
    val_sessions = {str(cache["session_ids"][i]) for i in val.tolist()}
    if train_sessions & val_sessions:
        raise ValueError("session leakage in cached split")


def align_teacher(pack: dict, path: Path, cache: dict) -> torch.Tensor:
    if list(pack.get("classes") or []) != ALL_CLASSES:
        raise ValueError(f"teacher class order mismatch: {path}")
    pack_ids = [str(value) for value in pack.get("ids") or []]
    cache_ids = [str(value) for value in cache["ids"]]
    if len(pack_ids) != len(set(pack_ids)) or set(pack_ids) != set(cache_ids):
        raise ValueError(f"teacher id coverage mismatch: {path}")
    logits = pack.get("logits")
    if not torch.is_tensor(logits) or tuple(logits.shape) != (len(pack_ids), len(ALL_CLASSES)):
        raise ValueError(f"invalid teacher logits: {path}")
    positions = {sample_id: i for i, sample_id in enumerate(pack_ids)}
    order = torch.tensor([positions[sample_id] for sample_id in cache_ids], dtype=torch.long)
    aligned = logits[order].float()
    if not bool(torch.isfinite(aligned).all()):
        raise ValueError("non-finite aligned teacher logits")
    teacher_y = pack.get("y_true")
    if torch.is_tensor(teacher_y):
        if not torch.equal(teacher_y[order].to(torch.long), cache["y_true"].to(torch.long)):
            raise ValueError("teacher/cache labels do not align by id")
    return aligned


def make_macro_windows(
    train_indices: list[int], y: torch.Tensor, window_size: int, rng: random.Random
) -> tuple[list[list[int]], dict]:
    if window_size < len(ALL_CLASSES):
        raise ValueError("macro window is smaller than the class count")
    order = list(train_indices)
    rng.shuffle(order)
    windows = [order[start : start + window_size] for start in range(0, len(order), window_size)]
    tail_merged = False
    if len(windows) > 1 and len(windows[-1]) < window_size // 2:
        windows[-2].extend(windows.pop())
        tail_merged = True
    flat = [idx for window in windows for idx in window]
    if len(flat) != len(train_indices) or len(flat) != len(set(flat)) or set(flat) != set(train_indices):
        raise AssertionError("macro windows dropped or duplicated training rows")
    supports = []
    missing = []
    for ordinal, window in enumerate(windows):
        counts = torch.bincount(y[torch.tensor(window, dtype=torch.long)], minlength=len(ALL_CLASSES))
        supports.append(counts.tolist())
        absent = [ALL_CLASSES[i] for i, count in enumerate(counts.tolist()) if count == 0]
        if absent:
            missing.append({"window": ordinal, "classes": absent})
    if missing:
        raise AssertionError(f"soft-F1 macro windows are not class-complete: {missing}")
    support_array = np.asarray(supports, dtype=np.int64)
    return windows, {
        "windows": len(windows),
        "window_size_min": min(map(len, windows)),
        "window_size_max": max(map(len, windows)),
        "tail_merged": tail_merged,
        "rows_expected": len(train_indices),
        "rows_seen": len(flat),
        "unique_rows_seen": len(set(flat)),
        "dropped_rows": len(set(train_indices) - set(flat)),
        "duplicated_rows": len(flat) - len(set(flat)),
        "all_14_classes_windows": int(
            sum(all(count > 0 for count in row) for row in supports)
        ),
        "missing_classes_by_window": missing,
        "min_support_by_class": {
            label: int(support_array[:, i].min()) for i, label in enumerate(ALL_CLASSES)
        },
        "mean_support_by_class": {
            label: float(support_array[:, i].mean()) for i, label in enumerate(ALL_CLASSES)
        },
    }


def soft_macro_f1_loss(
    logits: torch.Tensor, labels: torch.Tensor, epsilon: float = 1e-8
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Differentiable one-vs-rest Macro-F1 over a class-complete window."""
    probabilities = F.softmax(logits.float(), dim=-1)
    targets = F.one_hot(labels, num_classes=logits.shape[-1]).to(probabilities.dtype)
    support = targets.sum(dim=0)
    predicted_mass = probabilities.sum(dim=0)
    true_positive = (probabilities * targets).sum(dim=0)
    per_class = (2.0 * true_positive + epsilon) / (support + predicted_mass + epsilon)
    return 1.0 - per_class.mean(), per_class, support


def alpha_rows(y: torch.Tensor, alpha_rest: float, alpha_weak: float) -> torch.Tensor:
    weak_ids = {ALL_CLASSES.index(label) for label in WEAK4}
    return torch.tensor(
        [alpha_weak if int(label) in weak_ids else alpha_rest for label in y.tolist()],
        dtype=torch.float32,
    )


def mixed_base_loss(
    logits: torch.Tensor,
    labels: torch.Tensor,
    weights: torch.Tensor,
    teacher_p: torch.Tensor,
    alpha: torch.Tensor,
    label_smoothing: float,
    focal_gamma: float,
    temperature: float,
) -> torch.Tensor:
    arms, batch, classes = logits.shape
    hard = classification_loss_values(
        logits.reshape(arms * batch, classes),
        labels.view(1, batch).expand(arms, batch).reshape(-1),
        weights,
        label_smoothing,
        "focal",
        focal_gamma,
    ).view(arms, batch)
    student_lp = F.log_softmax(logits.float() / temperature, dim=-1)
    target = teacher_p.view(1, batch, classes).expand(arms, batch, classes)
    kd = F.kl_div(student_lp, target, reduction="none").sum(-1) * (temperature ** 2)
    alpha = alpha.view(1, batch)
    return ((1.0 - alpha) * hard + alpha * kd).mean(dim=1)


def predict(cache: dict, delta: torch.Tensor, device: torch.device, eval_batch_size: int) -> torch.Tensor:
    val_indices = cache["val_indices"].to(torch.long).tolist()
    outputs = []
    delta = delta.to(device=device, dtype=torch.float32)
    with torch.inference_mode():
        for start in range(0, len(val_indices), eval_batch_size):
            idx = torch.tensor(val_indices[start : start + eval_batch_size], dtype=torch.long)
            hidden = cache["hidden"][idx].to(device=device, dtype=torch.float32)
            base = cache["parent_logits"][idx].to(device=device, dtype=torch.float32)
            outputs.append((base.unsqueeze(0) + torch.einsum("bd,acd->abc", hidden, delta)).cpu())
    return torch.cat(outputs, dim=1)


def metrics_for_logits(logits: torch.Tensor, truth: torch.Tensor) -> dict:
    result = f1_metrics(truth.tolist(), logits.argmax(dim=-1).tolist())
    result["priority5_macro"] = float(
        np.mean([result["per_class_f1"][label] for label in PRIORITY5])
    )
    result["monitored_per_class_f1"] = {
        label: result["per_class_f1"][label] for label in MONITORED
    }
    return result


def compare_predictions(control: torch.Tensor, candidate: torch.Tensor, truth: torch.Tensor) -> dict:
    control_pred = control.argmax(dim=-1)
    candidate_pred = candidate.argmax(dim=-1)
    return {
        "rescues": int(((control_pred != truth) & (candidate_pred == truth)).sum()),
        "harms": int(((control_pred == truth) & (candidate_pred != truth)).sum()),
        "changed": int((control_pred != candidate_pred).sum()),
        "unchanged": int((control_pred == candidate_pred).sum()),
        "control_prediction_counts": dict(Counter(ALL_CLASSES[i] for i in control_pred.tolist())),
        "candidate_prediction_counts": dict(Counter(ALL_CLASSES[i] for i in candidate_pred.tolist())),
    }


def train_seed(args, cache: dict, teacher_p: torch.Tensor, order_seed: int, device: torch.device) -> dict:
    train_indices = cache["train_indices"].to(torch.long).tolist()
    y = cache["y_true"].to(torch.long)
    hidden_dim = int(cache["hidden"].shape[1])
    delta = torch.nn.Parameter(torch.zeros(len(ARMS), len(ALL_CLASSES), hidden_dim, device=device))
    optimizer = torch.optim.AdamW([delta], lr=args.lr, weight_decay=0.0)
    windows_per_epoch = math.ceil(len(train_indices) / args.macro_window_size)
    total_steps = windows_per_epoch * args.epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(total_steps * args.warmup_ratio),
        num_training_steps=total_steps,
    )
    weights = class_weights([int(y[i]) for i in train_indices], device, args.class_weight_power)
    alpha = alpha_rows(y, args.alpha_rest, args.alpha_weak)
    rng = random.Random(order_seed)
    history = []
    equality = None
    started = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        windows, coverage = make_macro_windows(train_indices, y, args.macro_window_size, rng)
        sums = {"base_control": 0.0, "base_candidate": 0.0, "soft_f1": 0.0,
                "weighted_aux": 0.0, "total_control": 0.0, "total_candidate": 0.0}
        seen = 0
        aux_weight = args.soft_f1_lambda if epoch >= args.aux_start_epoch else 0.0
        for window in windows:
            idx = torch.tensor(window, dtype=torch.long)
            hidden = cache["hidden"][idx].to(device=device, dtype=torch.float32)
            base_logits = cache["parent_logits"][idx].to(device=device, dtype=torch.float32)
            logits = base_logits.unsqueeze(0) + torch.einsum("bd,acd->abc", hidden, delta)
            labels = y[idx].to(device)
            base_losses = mixed_base_loss(
                logits, labels, weights, teacher_p[idx].to(device), alpha[idx].to(device),
                args.label_smoothing, args.focal_gamma, args.temperature,
            )
            aux_loss, _, support = soft_macro_f1_loss(logits[1], labels, args.epsilon)
            if bool((support <= 0).any()):
                raise AssertionError("class-complete macro window invariant failed")
            losses = torch.stack((base_losses[0], base_losses[1] + aux_weight * aux_loss))
            optimizer.zero_grad(set_to_none=True)
            losses.sum().backward()
            optimizer.step()
            scheduler.step()
            n = len(window)
            sums["base_control"] += float(base_losses[0].detach()) * n
            sums["base_candidate"] += float(base_losses[1].detach()) * n
            sums["soft_f1"] += float(aux_loss.detach()) * n
            sums["weighted_aux"] += float(aux_weight * aux_loss.detach()) * n
            sums["total_control"] += float(losses[0].detach()) * n
            sums["total_candidate"] += float(losses[1].detach()) * n
            seen += n
        means = {key: value / seen for key, value in sums.items()}
        means["weighted_aux_to_base_ratio"] = means["weighted_aux"] / max(
            means["base_candidate"], 1e-12
        )
        history.append({"epoch": epoch, "aux_weight": aux_weight, "loss_means": means,
                        "macro_window_coverage": coverage})
        if epoch == args.aux_start_epoch - 1:
            max_abs = float((delta[0] - delta[1]).detach().abs().max().cpu())
            state = optimizer.state[delta]
            exp_avg = state["exp_avg"]
            exp_avg_sq = state["exp_avg_sq"]
            exp_avg_max_abs = float((exp_avg[0] - exp_avg[1]).detach().abs().max().cpu())
            exp_avg_sq_max_abs = float(
                (exp_avg_sq[0] - exp_avg_sq[1]).detach().abs().max().cpu()
            )
            equality = {
                "checked_after_epoch": epoch,
                "parameter_max_abs": max_abs,
                "parameter_torch_equal": bool(
                    torch.equal(delta[0].detach(), delta[1].detach())
                ),
                "adam_exp_avg_max_abs": exp_avg_max_abs,
                "adam_exp_avg_torch_equal": bool(
                    torch.equal(exp_avg[0].detach(), exp_avg[1].detach())
                ),
                "adam_exp_avg_sq_max_abs": exp_avg_sq_max_abs,
                "adam_exp_avg_sq_torch_equal": bool(
                    torch.equal(exp_avg_sq[0].detach(), exp_avg_sq[1].detach())
                ),
            }
            if (
                max_abs != 0.0
                or exp_avg_max_abs != 0.0
                or exp_avg_sq_max_abs != 0.0
                or not equality["parameter_torch_equal"]
                or not equality["adam_exp_avg_torch_equal"]
                or not equality["adam_exp_avg_sq_torch_equal"]
            ):
                raise AssertionError(f"paired warmup arms diverged before auxiliary: {equality}")
    if equality is None:
        raise AssertionError("epoch-1 equality was not checked")
    logits = predict(cache, delta.detach().cpu(), device, args.eval_batch_size)
    val_idx = cache["val_indices"].to(torch.long)
    truth = y[val_idx]
    control = metrics_for_logits(logits[0], truth)
    candidate = metrics_for_logits(logits[1], truth)
    deltas = {
        "macro_f1": float(candidate["macro_f1"] - control["macro_f1"]),
        "priority5_macro": float(candidate["priority5_macro"] - control["priority5_macro"]),
        "per_class_f1": {
            label: float(candidate["per_class_f1"][label] - control["per_class_f1"][label])
            for label in ALL_CLASSES
        },
    }
    return {
        "order_seed": order_seed,
        "runtime_seconds": time.perf_counter() - started,
        "epoch1_paired_equality": equality,
        "history": history,
        "delta_norms": {ARMS[i]: float(delta[i].detach().norm().cpu()) for i in range(len(ARMS))},
        "raw_outer_val": {ARMS[0]: control, ARMS[1]: candidate, "candidate_minus_control": deltas},
        "rescue_harm": compare_predictions(logits[0], logits[1], truth),
    }


def aggregate(seed_reports: dict[str, dict], args) -> dict:
    macro_deltas = [seed_reports[str(seed)]["raw_outer_val"]["candidate_minus_control"]["macro_f1"]
                    for seed in args.order_seeds]
    class_means = {
        label: float(np.mean([
            seed_reports[str(seed)]["raw_outer_val"]["candidate_minus_control"]["per_class_f1"][label]
            for seed in args.order_seeds
        ])) for label in ALL_CLASSES
    }
    reasons = []
    if not all(value > 0.0 for value in macro_deltas):
        reasons.append("candidate raw Macro-F1 delta is not positive in every data-order seed")
    if float(np.mean(macro_deltas)) < args.gate_macro_delta:
        reasons.append(f"mean raw Macro-F1 delta is below {args.gate_macro_delta:+.6f}")
    negative_priority = [label for label in PRIORITY5 if class_means[label] < 0.0]
    if negative_priority:
        reasons.append(f"priority weak-class mean deltas are negative: {negative_priority}")
    return {
        "passed_rejection_gate": not reasons,
        "interpretation": "negative rejects; positive only justifies a full-model screen",
        "reject_reasons": reasons,
        "macro_delta_by_order_seed": dict(zip(map(str, args.order_seeds), macro_deltas)),
        "macro_delta_mean": float(np.mean(macro_deltas)),
        "macro_delta_std": float(np.std(macro_deltas)),
        "mean_per_class_delta": class_means,
        "mean_monitored_per_class_delta": {label: class_means[label] for label in MONITORED},
        "thresholds": {
            "all_seed_macro_deltas_positive": True,
            "mean_raw_macro_delta": args.gate_macro_delta,
            "priority5_mean_per_class_nonnegative": list(PRIORITY5),
        },
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="experiments/cache/kd_hcx_m8_screen_cache.pt")
    parser.add_argument("--teacher-logits", default="experiments/logits/m8_qwen35_refit_train70k_fp16.pt")
    parser.add_argument("--output", default="experiments/artifacts/20260712_soft_macro_f1_head_prevalidation.json")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu")
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--aux-start-epoch", type=int, default=2)
    parser.add_argument("--macro-window-size", type=int, default=1024)
    parser.add_argument("--eval-batch-size", type=int, default=1024)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--warmup-ratio", type=float, default=0.06)
    parser.add_argument("--class-weight-power", type=float, default=0.5)
    parser.add_argument("--label-smoothing", type=float, default=0.02)
    parser.add_argument("--focal-gamma", type=float, default=2.0)
    parser.add_argument("--temperature", type=float, default=3.0)
    parser.add_argument("--alpha-rest", type=float, default=0.5)
    parser.add_argument("--alpha-weak", type=float, default=0.7)
    parser.add_argument("--soft-f1-lambda", type=float, default=0.05)
    parser.add_argument("--epsilon", type=float, default=1e-8)
    parser.add_argument("--order-seeds", type=int, nargs="+", default=[42, 777, 2026])
    parser.add_argument("--gate-macro-delta", type=float, default=0.002)
    parser.add_argument("--append-results", action="store_true")
    args = parser.parse_args()
    if args.epochs != 2 or args.aux_start_epoch != 2:
        parser.error("pre-registered probe requires exactly 2 epochs and aux start at epoch 2")
    if not math.isclose(args.soft_f1_lambda, 0.05, rel_tol=0.0, abs_tol=1e-12):
        parser.error("pre-registered probe requires --soft-f1-lambda 0.05")
    if args.macro_window_size != 1024:
        parser.error("pre-registered probe requires --macro-window-size 1024")
    if args.order_seeds != [42, 777, 2026]:
        parser.error("pre-registered probe requires --order-seeds 42 777 2026")
    return args


def main():
    args = parse_args()
    root = Path(__file__).resolve().parent
    started = time.perf_counter()
    cache_path = root / args.cache
    teacher_path = root / args.teacher_logits
    cache_sha_before = sha256_file(cache_path)
    cache = load_pt(cache_path)
    validate_cache(cache)
    teacher_logits = align_teacher(load_pt(teacher_path), teacher_path, cache)
    teacher_p = F.softmax(teacher_logits / args.temperature, dim=-1)
    device = torch.device(
        "cuda" if args.device == "auto" and torch.cuda.is_available()
        else "cpu" if args.device == "auto" else args.device
    )
    seed_reports = {}
    for seed in args.order_seeds:
        print(f"soft-F1 frozen-head order_seed={seed} device={device}", flush=True)
        report = train_seed(args, cache, teacher_p, seed, device)
        seed_reports[str(seed)] = report
        delta = report["raw_outer_val"]["candidate_minus_control"]["macro_f1"]
        print(f"  raw macro delta={delta:+.6f} runtime={report['runtime_seconds']:.1f}s", flush=True)
    gates = aggregate(seed_reports, args)
    val_idx = cache["val_indices"].to(torch.long)
    parent_metrics = metrics_for_logits(cache["parent_logits"][val_idx], cache["y_true"][val_idx])
    runtime = time.perf_counter() - started
    cache_sha_after = sha256_file(cache_path)
    if cache_sha_before != cache_sha_after:
        raise AssertionError("cache file changed during read-only probe")
    report = {
        "format": REPORT_FORMAT,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "command": shlex.join(sys.argv),
        "probe_contract": {
            "purpose": "rejection-only frozen-head gate for preregistered soft Macro-F1 auxiliary",
            "trained_parameters": "zero-initialized additive 14x1024 classifier-head residual; backbone/cache immutable",
            "paired_control": "same initialization, rows, order, base loss, optimizer, and schedule",
            "base_loss": "original-row class-weighted focal CE (p=0.5, smoothing=.02, gamma=2) mixed with M8 KD (rest alpha=.5, Weak4 alpha=.7, T=3)",
            "candidate_change_only": "epoch 2 adds lambda=.05 soft Macro-F1 outside the per-row alpha mix",
            "macro_windows": "large shuffled class-complete cached-head windows; tail retained/merged only below half-window",
            "outer_validation": "raw fixed-session validation only; no bias, rules, threshold, lambda, or epoch tuning",
            "replay": "cache contains originals only; replay omitted identically from both paired arms",
            "consensus": "no consensus artifact loaded; champion sieve affects backbone only and cannot be represented by this frozen pre-sieve cache",
            "interpretation": "optimistic proxy: negative rejects; positive cannot promote without full-model screen",
        },
        "config": vars(args),
        "hashes": {
            "cache_sha256_before": cache_sha_before,
            "cache_sha256_after": cache_sha_after,
            "teacher_sha256": sha256_file(teacher_path),
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "all_ids_sha256": id_digest([str(value) for value in cache["ids"]]),
            "train_ids_sha256": id_digest([str(cache["ids"][i]) for i in cache["train_indices"].tolist()]),
            "val_ids_sha256": id_digest([str(cache["ids"][i]) for i in cache["val_indices"].tolist()]),
            "checkpoint_sha256": cache.get("checkpoint_sha256"),
        },
        "cache": {
            "path": str(cache_path), "format": cache.get("format"), "rows": cache.get("rows"),
            "train_rows": len(cache["train_indices"]), "val_rows": len(cache["val_indices"]),
            "hidden_shape": list(cache["hidden"].shape), "parent_anchor_check": cache.get("anchor_check"),
        },
        "teacher": {"path": str(teacher_path), "temperature": args.temperature},
        "parent_raw_outer_val": parent_metrics,
        "order_seed_reports": seed_reports,
        "gates": gates,
        "decision": "retain_for_full_model_screen_only" if gates["passed_rejection_gate"] else "reject",
        "runtime_seconds": runtime,
        "caveats": [
            "1024-row soft-F1 windows are an optimistic upper-bound proxy and do not map directly to full-model batch16 training.",
            "Frozen cached hidden states cannot test representation or backbone-gradient effects.",
            "Replay rows are absent; a full recipe has 10k fold-aware replay rows with pure hard-label loss and weight 0.5.",
            "The cached parent is the M8-KD screen, not the later consensus-sieve+conditional-alpha full-refit champion.",
            "M8 full-refit teacher saw all original rows, so absolute fixed-val levels are optimistic; only the paired delta is inspected.",
            "The three order seeds reuse one outer validation set; they measure optimizer/data-order sensitivity, not independent validation replicates.",
        ],
    }
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved {output} decision={report['decision']} mean_delta={gates['macro_delta_mean']:+.6f} runtime={runtime:.1f}s")
    if args.append_results:
        mean_candidate = float(np.mean([
            seed_reports[str(seed)]["raw_outer_val"][ARMS[1]]["macro_f1"] for seed in args.order_seeds
        ]))
        append_results_csv(root / "experiments/results.csv", {
            "experiment_id": "20260712_soft_macro_f1_head_preval",
            "model_family": "frozen_head_prevalidation",
            "base_model": "naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B",
            "features": "cached HCX hidden + preregistered soft Macro-F1 auxiliary",
            "serializer_name": "current_v1", "split_type": "session_fixed_rejection_only",
            "seed": "/".join(map(str, args.order_seeds)), "max_length": 384,
            "epochs": args.epochs, "learning_rate": args.lr, "batch_size": args.macro_window_size,
            "class_weight_power": args.class_weight_power, "label_smoothing": args.label_smoothing,
            "replay_mode": "none_cached_originals_only", "replay_size": 0,
            "macro_f1_raw": mean_candidate, "macro_f1": mean_candidate,
            "artifact_path": args.output, "runtime_sec": runtime, "train_command": shlex.join(sys.argv),
            "notes": f"rejection-only soft-F1 lambda=.05; mean paired raw delta={gates['macro_delta_mean']:+.6f}; large 1024-row macro-window proxy",
            "decision": report["decision"],
        })


if __name__ == "__main__":
    main()
