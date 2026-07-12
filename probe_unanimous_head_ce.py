#!/usr/bin/env python3
"""Rejection-only frozen-head probe for the 5-model unanimous c=0 rows.

The proposed full-refit intervention removes only the hard-label classifier-head
loss on rows where the three canonical OOF voters and the Gemma/Llama train-logit
voters all choose the same non-official label.  Those rows already have zero
hard-label backbone gradient in the champion sieve, so masking their hard loss
is exactly a head-CE intervention; the KD coefficient is deliberately unchanged.

This probe is not a promotion-grade validation.  The canonical consensus payload
is full-refit-only and Gemma/Llama saw all 70k rows, so the exact selector overlaps
the fixed validation surface.  We use the leak-free HCX screen cache only as a
cheap harm/mechanism gate: a negative result rejects the card, while a positive
result merely justifies building a nested selector or spending a Public-only slot.
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
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from transformers import get_linear_schedule_with_warmup

from script import ALL_CLASSES
from train import append_results_csv, f1_metrics
from train_transformer import (
    classification_loss_values,
    class_weights,
    make_batches,
)


REPORT_FORMAT = "unanimous-head-ce-prevalidation-v1"
CACHE_FORMAT = "selective-privileged-mode-cache-v1"
WEAK4 = {"read_file", "grep_search", "list_directory", "glob_pattern"}
MID3 = {"ask_user", "plan_task", "lint_or_typecheck"}
EXPECTED_MASK_ROWS = 5298
EXPECTED_MASK_DIGEST = "0704709d9c55bc49d5ed0b21d28b9b2fdc8fc46989f073e07682e2de5ae4b5ff"


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


def stable_session_fold(session_id: str, n_folds: int = 5) -> int:
    digest = hashlib.sha256(session_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % n_folds


def load_pt(path: Path):
    return torch.load(path, map_location="cpu", weights_only=False)


def validate_common_pack(pack, path: Path, classes: list[str]) -> tuple[list[str], torch.Tensor]:
    pack_classes = list(pack.get("classes") or [])
    if pack_classes != classes:
        raise ValueError(f"class order mismatch in {path}: {pack_classes}")
    ids = [str(value) for value in pack.get("ids") or []]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate ids in {path}")
    logits = pack.get("logits")
    if not torch.is_tensor(logits) or tuple(logits.shape) != (len(ids), len(classes)):
        raise ValueError(f"invalid logits in {path}: {getattr(logits, 'shape', None)}")
    if not bool(torch.isfinite(logits.float()).all()):
        raise ValueError(f"non-finite logits in {path}")
    return ids, logits.float()


def align_logits(pack, path: Path, ids: list[str], classes: list[str]) -> torch.Tensor:
    pack_ids, logits = validate_common_pack(pack, path, classes)
    if len(pack_ids) != len(ids) or set(pack_ids) != set(ids):
        raise ValueError(f"id coverage mismatch in {path}")
    positions = {sample_id: i for i, sample_id in enumerate(pack_ids)}
    order = torch.tensor([positions[sample_id] for sample_id in ids], dtype=torch.long)
    return logits[order]


def load_oof_predictions(
    report_path: Path,
    root: Path,
    ids: list[str],
    classes: list[str],
) -> tuple[list[torch.Tensor], list[dict]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    id_to_pos = {sample_id: i for i, sample_id in enumerate(ids)}
    predictions = []
    provenance = []
    for source in report.get("sources") or []:
        pred = torch.full((len(ids),), -1, dtype=torch.long)
        seen = set()
        source_files = []
        for entry in source.get("files") or []:
            path = root / entry["path"]
            pack = load_pt(path)
            pack_ids, logits = validate_common_pack(pack, path, classes)
            if entry.get("sha256"):
                actual_sha = sha256_file(path)
                if actual_sha != entry["sha256"]:
                    raise ValueError(f"OOF file SHA mismatch: {path}")
            fold_pred = logits.argmax(dim=-1)
            for sample_id, value in zip(pack_ids, fold_pred.tolist()):
                if sample_id not in id_to_pos:
                    raise ValueError(f"unknown OOF id {sample_id} in {path}")
                if sample_id in seen:
                    raise ValueError(f"duplicate OOF coverage for {sample_id} in {source.get('name')}")
                seen.add(sample_id)
                pred[id_to_pos[sample_id]] = int(value)
            source_files.append(
                {
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "rows": len(pack_ids),
                }
            )
        if len(seen) != len(ids) or bool((pred < 0).any()):
            raise ValueError(f"incomplete OOF coverage for {source.get('name')}")
        predictions.append(pred)
        provenance.append({"name": source.get("name"), "files": source_files})
    if len(predictions) != 3:
        raise ValueError(f"expected three OOF sources, found {len(predictions)}")
    return predictions, provenance


def reconstruct_selector(args, cache, root: Path) -> dict:
    consensus_path = root / args.consensus
    consensus = load_pt(consensus_path)
    if consensus.get("usage_scope") != "full_data_refit_only":
        raise ValueError("canonical consensus payload must be marked full_data_refit_only")
    classes = list(consensus.get("classes") or [])
    if classes != ALL_CLASSES or classes != list(cache.get("classes") or []):
        raise ValueError("class order mismatch across cache/consensus/runtime")
    ids = [str(value) for value in consensus.get("ids") or []]
    if ids != [str(value) for value in cache.get("ids") or []]:
        raise ValueError("cache and consensus ids/order do not match")
    y = consensus["y_true"].to(torch.long)
    if not torch.equal(y, cache["y_true"].to(torch.long)):
        raise ValueError("cache and consensus labels do not match")
    correct_counts = consensus["correct_counts"].to(torch.long)
    oof_preds, oof_provenance = load_oof_predictions(
        root / args.consensus_report, root, ids, classes
    )
    recomputed = sum((pred == y).to(torch.long) for pred in oof_preds)
    if not torch.equal(recomputed, correct_counts):
        raise ValueError("recomputed OOF correctness counts do not match consensus payload")
    gemma_path = root / args.gemma_logits
    llama_path = root / args.llama_logits
    gemma = align_logits(load_pt(gemma_path), gemma_path, ids, classes).argmax(dim=-1)
    llama = align_logits(load_pt(llama_path), llama_path, ids, classes).argmax(dim=-1)
    m7, m8, v6 = oof_preds
    c0 = correct_counts == 0
    oof3_unanimous = c0 & (m7 == m8) & (m8 == v6)
    exact = oof3_unanimous & (gemma == m8) & (llama == m8)
    selected_ids = [sample_id for sample_id, keep in zip(ids, exact.tolist()) if keep]
    digest = id_digest(selected_ids)
    if int(exact.sum()) != EXPECTED_MASK_ROWS:
        raise ValueError(f"exact selector count drifted: {int(exact.sum())}")
    if digest != EXPECTED_MASK_DIGEST:
        raise ValueError(f"exact selector digest drifted: {digest}")
    if bool((m8[exact] == y[exact]).any()):
        raise ValueError("unanimous alternative selector contains an official-label match")
    return {
        "classes": classes,
        "ids": ids,
        "y": y,
        "correct_counts": correct_counts,
        "c0": c0,
        "oof3_unanimous": oof3_unanimous,
        "exact": exact,
        "alt": m8,
        "gemma": gemma,
        "llama": llama,
        "oof_preds": oof_preds,
        "provenance": {
            "consensus": {
                "path": str(consensus_path),
                "sha256": sha256_file(consensus_path),
                "usage_scope": consensus.get("usage_scope"),
            },
            "oof_sources": oof_provenance,
            "gemma": {"path": str(gemma_path), "sha256": sha256_file(gemma_path)},
            "llama": {"path": str(llama_path), "sha256": sha256_file(llama_path)},
        },
        "digest": digest,
    }


def token_quantile_bins(lengths: torch.Tensor, pool: torch.Tensor) -> tuple[torch.Tensor, list[float]]:
    values = lengths[pool].float()
    if not len(values):
        raise ValueError("empty pool for token-length bins")
    raw_edges = torch.quantile(values, torch.tensor([0.2, 0.4, 0.6, 0.8])).tolist()
    edges = sorted(set(float(round(value, 6)) for value in raw_edges))
    bins = torch.bucketize(lengths.float(), torch.tensor(edges, dtype=torch.float32), right=True)
    return bins, edges


def source_name(sample_id: str) -> str:
    if sample_id.startswith("sess_au"):
        return "au"
    if sample_id.startswith("sess_sim"):
        return "sim"
    return "other"


def make_strata(
    ids: list[str],
    y: torch.Tensor,
    alt: torch.Tensor,
    turn_bins: list[str],
    length_bins: torch.Tensor,
) -> list[tuple]:
    return [
        (
            int(y[i]),
            int(alt[i]),
            source_name(ids[i]),
            str(turn_bins[i]),
            int(length_bins[i]),
        )
        for i in range(len(ids))
    ]


def stratified_permutation_mask(
    actual: torch.Tensor,
    eligible: torch.Tensor,
    strata: list[tuple],
    seed: int,
) -> tuple[torch.Tensor, dict]:
    if actual.dtype != torch.bool or eligible.dtype != torch.bool:
        raise TypeError("actual/eligible masks must be bool tensors")
    if bool((actual & ~eligible).any()):
        raise ValueError("actual selector is not a subset of the permutation pool")
    by_stratum = defaultdict(list)
    selected_counts = Counter()
    for i, can_select in enumerate(eligible.tolist()):
        if can_select:
            by_stratum[strata[i]].append(i)
        if bool(actual[i]):
            selected_counts[strata[i]] += 1
    rng = random.Random(seed)
    permuted = torch.zeros_like(actual)
    saturated_rows = 0
    for key, count in selected_counts.items():
        pool = list(by_stratum[key])
        if count > len(pool):
            raise ValueError(f"stratum {key} needs {count} rows from pool {len(pool)}")
        rng.shuffle(pool)
        permuted[pool[:count]] = True
        if count == len(pool):
            saturated_rows += count
    if int(permuted.sum()) != int(actual.sum()):
        raise AssertionError("stratified permutation did not preserve selected row count")
    actual_by = Counter(strata[i] for i, value in enumerate(actual.tolist()) if value)
    perm_by = Counter(strata[i] for i, value in enumerate(permuted.tolist()) if value)
    if actual_by != perm_by:
        raise AssertionError("stratified permutation did not preserve stratum counts")
    return permuted, {
        "seed": seed,
        "rows": int(permuted.sum()),
        "overlap_with_exact": int((permuted & actual).sum()),
        "saturated_rows": saturated_rows,
        "strata": len(selected_counts),
    }


def build_arms(args, cache, selector: dict) -> tuple[list[str], torch.Tensor, dict]:
    n = len(selector["ids"])
    train_mask = torch.zeros(n, dtype=torch.bool)
    train_mask[cache["train_indices"].to(torch.long)] = True
    actual = selector["exact"] & train_mask
    primary_pool = selector["oof3_unanimous"] & train_mask
    secondary_pool = selector["c0"] & train_mask
    length_bins, length_edges = token_quantile_bins(cache["token_lengths"], primary_pool)
    strata = make_strata(
        selector["ids"],
        selector["y"],
        selector["alt"],
        list(cache["turn_bins"]),
        length_bins,
    )
    names = ["C1_full_head", "H5_exact_head_off"]
    masks = [torch.zeros(n, dtype=torch.bool), actual.clone()]
    meta = {
        "C1_full_head": {"family": "control", "rows": 0},
        "H5_exact_head_off": {
            "family": "exact",
            "rows": int(actual.sum()),
            "id_digest": id_digest(
                [selector["ids"][i] for i in torch.where(actual)[0].tolist()]
            ),
        },
        "token_length_edges": length_edges,
        "permutations": {},
    }
    for ordinal in range(args.permutations):
        seed = args.permutation_seed + ordinal
        mask, details = stratified_permutation_mask(actual, primary_pool, strata, seed)
        name = f"P3U_{seed}"
        names.append(name)
        masks.append(mask)
        meta["permutations"][name] = {"family": "oof3_unanimous", **details}
    for ordinal in range(args.permutations):
        seed = args.permutation_seed + 1000 + ordinal
        mask, details = stratified_permutation_mask(actual, secondary_pool, strata, seed)
        name = f"PC0_{seed}"
        names.append(name)
        masks.append(mask)
        meta["permutations"][name] = {"family": "all_c0", **details}
    return names, torch.stack(masks), meta


def mixed_loss_values(
    logits: torch.Tensor,
    labels: torch.Tensor,
    loss_weights: torch.Tensor,
    teacher_p: torch.Tensor,
    alpha: torch.Tensor,
    hard_keep: torch.Tensor,
    label_smoothing: float,
    focal_gamma: float,
    temperature: float,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    if logits.ndim != 3:
        raise ValueError("logits must have shape [arms,batch,classes]")
    arms, batch, classes = logits.shape
    flat_logits = logits.reshape(arms * batch, classes)
    flat_labels = labels.view(1, batch).expand(arms, batch).reshape(-1)
    hard = classification_loss_values(
        flat_logits,
        flat_labels,
        loss_weights,
        label_smoothing,
        "focal",
        focal_gamma,
    ).view(arms, batch)
    student_lp = F.log_softmax(logits.float() / temperature, dim=-1)
    target = teacher_p.view(1, batch, classes).expand(arms, batch, classes)
    kd = F.kl_div(student_lp, target, reduction="none").sum(-1) * (temperature ** 2)
    alpha_rows = alpha.view(1, batch)
    values = hard_keep * (1.0 - alpha_rows) * hard + alpha_rows * kd
    return values, hard, kd


def alpha_rows(y: torch.Tensor, classes: list[str], alpha_rest: float, alpha_weak: float) -> torch.Tensor:
    weak_ids = {classes.index(label) for label in WEAK4}
    return torch.tensor(
        [alpha_weak if int(label) in weak_ids else alpha_rest for label in y.tolist()],
        dtype=torch.float32,
    )


def train_frozen_head_arms(
    args,
    cache,
    y: torch.Tensor,
    teacher_p: torch.Tensor,
    arm_names: list[str],
    off_masks: torch.Tensor,
    order_seed: int,
    device: torch.device,
) -> tuple[torch.Tensor, dict]:
    train_indices = cache["train_indices"].to(torch.long).tolist()
    hidden_dim = int(cache["hidden"].shape[1])
    delta = torch.nn.Parameter(
        torch.zeros(len(arm_names), len(ALL_CLASSES), hidden_dim, device=device)
    )
    optimizer = torch.optim.AdamW([delta], lr=args.lr, weight_decay=0.0)
    steps_per_epoch = math.ceil(len(train_indices) / args.batch_size)
    total_steps = steps_per_epoch * args.epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(total_steps * args.warmup_ratio),
        num_training_steps=total_steps,
    )
    weights = class_weights(
        [int(y[i]) for i in train_indices], device, args.class_weight_power
    )
    alpha = alpha_rows(y, ALL_CLASSES, args.alpha_rest, args.alpha_weak)
    histories = []
    rng = random.Random(order_seed)
    start = time.perf_counter()
    for epoch in range(args.epochs):
        loss_sums = torch.zeros(len(arm_names), dtype=torch.float64)
        seen = 0
        for batch_idx in make_batches(
            train_indices,
            args.batch_size,
            rng=rng,
            lengths=cache["token_lengths"].tolist(),
            bucket_multiplier=args.bucket_multiplier,
        ):
            idx = torch.tensor(batch_idx, dtype=torch.long)
            hidden = cache["hidden"][idx].to(device=device, dtype=torch.float32)
            base_logits = cache["parent_logits"][idx].to(device=device, dtype=torch.float32)
            corrections = torch.einsum("bd,acd->abc", hidden, delta)
            logits = base_logits.unsqueeze(0) + corrections
            labels = y[idx].to(device)
            keep = (~off_masks[:, idx]).to(device=device, dtype=torch.float32)
            values, _, _ = mixed_loss_values(
                logits,
                labels,
                weights,
                teacher_p[idx].to(device),
                alpha[idx].to(device),
                keep,
                args.label_smoothing,
                args.focal_gamma,
                args.temperature,
            )
            loss_by_arm = values.mean(dim=1)
            optimizer.zero_grad(set_to_none=True)
            loss_by_arm.sum().backward()
            optimizer.step()
            scheduler.step()
            loss_sums += loss_by_arm.detach().cpu().to(torch.float64) * len(batch_idx)
            seen += len(batch_idx)
        histories.append(
            {
                "epoch": epoch + 1,
                "loss_by_arm": {
                    name: float(loss_sums[i] / max(1, seen))
                    for i, name in enumerate(arm_names)
                },
            }
        )
    return delta.detach().cpu(), {
        "order_seed": order_seed,
        "runtime_seconds": time.perf_counter() - start,
        "history": histories,
        "delta_norms": {
            name: float(delta[i].detach().norm().cpu())
            for i, name in enumerate(arm_names)
        },
    }


def predict_arms(args, cache, delta: torch.Tensor, device: torch.device) -> torch.Tensor:
    val_indices = cache["val_indices"].to(torch.long).tolist()
    outputs = []
    delta_device = delta.to(device=device, dtype=torch.float32)
    with torch.inference_mode():
        for start in range(0, len(val_indices), args.eval_batch_size):
            batch_idx = val_indices[start : start + args.eval_batch_size]
            idx = torch.tensor(batch_idx, dtype=torch.long)
            hidden = cache["hidden"][idx].to(device=device, dtype=torch.float32)
            base_logits = cache["parent_logits"][idx].to(device=device, dtype=torch.float32)
            corrections = torch.einsum("bd,acd->abc", hidden, delta_device)
            outputs.append((base_logits.unsqueeze(0) + corrections).cpu())
    return torch.cat(outputs, dim=1)


def slice_metrics(
    logits: torch.Tensor,
    y: torch.Tensor,
    indices: torch.Tensor,
    exact_mask: torch.Tensor,
    alt: torch.Tensor,
    bias: torch.Tensor | None = None,
) -> dict:
    if bias is not None:
        logits = logits + bias.view(1, -1)
    pred = logits.argmax(dim=-1)
    y_list = y[indices].tolist()
    pred_list = pred.tolist()
    metrics = f1_metrics(y_list, pred_list)
    selected = exact_mask[indices]
    selected_y = y[indices][selected]
    selected_pred = pred[selected]
    selected_alt = alt[indices][selected]
    probs = F.softmax(logits.float(), dim=-1)
    rows = torch.arange(len(indices))
    official_prob = probs[rows, y[indices]]
    alt_prob = probs[rows, alt[indices]]
    metrics["weak4_macro"] = float(
        np.mean([metrics["per_class_f1"][label] for label in WEAK4])
    )
    metrics["mid3_f1_sum"] = float(
        sum(metrics["per_class_f1"][label] for label in MID3)
    )
    metrics["exact_val_slice"] = {
        "rows": int(selected.sum()),
        "official_accuracy": float((selected_pred == selected_y).float().mean()),
        "unanimous_alt_rate": float((selected_pred == selected_alt).float().mean()),
        "mean_official_probability": float(official_prob[selected].mean()),
        "mean_unanimous_alt_probability": float(alt_prob[selected].mean()),
    }
    return metrics


def compare_predictions(control_logits, candidate_logits, y, indices) -> dict:
    control = control_logits.argmax(dim=-1)
    candidate = candidate_logits.argmax(dim=-1)
    truth = y[indices]
    return {
        "rescues": int(((control != truth) & (candidate == truth)).sum()),
        "harms": int(((control == truth) & (candidate != truth)).sum()),
        "changed": int((control != candidate).sum()),
        "unchanged": int((control == candidate).sum()),
    }


def evaluate_seed(
    cache,
    selector,
    arm_names,
    logits_by_arm,
    locked_bias,
) -> dict:
    val_indices = cache["val_indices"].to(torch.long)
    y = selector["y"]
    arms = {}
    for i, name in enumerate(arm_names):
        raw = slice_metrics(
            logits_by_arm[i], y, val_indices, selector["exact"], selector["alt"]
        )
        biased = slice_metrics(
            logits_by_arm[i],
            y,
            val_indices,
            selector["exact"],
            selector["alt"],
            bias=locked_bias,
        )
        arms[name] = {"raw": raw, "locked_bias": biased}
    control_logits = logits_by_arm[0]
    control_raw = arms[arm_names[0]]["raw"]
    for i, name in enumerate(arm_names[1:], 1):
        raw = arms[name]["raw"]
        arms[name]["delta_vs_control"] = {
            "macro_f1": float(raw["macro_f1"] - control_raw["macro_f1"]),
            "weak4_macro": float(raw["weak4_macro"] - control_raw["weak4_macro"]),
            "mid3_f1_sum": float(raw["mid3_f1_sum"] - control_raw["mid3_f1_sum"]),
            "per_class_f1": {
                label: float(raw["per_class_f1"][label] - control_raw["per_class_f1"][label])
                for label in ALL_CLASSES
            },
        }
        arms[name]["rescue_harm_vs_control"] = compare_predictions(
            control_logits, logits_by_arm[i], y, val_indices
        )
    return arms


def cosine(left: torch.Tensor, right: torch.Tensor) -> float:
    denom = left.norm() * right.norm()
    if float(denom) == 0.0:
        return 0.0
    return float(torch.dot(left.flatten(), right.flatten()) / denom)


def gradient_audit(args, cache, selector, arm_names, off_masks, teacher_p, device) -> dict:
    train_indices = cache["train_indices"].to(torch.long).tolist()
    val_indices = cache["val_indices"].to(torch.long).tolist()
    y = selector["y"]
    weights = class_weights(
        [int(y[i]) for i in train_indices], device, args.class_weight_power
    )
    alpha = alpha_rows(y, ALL_CLASSES, args.alpha_rest, args.alpha_weak)
    selected_gradients = torch.zeros(
        len(arm_names), len(ALL_CLASSES), cache["hidden"].shape[1], dtype=torch.float64
    )
    kd_gradient = torch.zeros(len(ALL_CLASSES), cache["hidden"].shape[1], dtype=torch.float64)
    for start in range(0, len(train_indices), args.gradient_batch_size):
        batch_idx = train_indices[start : start + args.gradient_batch_size]
        idx = torch.tensor(batch_idx, dtype=torch.long)
        hidden = cache["hidden"][idx].to(device=device, dtype=torch.float32)
        logits = cache["parent_logits"][idx].to(device=device, dtype=torch.float32).requires_grad_(True)
        labels = y[idx].to(device)
        hard = classification_loss_values(
            logits,
            labels,
            weights,
            args.label_smoothing,
            "focal",
            args.focal_gamma,
        )
        hard_weighted = hard * (1.0 - alpha[idx].to(device))
        grad_logits = torch.autograd.grad(hard_weighted.sum(), logits)[0]
        mask = off_masks[:, idx].to(device=device, dtype=torch.float32)
        contribution = torch.einsum("ab,bc,bd->acd", mask, grad_logits, hidden)
        selected_gradients += contribution.detach().cpu().to(torch.float64)

        logits_kd = cache["parent_logits"][idx].to(device=device, dtype=torch.float32).requires_grad_(True)
        student_lp = F.log_softmax(logits_kd / args.temperature, dim=-1)
        kd = F.kl_div(
            student_lp,
            teacher_p[idx].to(device),
            reduction="none",
        ).sum(-1) * (args.temperature ** 2)
        kd = kd * alpha[idx].to(device)
        kd_grad_logits = torch.autograd.grad(kd.sum(), logits_kd)[0]
        actual = off_masks[1, idx].to(device=device, dtype=torch.float32)
        kd_contribution = torch.einsum("b,bc,bd->cd", actual, kd_grad_logits, hidden)
        kd_gradient += kd_contribution.detach().cpu().to(torch.float64)

    selected_gradients /= max(1, len(train_indices))
    kd_gradient /= max(1, len(train_indices))

    val_weights = class_weights([int(y[i]) for i in val_indices], device, 1.0)
    val_gradient = torch.zeros(len(ALL_CLASSES), cache["hidden"].shape[1], dtype=torch.float64)
    fold_gradients = torch.zeros(5, len(ALL_CLASSES), cache["hidden"].shape[1], dtype=torch.float64)
    fold_counts = torch.zeros(5, dtype=torch.long)
    session_folds = torch.tensor(
        [stable_session_fold(str(cache["session_ids"][i]), 5) for i in val_indices],
        dtype=torch.long,
    )
    for start in range(0, len(val_indices), args.gradient_batch_size):
        batch_idx = val_indices[start : start + args.gradient_batch_size]
        idx = torch.tensor(batch_idx, dtype=torch.long)
        hidden = cache["hidden"][idx].to(device=device, dtype=torch.float32)
        logits = cache["parent_logits"][idx].to(device=device, dtype=torch.float32).requires_grad_(True)
        labels = y[idx].to(device)
        loss = F.cross_entropy(logits, labels, weight=val_weights, reduction="sum")
        grad_logits = torch.autograd.grad(loss, logits)[0]
        val_gradient += torch.einsum("bc,bd->cd", grad_logits, hidden).detach().cpu().to(torch.float64)
        local_folds = session_folds[start : start + len(batch_idx)]
        for fold in range(5):
            fold_mask = local_folds == fold
            if bool(fold_mask.any()):
                fold_gradients[fold] += torch.einsum(
                    "bc,bd->cd",
                    grad_logits[fold_mask.to(device)],
                    hidden[fold_mask.to(device)],
                ).detach().cpu().to(torch.float64)
                fold_counts[fold] += int(fold_mask.sum())
    val_gradient /= max(1, len(val_indices))
    for fold in range(5):
        fold_gradients[fold] /= max(1, int(fold_counts[fold]))

    actual = selected_gradients[1]
    hard_kd_cosine = cosine(actual, kd_gradient)
    entries = {}
    for i, name in enumerate(arm_names[1:], 1):
        grad = selected_gradients[i]
        entries[name] = {
            "hard_gradient_norm": float(grad.norm()),
            "cosine_with_balanced_val_gradient": cosine(grad, val_gradient),
            "dot_with_balanced_val_gradient": float((grad * val_gradient).sum()),
        }
        if i == 1:
            entries[name]["cosine_by_val_session_fold"] = [
                cosine(grad, fold_gradients[fold]) for fold in range(5)
            ]
    primary = [entries[name]["cosine_with_balanced_val_gradient"] for name in arm_names if name.startswith("P3U_")]
    secondary = [entries[name]["cosine_with_balanced_val_gradient"] for name in arm_names if name.startswith("PC0_")]
    actual_cos = entries["H5_exact_head_off"]["cosine_with_balanced_val_gradient"]
    return {
        "interpretation": (
            "candidate-minus-control update is +eta*g_hard; a negative hard/val dot supports "
            "removal, while a positive dot says the official-label head gradient is useful"
        ),
        "balanced_val_gradient_norm": float(val_gradient.norm()),
        "exact_hard_gradient_norm": float(actual.norm()),
        "exact_kd_gradient_norm": float(kd_gradient.norm()),
        "exact_hard_vs_kd_cosine": hard_kd_cosine,
        "exact_cosine_percentile_among_primary": float(
            np.mean(np.asarray(primary) <= actual_cos) if primary else 0.0
        ),
        "exact_cosine_percentile_among_secondary": float(
            np.mean(np.asarray(secondary) <= actual_cos) if secondary else 0.0
        ),
        "arms": entries,
    }


def aggregate_results(seed_reports: dict, arm_names: list[str], args) -> dict:
    exact_deltas = [
        seed_reports[str(seed)]["arms"]["H5_exact_head_off"]["delta_vs_control"]["macro_f1"]
        for seed in args.order_seeds
    ]
    exact_weak = [
        seed_reports[str(seed)]["arms"]["H5_exact_head_off"]["delta_vs_control"]["weak4_macro"]
        for seed in args.order_seeds
    ]
    exact_mid = [
        seed_reports[str(seed)]["arms"]["H5_exact_head_off"]["delta_vs_control"]["mid3_f1_sum"]
        for seed in args.order_seeds
    ]
    mean_delta_by_arm = {}
    for name in arm_names[1:]:
        mean_delta_by_arm[name] = float(
            np.mean(
                [
                    seed_reports[str(seed)]["arms"][name]["delta_vs_control"]["macro_f1"]
                    for seed in args.order_seeds
                ]
            )
        )
    exact_mean = float(np.mean(exact_deltas))
    primary = [value for name, value in mean_delta_by_arm.items() if name.startswith("P3U_")]
    secondary = [value for name, value in mean_delta_by_arm.items() if name.startswith("PC0_")]

    def family_stats(values):
        arr = np.asarray(values, dtype=np.float64)
        return {
            "mean": float(arr.mean()),
            "std": float(arr.std()),
            "q95": float(np.quantile(arr, 0.95)),
            "max": float(arr.max()),
            "exact_minus_max": float(exact_mean - arr.max()),
            "empirical_p_ge_exact": float((1 + int((arr >= exact_mean).sum())) / (1 + len(arr))),
        }

    primary_stats = family_stats(primary)
    secondary_stats = family_stats(secondary)
    reasons = []
    if not all(delta > 0 for delta in exact_deltas):
        reasons.append("exact candidate is not positive in every data-order seed")
    if exact_mean < args.gate_macro_delta:
        reasons.append(f"mean macro delta {exact_mean:+.6f} < {args.gate_macro_delta:+.6f}")
    if primary_stats["exact_minus_max"] < args.gate_permutation_margin:
        reasons.append("exact candidate does not clear all OOF3-unanimous permutations")
    if secondary_stats["exact_minus_max"] < args.gate_permutation_margin:
        reasons.append("exact candidate does not clear all c0 permutations")
    if float(np.mean(exact_weak)) < 0:
        reasons.append("Weak4 macro delta is negative")
    if float(np.mean(exact_mid)) < 0:
        reasons.append("mid3 F1-sum delta is negative")
    return {
        "passed_rejection_gate": not reasons,
        "scope": "negative can reject; positive cannot promote because selector is full-data-only",
        "reject_reasons": reasons,
        "exact_macro_delta_by_order_seed": dict(zip(map(str, args.order_seeds), exact_deltas)),
        "exact_macro_delta_mean": exact_mean,
        "exact_macro_delta_std": float(np.std(exact_deltas)),
        "exact_weak4_macro_delta_mean": float(np.mean(exact_weak)),
        "exact_mid3_f1_sum_delta_mean": float(np.mean(exact_mid)),
        "mean_macro_delta_by_arm": mean_delta_by_arm,
        "primary_permutation_family": primary_stats,
        "secondary_permutation_family": secondary_stats,
        "thresholds": {
            "all_order_seeds_positive": True,
            "macro_delta_mean": args.gate_macro_delta,
            "exact_minus_every_permutation": args.gate_permutation_margin,
            "weak4_macro_delta_nonnegative": True,
            "mid3_f1_sum_delta_nonnegative": True,
        },
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="experiments/cache/kd_hcx_m8_screen_cache.pt")
    parser.add_argument("--consensus", default="experiments/artifacts/20260710_m7_m8_v6_oof_consensus.pt")
    parser.add_argument("--consensus-report", default="experiments/artifacts/20260710_m7_m8_v6_oof_consensus.json")
    parser.add_argument("--gemma-logits", default="teacher_archive_v21_20260710/teacher_gemma_bf16_fixpool_ep3_train70k_fp16.pt")
    parser.add_argument("--llama-logits", default="teacher_archive_v21_20260710/teacher_llama_train70k_fp16.pt")
    parser.add_argument("--teacher-logits", default="experiments/logits/m8_qwen35_refit_train70k_fp16.pt")
    parser.add_argument("--checkpoint-meta", default="experiments/incoming/models/kd_hcx_m8_screen/hf_meta.json")
    parser.add_argument("--output", default="experiments/artifacts/20260712_unanimous5298_headce_prevalidation.json")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--eval-batch-size", type=int, default=512)
    parser.add_argument("--gradient-batch-size", type=int, default=512)
    parser.add_argument("--bucket-multiplier", type=int, default=8)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--warmup-ratio", type=float, default=0.06)
    parser.add_argument("--class-weight-power", type=float, default=0.5)
    parser.add_argument("--label-smoothing", type=float, default=0.02)
    parser.add_argument("--focal-gamma", type=float, default=2.0)
    parser.add_argument("--temperature", type=float, default=3.0)
    parser.add_argument("--alpha-rest", type=float, default=0.5)
    parser.add_argument("--alpha-weak", type=float, default=0.7)
    parser.add_argument("--permutations", type=int, default=20)
    parser.add_argument("--permutation-seed", type=int, default=101)
    parser.add_argument("--order-seeds", type=int, nargs="+", default=[42, 777, 2026])
    parser.add_argument("--gate-macro-delta", type=float, default=0.002)
    parser.add_argument("--gate-permutation-margin", type=float, default=0.001)
    parser.add_argument("--append-results", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    root = Path(__file__).resolve().parent
    start = time.perf_counter()
    cache_path = root / args.cache
    cache = load_pt(cache_path)
    if cache.get("format") != CACHE_FORMAT:
        raise ValueError(f"unexpected cache format: {cache.get('format')}")
    if cache.get("split") != "session" or int(cache.get("seed", -1)) != 42:
        raise ValueError("probe cache must use canonical session split seed42")
    if set(cache["train_indices"].tolist()) & set(cache["val_indices"].tolist()):
        raise ValueError("cache train/val overlap")
    selector = reconstruct_selector(args, cache, root)
    y = selector["y"]
    arm_names, off_masks, arm_meta = build_arms(args, cache, selector)

    teacher_path = root / args.teacher_logits
    teacher_logits = align_logits(
        load_pt(teacher_path), teacher_path, selector["ids"], selector["classes"]
    )
    teacher_p = F.softmax(teacher_logits / args.temperature, dim=-1)
    exact = selector["exact"]
    teacher_pred = teacher_logits.argmax(dim=-1)
    cache_pred = cache["parent_logits"].argmax(dim=-1)
    selector_diagnostics = {
        "rows": int(exact.sum()),
        "train_rows": int(exact[cache["train_indices"].to(torch.long)].sum()),
        "val_rows": int(exact[cache["val_indices"].to(torch.long)].sum()),
        "id_digest": selector["digest"],
        "c0_rows": int(selector["c0"].sum()),
        "oof3_unanimous_rows": int(selector["oof3_unanimous"].sum()),
        "weak4_rows": int(sum(selector["classes"][int(v)] in WEAK4 for v in y[exact])),
        "official_class_counts": dict(
            Counter(selector["classes"][int(v)] for v in y[exact]).most_common()
        ),
        "alternative_class_counts": dict(
            Counter(selector["classes"][int(v)] for v in selector["alt"][exact]).most_common()
        ),
        "parent_official_accuracy": float((cache_pred[exact] == y[exact]).float().mean()),
        "parent_unanimous_alt_rate": float((cache_pred[exact] == selector["alt"][exact]).float().mean()),
        "teacher_official_accuracy": float((teacher_pred[exact] == y[exact]).float().mean()),
        "teacher_unanimous_alt_rate": float((teacher_pred[exact] == selector["alt"][exact]).float().mean()),
        "source_counts": dict(Counter(source_name(sid) for sid, keep in zip(selector["ids"], exact.tolist()) if keep)),
    }

    meta_path = root / args.checkpoint_meta
    checkpoint_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    locked_bias = torch.tensor(checkpoint_meta["class_bias"], dtype=torch.float32)
    if len(locked_bias) != len(ALL_CLASSES):
        raise ValueError("locked checkpoint bias length mismatch")

    device = torch.device(
        "cuda" if args.device == "auto" and torch.cuda.is_available() else
        "cpu" if args.device == "auto" else args.device
    )
    seed_reports = {}
    for seed in args.order_seeds:
        print(f"training frozen-head arms order_seed={seed} arms={len(arm_names)} device={device}")
        delta, train_meta = train_frozen_head_arms(
            args,
            cache,
            y,
            teacher_p,
            arm_names,
            off_masks,
            seed,
            device,
        )
        logits = predict_arms(args, cache, delta, device)
        arms = evaluate_seed(cache, selector, arm_names, logits, locked_bias)
        seed_reports[str(seed)] = {"train": train_meta, "arms": arms}
        print(
            f"  seed={seed} control={arms['C1_full_head']['raw']['macro_f1']:.6f} "
            f"exact={arms['H5_exact_head_off']['raw']['macro_f1']:.6f} "
            f"delta={arms['H5_exact_head_off']['delta_vs_control']['macro_f1']:+.6f}"
        )
        if device.type == "cuda":
            torch.cuda.empty_cache()

    gates = aggregate_results(seed_reports, arm_names, args)
    gradients = gradient_audit(
        args, cache, selector, arm_names, off_masks, teacher_p, device
    )
    runtime = time.perf_counter() - start
    report = {
        "format": REPORT_FORMAT,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "command": shlex.join(sys.argv),
        "probe_contract": {
            "purpose": "rejection-only harm/mechanism gate for exact 5-model unanimous head-CE removal",
            "backbone": "frozen cached HCX kd_hcx_m8_screen hidden states",
            "trained_parameters": "zero-init 14x1024 additive classifier-head delta per arm",
            "control_loss": "(1-alpha)*focal-hard + alpha*KD",
            "candidate_loss": "exact-mask rows: 0*hard + unchanged alpha*KD; all other rows control loss",
            "alpha": {"weak4": args.alpha_weak, "rest": args.alpha_rest},
            "replay": "not present in cache; omitted identically from every paired arm",
            "selector_scope": "full_data_refit_only; fixed validation is selector-leaky",
            "interpretation": "negative rejects; positive cannot promote",
        },
        "config": vars(args),
        "cache": {
            "path": str(cache_path),
            "sha256": sha256_file(cache_path),
            "checkpoint_sha256": cache.get("checkpoint_sha256"),
            "train_rows": len(cache["train_indices"]),
            "val_rows": len(cache["val_indices"]),
        },
        "selector_provenance": selector["provenance"],
        "teacher": {"path": str(teacher_path), "sha256": sha256_file(teacher_path)},
        "selector_diagnostics": selector_diagnostics,
        "arms": {"names": arm_names, "metadata": arm_meta},
        "order_seed_reports": seed_reports,
        "gradient_audit": gradients,
        "gates": gates,
        "decision": "retain_for_public_only" if gates["passed_rejection_gate"] else "reject",
        "runtime_seconds": runtime,
    }
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"saved {output} decision={report['decision']} "
        f"mean_delta={gates['exact_macro_delta_mean']:+.6f} "
        f"primary_margin={gates['primary_permutation_family']['exact_minus_max']:+.6f} "
        f"runtime={runtime:.1f}s"
    )

    if args.append_results:
        experiment_id = "20260712_unanimous5298_headce_preval"
        append_results_csv(
            root / "experiments/results.csv",
            {
                "experiment_id": experiment_id,
                "model_family": "frozen_head_prevalidation",
                "base_model": "naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B",
                "features": "cached HCX hidden + exact 5-model unanimous c0 selector",
                "serializer_name": "current_v1",
                "split_type": "session_fixed_selector_leaky_rejection_only",
                "seed": "/".join(map(str, args.order_seeds)),
                "max_length": 384,
                "epochs": args.epochs,
                "learning_rate": args.lr,
                "batch_size": args.batch_size,
                "class_weight_power": args.class_weight_power,
                "label_smoothing": args.label_smoothing,
                "replay_mode": "none_cached_proxy",
                "replay_size": 0,
                "macro_f1_raw": float(
                    np.mean(
                        [
                            seed_reports[str(seed)]["arms"]["H5_exact_head_off"]["raw"]["macro_f1"]
                            for seed in args.order_seeds
                        ]
                    )
                ),
                "macro_f1": float(
                    np.mean(
                        [
                            seed_reports[str(seed)]["arms"]["H5_exact_head_off"]["raw"]["macro_f1"]
                            for seed in args.order_seeds
                        ]
                    )
                ),
                "artifact_path": args.output,
                "runtime_sec": runtime,
                "train_command": shlex.join(sys.argv),
                "notes": (
                    f"rejection-only frozen-head exact-mask delta={gates['exact_macro_delta_mean']:+.6f}; "
                    f"primary_perm_margin={gates['primary_permutation_family']['exact_minus_max']:+.6f}; "
                    "positive result cannot promote because selector is full-data-only"
                ),
                "decision": report["decision"],
            },
        )


if __name__ == "__main__":
    main()
