#!/usr/bin/env python3
"""Build the clean fixed-parent surface for the live-pair residual expert."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import torch

from script import ALL_CLASSES, load_jsonl, safe_text
from train import CLASS_TO_ID, load_labels, split_indices
from weak4_pair_residual import (
    CLEAN_USAGE_SCOPE,
    CLEAN_VALIDATION_SCOPE,
    DATASET_KIND,
    DIAGNOSTIC_USAGE_SCOPE,
    FEATURE_SCHEMA,
    LIVE_WEAK4_PAIRS,
    SCHEMA_VERSION,
    assign_session_folds,
    build_pair_targets,
    main_numeric_features,
    select_live_pair_routes,
    target_histogram,
)


DEFAULT_PARENT = (
    "experiments/logits/"
    "20260707_hcx05b_len384_screen_seed42_val_logits.pt"
)
DEFAULT_CONSENSUS = ""


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


def _validate_parent(parent: dict) -> tuple[list[str], torch.Tensor, torch.Tensor]:
    if list(parent.get("classes") or []) != ALL_CLASSES:
        raise ValueError("parent class order does not match canonical ALL_CLASSES")
    ids = [safe_text(value) for value in parent.get("ids") or []]
    logits = torch.as_tensor(parent.get("logits"), dtype=torch.float32)
    y_true = torch.as_tensor(parent.get("y_true"), dtype=torch.long).view(-1)
    if logits.ndim != 2 or logits.shape[1] != len(ALL_CLASSES):
        raise ValueError(f"parent logits have invalid shape: {tuple(logits.shape)}")
    if len(ids) != len(set(ids)):
        raise ValueError("parent payload contains duplicate ids")
    if not (len(ids) == len(logits) == len(y_true)):
        raise ValueError("parent ids/logits/y_true lengths differ")
    if not torch.isfinite(logits).all():
        raise ValueError("parent logits contain non-finite values")
    return ids, logits, y_true


def _consensus_counts(consensus: dict, ids: list[str], y_true: torch.Tensor) -> torch.Tensor:
    if list(consensus.get("classes") or []) != ALL_CLASSES:
        raise ValueError("consensus class order does not match ALL_CLASSES")
    consensus_ids = [safe_text(value) for value in consensus.get("ids") or []]
    counts = torch.as_tensor(consensus.get("correct_counts"), dtype=torch.long).view(-1)
    labels = torch.as_tensor(consensus.get("y_true"), dtype=torch.long).view(-1)
    if not (len(consensus_ids) == len(counts) == len(labels)):
        raise ValueError("consensus ids/counts/y_true lengths differ")
    if len(consensus_ids) != len(set(consensus_ids)):
        raise ValueError("consensus payload contains duplicate ids")
    row_by_id = {sample_id: idx for idx, sample_id in enumerate(consensus_ids)}
    missing = [sample_id for sample_id in ids if sample_id not in row_by_id]
    if missing:
        raise ValueError(f"parent ids are missing from consensus: {missing[:5]}")
    selected = torch.tensor([row_by_id[sample_id] for sample_id in ids], dtype=torch.long)
    if not torch.equal(labels[selected], y_true):
        raise ValueError("consensus labels differ from parent labels")
    return counts[selected]


def build_dataset_payload(
    samples: list[dict],
    labels_by_id: dict[str, str],
    parent: dict,
    consensus: dict | None,
    *,
    n_folds: int = 3,
    fold_seed: int = 42,
    require_clean_fixed: bool = True,
    allow_crossfold_consensus: bool = False,
) -> dict:
    ids, logits, y_true = _validate_parent(parent)
    sample_by_id = {safe_text(sample.get("id")): (idx, sample) for idx, sample in enumerate(samples)}
    if len(sample_by_id) != len(samples):
        raise ValueError("training samples contain duplicate ids")
    missing = [sample_id for sample_id in ids if sample_id not in sample_by_id]
    if missing:
        raise ValueError(f"parent ids are missing from training data: {missing[:5]}")
    expected_y = []
    for sample_id in ids:
        label = labels_by_id.get(sample_id)
        if label not in CLASS_TO_ID:
            raise ValueError(f"missing or invalid label for {sample_id}: {label!r}")
        expected_y.append(CLASS_TO_ID[label])
    if expected_y != y_true.tolist():
        raise ValueError("parent labels do not match train_labels.csv")

    if consensus is not None and require_clean_fixed and not allow_crossfold_consensus:
        raise ValueError(
            "full-data OOF consensus crosses outer specialist folds; "
            "omit --consensus for clean fixed-parent CV or explicitly allow a diagnostic build"
        )

    parent_split = parent.get("split")
    parent_seed = int(parent.get("seed", -1))
    if require_clean_fixed:
        if parent_split != "session" or parent_seed != 42:
            raise ValueError(
                f"clean parent must be split=session seed=42, got {parent_split!r}/{parent_seed}"
            )
        all_y = [CLASS_TO_ID[labels_by_id[safe_text(sample.get("id"))]] for sample in samples]
        _, fixed_val = split_indices(samples, all_y, "session", parent_seed)
        fixed_ids = {safe_text(samples[idx].get("id")) for idx in fixed_val}
        if set(ids) != fixed_ids:
            raise ValueError(
                "parent ids are not the untouched fixed-session validation surface: "
                f"parent={len(ids)} expected={len(fixed_ids)}"
            )

    if consensus is not None:
        correct_counts = _consensus_counts(consensus, ids, y_true)
        reliability_source = "explicit_consensus_diagnostic"
    else:
        # The fixed parent itself never trained on these 14,001 rows. Treat all
        # in-pair alternatives as eligible RESCUE targets; importing full-data
        # OOF correctness would reintroduce an outer-fold path through its fits.
        correct_counts = torch.ones(len(ids), dtype=torch.long)
        reliability_source = "none"
    if require_clean_fixed and consensus is None:
        usage_scope = CLEAN_USAGE_SCOPE
        validation_scope = CLEAN_VALIDATION_SCOPE
    else:
        reasons = []
        if not require_clean_fixed:
            reasons.append("nonfixed_parent")
        if consensus is not None:
            reasons.append("crossfold_consensus")
        usage_scope = DIAGNOSTIC_USAGE_SCOPE
        validation_scope = "construction_diagnostic_" + "_".join(reasons or ["unspecified"])
    route = select_live_pair_routes(logits)
    features = main_numeric_features(logits, route)
    target_kinds, target_signs = build_pair_targets(y_true, route, correct_counts)
    route_indices = route["indices"]
    route_ids = [ids[idx] for idx in route_indices.tolist()]

    # Fold the entire clean parent surface, not only routed rows, so aggregate
    # fold metrics include untouched examples under the same session boundary.
    full_strata = y_true.clone()
    for local_idx, full_idx in enumerate(route_indices.tolist()):
        full_strata[full_idx] = 100 + int(route["pair_ids"][local_idx]) * 4 + int(target_kinds[local_idx])
    full_fold_ids = assign_session_folds(ids, full_strata, n_folds=n_folds, seed=fold_seed)
    route_fold_ids = full_fold_ids[route_indices]

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": DATASET_KIND,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "classes": list(ALL_CLASSES),
        "live_pairs": [list(pair) for pair in LIVE_WEAK4_PAIRS],
        "feature_schema": FEATURE_SCHEMA,
        "usage_scope": usage_scope,
        "validation_scope": validation_scope,
        "parent_split": parent_split,
        "parent_seed": parent_seed,
        "parent_base_model": parent.get("base_model"),
        "parent_serializer": parent.get("serializer_name"),
        "reliability_source": reliability_source,
        "ids": ids,
        "train_indices": torch.tensor([sample_by_id[sample_id][0] for sample_id in ids], dtype=torch.long),
        "y_true": y_true.to(torch.long),
        "parent_logits": logits.float(),
        "correct_counts": correct_counts.to(torch.uint8),
        "full_fold_ids": full_fold_ids,
        "n_folds": int(n_folds),
        "fold_seed": int(fold_seed),
        "route_indices": route_indices,
        "route_ids": route_ids,
        "pair_ids": route["pair_ids"],
        "pair_classes": route["pair_classes"],
        "top_sides": route["top_sides"],
        "base_gaps": route["base_gaps"],
        "main_predictions": route["main_predictions"],
        "alternative_predictions": route["alternative_predictions"],
        "numeric_features": features,
        "target_kinds": target_kinds,
        "target_signs": target_signs,
        "route_fold_ids": route_fold_ids,
    }


def dataset_summary(payload: dict) -> dict:
    route_folds = torch.as_tensor(payload["route_fold_ids"], dtype=torch.long)
    return {
        "schema_version": payload["schema_version"],
        "kind": payload["kind"],
        "created_utc": payload["created_utc"],
        "rows": len(payload["ids"]),
        "routed_rows": len(payload["route_ids"]),
        "route_fraction": len(payload["route_ids"]) / max(1, len(payload["ids"])),
        "target_histogram": target_histogram(payload["target_kinds"]),
        "pair_histogram": {
            str(pair): int((torch.as_tensor(payload["pair_ids"]) == pair_id).sum())
            for pair_id, pair in enumerate(LIVE_WEAK4_PAIRS)
        },
        "fold_rows": {
            str(fold): int((torch.as_tensor(payload["full_fold_ids"]) == fold).sum())
            for fold in range(int(payload["n_folds"]))
        },
        "fold_route_rows": {
            str(fold): int((route_folds == fold).sum())
            for fold in range(int(payload["n_folds"]))
        },
        "parent_split": payload["parent_split"],
        "parent_seed": payload["parent_seed"],
        "parent_base_model": payload["parent_base_model"],
        "parent_serializer": payload["parent_serializer"],
        "reliability_source": payload["reliability_source"],
        "usage_scope": payload["usage_scope"],
        "validation_scope": payload["validation_scope"],
    }


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--parent-logits", default=DEFAULT_PARENT)
    parser.add_argument(
        "--consensus",
        default=DEFAULT_CONSENSUS,
        help="diagnostic only; omit for clean fixed-parent CV",
    )
    parser.add_argument("--allow-crossfold-consensus", action="store_true")
    parser.add_argument(
        "--output",
        default="experiments/artifacts/20260712_weak4_pair_fixed_parent_dataset.pt",
    )
    parser.add_argument("--summary-json", default="")
    parser.add_argument("--n-folds", type=int, default=3)
    parser.add_argument("--fold-seed", type=int, default=42)
    parser.add_argument("--allow-nonfixed-parent", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    data_dir = Path(args.data_dir)
    parent_path = Path(args.parent_logits)
    consensus_path = Path(args.consensus) if args.consensus else None
    output_path = Path(args.output)
    samples = load_jsonl(data_dir / "train.jsonl")
    labels = load_labels(data_dir / "train_labels.csv")
    parent = torch_load(parent_path)
    consensus = torch_load(consensus_path) if consensus_path is not None else None
    payload = build_dataset_payload(
        samples,
        labels,
        parent,
        consensus,
        n_folds=args.n_folds,
        fold_seed=args.fold_seed,
        require_clean_fixed=not args.allow_nonfixed_parent,
        allow_crossfold_consensus=args.allow_crossfold_consensus,
    )
    payload["provenance"] = {
        "parent_path": str(parent_path),
        "parent_sha256": sha256_file(parent_path),
        "consensus_path": str(consensus_path) if consensus_path is not None else "",
        "consensus_sha256": sha256_file(consensus_path) if consensus_path is not None else "",
        "train_jsonl_sha256": sha256_file(data_dir / "train.jsonl"),
        "train_labels_sha256": sha256_file(data_dir / "train_labels.csv"),
        "parent_logits_usage": "raw_logits_without_saved_class_bias",
        "usage_scope": payload["usage_scope"],
        "validation_scope": payload["validation_scope"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, output_path)
    summary = dataset_summary(payload)
    summary["artifact_path"] = str(output_path)
    summary["artifact_sha256"] = sha256_file(output_path)
    summary["provenance"] = payload["provenance"]
    summary_path = Path(args.summary_json) if args.summary_json else output_path.with_suffix(".json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"saved {output_path} and {summary_path}")


if __name__ == "__main__":
    main()
