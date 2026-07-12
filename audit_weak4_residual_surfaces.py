#!/usr/bin/env python3
"""Fail-closed audit for Weak4 residual-specialist parent surfaces.

The utility has no training or packaging side effects.  It aligns a parent
logit payload to the official training rows by ``id``, verifies label and
class-column provenance, and reports the two proposed routing contracts:

* ``full_weak4``: main top-1 is Weak4; target is KEEP_MAIN or a Weak4 switch.
* ``live_pair``: main top-1/top-2 form one of the pre-registered live pairs.

There are only two accepted surface kinds.  A fixed ``split=session`` payload
must exactly match the repository's deterministic held-out IDs and is marked
as a clean selector screen.  A full-coverage payload must carry explicit OOF
provenance and is marked construction-only: a second-level CV on a stitched
OOF asset is not nested validation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

import torch

from script import ALL_CLASSES, load_jsonl
from train import CLASS_TO_ID, f1_metrics, session_id, split_indices


REPORT_FORMAT = "weak4-residual-surface-audit-v1"
CLEAN_FIXED_SESSION = "clean_fixed_session"
CONSTRUCTION_ONLY_FULL_OOF = "construction_only_full_oof"
WEAK4_CLASSES = tuple(ALL_CLASSES[:4])
WEAK4_IDS = frozenset(range(4))

# These are deliberately narrow.  In particular read<->grep and list<->grep
# are not live pairs in the first residual experiment.
LIVE_PAIRS = (
    ("glob_pattern", "grep_search"),
    ("glob_pattern", "read_file"),
    ("glob_pattern", "list_directory"),
    ("list_directory", "read_file"),
)
LIVE_PAIR_ID_SETS = frozenset(
    frozenset((CLASS_TO_ID[left], CLASS_TO_ID[right]))
    for left, right in LIVE_PAIRS
)
LIVE_PAIR_KEY_BY_IDS = {
    frozenset((CLASS_TO_ID[left], CLASS_TO_ID[right])): f"{left}<>{right}"
    for left, right in LIVE_PAIRS
}
FULL_SWITCH_TARGET_BY_CLASS = {
    CLASS_TO_ID["read_file"]: "SWITCH_READ",
    CLASS_TO_ID["grep_search"]: "SWITCH_GREP",
    CLASS_TO_ID["list_directory"]: "SWITCH_LIST",
    CLASS_TO_ID["glob_pattern"]: "SWITCH_GLOB",
}
FULL_TARGET_NAMES = ("KEEP_MAIN", *FULL_SWITCH_TARGET_BY_CLASS.values())
LIVE_TARGET_NAMES = ("KEEP_MAIN", "SWITCH_TOP2", "OUTSIDE_PAIR_IDENTITY")


class SurfaceAuditError(ValueError):
    """Raised when a parent surface cannot be proved safe for its claimed use."""


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def torch_load(path: Path):
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:  # pragma: no cover - compatibility with older torch
        return torch.load(path, map_location="cpu")


def _strict_string_list(value, field: str, path: Path) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise SurfaceAuditError(f"{path}: {field} must be a list of strings")
    output = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise SurfaceAuditError(
                f"{path}: {field}[{index}] must be a non-empty string"
            )
        output.append(item)
    return output


def _strict_integer_vector(value, field: str, path: Path) -> torch.Tensor:
    try:
        tensor = torch.as_tensor(value)
    except (TypeError, ValueError) as exc:
        raise SurfaceAuditError(f"{path}: {field} must be an integer vector") from exc
    if tensor.ndim != 1:
        raise SurfaceAuditError(
            f"{path}: {field} must be one-dimensional, got shape={tuple(tensor.shape)}"
        )
    if tensor.dtype == torch.bool or tensor.is_complex():
        raise SurfaceAuditError(f"{path}: {field} must contain integer class/row ids")
    if tensor.is_floating_point():
        if not bool(torch.isfinite(tensor).all()) or not bool((tensor == tensor.round()).all()):
            raise SurfaceAuditError(f"{path}: {field} contains non-integral values")
    return tensor.to(dtype=torch.long, device="cpu")


def load_labels_strict(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise SurfaceAuditError(f"missing labels file: {path}")
    labels: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not {"id", "action"}.issubset(reader.fieldnames):
            raise SurfaceAuditError(f"{path}: expected CSV columns id,action")
        for row_number, row in enumerate(reader, 2):
            sample_id = row.get("id", "")
            action = row.get("action", "")
            if not sample_id:
                raise SurfaceAuditError(f"{path}:{row_number}: empty id")
            if sample_id in labels:
                raise SurfaceAuditError(f"{path}:{row_number}: duplicate id {sample_id!r}")
            if action not in CLASS_TO_ID:
                raise SurfaceAuditError(
                    f"{path}:{row_number}: non-canonical action {action!r}"
                )
            labels[sample_id] = action
    return labels


def load_training_contract(data_dir: Path) -> dict:
    train_path = data_dir / "train.jsonl"
    labels_path = data_dir / "train_labels.csv"
    if not train_path.is_file():
        raise SurfaceAuditError(f"missing training data: {train_path}")
    samples = load_jsonl(train_path)
    if not samples:
        raise SurfaceAuditError(f"{train_path}: no rows")

    ids: list[str] = []
    positions: dict[str, int] = {}
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise SurfaceAuditError(f"{train_path}: row {index + 1} must be an object")
        sample_id = sample.get("id")
        if not isinstance(sample_id, str) or not sample_id:
            raise SurfaceAuditError(f"{train_path}: row {index + 1} has invalid id")
        if sample_id in positions:
            raise SurfaceAuditError(f"{train_path}: duplicate id {sample_id!r}")
        positions[sample_id] = index
        ids.append(sample_id)

    labels_by_id = load_labels_strict(labels_path)
    id_set = set(ids)
    label_id_set = set(labels_by_id)
    if id_set != label_id_set:
        missing = sorted(id_set - label_id_set)[:5]
        extra = sorted(label_id_set - id_set)[:5]
        raise SurfaceAuditError(
            f"training/label ID coverage mismatch: train={len(ids)} "
            f"labels={len(labels_by_id)} missing={missing} extra={extra}"
        )
    y = [CLASS_TO_ID[labels_by_id[sample_id]] for sample_id in ids]
    return {
        "samples": samples,
        "ids": ids,
        "positions": positions,
        "labels_by_id": labels_by_id,
        "y": y,
        "train_path": train_path,
        "labels_path": labels_path,
    }


def validate_parent_payload(payload, path: Path, training: dict) -> dict:
    if not isinstance(payload, dict):
        raise SurfaceAuditError(f"{path}: parent payload must be a dict")

    classes = _strict_string_list(payload.get("classes"), "classes", path)
    if classes != ALL_CLASSES:
        raise SurfaceAuditError(
            f"{path}: canonical class order mismatch: expected={ALL_CLASSES} actual={classes}"
        )
    ids = _strict_string_list(payload.get("ids"), "ids", path)
    duplicate_count = len(ids) - len(set(ids))
    if duplicate_count:
        duplicates = [item for item, count in Counter(ids).items() if count > 1][:5]
        raise SurfaceAuditError(
            f"{path}: duplicate parent ids count={duplicate_count} examples={duplicates}"
        )
    if not ids:
        raise SurfaceAuditError(f"{path}: parent payload has no rows")

    source_logits = payload.get("logits")
    if not torch.is_tensor(source_logits):
        raise SurfaceAuditError(f"{path}: logits must be a torch tensor")
    source_logit_dtype = str(source_logits.dtype)
    if source_logits.ndim != 2 or tuple(source_logits.shape) != (len(ids), len(ALL_CLASSES)):
        raise SurfaceAuditError(
            f"{path}: logits must have shape {(len(ids), len(ALL_CLASSES))}, "
            f"got {tuple(source_logits.shape)}"
        )
    logits = source_logits.detach().cpu().float()
    finite = torch.isfinite(logits)
    if not bool(finite.all()):
        bad = torch.nonzero(~finite, as_tuple=False)[0].tolist()
        raise SurfaceAuditError(
            f"{path}: non-finite logits count={int((~finite).sum())} first_row_col={bad}"
        )

    train_positions = training["positions"]
    unknown = sorted(set(ids) - set(train_positions))
    if unknown:
        raise SurfaceAuditError(
            f"{path}: parent contains {len(unknown)} IDs absent from train data: {unknown[:5]}"
        )

    official_y = torch.tensor(
        [training["y"][train_positions[sample_id]] for sample_id in ids],
        dtype=torch.long,
    )
    payload_y_value = payload.get("y_true")
    if payload_y_value is None:
        raise SurfaceAuditError(f"{path}: y_true is required for an auditable ID join")
    payload_y = _strict_integer_vector(payload_y_value, "y_true", path)
    if len(payload_y) != len(ids):
        raise SurfaceAuditError(
            f"{path}: y_true length mismatch ids={len(ids)} y_true={len(payload_y)}"
        )
    if bool(((payload_y < 0) | (payload_y >= len(ALL_CLASSES))).any()):
        raise SurfaceAuditError(f"{path}: y_true contains an out-of-range class id")
    mismatches = torch.where(payload_y != official_y)[0]
    if len(mismatches):
        examples = [ids[int(index)] for index in mismatches[:5]]
        raise SurfaceAuditError(
            f"{path}: ID-aligned y_true disagrees with official labels for "
            f"{len(mismatches)} rows: {examples}"
        )

    labels_value = payload.get("labels")
    if labels_value is not None:
        labels = _strict_string_list(labels_value, "labels", path)
        if len(labels) != len(ids):
            raise SurfaceAuditError(
                f"{path}: labels length mismatch ids={len(ids)} labels={len(labels)}"
            )
        bad_labels = [label for label in labels if label not in CLASS_TO_ID]
        if bad_labels:
            raise SurfaceAuditError(f"{path}: labels contains non-canonical values: {bad_labels[:5]}")
        label_y = torch.tensor([CLASS_TO_ID[label] for label in labels], dtype=torch.long)
        if not torch.equal(label_y, official_y):
            raise SurfaceAuditError(f"{path}: ID-aligned labels disagree with official labels")

    indices_value = payload.get("indices")
    if indices_value is not None:
        indices = _strict_integer_vector(indices_value, "indices", path)
        if len(indices) != len(ids):
            raise SurfaceAuditError(
                f"{path}: indices length mismatch ids={len(ids)} indices={len(indices)}"
            )
        if bool(((indices < 0) | (indices >= len(training["ids"]))).any()):
            raise SurfaceAuditError(f"{path}: indices contains an out-of-range train row")
        index_mismatches = [
            sample_id
            for sample_id, index in zip(ids, indices.tolist())
            if training["ids"][index] != sample_id
        ]
        if index_mismatches:
            raise SurfaceAuditError(
                f"{path}: indices are not ID-aligned: {index_mismatches[:5]}"
            )

    return {
        "payload": payload,
        "classes": classes,
        "ids": ids,
        "logits": logits,
        "source_logit_dtype": source_logit_dtype,
        "y_true": official_y,
        "train_indices": [train_positions[sample_id] for sample_id in ids],
    }


def _oof_provenance_evidence(payload: dict, path: Path) -> list[str]:
    del path  # A suggestive filename alone is not auditable provenance.
    candidates = []
    split = payload.get("split")
    if split is not None:
        candidates.append(("split", str(split)))
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in (
            "input_payload",
            "source",
            "split",
            "split_type",
            "kind",
            "description",
        ):
            if metadata.get(key) is not None:
                candidates.append((f"metadata.{key}", str(metadata[key])))
    evidence = []
    for field, value in candidates:
        lowered = value.lower()
        if "session_oof" in lowered or "fold_all_val" in lowered or "oof" in lowered:
            evidence.append(f"{field}={value}")
    return evidence


def classify_surface(
    validated: dict,
    training: dict,
    path: Path,
    expected_surface: str = "auto",
) -> dict:
    payload = validated["payload"]
    ids = validated["ids"]
    parent_id_set = set(ids)
    train_id_set = set(training["ids"])
    split = payload.get("split")

    if split == "session":
        seed_value = payload.get("seed")
        if seed_value is None:
            raise SurfaceAuditError(f"{path}: fixed session payload is missing seed")
        try:
            seed = int(seed_value)
        except (TypeError, ValueError) as exc:
            raise SurfaceAuditError(f"{path}: invalid fixed session seed {seed_value!r}") from exc
        train_idx, val_idx = split_indices(
            training["samples"], training["y"], "session", seed
        )
        expected_ids = {training["ids"][index] for index in val_idx}
        if parent_id_set != expected_ids:
            missing = sorted(expected_ids - parent_id_set)[:5]
            extra = sorted(parent_id_set - expected_ids)[:5]
            raise SurfaceAuditError(
                f"{path}: fixed session IDs do not match deterministic split seed={seed}: "
                f"expected={len(expected_ids)} actual={len(parent_id_set)} "
                f"missing={missing} extra={extra}"
            )
        train_sessions = {
            session_id(training["ids"][index]) for index in train_idx
        }
        val_sessions = {session_id(sample_id) for sample_id in ids}
        overlap = train_sessions & val_sessions
        if overlap:
            raise SurfaceAuditError(
                f"{path}: fixed session train/validation session overlap: {sorted(overlap)[:5]}"
            )
        result = {
            "kind": CLEAN_FIXED_SESSION,
            "promotion_scope": "clean_selector_screen",
            "reason": (
                "payload IDs exactly match the deterministic held-out session split; "
                "the parent surface is outside that main model's fit rows"
            ),
            "split": "session",
            "seed": seed,
            "main_train_rows": len(train_idx),
            "main_validation_rows": len(val_idx),
            "main_train_sessions": len(train_sessions),
            "main_validation_sessions": len(val_sessions),
        }
    elif parent_id_set == train_id_set:
        evidence = _oof_provenance_evidence(payload, path)
        if not evidence:
            raise SurfaceAuditError(
                f"{path}: full train coverage is ambiguous; explicit OOF provenance "
                "(session_oof/fold_all_val/oof) is required"
            )
        result = {
            "kind": CONSTRUCTION_ONLY_FULL_OOF,
            "promotion_scope": "construction_only_not_nested_validation",
            "reason": (
                "each row may be OOF, but a second-level CV on the same stitched "
                "surface is not nested because other-fold parent fits can contain "
                "the specialist validation sessions"
            ),
            "split": "full_oof",
            "seed": payload.get("seed"),
            "oof_provenance_evidence": evidence,
        }
    else:
        raise SurfaceAuditError(
            f"{path}: unsupported partial surface: split={split!r} "
            f"rows={len(ids)} train_rows={len(training['ids'])}"
        )

    if expected_surface != "auto" and result["kind"] != expected_surface:
        raise SurfaceAuditError(
            f"{path}: expected surface {expected_surface!r}, classified {result['kind']!r}"
        )
    return result


def _zero_histogram(names: Iterable[str]) -> dict[str, int]:
    return {name: 0 for name in names}


def route_assignments(logits: torch.Tensor, y_true: torch.Tensor) -> dict:
    # Stable sorting makes exact ties deterministic in canonical class order.
    ranking = torch.argsort(logits, dim=1, descending=True, stable=True)
    top1 = ranking[:, 0].tolist()
    overall_top2 = ranking[:, 1].tolist()
    # Pair residuals operate inside the conditional Weak4 distribution.  A
    # strong non-Weak4 runner-up must not hide the actual Weak4 alternative.
    weak_ranking = torch.argsort(
        logits[:, : len(WEAK4_CLASSES)], dim=1, descending=True, stable=True
    )
    weak_top1 = weak_ranking[:, 0].tolist()
    weak_top2 = weak_ranking[:, 1].tolist()
    truth = y_true.tolist()

    full_eligible: list[bool] = []
    full_targets: list[str | None] = []
    full_relations: list[str | None] = []
    live_eligible: list[bool] = []
    live_targets: list[str | None] = []
    live_pairs: list[str | None] = []
    live_directions: list[str | None] = []

    for main_id, conditional_main_id, alternative_id, true_id in zip(
        top1, weak_top1, weak_top2, truth
    ):
        is_full = main_id in WEAK4_IDS
        if is_full and conditional_main_id != main_id:
            raise AssertionError("overall Weak4 top1 differs from conditional Weak4 top1")
        full_eligible.append(is_full)
        if not is_full:
            full_targets.append(None)
            full_relations.append(None)
        elif true_id == main_id:
            full_targets.append("KEEP_MAIN")
            full_relations.append("main_correct")
        elif true_id in WEAK4_IDS:
            full_targets.append(FULL_SWITCH_TARGET_BY_CLASS[true_id])
            full_relations.append("weak4_internal_switch")
        else:
            full_targets.append("KEEP_MAIN")
            full_relations.append("true_nonweak_identity")

        pair_ids = frozenset((main_id, alternative_id))
        is_live = (
            main_id in WEAK4_IDS
            and alternative_id in WEAK4_IDS
            and pair_ids in LIVE_PAIR_ID_SETS
        )
        live_eligible.append(is_live)
        if not is_live:
            live_targets.append(None)
            live_pairs.append(None)
            live_directions.append(None)
            continue
        live_pairs.append(LIVE_PAIR_KEY_BY_IDS[pair_ids])
        live_directions.append(f"{ALL_CLASSES[main_id]}->{ALL_CLASSES[alternative_id]}")
        if true_id == main_id:
            live_targets.append("KEEP_MAIN")
        elif true_id == alternative_id:
            live_targets.append("SWITCH_TOP2")
        else:
            live_targets.append("OUTSIDE_PAIR_IDENTITY")

    return {
        "top1": top1,
        "overall_top2": overall_top2,
        "weak_top2": weak_top2,
        "full_eligible": full_eligible,
        "full_targets": full_targets,
        "full_relations": full_relations,
        "live_eligible": live_eligible,
        "live_targets": live_targets,
        "live_pairs": live_pairs,
        "live_directions": live_directions,
    }


def summarize_routes(assignments: dict, row_indices: Iterable[int] | None = None) -> dict:
    if row_indices is None:
        row_indices = range(len(assignments["top1"]))
    rows = list(row_indices)

    full_target_histogram = _zero_histogram(FULL_TARGET_NAMES)
    full_relation_histogram = _zero_histogram(
        ("main_correct", "weak4_internal_switch", "true_nonweak_identity")
    )
    full_by_parent = _zero_histogram(WEAK4_CLASSES)
    live_target_histogram = _zero_histogram(LIVE_TARGET_NAMES)
    live_pair_histogram = _zero_histogram(left + "<>" + right for left, right in LIVE_PAIRS)
    live_direction_histogram: Counter[str] = Counter()

    full_rows = 0
    live_rows = 0
    for index in rows:
        if assignments["full_eligible"][index]:
            full_rows += 1
            full_target_histogram[assignments["full_targets"][index]] += 1
            full_relation_histogram[assignments["full_relations"][index]] += 1
            full_by_parent[ALL_CLASSES[assignments["top1"][index]]] += 1
        if assignments["live_eligible"][index]:
            live_rows += 1
            live_target_histogram[assignments["live_targets"][index]] += 1
            live_pair_histogram[assignments["live_pairs"][index]] += 1
            live_direction_histogram[assignments["live_directions"][index]] += 1

    total = len(rows)
    return {
        "rows": total,
        "full_weak4": {
            "definition": "parent top1 is Weak4",
            "route_rows": full_rows,
            "nonroute_rows": total - full_rows,
            "route_fraction": full_rows / total if total else 0.0,
            "target_histogram": full_target_histogram,
            "relation_histogram": full_relation_histogram,
            "parent_top1_histogram": full_by_parent,
        },
        "live_pair": {
            "definition": "parent top1/top2 form a pre-registered live Weak4 pair",
            "live_pairs": [list(pair) for pair in LIVE_PAIRS],
            "route_rows": live_rows,
            "nonroute_rows": total - live_rows,
            "route_fraction": live_rows / total if total else 0.0,
            "target_histogram": live_target_histogram,
            "pair_histogram": live_pair_histogram,
            "direction_histogram": dict(sorted(live_direction_histogram.items())),
        },
    }


def _weak4_macro(metrics: dict) -> float:
    return sum(float(metrics["per_class_f1"][label]) for label in WEAK4_CLASSES) / 4.0


def _oracle_metric_summary(
    metrics: dict,
    baseline_metrics: dict,
    *,
    route_rows: int,
    oracle_eligible_rows: int,
    route_correction_count: int,
) -> dict:
    baseline_weak4 = _weak4_macro(baseline_metrics)
    weak4 = _weak4_macro(metrics)
    return {
        "full_macro_f1": float(metrics["macro_f1"]),
        "full_macro_delta": float(metrics["macro_f1"] - baseline_metrics["macro_f1"]),
        "weak4_macro_f1": weak4,
        "weak4_macro_delta": weak4 - baseline_weak4,
        "route_rows": int(route_rows),
        "oracle_eligible_rows": int(oracle_eligible_rows),
        "route_correction_count": int(route_correction_count),
    }


def oracle_headroom(assignments: dict, y_true: torch.Tensor) -> dict:
    """Return privileged-label upper bounds for the pre-registered routes.

    These predictions are diagnostics only.  They use the official target and
    therefore must never become model inputs, inference rules, or replay data.
    """

    truth = [int(value) for value in torch.as_tensor(y_true, dtype=torch.long).tolist()]
    baseline = [int(value) for value in assignments["top1"]]
    if len(truth) != len(baseline):
        raise SurfaceAuditError("oracle y_true/assignment row mismatch")
    baseline_metrics = f1_metrics(truth, baseline)

    full_predictions = list(baseline)
    full_route_rows = 0
    full_eligible_rows = 0
    full_corrections = 0
    for index, true_id in enumerate(truth):
        if not assignments["full_eligible"][index]:
            continue
        full_route_rows += 1
        if true_id not in WEAK4_IDS:
            continue
        full_eligible_rows += 1
        if full_predictions[index] != true_id:
            full_predictions[index] = true_id
            full_corrections += 1
    full_metrics = f1_metrics(truth, full_predictions)

    live_predictions = list(baseline)
    live_route_rows = 0
    live_eligible_rows = 0
    live_corrections = 0
    for index, true_id in enumerate(truth):
        if not assignments["live_eligible"][index]:
            continue
        live_route_rows += 1
        alternative = int(assignments["weak_top2"][index])
        if true_id != alternative:
            continue
        live_eligible_rows += 1
        if live_predictions[index] != alternative:
            live_predictions[index] = alternative
            live_corrections += 1
    live_metrics = f1_metrics(truth, live_predictions)

    return {
        "usage": "privileged_official_label_oracle_only",
        "raw_baseline": _oracle_metric_summary(
            baseline_metrics,
            baseline_metrics,
            route_rows=0,
            oracle_eligible_rows=0,
            route_correction_count=0,
        ),
        "full_weak4_internal_oracle": {
            "definition": "only parent-Weak4 routes with y_true in Weak4 are set to y_true",
            **_oracle_metric_summary(
                full_metrics,
                baseline_metrics,
                route_rows=full_route_rows,
                oracle_eligible_rows=full_eligible_rows,
                route_correction_count=full_corrections,
            ),
        },
        "live_pair_top2_oracle": {
            "definition": "only live-pair routes whose y_true equals conditional Weak4 top2 switch to top2",
            **_oracle_metric_summary(
                live_metrics,
                baseline_metrics,
                route_rows=live_route_rows,
                oracle_eligible_rows=live_eligible_rows,
                route_correction_count=live_corrections,
            ),
        },
    }


def session_hash_fold(session: str, n_folds: int, seed: int) -> int:
    if n_folds < 2:
        raise SurfaceAuditError("n_folds must be at least 2")
    material = f"weak4-residual-v1\0{seed}\0{session}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    return value % n_folds


def summarize_session_hash_folds(
    ids: list[str], assignments: dict, n_folds: int, seed: int
) -> dict:
    if n_folds < 2:
        raise SurfaceAuditError("n_folds must be at least 2")
    sessions = [session_id(sample_id) for sample_id in ids]
    session_to_fold = {
        value: session_hash_fold(value, n_folds=n_folds, seed=seed)
        for value in set(sessions)
    }
    rows_by_fold = {fold: [] for fold in range(n_folds)}
    sessions_by_fold = {fold: set() for fold in range(n_folds)}
    for row_index, value in enumerate(sessions):
        fold = session_to_fold[value]
        rows_by_fold[fold].append(row_index)
        sessions_by_fold[fold].add(value)

    digest = hashlib.sha256()
    for value in sorted(session_to_fold):
        digest.update(f"{value}\t{session_to_fold[value]}\n".encode("utf-8"))

    folds = {}
    for fold in range(n_folds):
        route_summary = summarize_routes(assignments, rows_by_fold[fold])
        folds[str(fold)] = {
            "rows": len(rows_by_fold[fold]),
            "sessions": len(sessions_by_fold[fold]),
            "full_route_rows": route_summary["full_weak4"]["route_rows"],
            "full_target_histogram": route_summary["full_weak4"]["target_histogram"],
            "live_pair_route_rows": route_summary["live_pair"]["route_rows"],
            "live_pair_target_histogram": route_summary["live_pair"]["target_histogram"],
        }
    return {
        "algorithm": (
            f"sha256('weak4-residual-v1\\0{seed}\\0{{session}}')[:8] mod {n_folds}"
        ),
        "seed": seed,
        "n_folds": n_folds,
        "rows": len(ids),
        "sessions": len(session_to_fold),
        "assignment_sha256": digest.hexdigest(),
        "folds": folds,
    }


def audit_surface(
    data_dir: str | Path,
    parent_logits: str | Path,
    *,
    expected_surface: str = "auto",
    n_folds: int = 3,
    fold_seed: int = 42,
) -> dict:
    data_dir = Path(data_dir)
    parent_path = Path(parent_logits)
    if not parent_path.is_file():
        raise SurfaceAuditError(f"missing parent payload: {parent_path}")
    training = load_training_contract(data_dir)
    validated = validate_parent_payload(torch_load(parent_path), parent_path, training)
    surface = classify_surface(
        validated, training, parent_path, expected_surface=expected_surface
    )
    assignments = route_assignments(validated["logits"], validated["y_true"])
    routes = summarize_routes(assignments)
    oracle = oracle_headroom(assignments, validated["y_true"])
    folds = summarize_session_hash_folds(
        validated["ids"], assignments, n_folds=n_folds, seed=fold_seed
    )
    full_coverage = set(validated["ids"]) == set(training["ids"])
    return {
        "format": REPORT_FORMAT,
        "surface": surface,
        "parent_payload": {
            "path": str(parent_path),
            "sha256": sha256_file(parent_path),
            "rows": len(validated["ids"]),
            "source_logit_dtype": validated["source_logit_dtype"],
            "audit_logit_dtype": str(validated["logits"].dtype),
            "class_count": len(validated["classes"]),
        },
        "join": {
            "strategy": "exact_id",
            "train_rows": len(training["ids"]),
            "label_rows": len(training["labels_by_id"]),
            "parent_rows": len(validated["ids"]),
            "parent_unique_ids": len(set(validated["ids"])),
            "full_train_coverage": full_coverage,
            "official_label_mismatches": 0,
        },
        "classes": list(ALL_CLASSES),
        "weak4_classes": list(WEAK4_CLASSES),
        "route_histograms": routes,
        "oracle_headroom": oracle,
        "session_hash_folds": folds,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--parent-logits", required=True)
    parser.add_argument(
        "--expected-surface",
        choices=("auto", CLEAN_FIXED_SESSION, CONSTRUCTION_ONLY_FULL_OOF),
        default="auto",
        help="optional fail-closed assertion after automatic classification",
    )
    parser.add_argument("--n-folds", type=int, default=3)
    parser.add_argument("--fold-seed", type=int, default=42)
    parser.add_argument("--output", default="", help="optional JSON report path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_surface(
        args.data_dir,
        args.parent_logits,
        expected_surface=args.expected_surface,
        n_folds=args.n_folds,
        fold_seed=args.fold_seed,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"saved {output_path}", file=sys.stderr)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
