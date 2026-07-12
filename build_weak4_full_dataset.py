#!/usr/bin/env python3
"""Build an ID-aligned typed dataset for the full Weak4 residual specialist."""

from __future__ import annotations

import argparse
import csv
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch

from script import ALL_CLASSES, load_jsonl
from train import f1_metrics, split_indices
from weak4_full_residual import (
    WEAK4_CLASSES,
    WEAK4_ID_SET,
    Weak4RouteRow,
    build_residual_target,
    route_row_to_payload,
    serialize_weak4_full,
    session_id_from_sample_id,
)


DATASET_FORMAT = "weak4-full-residual-dataset-v1"


def torch_load(path: str | Path) -> Any:
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def ids_digest(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def load_labels(path: str | Path) -> dict[str, int]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    labels: dict[str, int] = {}
    for row in rows:
        sample_id = str(row.get("id", ""))
        action = row.get("action")
        if not sample_id or action not in ALL_CLASSES:
            raise ValueError(f"invalid label row: id={sample_id!r} action={action!r}")
        if sample_id in labels:
            raise ValueError(f"duplicate label id: {sample_id}")
        labels[sample_id] = ALL_CLASSES.index(action)
    return labels


def validate_parent_payload(
    payload: Any,
    path: str | Path,
    *,
    allow_legacy_canonical_parent: bool,
    require_split: str = "",
    require_seed: int | None = None,
    require_rows: int | None = None,
) -> tuple[torch.Tensor, list[str], list[int]]:
    if not isinstance(payload, dict):
        raise ValueError("parent payload must be a dictionary")
    missing = sorted({"ids", "logits", "y_true"} - set(payload))
    if missing:
        raise ValueError(f"parent payload is missing keys: {missing}")
    classes = payload.get("classes")
    if classes is None:
        if not allow_legacy_canonical_parent:
            raise ValueError(
                "parent payload lacks class metadata; pass "
                "--allow-legacy-canonical-parent only after verifying canonical order"
            )
    elif list(classes) != ALL_CLASSES:
        raise ValueError("parent payload class order is not canonical")

    ids = [str(value) for value in payload["ids"]]
    logits = torch.as_tensor(payload["logits"], dtype=torch.float32).cpu()
    y_true = [int(value) for value in payload["y_true"]]
    if logits.ndim != 2 or logits.shape[1] != len(ALL_CLASSES):
        raise ValueError(f"parent logits must have shape [N, {len(ALL_CLASSES)}]")
    if len(ids) != logits.shape[0] or len(y_true) != len(ids):
        raise ValueError("parent ids/logits/y_true row mismatch")
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("parent ids must be nonempty and unique")
    if not bool(torch.isfinite(logits).all()):
        raise ValueError("parent logits contain nonfinite values")
    if any(value < 0 or value >= len(ALL_CLASSES) for value in y_true):
        raise ValueError("parent y_true contains a noncanonical class id")
    if require_rows is not None and len(ids) != require_rows:
        raise ValueError(f"parent row count {len(ids)} != required {require_rows}")
    if require_split and payload.get("split") != require_split:
        raise ValueError(
            f"parent split {payload.get('split')!r} != required {require_split!r}"
        )
    if require_seed is not None and int(payload.get("seed", -1)) != require_seed:
        raise ValueError(
            f"parent seed {payload.get('seed')!r} != required {require_seed}"
        )
    return logits, ids, y_true


def build_dataset(
    *,
    data_dir: str | Path,
    parent_logits_path: str | Path,
    parent_surface: str = "generic_raw",
    validation_scope: str = "clean_fixed_parent",
    allow_construction_only_oof: bool = False,
    allow_legacy_canonical_parent: bool = False,
    require_split: str = "",
    require_seed: int | None = None,
    require_rows: int | None = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    parent_logits_path = Path(parent_logits_path)
    if not parent_logits_path.is_file():
        raise FileNotFoundError(parent_logits_path)
    samples = load_jsonl(data_dir / "train.jsonl")
    labels = load_labels(data_dir / "train_labels.csv")
    by_id: dict[str, dict[str, Any]] = {}
    for sample in samples:
        sample_id = str(sample.get("id", ""))
        if not sample_id or sample_id in by_id:
            raise ValueError(f"train samples contain an invalid/duplicate id: {sample_id!r}")
        by_id[sample_id] = sample
    if set(by_id) != set(labels):
        only_samples = sorted(set(by_id) - set(labels))[:5]
        only_labels = sorted(set(labels) - set(by_id))[:5]
        raise ValueError(
            f"train sample/label id sets differ: samples_only={only_samples} labels_only={only_labels}"
        )
    if validation_scope not in {"clean_fixed_parent", "construction_only_oof", "unit_test"}:
        raise ValueError(f"unknown validation_scope: {validation_scope}")
    if validation_scope == "construction_only_oof" and not allow_construction_only_oof:
        raise ValueError(
            "construction-only OOF datasets require allow_construction_only_oof=True"
        )

    parent = torch_load(parent_logits_path)
    logits, ids, y_true = validate_parent_payload(
        parent,
        parent_logits_path,
        allow_legacy_canonical_parent=allow_legacy_canonical_parent,
        require_split=require_split,
        require_seed=require_seed,
        require_rows=require_rows,
    )
    missing = [sample_id for sample_id in ids if sample_id not in by_id]
    if missing:
        raise ValueError(f"{len(missing)} parent ids are absent from train.jsonl: {missing[:5]}")
    mismatches = [
        (sample_id, expected, actual)
        for sample_id, expected, actual in zip(ids, y_true, (labels[value] for value in ids))
        if expected != actual
    ]
    if mismatches:
        raise ValueError(f"parent labels disagree with train_labels.csv: {mismatches[:3]}")

    clean_contract = None
    raw_argmax_macro_f1 = f1_metrics(y_true, logits.argmax(dim=1).tolist())["macro_f1"]
    if validation_scope == "clean_fixed_parent":
        if parent.get("split") != "session" or int(parent.get("seed", -1)) != 42:
            raise ValueError(
                "clean fixed parent must declare split='session' and seed=42"
            )
        if parent.get("classes") is None:
            raise ValueError("clean fixed parent must carry explicit canonical classes")
        declared_raw = (parent.get("raw_metrics") or {}).get("macro_f1")
        if declared_raw is None or abs(float(declared_raw) - raw_argmax_macro_f1) > 1e-12:
            raise ValueError(
                "clean fixed parent logits do not reproduce payload raw_metrics; "
                "a bias-adjusted surface may have been supplied"
            )
        full_y = [labels[str(sample["id"])] for sample in samples]
        _, expected_val_indices = split_indices(samples, full_y, "session", 42)
        expected_ids = [str(samples[idx]["id"]) for idx in expected_val_indices]
        if len(ids) != len(expected_ids) or set(ids) != set(expected_ids):
            missing_fixed = sorted(set(expected_ids) - set(ids))[:5]
            extra_parent = sorted(set(ids) - set(expected_ids))[:5]
            raise ValueError(
                "parent IDs are not the exact fixed session(seed42) validation set: "
                f"expected={len(expected_ids)} actual={len(ids)} "
                f"missing={missing_fixed} extra={extra_parent}"
            )
        clean_contract = {
            "split_function": "train.split_indices",
            "split": "session",
            "seed": 42,
            "expected_rows": len(expected_ids),
            "expected_ids_sha256": ids_digest(expected_ids),
        }

    parent_pred = logits.argmax(dim=1)
    route_indices = (parent_pred < 4).nonzero(as_tuple=False).squeeze(1).tolist()
    rows: list[dict[str, Any]] = []
    target_counts = Counter()
    direction_counts = Counter()
    for full_index in route_indices:
        sample_id = ids[full_index]
        target_action, change_target, alt_target = build_residual_target(
            logits[full_index], y_true[full_index]
        )
        typed = serialize_weak4_full(by_id[sample_id], logits[full_index])
        identity_weight = 2.0 if y_true[full_index] not in WEAK4_ID_SET else 1.0
        row = Weak4RouteRow(
            sample_id=sample_id,
            session_id=session_id_from_sample_id(sample_id),
            full_index=full_index,
            typed=typed,
            y_true=y_true[full_index],
            target_action=target_action,
            change_target=change_target,
            alt_target=alt_target,
            identity_weight=identity_weight,
        )
        rows.append(route_row_to_payload(row))
        target_counts[target_action] += 1
        if change_target:
            direction_counts[(typed.main_pred, alt_target)] += 1

    if not rows:
        raise ValueError("parent payload produced zero Weak4 routes")
    source_metadata = {
        key: parent.get(key)
        for key in ("base_model", "serializer_name", "max_length", "split", "seed", "fold_id", "n_folds")
        if key in parent
    }
    artifact = {
        "format": DATASET_FORMAT,
        "schema_version": 1,
        "usage_scope": {
            "clean_fixed_parent": "clean_fixed_parent_session_cv",
            "construction_only_oof": "construction_only_no_promotion",
            "unit_test": "unit_test_only",
        }[validation_scope],
        "validation_scope": validation_scope,
        "clean_fixed_contract": clean_contract,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "classes": list(ALL_CLASSES),
        "weak4_classes": list(WEAK4_CLASSES),
        "ids": ids,
        "session_ids": [session_id_from_sample_id(value) for value in ids],
        "ids_sha256": ids_digest(ids),
        "y_true": torch.tensor(y_true, dtype=torch.int64),
        "parent_logits": logits,
        "parent_pred": parent_pred.to(torch.int64),
        "route_indices": torch.tensor(route_indices, dtype=torch.int64),
        "route_rows": rows,
        "target_histogram": {str(idx): int(target_counts[idx]) for idx in range(5)},
        "direction_histogram": {
            f"{WEAK4_CLASSES[source]}->{WEAK4_CLASSES[target]}": int(count)
            for (source, target), count in sorted(direction_counts.items())
        },
        "parent": {
            "path": str(parent_logits_path),
            "sha256": sha256_file(parent_logits_path),
            "surface": str(parent_surface),
            "score_contract": "payload_logits_unmodified_raw_argmax",
            "class_bias_applied": False,
            "raw_argmax_macro_f1": raw_argmax_macro_f1,
            "legacy_canonical_class_assumption": parent.get("classes") is None,
            "metadata": source_metadata,
        },
        "serializer": {
            "name": "weak4_full_v1",
            "prompt_cap": 384,
            "event_cap": 512,
            "workspace_cap": 384,
            "max_events": 4,
        },
    }
    return artifact


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--parent-logits", required=True)
    parser.add_argument("--parent-surface", default="fixed_raw_parent")
    parser.add_argument(
        "--validation-scope",
        choices=["clean_fixed_parent", "construction_only_oof"],
        default="clean_fixed_parent",
    )
    parser.add_argument("--allow-construction-only-oof", action="store_true")
    parser.add_argument("--allow-legacy-canonical-parent", action="store_true")
    parser.add_argument("--require-split", default="")
    parser.add_argument("--require-seed", type=int)
    parser.add_argument("--require-rows", type=int)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    artifact = build_dataset(
        data_dir=args.data_dir,
        parent_logits_path=args.parent_logits,
        parent_surface=args.parent_surface,
        validation_scope=args.validation_scope,
        allow_construction_only_oof=args.allow_construction_only_oof,
        allow_legacy_canonical_parent=args.allow_legacy_canonical_parent,
        require_split=args.require_split,
        require_seed=args.require_seed,
        require_rows=args.require_rows,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(artifact, output)
    print(
        f"saved {output} rows={len(artifact['ids'])} routed={len(artifact['route_rows'])} "
        f"targets={artifact['target_histogram']}"
    )


if __name__ == "__main__":
    main()
