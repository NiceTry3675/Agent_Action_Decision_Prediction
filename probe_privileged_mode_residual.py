#!/usr/bin/env python3
"""Run the cached-hidden selective privileged mode residual experiment.

The script fits its taxonomy from the canonical fixed-train sessions only,
then compares C0/C1/M against five action-by-turn-bin permutation controls.
The transformer backbone and the parent 14-way logits never change.
"""

import argparse
import hashlib
import json
import math
import random
import shlex
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from transformers import get_linear_schedule_with_warmup

from privileged_event_modes import (
    DEFAULT_SELECTED_ACTIONS,
    UNKNOWN_MODE_ID,
    event_mode_signature,
    fit_selective_taxonomy,
    reconstruct_full_events,
    transform_mode_targets,
    turn_bin,
)
from privileged_mode_residual import (
    SelectiveModeResidual,
    conditional_mode_loss_values,
)
from script import ALL_CLASSES, load_jsonl
from train import CLASS_TO_ID, f1_metrics, load_labels, session_id
from train_transformer import class_weights, classification_loss_values, make_batches


CACHE_FORMAT = "selective-privileged-mode-cache-v1"
REPORT_FORMAT = "selective-privileged-mode-probe-v1"


def sha256_file(path, chunk_size=8 * 1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(payload):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_cache(path):
    payload = torch.load(path, map_location="cpu", weights_only=False)
    required = {
        "format",
        "ids",
        "y_true",
        "hidden",
        "parent_logits",
        "token_lengths",
        "event_recovery_statuses",
        "event_recovered",
        "candidate_mode_labels",
        "session_ids",
        "turn_bins",
        "train_indices",
        "val_indices",
        "classes",
        "split",
        "seed",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"hidden cache missing keys: {sorted(missing)}")
    if payload["format"] != CACHE_FORMAT:
        raise ValueError(f"unsupported cache format: {payload['format']!r}")
    if list(payload["classes"]) != ALL_CLASSES:
        raise ValueError("hidden cache class order does not match ALL_CLASSES")
    if payload["split"] != "session" or int(payload["seed"]) != 42:
        raise ValueError("hidden cache must use the canonical session/seed42 split")
    ids = [str(value) for value in payload["ids"]]
    n_rows = len(ids)
    if len(ids) != len(set(ids)):
        raise ValueError("hidden cache contains duplicate ids")
    if tuple(payload["hidden"].shape[:1]) != (n_rows,):
        raise ValueError("hidden cache row count does not match ids")
    if tuple(payload["parent_logits"].shape) != (n_rows, len(ALL_CLASSES)):
        raise ValueError("parent logits must have shape [N,14]")
    if len(payload["y_true"]) != n_rows or len(payload["token_lengths"]) != n_rows:
        raise ValueError("cache target/length tensors do not align with ids")
    for key in (
        "event_recovery_statuses",
        "event_recovered",
        "candidate_mode_labels",
        "session_ids",
        "turn_bins",
    ):
        if len(payload[key]) != n_rows:
            raise ValueError(f"cache {key} does not align with ids")
    train_idx = payload["train_indices"].to(torch.long)
    val_idx = payload["val_indices"].to(torch.long)
    if set(train_idx.tolist()) & set(val_idx.tolist()):
        raise ValueError("hidden cache train/validation indices overlap")
    if len(train_idx) + len(val_idx) != n_rows:
        raise ValueError("hidden cache split does not cover every original row")
    return payload


def load_and_validate_data(data_dir, cache):
    data_dir = Path(data_dir)
    samples = load_jsonl(data_dir / "train.jsonl")
    ids = [str(sample.get("id", "")) for sample in samples]
    if ids != [str(value) for value in cache["ids"]]:
        raise ValueError("train.jsonl order/ids do not match the hidden cache")
    if cache.get("train_jsonl_sha256"):
        actual = sha256_file(data_dir / "train.jsonl")
        if actual != cache["train_jsonl_sha256"]:
            raise ValueError("train.jsonl SHA256 does not match hidden cache provenance")
    if cache.get("train_labels_sha256"):
        actual = sha256_file(data_dir / "train_labels.csv")
        if actual != cache["train_labels_sha256"]:
            raise ValueError("train_labels.csv SHA256 does not match hidden cache provenance")
    expected_code_hash = (cache.get("code_sha256") or {}).get(
        "privileged_event_modes.py"
    )
    if expected_code_hash:
        actual = sha256_file(Path(__file__).with_name("privileged_event_modes.py"))
        if actual != expected_code_hash:
            raise ValueError(
                "privileged_event_modes.py SHA256 does not match hidden cache provenance"
            )
    labels_by_id = load_labels(data_dir / "train_labels.csv")
    labels = [labels_by_id[sample_id] for sample_id in ids]
    y = torch.tensor([CLASS_TO_ID[label] for label in labels], dtype=torch.long)
    if not torch.equal(y, cache["y_true"].to(torch.long)):
        raise ValueError("train labels do not match hidden cache targets")
    return samples, labels, y


def session_hash_fold(sample_id, n_folds=3):
    digest = hashlib.sha256(session_id(sample_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % n_folds


def evaluate_mode_predictability(
    hidden,
    ids,
    train_indices,
    actions,
    local_mode_ids,
    active_actions,
    mode_names_by_action,
    seed,
    logistic_c,
    n_folds=3,
):
    """Fixed-train-only grouped linear check against an empirical prior."""

    train_set = set(int(value) for value in train_indices)
    report = {}
    hidden_np = hidden
    if isinstance(hidden_np, torch.Tensor):
        hidden_np = hidden_np.numpy()
    for action in active_actions:
        rows = sorted(
            i
            for i in train_set
            if actions[i] == action and int(local_mode_ids[i]) != UNKNOWN_MODE_ID
        )
        if not rows:
            raise ValueError(f"predictability check has no rows for {action}")
        targets = np.asarray([int(local_mode_ids[i]) for i in rows], dtype=np.int64)
        folds = np.asarray([session_hash_fold(ids[i], n_folds) for i in rows])
        model_correct = prior_correct = 0
        model_nll = prior_nll = 0.0
        seen = 0
        fold_rows = []
        per_mode = defaultdict(
            lambda: {
                "rows": 0,
                "model_correct": 0,
                "prior_correct": 0,
                "model_nll_sum": 0.0,
                "prior_nll_sum": 0.0,
            }
        )
        for fold in range(n_folds):
            fit_mask = folds != fold
            test_mask = folds == fold
            if not bool(test_mask.any()):
                raise ValueError(f"empty inner fold {fold} for action {action}")
            fit_y = targets[fit_mask]
            test_y = targets[test_mask]
            classes = np.unique(targets)
            if set(np.unique(fit_y)) != set(classes):
                raise ValueError(f"inner fold {fold} is missing a mode for action {action}")
            scaler = StandardScaler()
            fit_rows = np.asarray(rows)[fit_mask]
            test_rows = np.asarray(rows)[test_mask]
            fit_x = scaler.fit_transform(hidden_np[fit_rows].astype(np.float32))
            test_x = scaler.transform(hidden_np[test_rows].astype(np.float32))
            classifier = LogisticRegression(
                C=logistic_c,
                solver="lbfgs",
                max_iter=1000,
                tol=1e-4,
                random_state=seed,
            )
            classifier.fit(fit_x, fit_y)
            probabilities = classifier.predict_proba(test_x)
            class_to_col = {int(value): col for col, value in enumerate(classifier.classes_)}
            target_prob = np.asarray(
                [probabilities[j, class_to_col[int(value)]] for j, value in enumerate(test_y)]
            )
            model_pred = classifier.classes_[probabilities.argmax(1)]
            counts = np.bincount(fit_y, minlength=int(classes.max()) + 1).astype(np.float64)
            counts += 1.0
            prior = counts / counts.sum()
            prior_pred = int(prior.argmax())
            n = len(test_y)
            model_correct += int((model_pred == test_y).sum())
            prior_correct += int((test_y == prior_pred).sum())
            model_nll += float(-np.log(np.clip(target_prob, 1e-12, 1.0)).sum())
            prior_nll += float(-np.log(np.clip(prior[test_y], 1e-12, 1.0)).sum())
            for row_no, target in enumerate(test_y):
                target = int(target)
                stats = per_mode[target]
                stats["rows"] += 1
                stats["model_correct"] += int(int(model_pred[row_no]) == target)
                stats["prior_correct"] += int(prior_pred == target)
                stats["model_nll_sum"] += float(
                    -np.log(np.clip(target_prob[row_no], 1e-12, 1.0))
                )
                stats["prior_nll_sum"] += float(
                    -np.log(np.clip(prior[target], 1e-12, 1.0))
                )
            seen += n
            fold_rows.append({"fold": fold, "train": int(fit_mask.sum()), "val": n})
        result = {
            "rows": seen,
            "classifier": {
                "kind": "fold-local StandardScaler + L2 LogisticRegression",
                "C": logistic_c,
            },
            "folds": fold_rows,
            "prior_accuracy": prior_correct / seen,
            "model_accuracy": model_correct / seen,
            "prior_nll": prior_nll / seen,
            "model_nll": model_nll / seen,
        }
        result["nll_delta"] = result["model_nll"] - result["prior_nll"]
        result["passed"] = result["model_nll"] < result["prior_nll"]
        names = mode_names_by_action[action]
        result["per_mode"] = {}
        for mode_id, stats in sorted(per_mode.items()):
            rows = stats.pop("rows")
            model_mode_nll = stats.pop("model_nll_sum") / rows
            prior_mode_nll = stats.pop("prior_nll_sum") / rows
            result["per_mode"][names[mode_id]] = {
                "rows": rows,
                "model_accuracy": stats.pop("model_correct") / rows,
                "prior_accuracy": stats.pop("prior_correct") / rows,
                "model_nll": model_mode_nll,
                "prior_nll": prior_mode_nll,
                "nll_delta": model_mode_nll - prior_mode_nll,
                "passed": model_mode_nll < prior_mode_nll,
            }
        report[action] = result
    return report


def build_mode_target_tensors(targets):
    action_ids = torch.full((len(targets.indices),), UNKNOWN_MODE_ID, dtype=torch.long)
    local_ids = torch.tensor(targets.local_mode_ids, dtype=torch.long)
    for i, (action, supervised) in enumerate(zip(targets.actions, targets.mode_mask)):
        if supervised:
            action_ids[i] = CLASS_TO_ID[action]
    return action_ids, local_ids


def make_permuted_targets(
    local_ids,
    action_ids,
    train_indices,
    turn_bins,
    seed,
):
    output = local_ids.clone()
    groups = defaultdict(list)
    for index in train_indices:
        index = int(index)
        if int(action_ids[index]) < 0 or int(local_ids[index]) < 0:
            continue
        groups[(int(action_ids[index]), turn_bins[index])].append(index)
    rng = np.random.default_rng(seed)
    mutable = singleton = changed = total = 0
    by_group = {}
    for key, rows in sorted(groups.items()):
        values = output[rows].clone()
        unique = int(values.unique().numel())
        if len(rows) < 2 or unique < 2:
            singleton += 1
            by_group[f"{key[0]}:{key[1]}"] = {
                "rows": len(rows), "unique_modes": unique, "changed": 0
            }
            continue
        permutation = torch.from_numpy(rng.permutation(len(rows))).to(torch.long)
        shuffled = values[permutation]
        group_changed = int((shuffled != values).sum())
        output[rows] = shuffled
        mutable += 1
        changed += group_changed
        total += len(rows)
        by_group[f"{key[0]}:{key[1]}"] = {
            "rows": len(rows), "unique_modes": unique, "changed": group_changed
        }
    return output, {
        "seed": seed,
        "groups": len(groups),
        "mutable_groups": mutable,
        "single_or_one_mode_groups": singleton,
        "mutable_rows": total,
        "changed_rows": changed,
        "hamming_change_rate": changed / total if total else 0.0,
        "by_group": by_group,
    }


def train_residual_arm(
    cache,
    y,
    priors_by_action,
    mode_action_ids,
    mode_local_ids,
    args,
    mode_weight,
):
    device = torch.device(args.device)
    torch.manual_seed(args.head_seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(args.head_seed)
    model = SelectiveModeResidual(
        hidden_size=cache["hidden"].shape[1],
        num_actions=len(ALL_CLASSES),
        priors_by_action=priors_by_action,
    ).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay
    )
    train_indices = cache["train_indices"].tolist()
    lengths = cache["token_lengths"].tolist()
    batches_per_epoch = math.ceil(len(train_indices) / args.batch_size)
    total_steps = batches_per_epoch * args.epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        int(total_steps * args.warmup_ratio),
        total_steps,
    )
    weights = class_weights(
        [int(y[i]) for i in train_indices], device, args.class_weight_power
    )
    rng = random.Random(args.head_seed)
    history = []
    for epoch in range(1, args.epochs + 1):
        total_action = total_mode = total_loss = 0.0
        total_rows = total_mode_rows = 0
        for batch_idx in make_batches(
            train_indices,
            args.batch_size,
            rng,
            lengths,
            args.bucket_multiplier,
        ):
            hidden = cache["hidden"][batch_idx].to(device=device, dtype=torch.float32)
            parent = cache["parent_logits"][batch_idx].to(device=device, dtype=torch.float32)
            labels = y[batch_idx].to(device)
            logits = model(hidden, parent)
            action_values = classification_loss_values(
                logits,
                labels,
                weights,
                args.label_smoothing,
                "focal",
                args.focal_gamma,
            )
            row_loss = action_values
            mode_values = torch.zeros_like(action_values)
            known = torch.zeros_like(labels, dtype=torch.bool)
            if mode_weight > 0:
                mode_values, known = conditional_mode_loss_values(
                    model,
                    hidden,
                    mode_action_ids[batch_idx].to(device),
                    mode_local_ids[batch_idx].to(device),
                    normalization=args.mode_loss_normalization,
                )
                row_loss = row_loss + mode_weight * mode_values
            loss = row_loss.mean()
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
            optimizer.step()
            scheduler.step()
            n = len(batch_idx)
            total_rows += n
            total_mode_rows += int(known.sum())
            total_action += float(action_values.detach().sum().cpu())
            total_mode += float(mode_values.detach().sum().cpu())
            total_loss += float(row_loss.detach().sum().cpu())
        history.append(
            {
                "epoch": epoch,
                "rows": total_rows,
                "mode_rows": total_mode_rows,
                "action_loss": total_action / max(1, total_rows),
                "mode_loss_all_row_mean": total_mode / max(1, total_rows),
                "mode_loss_known_mean": total_mode / max(1, total_mode_rows),
                "total_loss": total_loss / max(1, total_rows),
            }
        )
    return model, history


def predict_residual(model, cache, indices, batch_size, device):
    device = torch.device(device)
    output = torch.empty((len(indices), len(ALL_CLASSES)), dtype=torch.float32)
    corrections = torch.empty_like(output)
    model.eval()
    with torch.inference_mode():
        for start in range(0, len(indices), batch_size):
            batch_idx = indices[start:start + batch_size]
            hidden = cache["hidden"][batch_idx].to(device=device, dtype=torch.float32)
            parent = cache["parent_logits"][batch_idx].to(device=device, dtype=torch.float32)
            correction = model.corrections(hidden)
            output[start:start + len(batch_idx)] = (parent + correction).cpu()
            corrections[start:start + len(batch_idx)] = correction.cpu()
    return output, corrections


def slice_metrics(y, logits, indices, selected_actions):
    indices = [int(value) for value in indices]
    true = [int(y[i]) for i in indices]
    pred = logits.argmax(1).tolist() if indices else []
    metrics = f1_metrics(true, pred)
    support = Counter(ALL_CLASSES[value] for value in true)
    selected_sum = sum(metrics["per_class_f1"][action] for action in selected_actions)
    return {
        "rows": len(indices),
        "support": dict(sorted(support.items())),
        "accuracy": (
            sum(int(a == b) for a, b in zip(true, pred)) / len(indices)
            if indices
            else 0.0
        ),
        "macro_f1": metrics["macro_f1"],
        "selected_f1_sum": selected_sum,
        "per_class_f1": metrics["per_class_f1"],
        "prediction_distribution": metrics["prediction_distribution"],
    }


def evaluate_slices(
    y,
    val_indices,
    val_logits,
    recovery_statuses,
    terminal_flags,
    turn_bins,
    selected_actions,
):
    val_indices = [int(value) for value in val_indices]
    selected_ids = {CLASS_TO_ID[action] for action in selected_actions}
    masks = {
        "full": [True] * len(val_indices),
        "recovered": [recovery_statuses[i] == "recovered" for i in val_indices],
        "unrecovered": [recovery_statuses[i] != "recovered" for i in val_indices],
        "terminal": [terminal_flags[i] for i in val_indices],
        "nonterminal": [not terminal_flags[i] for i in val_indices],
        "true_selected": [int(y[i]) in selected_ids for i in val_indices],
        "true_nonselected": [int(y[i]) not in selected_ids for i in val_indices],
    }
    for name in ("start", "early", "mid", "late", "long", "unknown"):
        masks[f"turn_{name}"] = [turn_bins[i] == name for i in val_indices]
    output = {}
    for name, mask in masks.items():
        local_rows = [j for j, keep in enumerate(mask) if keep]
        global_rows = [val_indices[j] for j in local_rows]
        output[name] = slice_metrics(
            y,
            val_logits[local_rows],
            global_rows,
            selected_actions,
        )
    return output


def correction_diagnostics(corrections, y_val, selected_actions, model):
    report = {}
    for action in selected_actions:
        action_id = CLASS_TO_ID[action]
        values = corrections[:, action_id]
        positive = values[y_val == action_id]
        negative = values[y_val != action_id]

        def stats(tensor):
            if not len(tensor):
                return {"rows": 0}
            abs_values = tensor.abs()
            return {
                "rows": len(tensor),
                "mean": float(tensor.mean()),
                "std": float(tensor.std(unbiased=False)),
                "p95_abs": float(torch.quantile(abs_values, 0.95)),
                "max_abs": float(abs_values.max()),
            }

        report[action] = {"positive": stats(positive), "negative": stats(negative)}
    report["parameter_l2"] = math.sqrt(
        sum(float((parameter.detach().float() ** 2).sum()) for parameter in model.parameters())
    )
    return report


def mode_validation_metrics(model, cache, val_indices, action_ids, local_ids, taxonomy, device):
    device = torch.device(device)
    report = {}
    for action in taxonomy.active_actions:
        action_id = CLASS_TO_ID[action]
        rows = [
            int(i)
            for i in val_indices
            if int(action_ids[int(i)]) == action_id and int(local_ids[int(i)]) >= 0
        ]
        if not rows:
            report[action] = {"rows": 0}
            continue
        with torch.inference_mode():
            hidden = cache["hidden"][rows].to(device=device, dtype=torch.float32)
            logits = model.mode_logits(hidden, action_id)
            targets = local_ids[rows].to(device)
            nll = torch.nn.functional.cross_entropy(logits, targets)
            prior_logits = model.log_prior(action_id).expand(len(rows), -1)
            prior_nll = torch.nn.functional.cross_entropy(prior_logits, targets)
            pred = logits.argmax(1)
            recalls = []
            for mode_id in range(logits.shape[1]):
                mask = targets == mode_id
                if bool(mask.any()):
                    recalls.append(float((pred[mask] == mode_id).float().mean()))
        report[action] = {
            "rows": len(rows),
            "prior_nll": float(prior_nll),
            "model_nll": float(nll),
            "accuracy": float((pred == targets).float().mean()),
            "balanced_accuracy": sum(recalls) / len(recalls),
        }
    return report


def delta_metrics(current, reference):
    output = {}
    for slice_name, metrics in current.items():
        base = reference[slice_name]
        output[slice_name] = {
            "macro_f1": metrics["macro_f1"] - base["macro_f1"],
            "accuracy": metrics["accuracy"] - base["accuracy"],
            "selected_f1_sum": metrics["selected_f1_sum"] - base["selected_f1_sum"],
            "per_class_f1": {
                action: metrics["per_class_f1"][action] - base["per_class_f1"][action]
                for action in ALL_CLASSES
            },
        }
    return output


def evaluate_gates(arms, selected_actions):
    c0 = arms["C0"]["metrics"]
    c1 = arms["C1"]["metrics"]
    mode = arms["M"]["metrics"]
    permutation_scores = [
        payload["metrics"]["full"]["macro_f1"]
        for name, payload in arms.items()
        if name.startswith("P")
    ]
    per_action_delta = {
        action: mode["full"]["per_class_f1"][action] - c0["full"]["per_class_f1"][action]
        for action in selected_actions
    }
    checks = {
        "G1_full_macro_vs_C0": mode["full"]["macro_f1"] - c0["full"]["macro_f1"] >= 0.002,
        "G2_full_macro_vs_C1": mode["full"]["macro_f1"] - c1["full"]["macro_f1"] > 0.001,
        "G3_above_all_permutations": bool(permutation_scores)
        and mode["full"]["macro_f1"] > max(permutation_scores),
        "G4_selected_sum_vs_C0": (
            mode["full"]["selected_f1_sum"] - c0["full"]["selected_f1_sum"] >= 0.015
        ),
        "G5_two_of_three_selected_positive": sum(value > 0 for value in per_action_delta.values()) >= 2,
        "G6_unrecovered_selected_sum_no_drop": (
            mode["unrecovered"]["selected_f1_sum"]
            - c0["unrecovered"]["selected_f1_sum"]
            >= -0.005
        ),
        "G7_late_long_macro_no_drop": all(
            mode[name]["macro_f1"] - c0[name]["macro_f1"] >= -0.001
            for name in ("turn_late", "turn_long")
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "selected_action_f1_delta_vs_C0": per_action_delta,
        "mode_macro_delta_vs_C0": mode["full"]["macro_f1"] - c0["full"]["macro_f1"],
        "mode_macro_delta_vs_C1": mode["full"]["macro_f1"] - c1["full"]["macro_f1"],
        "mode_minus_best_permutation": (
            mode["full"]["macro_f1"] - max(permutation_scores)
            if permutation_scores
            else None
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--taxonomy-output", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--selected-actions",
        nargs="+",
        default=list(DEFAULT_SELECTED_ACTIONS),
        choices=list(DEFAULT_SELECTED_ACTIONS),
    )
    parser.add_argument("--min-mode-support", type=int, default=200)
    parser.add_argument("--prior-smoothing", type=float, default=1.0)
    parser.add_argument("--mode-lambda", type=float, default=0.15)
    parser.add_argument(
        "--mode-loss-normalization",
        choices=["none", "log_k", "prior_entropy"],
        default="prior_entropy",
    )
    parser.add_argument("--permutation-seeds", default="101,102,103,104,105")
    parser.add_argument("--head-seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--eval-batch-size", type=int, default=512)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.06)
    parser.add_argument("--grad-clip", type=float, default=1.0)
    parser.add_argument("--bucket-multiplier", type=int, default=50)
    parser.add_argument("--class-weight-power", type=float, default=0.5)
    parser.add_argument("--label-smoothing", type=float, default=0.02)
    parser.add_argument("--focal-gamma", type=float, default=2.0)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--skip-predictability-gate", action="store_true")
    parser.add_argument("--predictability-logistic-c", type=float, default=0.01)
    args = parser.parse_args()
    if args.mode_lambda <= 0:
        parser.error("--mode-lambda must be positive for the M/P arms")
    if args.predictability_logistic_c <= 0:
        parser.error("--predictability-logistic-c must be positive")
    permutation_seeds = [int(value) for value in args.permutation_seeds.split(",") if value]
    if len(permutation_seeds) != 5 or len(set(permutation_seeds)) != 5:
        parser.error("--permutation-seeds must contain exactly five distinct integers")
    if tuple(args.selected_actions) != DEFAULT_SELECTED_ACTIONS:
        parser.error(
            "first probe selected actions are frozen as read_file edit_file run_bash"
        )

    start = time.perf_counter()
    cache_path = Path(args.cache)
    cache = load_cache(cache_path)
    samples, labels, y = load_and_validate_data(args.data_dir, cache)
    reconstruction = reconstruct_full_events(samples, labels)
    if reconstruction.metadata["conflicting_rows"]:
        raise ValueError("full-event reconstruction contains conflicts")
    if reconstruction.metadata["name_agreement_rate"] != 1.0:
        raise ValueError("full-event reconstruction name agreement is not 100%")
    if list(reconstruction.statuses) != list(cache["event_recovery_statuses"]):
        raise ValueError("recomputed event recovery statuses do not match the cache")
    recovered_flags = torch.tensor(
        [status == "recovered" for status in reconstruction.statuses], dtype=torch.bool
    )
    if not torch.equal(recovered_flags, cache["event_recovered"].to(torch.bool)):
        raise ValueError("recomputed event recovery flags do not match the cache")
    candidate_mode_labels = [
        event_mode_signature(event) if event is not None else None
        for event in reconstruction.events
    ]
    if candidate_mode_labels != list(cache["candidate_mode_labels"]):
        raise ValueError("recomputed candidate mode labels do not match the cache")
    recomputed_turn_bins = [turn_bin(sample) for sample in samples]
    if recomputed_turn_bins != list(cache["turn_bins"]):
        raise ValueError("recomputed turn bins do not match the cache")
    recomputed_session_ids = [session_id(sample_id) for sample_id in cache["ids"]]
    if recomputed_session_ids != list(cache["session_ids"]):
        raise ValueError("recomputed session ids do not match the cache")
    train_indices = [int(value) for value in cache["train_indices"]]
    val_indices = [int(value) for value in cache["val_indices"]]
    taxonomy = fit_selective_taxonomy(
        samples,
        reconstruction,
        train_indices,
        labels,
        selected_actions=args.selected_actions,
        min_support=args.min_mode_support,
        prior_smoothing=args.prior_smoothing,
    )
    if tuple(taxonomy.active_actions) != DEFAULT_SELECTED_ACTIONS:
        raise ValueError(
            f"all three first-probe actions need K>=2, active={taxonomy.active_actions}"
        )
    targets = transform_mode_targets(samples, reconstruction, taxonomy, labels)
    mode_action_ids, mode_local_ids = build_mode_target_tensors(targets)
    predictability = evaluate_mode_predictability(
        cache["hidden"],
        cache["ids"],
        train_indices,
        targets.actions,
        targets.local_mode_ids,
        taxonomy.active_actions,
        taxonomy.modes_by_action,
        args.head_seed,
        args.predictability_logistic_c,
    )
    failed_predictability = [
        action for action, result in predictability.items() if not result["passed"]
    ]
    failed_read_modes = [
        mode
        for mode, result in predictability["read_file"]["per_mode"].items()
        if not result["passed"]
    ]

    taxonomy_payload = taxonomy.to_dict()
    taxonomy_payload["classes"] = ALL_CLASSES
    taxonomy_payload["predictability"] = predictability
    taxonomy_payload["taxonomy_sha256"] = canonical_hash(taxonomy_payload)
    taxonomy_output = Path(args.taxonomy_output)
    taxonomy_output.parent.mkdir(parents=True, exist_ok=True)
    taxonomy_output.write_text(
        json.dumps(taxonomy_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"taxonomy active={taxonomy.active_actions} "
        f"K={{{', '.join(f'{a}:{len(taxonomy.modes_by_action[a])}' for a in taxonomy.active_actions)}}}"
    )
    if (failed_predictability or failed_read_modes) and not args.skip_predictability_gate:
        raise ValueError(
            "fixed-train mode predictability failed: "
            f"actions={failed_predictability} read_file_modes={failed_read_modes}; "
            f"diagnostics saved to {taxonomy_output}"
        )

    priors_by_action = {
        CLASS_TO_ID[action]: taxonomy.priors_by_action[action]
        for action in taxonomy.active_actions
    }
    turn_bins = [turn_bin(sample) for sample in samples]
    terminal_flags = [status == "terminal" for status in reconstruction.statuses]
    val_y = y[val_indices]
    c0_logits = cache["parent_logits"][val_indices].float()
    identity_model = SelectiveModeResidual(
        hidden_size=cache["hidden"].shape[1],
        num_actions=len(ALL_CLASSES),
        priors_by_action=priors_by_action,
    ).to(args.device)
    identity_logits, identity_correction = predict_residual(
        identity_model,
        cache,
        val_indices,
        args.eval_batch_size,
        args.device,
    )
    identity_check = {
        "rows": len(val_indices),
        "max_abs_correction": float(identity_correction.abs().max()),
        "logits_exact": bool(torch.equal(identity_logits, c0_logits)),
        "argmax_agreement": float(
            (identity_logits.argmax(1) == c0_logits.argmax(1)).float().mean()
        ),
    }
    if identity_check["max_abs_correction"] != 0.0 or not identity_check["logits_exact"]:
        raise AssertionError(f"zero-init residual identity failed: {identity_check}")
    del identity_model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    c0_metrics = evaluate_slices(
        y,
        val_indices,
        c0_logits,
        reconstruction.statuses,
        terminal_flags,
        turn_bins,
        args.selected_actions,
    )
    arms = {
        "C0": {
            "description": "cached parent logits; no training",
            "metrics": c0_metrics,
        }
    }

    arm_targets = {"C1": mode_local_ids, "M": mode_local_ids}
    permutation_meta = {}
    for ordinal, seed in enumerate(permutation_seeds, 1):
        permuted, metadata = make_permuted_targets(
            mode_local_ids,
            mode_action_ids,
            train_indices,
            turn_bins,
            seed,
        )
        arm_targets[f"P{ordinal}"] = permuted
        permutation_meta[f"P{ordinal}"] = metadata

    for arm_name in ("C1", "M", "P1", "P2", "P3", "P4", "P5"):
        mode_weight = 0.0 if arm_name == "C1" else args.mode_lambda
        print(f"training arm={arm_name} mode_weight={mode_weight}")
        model, train_history = train_residual_arm(
            cache,
            y,
            priors_by_action,
            mode_action_ids,
            arm_targets[arm_name],
            args,
            mode_weight,
        )
        logits, corrections = predict_residual(
            model,
            cache,
            val_indices,
            args.eval_batch_size,
            args.device,
        )
        metrics = evaluate_slices(
            y,
            val_indices,
            logits,
            reconstruction.statuses,
            terminal_flags,
            turn_bins,
            args.selected_actions,
        )
        arms[arm_name] = {
            "description": (
                "capacity control; action focal only"
                if arm_name == "C1"
                else "actual args-only modes"
                if arm_name == "M"
                else "action-by-turn-bin permuted modes"
            ),
            "train_history": train_history,
            "metrics": metrics,
            "delta_vs_C0": delta_metrics(metrics, c0_metrics),
            "corrections": correction_diagnostics(
                corrections, val_y, args.selected_actions, model
            ),
            "mode_validation": mode_validation_metrics(
                model,
                cache,
                val_indices,
                mode_action_ids,
                mode_local_ids,
                taxonomy,
                args.device,
            ),
        }
        if arm_name != "C1":
            arms[arm_name]["delta_vs_C1"] = delta_metrics(
                metrics, arms["C1"]["metrics"]
            )
        if arm_name.startswith("P"):
            arms[arm_name]["permutation"] = permutation_meta[arm_name]
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    gates = evaluate_gates(arms, args.selected_actions)
    report = {
        "format": REPORT_FORMAT,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "command": shlex.join(sys.argv),
        "cache": str(cache_path.resolve()),
        "cache_sha256": sha256_file(cache_path),
        "taxonomy": str(taxonomy_output.resolve()),
        "taxonomy_sha256": taxonomy_payload["taxonomy_sha256"],
        "probe_contract": {
            "backbone": "frozen",
            "parent_head": "frozen cached logits",
            "trained_parameters": "zero-init selected mode residual only",
            "action_loss_rows": "all original fixed-train rows",
            "mode_loss_rows": "selected recovered stable-mode fixed-train rows",
            "kd": "none in frozen head probe",
            "replay": "none in frozen head cache; metadata support reserved for full screen",
            "mode_loss_position": "additive outside any KD interpolation",
        },
        "config": vars(args),
        "reconstruction": reconstruction.metadata,
        "taxonomy_summary": taxonomy_payload,
        "target_diagnostics": targets.metadata,
        "identity_check": identity_check,
        "arms": arms,
        "gates": gates,
        "runtime_seconds": time.perf_counter() - start,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"saved {output} passed={gates['passed']} "
        f"M-C0={gates['mode_macro_delta_vs_C0']:+.6f} "
        f"M-C1={gates['mode_macro_delta_vs_C1']:+.6f}"
    )


if __name__ == "__main__":
    main()
