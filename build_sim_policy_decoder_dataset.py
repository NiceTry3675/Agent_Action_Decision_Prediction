#!/usr/bin/env python3
"""Build the locked diagnostic champion-OOF surface for the SIM policy decoder.

The supplied three-fold stitch is useful for a cheap signal screen but is not
a strict stacking gate.  Parent provenance is recorded fail-closed so a future
nested-main dataset can be distinguished without relying on filenames.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch

from script import ALL_CLASSES, load_jsonl, safe_text
from sim_policy_decoder import (
    DATASET_FORMAT,
    DIAGNOSTIC_USAGE_SCOPE,
    FEATURE_SCHEMA,
    MODEL_SCHEMA,
    OUTPUT_NAMES,
    SERIALIZER_NAME,
    STRICT_USAGE_SCOPE,
    SUPPORTED_USAGE_SCOPES,
    build_recorded_policy_targets,
    ids_sha256,
    main_posterior_features,
    select_sim_weak4_routes,
    session_id_from_sample_id,
    target_histogram,
)


PARENT_PROVENANCE_STITCHED = "stitched_three_parent_oof"
PARENT_PROVENANCE_STRICT = "strict_outer_nested_main_v1"
LOCKED_CHAMPION_OOF_SHA256 = {
    0: "b505ed8b30d60d739afba9a32709774b464e0b85abf5e261e0ef1fe1389758a9",
    1: "e8d6c8811990c2eb681b241669a8f32ff3291e46e046af6c861bb19f95bbfa64",
    2: "a7807a698da1b90c4f32ccf063e3c5770b2215ca30b00e0b507676464f5fd826",
}


def validate_parent_provenance(
    parent: Mapping[str, Any], usage_scope: str
) -> dict[str, Any]:
    """Validate promotion eligibility independently from tensor shape.

    The currently supplied champion stitch is explicitly diagnostic: each
    meta-training row can come from a parent model that trained on a different
    specialist outer fold, and its full-data KD/consensus inputs carry an
    additional label path.  A future strict artifact must declare outer-fold
    nested parent fits and their exclusion guarantees instead of relabelling
    this stitch.
    """

    mode = parent.get("provenance_mode")
    if usage_scope == DIAGNOSTIC_USAGE_SCOPE:
        if mode != PARENT_PROVENANCE_STITCHED:
            raise ValueError("diagnostic stitch has an invalid parent provenance mode")
        if parent.get("promotion_eligible") is not False:
            raise ValueError("diagnostic stitch must be promotion_eligible=false")
        reasons = list(parent.get("ineligibility_reasons") or [])
        required = {
            "level2_cross_fold_parent_training_path",
            "full_data_kd_or_consensus_label_path_not_excluded",
        }
        if not required.issubset(reasons):
            raise ValueError("diagnostic parent provenance is missing leakage reasons")
        return {"promotion_eligible": False, "reasons": reasons}
    if usage_scope == STRICT_USAGE_SCOPE:
        if mode != PARENT_PROVENANCE_STRICT:
            raise ValueError("strict dataset requires strict_outer_nested_main_v1")
        if parent.get("promotion_eligible") is not True:
            raise ValueError("strict parent must explicitly be promotion eligible")
        outer = list(parent.get("outer_folds") or [])
        if len(outer) != 3 or {int(row.get("fold_id", -1)) for row in outer} != {0, 1, 2}:
            raise ValueError("strict parent provenance requires outer folds 0,1,2")
        required_true = (
            "outer_val_sessions_excluded_from_parent_fit",
            "meta_train_parent_features_inner_oof",
            "label_dependent_inputs_fold_local",
        )
        for row in outer:
            if not all(row.get(key) is True for key in required_true):
                raise ValueError(
                    f"strict outer fold {row.get('fold_id')} lacks exclusion guarantees"
                )
            for key in ("outer_train_ids_sha256", "outer_val_ids_sha256"):
                value = safe_text(row.get(key))
                if len(value) != 64:
                    raise ValueError(f"strict outer fold provenance lacks {key}")
        return {"promotion_eligible": True, "reasons": []}
    raise ValueError(f"unsupported usage scope: {usage_scope!r}")


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


def load_label_ids(path: str | Path) -> dict[str, int]:
    class_to_id = {name: index for index, name in enumerate(ALL_CLASSES)}
    output: dict[str, int] = {}
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sample_id = safe_text(row.get("id"))
            label = row.get("action")
            if not sample_id or label not in class_to_id:
                raise ValueError(f"invalid label row: {row}")
            if sample_id in output:
                raise ValueError(f"duplicate label id: {sample_id}")
            output[sample_id] = class_to_id[label]
    return output


def _validate_fold_payload(payload: Mapping[str, Any], path: str | Path) -> int:
    if list(payload.get("classes") or []) != ALL_CLASSES:
        raise ValueError(f"class order mismatch in {path}")
    if payload.get("split") != "session_oof":
        raise ValueError(f"fold is not session_oof: {path}")
    if int(payload.get("n_folds", -1)) != 3 or int(payload.get("seed", -1)) != 42:
        raise ValueError(f"fold must use n_folds=3 seed=42: {path}")
    if payload.get("serializer_name") != "current_v1":
        raise ValueError(f"champion parent serializer must be current_v1: {path}")
    class_bias = payload.get("class_bias")
    if class_bias is None or any(float(value) != 0.0 for value in class_bias):
        raise ValueError(f"champion OOF must carry zero class bias: {path}")
    fold_id = int(payload.get("fold_id", -1))
    if fold_id not in (0, 1, 2):
        raise ValueError(f"invalid fold_id in {path}: {fold_id}")
    ids = list(payload.get("ids") or [])
    indices = list(payload.get("indices") or [])
    y_true = list(payload.get("y_true") or [])
    logits = torch.as_tensor(payload.get("logits"))
    if logits.shape != (len(ids), len(ALL_CLASSES)):
        raise ValueError(f"invalid logit shape in {path}: {tuple(logits.shape)}")
    if not (len(ids) == len(indices) == len(y_true)):
        raise ValueError(f"fold ids/indices/y_true differ in {path}")
    if not torch.isfinite(logits.float()).all():
        raise ValueError(f"fold logits contain non-finite values: {path}")
    return fold_id


def build_dataset_payload(
    samples: Sequence[Mapping[str, Any]],
    labels_by_id: Mapping[str, int],
    fold_payloads: Sequence[Mapping[str, Any]],
    *,
    fold_sources: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Stitch exactly three supplied parent folds in canonical train order."""

    if len(fold_payloads) != 3:
        raise ValueError(f"expected exactly three champion OOF folds, got {len(fold_payloads)}")
    ids = [safe_text(sample.get("id")) for sample in samples]
    if any(not sample_id for sample_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("training samples contain empty or duplicate ids")
    if set(labels_by_id) != set(ids):
        missing = sorted(set(ids) - set(labels_by_id))[:5]
        extra = sorted(set(labels_by_id) - set(ids))[:5]
        raise ValueError(f"label/sample ids differ: missing={missing} extra={extra}")

    row_by_id = {sample_id: index for index, sample_id in enumerate(ids)}
    logits = torch.empty((len(ids), len(ALL_CLASSES)), dtype=torch.float32)
    y_true = torch.empty(len(ids), dtype=torch.long)
    full_fold_ids = torch.full((len(ids),), -1, dtype=torch.long)
    seen = torch.zeros(len(ids), dtype=torch.bool)
    seen_folds: set[int] = set()
    parent_configs: set[tuple[Any, ...]] = set()
    for ordinal, payload in enumerate(fold_payloads):
        source_name = (
            safe_text(fold_sources[ordinal].get("path"))
            if fold_sources and ordinal < len(fold_sources)
            else f"fold_payload_{ordinal}"
        )
        fold_id = _validate_fold_payload(payload, source_name)
        if fold_id in seen_folds:
            raise ValueError(f"duplicate champion fold_id: {fold_id}")
        seen_folds.add(fold_id)
        parent_configs.add(
            (
                payload.get("base_model"),
                payload.get("serializer_name"),
                int(payload.get("max_length", -1)),
                int(payload.get("seed", -1)),
            )
        )
        fold_logits = torch.as_tensor(payload["logits"], dtype=torch.float32)
        for local, (sample_id, label, source_index) in enumerate(
            zip(payload["ids"], payload["y_true"], payload["indices"])
        ):
            sample_id = safe_text(sample_id)
            if sample_id not in row_by_id:
                raise ValueError(f"OOF id is absent from train data: {sample_id}")
            row = row_by_id[sample_id]
            if int(source_index) != row:
                raise ValueError(
                    f"OOF train index mismatch for {sample_id}: {source_index} != {row}"
                )
            if bool(seen[row]):
                raise ValueError(f"duplicate OOF id across folds: {sample_id}")
            expected = int(labels_by_id[sample_id])
            if int(label) != expected:
                raise ValueError(f"OOF label mismatch for {sample_id}: {label} != {expected}")
            logits[row] = fold_logits[local]
            y_true[row] = expected
            full_fold_ids[row] = fold_id
            seen[row] = True
    if seen_folds != {0, 1, 2} or not bool(seen.all()):
        raise ValueError(
            f"champion folds do not cover train exactly: folds={seen_folds} "
            f"missing={int((~seen).sum())}"
        )
    if len(parent_configs) != 1:
        raise ValueError(f"champion folds disagree on parent config: {parent_configs}")

    session_fold: dict[str, int] = {}
    for row, sample_id in enumerate(ids):
        session_id = session_id_from_sample_id(sample_id)
        fold = int(full_fold_ids[row])
        prior = session_fold.setdefault(session_id, fold)
        if prior != fold:
            raise ValueError(f"session crosses parent OOF folds: {session_id}")

    route_indices = select_sim_weak4_routes(ids, logits)
    parent_pred = logits.argmax(dim=1)
    route_main = parent_pred[route_indices]
    route_targets = build_recorded_policy_targets(
        y_true[route_indices], route_main
    )
    route_features = main_posterior_features(logits[route_indices])
    parent_config = next(iter(parent_configs))
    return {
        "schema_version": 1,
        "format": DATASET_FORMAT,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "usage_scope": DIAGNOSTIC_USAGE_SCOPE,
        "classes": list(ALL_CLASSES),
        "outputs": list(OUTPUT_NAMES),
        "serializer_name": SERIALIZER_NAME,
        "feature_schema": FEATURE_SCHEMA,
        "model_schema": MODEL_SCHEMA,
        "ids": ids,
        "ids_sha256": ids_sha256(ids),
        "session_ids": [session_id_from_sample_id(value) for value in ids],
        "y_true": y_true,
        "parent_logits": logits,
        "parent_predictions": parent_pred,
        "full_fold_ids": full_fold_ids,
        "n_folds": 3,
        "fold_seed": 42,
        "route_indices": route_indices,
        "route_ids": [ids[row] for row in route_indices.tolist()],
        "route_fold_ids": full_fold_ids[route_indices],
        "route_main_predictions": route_main,
        "numeric_features": route_features,
        "targets": route_targets,
        "parent": {
            "base_model": parent_config[0],
            "serializer_name": parent_config[1],
            "max_length": parent_config[2],
            "split": "session_oof",
            "seed": parent_config[3],
            "class_bias_applied": False,
            "fold_sources": list(fold_sources or []),
            "provenance_mode": PARENT_PROVENANCE_STITCHED,
            "promotion_eligible": False,
            "ineligibility_reasons": [
                "level2_cross_fold_parent_training_path",
                "full_data_kd_or_consensus_label_path_not_excluded",
            ],
        },
        "contract": {
            "route": "sample id starts sess_sim_ and raw parent argmax is class 0..3",
            "target": "KEEP when y==main or y>=4; otherwise y+1",
            "text": "<SIM_POLICY> newline <P>current_prompt token head/tail cap</P> newline <A>all assistant action names in order, or none</A>",
            "numeric": "row-z-normalized p14: q4 conditional + weak mass + q4 top1/top2 margin + topWeak/bestNonWeak margin",
            "selection": "direct five-way argmax; no threshold or outer-fold tuning",
        },
    }


def validate_dataset(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("format") != DATASET_FORMAT:
        raise ValueError(f"dataset format must be {DATASET_FORMAT!r}")
    usage_scope = payload.get("usage_scope")
    if usage_scope not in SUPPORTED_USAGE_SCOPES:
        raise ValueError(f"unsupported dataset usage scope: {usage_scope!r}")
    validate_parent_provenance(payload.get("parent") or {}, safe_text(usage_scope))
    if list(payload.get("classes") or []) != ALL_CLASSES:
        raise ValueError("dataset class order is not canonical")
    if tuple(payload.get("outputs") or ()) != OUTPUT_NAMES:
        raise ValueError("dataset specialist output order is invalid")
    if payload.get("serializer_name") != SERIALIZER_NAME:
        raise ValueError("dataset serializer is invalid")
    if payload.get("feature_schema") != FEATURE_SCHEMA:
        raise ValueError("dataset feature schema is invalid")
    ids = list(payload.get("ids") or [])
    rows = len(ids)
    routes = len(payload.get("route_ids") or [])
    if not ids or any(not safe_text(value) for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("dataset ids are empty or duplicated")
    if payload.get("ids_sha256") != ids_sha256(ids):
        raise ValueError("dataset ids_sha256 does not match ids")
    required_full = ("session_ids", "y_true", "parent_predictions", "full_fold_ids")
    for key in required_full:
        value = payload.get(key)
        if value is None or len(value) != rows:
            raise ValueError(f"dataset {key} has the wrong row count")
    parent_logits = torch.as_tensor(payload.get("parent_logits"), dtype=torch.float32)
    if parent_logits.shape != (rows, len(ALL_CLASSES)):
        raise ValueError("dataset parent_logits shape is invalid")
    if not torch.isfinite(parent_logits).all():
        raise ValueError("dataset parent_logits contains non-finite values")
    truth = torch.as_tensor(payload.get("y_true"), dtype=torch.long).view(-1)
    if len(truth) != rows or not bool(((truth >= 0) & (truth < len(ALL_CLASSES))).all()):
        raise ValueError("dataset y_true is invalid")
    required_route = (
        "route_indices", "route_ids", "route_fold_ids", "route_main_predictions", "targets"
    )
    for key in required_route:
        value = payload.get(key)
        if value is None or len(value) != routes:
            raise ValueError(f"dataset {key} has the wrong route count")
    numeric = torch.as_tensor(payload.get("numeric_features"), dtype=torch.float32)
    if numeric.shape != (routes, 7):
        raise ValueError("dataset numeric_features shape is invalid")
    if not torch.isfinite(numeric).all():
        raise ValueError("dataset numeric_features contains non-finite values")
    indices = torch.as_tensor(payload["route_indices"], dtype=torch.long)
    if routes and (int(indices.min()) < 0 or int(indices.max()) >= rows):
        raise ValueError("dataset route index is out of range")
    expected_ids = [payload["ids"][row] for row in indices.tolist()]
    if expected_ids != list(payload["route_ids"]):
        raise ValueError("dataset route ids are misaligned")
    expected_indices = select_sim_weak4_routes(ids, parent_logits)
    if not torch.equal(indices, expected_indices):
        raise ValueError("dataset route indices do not reproduce the locked route")
    parent_pred = parent_logits.argmax(dim=1)
    if not torch.equal(parent_pred, torch.as_tensor(payload["parent_predictions"])):
        raise ValueError("dataset parent predictions do not match logits")
    if routes and not bool((parent_pred[indices] < 4).all()):
        raise ValueError("dataset route contains a non-Weak4 parent prediction")
    if any(not safe_text(value).startswith("sess_sim_") for value in payload["route_ids"]):
        raise ValueError("dataset route contains a non-SIM id")
    if int(payload.get("n_folds", -1)) != 3 or int(payload.get("fold_seed", -1)) != 42:
        raise ValueError("dataset fold contract is invalid")
    folds = torch.as_tensor(payload["full_fold_ids"], dtype=torch.long)
    if set(folds.tolist()) != {0, 1, 2}:
        raise ValueError("dataset does not contain all three folds")
    session_folds: dict[str, int] = {}
    for session, fold in zip(payload["session_ids"], folds.tolist()):
        prior = session_folds.setdefault(safe_text(session), int(fold))
        if prior != int(fold):
            raise ValueError(f"dataset session crosses folds: {session}")
    expected_sessions = [session_id_from_sample_id(value) for value in ids]
    if list(payload["session_ids"]) != expected_sessions:
        raise ValueError("dataset session ids do not match sample ids")
    if not torch.equal(
        torch.as_tensor(payload["route_fold_ids"], dtype=torch.long), folds[indices]
    ):
        raise ValueError("dataset route fold ids do not match full folds")
    route_main = parent_pred[indices]
    if not torch.equal(
        torch.as_tensor(payload["route_main_predictions"], dtype=torch.long), route_main
    ):
        raise ValueError("dataset route main predictions do not match parent logits")
    targets = torch.as_tensor(payload["targets"], dtype=torch.long)
    if routes and not bool(((targets >= 0) & (targets < len(OUTPUT_NAMES))).all()):
        raise ValueError("dataset contains an invalid target")
    expected_targets = build_recorded_policy_targets(truth[indices], route_main)
    if not torch.equal(targets, expected_targets):
        raise ValueError("dataset targets do not reproduce the locked target contract")
    expected_numeric = main_posterior_features(parent_logits[indices])
    # Softmax kernels can differ by a few float32 ULPs across the local and
    # Colab torch builds.  Keep this strict enough to catch stale/misaligned
    # features without requiring cross-version bit identity.
    if not torch.allclose(numeric, expected_numeric, atol=1e-6, rtol=1e-6):
        raise ValueError("dataset numeric features do not reproduce the locked 7d contract")
    return dict(payload)


def dataset_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    validate_dataset(payload)
    full_folds = torch.as_tensor(payload["full_fold_ids"], dtype=torch.long)
    route_folds = torch.as_tensor(payload["route_fold_ids"], dtype=torch.long)
    return {
        "format": payload["format"],
        "usage_scope": payload["usage_scope"],
        "rows": len(payload["ids"]),
        "sim_rows": sum(safe_text(value).startswith("sess_sim_") for value in payload["ids"]),
        "sessions": len(set(payload["session_ids"])),
        "route_rows": len(payload["route_ids"]),
        "route_fraction": len(payload["route_ids"]) / max(1, len(payload["ids"])),
        "target_histogram": target_histogram(payload["targets"]),
        "fold_rows": {
            str(fold): int((full_folds == fold).sum()) for fold in range(3)
        },
        "fold_route_rows": {
            str(fold): int((route_folds == fold).sum()) for fold in range(3)
        },
        "ids_sha256": payload["ids_sha256"],
        "parent": payload["parent"],
        "contract": payload["contract"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument(
        "--oof-glob",
        default="/tmp/champ_oof_3fold/champ_oof_f*_val_logits.pt",
    )
    parser.add_argument(
        "--output",
        default="experiments/artifacts/20260713_sim_policy_decoder_dataset.pt",
    )
    parser.add_argument(
        "--summary-json",
        default="experiments/artifacts/20260713_sim_policy_decoder_dataset.json",
    )
    parser.add_argument(
        "--allow-unlocked-oof",
        action="store_true",
        help="allow a separately named diagnostic built from non-locked parent fold hashes",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = sorted(Path(value) for value in glob.glob(args.oof_glob))
    if len(paths) != 3:
        raise ValueError(f"expected exactly three OOF files, found {len(paths)}: {paths}")
    data_dir = Path(args.data_dir)
    samples = load_jsonl(data_dir / "train.jsonl")
    labels = load_label_ids(data_dir / "train_labels.csv")
    folds = [torch_load(path) for path in paths]
    sources = [
        {"path": str(path), "sha256": sha256_file(path)} for path in paths
    ]
    if not args.allow_unlocked_oof:
        for fold, source in zip(folds, sources):
            fold_id = _validate_fold_payload(fold, source["path"])
            expected = LOCKED_CHAMPION_OOF_SHA256[fold_id]
            if source["sha256"] != expected:
                raise ValueError(
                    "champion OOF hash mismatch for fold "
                    f"{fold_id}: {source['sha256']} != {expected}; use "
                    "--allow-unlocked-oof only for a separately named diagnostic"
                )
    payload = build_dataset_payload(samples, labels, folds, fold_sources=sources)
    validate_dataset(payload)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, output)
    summary = dataset_summary(payload)
    summary.update(dataset_path=str(output), dataset_sha256=sha256_file(output))
    summary_path = Path(args.summary_json)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
