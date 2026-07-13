#!/usr/bin/env python3
"""Build and audit a fail-closed fixed-screen ``c_clean`` panel.

This asset is deliberately narrower than the full-data OOF consensus used by
the refit sieve.  Every voter must be a direct ``split=session, seed=42``
screen whose raw logits cover exactly the same deterministic held-out sessions.
The builder verifies the logit payload, its immutable SHA, the matching
``experiments/results.csv`` training command, and the metrics sidecar before it
counts official-label-correct voters.

The output is construction/audit evidence only.  It is intentionally not a
Card B target and must not be consumed by a residual trainer without a new,
explicit promotion decision.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shlex
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import torch

from audit_weak4_residual_surfaces import (
    CLEAN_FIXED_SESSION,
    classify_surface,
    load_training_contract,
    sha256_file,
    torch_load,
    validate_parent_payload,
)
from script import ALL_CLASSES
from train import f1_metrics, session_id, split_indices


SCHEMA_VERSION = 1
ARTIFACT_KIND = "weak4_c_clean_fixed_panel"
USAGE_SCOPE = "construction_only_not_connected_to_card_b"
EXPECTED_FIXED_ROWS = 14_001
EXPECTED_SPLIT = "session"
EXPECTED_SEED = 42
WEAK4_COUNT = 4
DEFAULT_CARD_B_DATASET = (
    "experiments/artifacts/20260712_weak4_pair_fixed_parent_dataset.pt"
)
DEFAULT_CARD_B_DATASET_SHA256 = (
    "cc287d8e05b3782e98e956ed010471a3f95262a618c0ea873f962f1b8fd465e9"
)
CARD_B_TARGET_NAMES = ("protect", "rescue", "noisy_alt", "outside")


class CleanPanelAuditError(ValueError):
    """Raised when a voter or constructed panel cannot prove its provenance."""


@dataclass(frozen=True)
class VoterSpec:
    name: str
    experiment_id: str
    logits_path: str
    logits_sha256: str
    metrics_path: str
    metrics_sha256: str
    base_model: str
    serializer: str
    max_length: int
    split: str = EXPECTED_SPLIT
    seed: int = EXPECTED_SEED


DEFAULT_VOTER_SPECS: tuple[VoterSpec, ...] = (
    VoterSpec(
        name="m7_qwen3_06b_v1",
        experiment_id=(
            "20260704_141516_gpu_transformer_session_current_v1_len416_"
            "replay-last1_m7_qwen3_06b_len416_focal_ep3_s42"
        ),
        logits_path=(
            "experiments/logits/20260704_141516_gpu_transformer_session_"
            "current_v1_len416_replay-last1_m7_qwen3_06b_len416_focal_"
            "ep3_s42_val_logits.pt"
        ),
        logits_sha256="9883833a6ca9fe95de5e1995a8f745810a1a8678437bd768204585f3f5c81dad",
        metrics_path=(
            "experiments/artifacts/20260704_141516_gpu_transformer_session_"
            "current_v1_len416_replay-last1_m7_qwen3_06b_len416_focal_"
            "ep3_s42_metrics.json"
        ),
        metrics_sha256="f9fe5ad8cf01a1a7c39794b310f0251d14add2cfe0664470b08ee8eb60eba94c",
        base_model="Qwen/Qwen3-0.6B",
        serializer="current_v1",
        max_length=416,
    ),
    VoterSpec(
        name="m8_qwen35_08b_v1_ep5",
        experiment_id=(
            "20260705_044047_gpu_transformer_session_current_v1_len400_"
            "replay-last1_m8_qwen35_08b_screen"
        ),
        logits_path=(
            "experiments/logits/20260705_044047_gpu_transformer_session_"
            "current_v1_len400_replay-last1_m8_qwen35_08b_screen_val_logits.pt"
        ),
        logits_sha256="1ec8645b06c5300470ad8911f561a5f135519a81a07475f2a9265eea8c11a140",
        metrics_path=(
            "experiments/artifacts/20260705_044047_gpu_transformer_session_"
            "current_v1_len400_replay-last1_m8_qwen35_08b_screen_metrics.json"
        ),
        metrics_sha256="d2e4f8cfc6c837b7655a833585cec0d091d3cba2cf03c97ba70d939d73e8346b",
        base_model="igorktech/Qwen3.5-0.8B-Base-LM",
        serializer="current_v1",
        max_length=400,
    ),
    VoterSpec(
        name="v6_qwen3_06b",
        experiment_id=(
            "20260706_070504_gpu_transformer_session_current_v6_len384_"
            "replay-last1_v6_qwen3_06b_screen"
        ),
        logits_path=(
            "experiments/logits/20260706_070504_gpu_transformer_session_"
            "current_v6_len384_replay-last1_v6_qwen3_06b_screen_val_logits.pt"
        ),
        logits_sha256="0b08f587e49f8627ee3b0f2bd0c946b833099790829a0c93ca212e0a2d303437",
        metrics_path=(
            "experiments/artifacts/20260706_070504_gpu_transformer_session_"
            "current_v6_len384_replay-last1_v6_qwen3_06b_screen_metrics.json"
        ),
        metrics_sha256="95b31956db2c9a21f9f56c30398e364182de3aac69d6b7f8ac4c81045e6e9c19",
        base_model="Qwen/Qwen3-0.6B",
        serializer="current_v6",
        max_length=384,
    ),
    VoterSpec(
        name="xlmr_base_v1_focal448",
        experiment_id=(
            "20260703_170302_gpu_transformer_session_current_v1_len448_"
            "replay-last1_m2_focal_len448_ep5_replay_last1_seed42"
        ),
        logits_path=(
            "experiments/logits/20260703_170302_gpu_transformer_session_"
            "current_v1_len448_replay-last1_m2_focal_len448_ep5_replay_"
            "last1_seed42_val_logits.pt"
        ),
        logits_sha256="6ec8f3cee263bf4b340fbef4920d21cde9a7902b8f5210c8770ae916299fd731",
        metrics_path=(
            "experiments/artifacts/20260703_170302_gpu_transformer_session_"
            "current_v1_len448_replay-last1_m2_focal_len448_ep5_replay_"
            "last1_seed42_metrics.json"
        ),
        metrics_sha256="6cc3899e7eba199bf5d3f2a57d64a0d6460bb52f62061c1e9360e0123928ca48",
        base_model="xlm-roberta-base",
        serializer="current_v1",
        max_length=448,
    ),
    VoterSpec(
        name="xlmr_large_v1_focal448",
        experiment_id=(
            "20260704_050057_gpu_transformer_session_current_v1_len448_"
            "replay-last1_m4_large_len448_focal_ep5_replay_last1_s42_valmo"
        ),
        logits_path=(
            "experiments/logits/20260704_050057_gpu_transformer_session_"
            "current_v1_len448_replay-last1_m4_large_len448_focal_ep5_"
            "replay_last1_s42_valmo_val_logits.pt"
        ),
        logits_sha256="ec9343d5bb281fd3baf1490b8d5eccb0dd48f63e4968feab2b0fb894f902504e",
        metrics_path=(
            "experiments/artifacts/20260704_050057_gpu_transformer_session_"
            "current_v1_len448_replay-last1_m4_large_len448_focal_ep5_"
            "replay_last1_s42_valmo_metrics.json"
        ),
        metrics_sha256="3acc1237cf8c3827d9f2ada05041b36e60bff42a275cf7559e7b7fd6da4c13ef",
        base_model="xlm-roberta-large",
        serializer="current_v1",
        max_length=448,
    ),
)


# These two attractive assets are deliberately rejected by the default audit.
# Their predictions remain useful diagnostics, but the retained repository
# evidence does not close the direct-fit lineage as tightly as the five voters
# above.
DEFAULT_EXCLUSIONS = (
    {
        "name": "m8_qwen35_08b_v1_ep3_eval",
        "path": (
            "experiments/logits/20260705_044826_gpu_transformer_session_"
            "current_v1_len400_replay-last1_m8_qwen35_08b_ep3_eval_val_logits.pt"
        ),
        "sha256": "cb2b2bfdabab54aa989026c48c4606c340a23cbed3323300bfd71db047db617a",
        "reason": (
            "eval-only epochs=0 payload; the preserved ep3 checkpoint has no retained "
            "checkpoint SHA/metadata that cryptographically ties it to the direct fixed fit"
        ),
    },
    {
        "name": "hcx05b_handoff_fixed_screen",
        "path": "experiments/logits/20260707_hcx05b_len384_screen_seed42_val_logits.pt",
        "sha256": "d9a0b88e99f6339fa56483ef0453b546890c3ade9ef6dcee686334ec52f8bb09",
        "reason": (
            "absorbed handoff; results.csv lacks the original screen train command and "
            "the retained recipe command describes final refit rather than the screen fit"
        ),
    },
)


FORBIDDEN_TRAIN_OPTIONS = frozenset(
    {
        "--class-bias-artifact",
        "--consensus-reliability",
        "--distill-logits",
        "--final-model",
        "--final-only",
        "--resume-from",
        "--rule-boosts-path",
        "--warm-start-specialist",
    }
)


def _resolve(repo_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def _sha256_lines(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _command_sha256(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def _read_results_rows(path: Path) -> dict[str, list[dict[str, str]]]:
    if not path.is_file():
        raise CleanPanelAuditError(f"missing results ledger: {path}")
    # The historical ledger contains a few duplicated early experiment IDs.
    # Do not let unrelated legacy rows block the audit, but require each voter
    # selected below to resolve to exactly one row.
    rows: dict[str, list[dict[str, str]]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not {
            "experiment_id",
            "base_model",
            "serializer_name",
            "split_type",
            "seed",
            "max_length",
            "epochs",
            "fold_id",
            "val_logits_path",
            "macro_f1_raw",
            "train_command",
        }.issubset(reader.fieldnames):
            raise CleanPanelAuditError(f"{path}: results ledger schema is incomplete")
        for line_no, row in enumerate(reader, 2):
            experiment_id = row.get("experiment_id", "")
            if not experiment_id:
                raise CleanPanelAuditError(f"{path}:{line_no}: missing experiment_id")
            rows.setdefault(experiment_id, []).append(row)
    return rows


def _parse_command(command: str, *, voter_name: str) -> tuple[list[str], dict[str, list[str | None]]]:
    if not command.strip():
        raise CleanPanelAuditError(f"{voter_name}: results row has no train_command")
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise CleanPanelAuditError(f"{voter_name}: invalid train_command quoting") from exc
    if not tokens or Path(tokens[0]).name != "train_transformer.py":
        raise CleanPanelAuditError(
            f"{voter_name}: direct train_transformer.py command required, got {tokens[:2]}"
        )
    options: dict[str, list[str | None]] = {}
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("--"):
            index += 1
            continue
        value: str | None = None
        if index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
            value = tokens[index + 1]
            index += 1
        options.setdefault(token, []).append(value)
        index += 1
    return tokens, options


def _required_option(
    options: dict[str, list[str | None]], option: str, expected: str, voter_name: str
) -> None:
    actual = options.get(option, [])
    if actual != [expected]:
        raise CleanPanelAuditError(
            f"{voter_name}: command requires exactly {option} {expected!r}, got {actual}"
        )


def _validate_results_provenance(
    spec: VoterSpec,
    row: dict[str, str],
    *,
    repo_root: Path,
    results_csv: Path,
) -> dict:
    expected_fields = {
        "model_family": "torch_gpu_transformer",
        "base_model": spec.base_model,
        "serializer_name": spec.serializer,
        "split_type": spec.split,
        "seed": str(spec.seed),
        "fold_id": "",
        "max_length": str(spec.max_length),
        "val_logits_path": spec.logits_path,
    }
    for field, expected in expected_fields.items():
        actual = row.get(field, "")
        if actual != expected:
            raise CleanPanelAuditError(
                f"{spec.name}: results {field} mismatch expected={expected!r} actual={actual!r}"
            )
    try:
        epochs = int(row.get("epochs", ""))
    except ValueError as exc:
        raise CleanPanelAuditError(f"{spec.name}: invalid results epochs") from exc
    if epochs < 1:
        raise CleanPanelAuditError(
            f"{spec.name}: direct fitted screen required; epochs={epochs} is eval-only"
        )

    command = row.get("train_command", "")
    _, options = _parse_command(command, voter_name=spec.name)
    present_forbidden = sorted(FORBIDDEN_TRAIN_OPTIONS.intersection(options))
    if present_forbidden:
        raise CleanPanelAuditError(
            f"{spec.name}: forbidden cross-path/post-fit command options {present_forbidden}"
        )
    _required_option(options, "--base-model", spec.base_model, spec.name)
    _required_option(options, "--serializer", spec.serializer, spec.name)
    _required_option(options, "--split", spec.split, spec.name)
    _required_option(options, "--seed", str(spec.seed), spec.name)
    _required_option(options, "--max-length", str(spec.max_length), spec.name)
    if options.get("--epochs") != [str(epochs)]:
        raise CleanPanelAuditError(
            f"{spec.name}: command/results epochs mismatch {options.get('--epochs')} vs {epochs}"
        )
    if spec.split != EXPECTED_SPLIT or "session_oof" in command:
        raise CleanPanelAuditError(f"{spec.name}: cross-fold/session_oof path is forbidden")

    return {
        "results_ledger": str(results_csv.relative_to(repo_root)),
        "train_command": command,
        "train_command_sha256": _command_sha256(command),
        "epochs": epochs,
        "model_family": row.get("model_family"),
        "replay_mode": row.get("replay_mode"),
        "replay_size": row.get("replay_size"),
    }


def _validate_metrics_sidecar(
    spec: VoterSpec,
    *,
    repo_root: Path,
    raw_macro_f1: float,
    expected_rows: int | None,
) -> dict:
    path = _resolve(repo_root, spec.metrics_path)
    if not path.is_file():
        raise CleanPanelAuditError(f"{spec.name}: missing metrics sidecar {path}")
    actual_sha = sha256_file(path)
    if actual_sha != spec.metrics_sha256:
        raise CleanPanelAuditError(
            f"{spec.name}: metrics SHA mismatch expected={spec.metrics_sha256} actual={actual_sha}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CleanPanelAuditError(f"{spec.name}: invalid metrics sidecar {path}") from exc
    expected_values = {
        "experiment_id": spec.experiment_id,
        "serializer_name": spec.serializer,
        "val_logits_path": spec.logits_path,
    }
    for field, expected in expected_values.items():
        if payload.get(field) != expected:
            raise CleanPanelAuditError(
                f"{spec.name}: metrics {field} mismatch expected={expected!r} "
                f"actual={payload.get(field)!r}"
            )
    validation_rows = int(payload.get("validation_rows", -1))
    full_validation_rows = int(payload.get("full_validation_rows", -1))
    if expected_rows is not None and (
        validation_rows != expected_rows or full_validation_rows != expected_rows
    ):
        raise CleanPanelAuditError(
            f"{spec.name}: metrics validation rows mismatch "
            f"{validation_rows}/{full_validation_rows} != {expected_rows}"
        )
    sidecar_macro = float((payload.get("raw_metrics") or {}).get("macro_f1", float("nan")))
    if not torch.isfinite(torch.tensor(sidecar_macro)) or abs(sidecar_macro - raw_macro_f1) > 1e-12:
        raise CleanPanelAuditError(
            f"{spec.name}: sidecar raw macro mismatch {sidecar_macro} != {raw_macro_f1}"
        )
    return {
        "path": spec.metrics_path,
        "sha256": actual_sha,
        "validation_rows": validation_rows,
        "full_validation_rows": full_validation_rows,
    }


def _validate_voter(
    spec: VoterSpec,
    *,
    repo_root: Path,
    training: dict,
    results_rows: dict[str, list[dict[str, str]]],
    results_csv: Path,
    expected_rows: int | None,
) -> dict:
    path = _resolve(repo_root, spec.logits_path)
    if not path.is_file():
        raise CleanPanelAuditError(f"{spec.name}: missing logits payload {path}")
    actual_sha = sha256_file(path)
    if actual_sha != spec.logits_sha256:
        raise CleanPanelAuditError(
            f"{spec.name}: logits SHA mismatch expected={spec.logits_sha256} actual={actual_sha}"
        )
    matching_rows = results_rows.get(spec.experiment_id, [])
    if not matching_rows:
        raise CleanPanelAuditError(
            f"{spec.name}: experiment {spec.experiment_id!r} absent from results ledger"
        )
    if len(matching_rows) != 1:
        raise CleanPanelAuditError(
            f"{spec.name}: experiment {spec.experiment_id!r} resolves to "
            f"{len(matching_rows)} ledger rows"
        )
    row = matching_rows[0]
    results_evidence = _validate_results_provenance(
        spec,
        row,
        repo_root=repo_root,
        results_csv=results_csv,
    )

    payload = torch_load(path)
    validated = validate_parent_payload(payload, path, training)
    surface = classify_surface(
        validated,
        training,
        path,
        expected_surface=CLEAN_FIXED_SESSION,
    )
    if expected_rows is not None and len(validated["ids"]) != expected_rows:
        raise CleanPanelAuditError(
            f"{spec.name}: fixed rows={len(validated['ids'])} expected={expected_rows}"
        )
    payload_expected = {
        "base_model": spec.base_model,
        "serializer_name": spec.serializer,
        "split": spec.split,
        "seed": spec.seed,
        "max_length": spec.max_length,
        "fold_id": None,
        "n_folds": None,
    }
    for field, expected in payload_expected.items():
        actual = payload.get(field)
        if actual != expected:
            raise CleanPanelAuditError(
                f"{spec.name}: payload {field} mismatch expected={expected!r} actual={actual!r}"
            )

    predictions = validated["logits"].argmax(dim=1).to(torch.uint8)
    y_true = validated["y_true"]
    raw_metrics = f1_metrics(y_true.tolist(), predictions.tolist())
    payload_macro = float((payload.get("raw_metrics") or {}).get("macro_f1", float("nan")))
    if not torch.isfinite(torch.tensor(payload_macro)) or abs(
        payload_macro - raw_metrics["macro_f1"]
    ) > 1e-12:
        raise CleanPanelAuditError(
            f"{spec.name}: payload raw_metrics disagrees with raw logits"
        )
    try:
        ledger_macro = float(row.get("macro_f1_raw", ""))
    except ValueError as exc:
        raise CleanPanelAuditError(f"{spec.name}: invalid ledger raw macro") from exc
    if abs(ledger_macro - raw_metrics["macro_f1"]) > 5e-7:
        raise CleanPanelAuditError(
            f"{spec.name}: rounded ledger raw macro disagrees with raw logits"
        )
    metrics_evidence = _validate_metrics_sidecar(
        spec,
        repo_root=repo_root,
        raw_macro_f1=raw_metrics["macro_f1"],
        expected_rows=expected_rows,
    )
    return {
        "spec": spec,
        "path": path,
        "payload": payload,
        "ids": validated["ids"],
        "y_true": y_true,
        "predictions": predictions,
        "raw_macro_f1": raw_metrics["macro_f1"],
        "source_logit_dtype": validated["source_logit_dtype"],
        "surface": surface,
        "results_evidence": results_evidence,
        "metrics_evidence": metrics_evidence,
    }


def build_card_b_route_overlay_audit(
    panel: dict,
    dataset_path: Path,
    *,
    repo_root: Path,
    expected_sha256: str | None = None,
    expected_rescue_c0: int | None = None,
    expected_rescue_c_gt0: int | None = None,
) -> dict:
    """Overlay ``c_clean`` on Card B route kinds without creating a target.

    The Card B dataset is used only for its already-frozen route membership and
    protect/rescue/outside definitions.  This function does not return a tensor
    intended for training and it verifies the kinds again from official labels,
    main predictions, and pair alternatives before reporting histograms.
    """

    dataset_path = dataset_path if dataset_path.is_absolute() else repo_root / dataset_path
    if not dataset_path.is_file():
        raise CleanPanelAuditError(f"missing Card B dataset: {dataset_path}")
    actual_sha = sha256_file(dataset_path)
    if expected_sha256 is not None and actual_sha != expected_sha256:
        raise CleanPanelAuditError(
            "Card B dataset SHA mismatch "
            f"expected={expected_sha256} actual={actual_sha}"
        )
    dataset = torch_load(dataset_path)
    if not isinstance(dataset, dict):
        raise CleanPanelAuditError("Card B dataset must be a dict")
    expected_contract = {
        "kind": "weak4_live_pair_residual_dataset",
        "usage_scope": "specialist_clean_cv",
        "validation_scope": "clean_fixed_parent_session_cv",
        "classes": list(ALL_CLASSES),
        "parent_split": EXPECTED_SPLIT,
        "parent_seed": EXPECTED_SEED,
    }
    for field, expected in expected_contract.items():
        if dataset.get(field) != expected:
            raise CleanPanelAuditError(
                f"Card B {field} mismatch expected={expected!r} actual={dataset.get(field)!r}"
            )

    ids = list(dataset.get("ids") or [])
    route_ids = list(dataset.get("route_ids") or [])
    if not ids or len(ids) != len(set(ids)):
        raise CleanPanelAuditError("Card B ids are empty or duplicated")
    if len(route_ids) != len(set(route_ids)):
        raise CleanPanelAuditError("Card B route_ids are duplicated")
    panel_ids = list(panel.get("ids") or [])
    if set(ids) != set(panel_ids):
        raise CleanPanelAuditError("Card B IDs do not match the clean fixed panel")
    panel_y_by_id = {
        sample_id: int(label)
        for sample_id, label in zip(panel_ids, torch.as_tensor(panel["y_true"]).tolist())
    }
    dataset_y = torch.as_tensor(dataset.get("y_true"), dtype=torch.long).view(-1)
    if len(dataset_y) != len(ids):
        raise CleanPanelAuditError("Card B y_true length does not match ids")
    label_mismatches = [
        sample_id
        for sample_id, label in zip(ids, dataset_y.tolist())
        if panel_y_by_id[sample_id] != int(label)
    ]
    if label_mismatches:
        raise CleanPanelAuditError(
            f"Card B official labels disagree with panel: {label_mismatches[:5]}"
        )

    route_indices = torch.as_tensor(dataset.get("route_indices"), dtype=torch.long).view(-1)
    kinds = torch.as_tensor(dataset.get("target_kinds"), dtype=torch.long).view(-1)
    main = torch.as_tensor(dataset.get("main_predictions"), dtype=torch.long).view(-1)
    alternative = torch.as_tensor(
        dataset.get("alternative_predictions"), dtype=torch.long
    ).view(-1)
    route_rows = len(route_ids)
    if any(len(value) != route_rows for value in (route_indices, kinds, main, alternative)):
        raise CleanPanelAuditError("Card B routed tensor lengths do not match route_ids")
    if bool(((route_indices < 0) | (route_indices >= len(ids))).any()):
        raise CleanPanelAuditError("Card B route_indices are out of range")
    aligned_route_ids = [ids[index] for index in route_indices.tolist()]
    if aligned_route_ids != route_ids:
        raise CleanPanelAuditError("Card B route_indices are not aligned to route_ids")
    if bool(((main < 0) | (main >= WEAK4_COUNT)).any()) or bool(
        ((alternative < 0) | (alternative >= WEAK4_COUNT)).any()
    ):
        raise CleanPanelAuditError("Card B routed pair contains a non-Weak4 class")
    if bool((main == alternative).any()):
        raise CleanPanelAuditError("Card B routed main and alternative classes must differ")

    route_y = dataset_y[route_indices]
    expected_kinds = torch.full_like(route_y, 3)
    expected_kinds[route_y == main] = 0
    expected_kinds[route_y == alternative] = 1
    kind_mismatch = torch.where(kinds != expected_kinds)[0]
    if len(kind_mismatch):
        examples = [route_ids[int(index)] for index in kind_mismatch[:5]]
        raise CleanPanelAuditError(
            f"Card B target kinds disagree with official route semantics: {examples}"
        )

    c_by_id = {
        sample_id: int(value)
        for sample_id, value in zip(panel_ids, torch.as_tensor(panel["c_clean"]).tolist())
    }
    by_kind = {}
    for kind, name in enumerate(CARD_B_TARGET_NAMES):
        values = [
            c_by_id[sample_id]
            for sample_id, actual_kind in zip(route_ids, kinds.tolist())
            if actual_kind == kind
        ]
        histogram = Counter(values)
        by_kind[name] = {
            "rows": len(values),
            "c_clean_histogram": {
                str(value): int(histogram.get(value, 0))
                for value in range(int(panel["voter_count"]) + 1)
            },
            "c0_rows": int(histogram.get(0, 0)),
            "c_gt0_rows": len(values) - int(histogram.get(0, 0)),
        }
    rescue = by_kind["rescue"]
    if expected_rescue_c0 is not None and rescue["c0_rows"] != expected_rescue_c0:
        raise CleanPanelAuditError(
            f"Card B rescue c0 changed: {rescue['c0_rows']} != {expected_rescue_c0}"
        )
    if expected_rescue_c_gt0 is not None and rescue["c_gt0_rows"] != expected_rescue_c_gt0:
        raise CleanPanelAuditError(
            "Card B rescue c>0 changed: "
            f"{rescue['c_gt0_rows']} != {expected_rescue_c_gt0}"
        )
    provenance = dataset.get("provenance") or {}
    return {
        "usage_scope": "diagnostic_overlay_only_not_a_training_target",
        "training_consumption_forbidden": True,
        "card_b_connection": False,
        "dataset_path": str(dataset_path.relative_to(repo_root)),
        "dataset_sha256": actual_sha,
        "dataset_kind": dataset.get("kind"),
        "dataset_usage_scope": dataset.get("usage_scope"),
        "parent_path": provenance.get("parent_path"),
        "parent_sha256": provenance.get("parent_sha256"),
        "route_rows": route_rows,
        "definitions": {
            "protect": "official y equals the routed main prediction",
            "rescue": "official y equals the routed alternative prediction",
            "noisy_alt": "not present in the frozen clean Card B dataset",
            "outside": "official y is outside the routed Weak4 pair",
        },
        "by_target_kind": by_kind,
        "requested_rescue_check": {
            "expected_c0_rows": expected_rescue_c0,
            "actual_c0_rows": rescue["c0_rows"],
            "expected_c_gt0_rows": expected_rescue_c_gt0,
            "actual_c_gt0_rows": rescue["c_gt0_rows"],
            "passed": (
                (expected_rescue_c0 is None or rescue["c0_rows"] == expected_rescue_c0)
                and (
                    expected_rescue_c_gt0 is None
                    or rescue["c_gt0_rows"] == expected_rescue_c_gt0
                )
            ),
        },
    }


def build_clean_panel(
    *,
    repo_root: Path,
    data_dir: Path,
    results_csv: Path,
    voter_specs: Sequence[VoterSpec],
    expected_rows: int | None = EXPECTED_FIXED_ROWS,
    card_b_dataset: Path | None = None,
    expected_card_b_sha256: str | None = None,
    expected_rescue_c0: int | None = None,
    expected_rescue_c_gt0: int | None = None,
) -> dict:
    """Validate voters and return an in-memory construction-only panel."""

    repo_root = repo_root.resolve()
    data_dir = data_dir if data_dir.is_absolute() else repo_root / data_dir
    results_csv = results_csv if results_csv.is_absolute() else repo_root / results_csv
    if not voter_specs:
        raise CleanPanelAuditError("at least one fixed-screen voter is required")
    names = [spec.name for spec in voter_specs]
    if len(names) != len(set(names)):
        raise CleanPanelAuditError("voter names must be unique")
    experiments = [spec.experiment_id for spec in voter_specs]
    if len(experiments) != len(set(experiments)):
        raise CleanPanelAuditError("voter experiment IDs must be unique")

    training = load_training_contract(data_dir)
    results_rows = _read_results_rows(results_csv)
    validated_voters = [
        _validate_voter(
            spec,
            repo_root=repo_root,
            training=training,
            results_rows=results_rows,
            results_csv=results_csv,
            expected_rows=expected_rows,
        )
        for spec in voter_specs
    ]

    train_idx, val_idx = split_indices(
        training["samples"], training["y"], EXPECTED_SPLIT, EXPECTED_SEED
    )
    fixed_val_ids = sorted(training["ids"][index] for index in val_idx)
    fixed_train_ids = sorted(training["ids"][index] for index in train_idx)
    if expected_rows is not None and len(fixed_val_ids) != expected_rows:
        raise CleanPanelAuditError(
            f"deterministic fixed split rows={len(fixed_val_ids)} expected={expected_rows}"
        )
    fixed_val_set = set(fixed_val_ids)
    fixed_train_set = set(fixed_train_ids)
    if fixed_val_set & fixed_train_set:
        raise CleanPanelAuditError("deterministic fixed train/validation IDs overlap")
    train_sessions = {session_id(sample_id) for sample_id in fixed_train_ids}
    val_sessions = {session_id(sample_id) for sample_id in fixed_val_ids}
    if train_sessions & val_sessions:
        raise CleanPanelAuditError("deterministic fixed train/validation sessions overlap")

    official_y = torch.tensor(
        [training["y"][training["positions"][sample_id]] for sample_id in fixed_val_ids],
        dtype=torch.int16,
    )
    prediction_columns = []
    sources = []
    for voter in validated_voters:
        ids = voter["ids"]
        if set(ids) != fixed_val_set:
            raise CleanPanelAuditError(f"{voter['spec'].name}: common fixed ID set mismatch")
        positions = {sample_id: index for index, sample_id in enumerate(ids)}
        column = torch.tensor(
            [int(voter["predictions"][positions[sample_id]]) for sample_id in fixed_val_ids],
            dtype=torch.uint8,
        )
        prediction_columns.append(column)
        source = {
            **asdict(voter["spec"]),
            "rows": len(ids),
            "classes": list(ALL_CLASSES),
            "class_order_sha256": _sha256_lines(ALL_CLASSES),
            "source_logit_dtype": voter["source_logit_dtype"],
            "prediction_contract": "argmax(payload.logits); class_bias/rules ignored",
            "raw_macro_f1_recomputed": voter["raw_macro_f1"],
            "fixed_val_id_set_sha256": _sha256_lines(fixed_val_ids),
            "deterministic_train_id_set_sha256": _sha256_lines(fixed_train_ids),
            "deterministic_train_rows": len(fixed_train_ids),
            "deterministic_val_rows": len(fixed_val_ids),
            "train_val_id_overlap": 0,
            "train_val_session_overlap": 0,
            "train_sessions": len(train_sessions),
            "val_sessions": len(val_sessions),
            "surface": voter["surface"],
            "results_provenance": voter["results_evidence"],
            "metrics_sidecar": voter["metrics_evidence"],
        }
        sources.append(source)

    predictions = torch.stack(prediction_columns, dim=1)
    correct_matrix = predictions.to(torch.int16).eq(official_y.view(-1, 1))
    c_clean = correct_matrix.sum(dim=1).to(torch.uint8)
    histogram = Counter(int(value) for value in c_clean.tolist())

    for column, source in enumerate(sources):
        correct = correct_matrix[:, column]
        weak_mask = official_y < WEAK4_COUNT
        unique = correct & (c_clean == 1)
        source.update(
            {
                "official_label_correct_count": int(correct.sum()),
                "official_label_accuracy": float(correct.float().mean()),
                "official_weak4_rows": int(weak_mask.sum()),
                "official_weak4_correct_count": int((correct & weak_mask).sum()),
                "unique_correct_contribution": int(unique.sum()),
                "weak4_unique_correct_contribution": int((unique & weak_mask).sum()),
            }
        )

    per_class = {}
    for class_id, label in enumerate(ALL_CLASSES):
        mask = official_y == class_id
        counts = c_clean[mask]
        class_hist = Counter(int(value) for value in counts.tolist())
        per_class[label] = {
            "rows": int(mask.sum()),
            "c_clean_histogram": {
                str(value): int(class_hist.get(value, 0))
                for value in range(len(voter_specs) + 1)
            },
        }

    panel = {
        "schema_version": SCHEMA_VERSION,
        "kind": ARTIFACT_KIND,
        "usage_scope": USAGE_SCOPE,
        "card_b_connection": False,
        "training_target": False,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "classes": list(ALL_CLASSES),
        "ids": fixed_val_ids,
        "y_true": official_y,
        "voter_names": names,
        "raw_predictions": predictions,
        "correct_matrix": correct_matrix,
        "c_clean": c_clean,
        "rows": len(fixed_val_ids),
        "voter_count": len(voter_specs),
        "c_clean_histogram": {
            str(value): int(histogram.get(value, 0))
            for value in range(len(voter_specs) + 1)
        },
        "per_class": per_class,
        "fixed_split_provenance": {
            "split": EXPECTED_SPLIT,
            "seed": EXPECTED_SEED,
            "fixed_val_id_set_sha256": _sha256_lines(fixed_val_ids),
            "deterministic_train_id_set_sha256": _sha256_lines(fixed_train_ids),
            "fixed_val_session_set_sha256": _sha256_lines(sorted(val_sessions)),
            "deterministic_train_session_set_sha256": _sha256_lines(sorted(train_sessions)),
            "train_rows": len(fixed_train_ids),
            "validation_rows": len(fixed_val_ids),
            "train_sessions": len(train_sessions),
            "validation_sessions": len(val_sessions),
            "train_val_id_overlap": 0,
            "train_val_session_overlap": 0,
            "train_jsonl": str((data_dir / "train.jsonl").relative_to(repo_root)),
            "train_jsonl_sha256": sha256_file(data_dir / "train.jsonl"),
            "train_labels": str((data_dir / "train_labels.csv").relative_to(repo_root)),
            "train_labels_sha256": sha256_file(data_dir / "train_labels.csv"),
            "results_csv": str(results_csv.relative_to(repo_root)),
            "results_csv_sha256_at_construction": sha256_file(results_csv),
        },
        "sources": sources,
        "excluded_candidates": list(DEFAULT_EXCLUSIONS),
    }
    if card_b_dataset is not None:
        panel["card_b_route_overlay_audit"] = build_card_b_route_overlay_audit(
            panel,
            card_b_dataset,
            repo_root=repo_root,
            expected_sha256=expected_card_b_sha256,
            expected_rescue_c0=expected_rescue_c0,
            expected_rescue_c_gt0=expected_rescue_c_gt0,
        )
    return panel


def audit_clean_panel_artifact(
    artifact_path: Path,
    *,
    repo_root: Path,
    data_dir: Path,
    results_csv: Path,
    voter_specs: Sequence[VoterSpec],
    expected_rows: int | None = EXPECTED_FIXED_ROWS,
    card_b_dataset: Path | None = None,
    expected_card_b_sha256: str | None = None,
    expected_rescue_c0: int | None = None,
    expected_rescue_c_gt0: int | None = None,
) -> dict:
    """Rebuild from immutable sources and require exact semantic equality."""

    artifact_path = artifact_path if artifact_path.is_absolute() else repo_root / artifact_path
    if not artifact_path.is_file():
        raise CleanPanelAuditError(f"missing panel artifact: {artifact_path}")
    saved = torch_load(artifact_path)
    if not isinstance(saved, dict):
        raise CleanPanelAuditError("panel artifact must be a dict")
    expected_contract = {
        "schema_version": SCHEMA_VERSION,
        "kind": ARTIFACT_KIND,
        "usage_scope": USAGE_SCOPE,
        "card_b_connection": False,
        "training_target": False,
    }
    for field, expected in expected_contract.items():
        if saved.get(field) != expected:
            raise CleanPanelAuditError(
                f"artifact {field} mismatch expected={expected!r} actual={saved.get(field)!r}"
            )
    rebuilt = build_clean_panel(
        repo_root=repo_root,
        data_dir=data_dir,
        results_csv=results_csv,
        voter_specs=voter_specs,
        expected_rows=expected_rows,
        card_b_dataset=card_b_dataset,
        expected_card_b_sha256=expected_card_b_sha256,
        expected_rescue_c0=expected_rescue_c0,
        expected_rescue_c_gt0=expected_rescue_c_gt0,
    )
    scalar_fields = (
        "classes",
        "ids",
        "voter_names",
        "rows",
        "voter_count",
        "c_clean_histogram",
        "per_class",
        "sources",
        "excluded_candidates",
        "card_b_route_overlay_audit",
    )
    for field in scalar_fields:
        if saved.get(field) != rebuilt.get(field):
            raise CleanPanelAuditError(f"artifact {field} differs from rebuilt sources")
    # A ledger may receive unrelated rows after construction.  Its whole-file
    # digest is historical context, while each selected row is still checked
    # exactly through the source train command and command SHA above.
    saved_split = dict(saved.get("fixed_split_provenance") or {})
    rebuilt_split = dict(rebuilt.get("fixed_split_provenance") or {})
    saved_split.pop("results_csv_sha256_at_construction", None)
    rebuilt_split.pop("results_csv_sha256_at_construction", None)
    if saved_split != rebuilt_split:
        raise CleanPanelAuditError(
            "artifact fixed_split_provenance differs from rebuilt sources"
        )
    tensor_fields = ("y_true", "raw_predictions", "correct_matrix", "c_clean")
    for field in tensor_fields:
        try:
            saved_tensor = torch.as_tensor(saved.get(field))
        except (TypeError, ValueError) as exc:
            raise CleanPanelAuditError(f"artifact {field} is not tensor-like") from exc
        if not torch.equal(saved_tensor.cpu(), rebuilt[field].cpu()):
            raise CleanPanelAuditError(f"artifact {field} differs from rebuilt sources")
    return {
        "artifact_path": str(artifact_path),
        "artifact_sha256": sha256_file(artifact_path),
        "rows": rebuilt["rows"],
        "voter_count": rebuilt["voter_count"],
        "c_clean_histogram": rebuilt["c_clean_histogram"],
        "usage_scope": rebuilt["usage_scope"],
        "audit": "passed",
    }


def summary_payload(payload: dict, artifact_path: Path) -> dict:
    return {
        "schema_version": payload["schema_version"],
        "kind": payload["kind"],
        "usage_scope": payload["usage_scope"],
        "card_b_connection": payload["card_b_connection"],
        "training_target": payload["training_target"],
        "created_utc": payload["created_utc"],
        "artifact_path": str(artifact_path),
        "artifact_sha256": sha256_file(artifact_path),
        "rows": payload["rows"],
        "voter_count": payload["voter_count"],
        "c_clean_histogram": payload["c_clean_histogram"],
        "per_class": payload["per_class"],
        "fixed_split_provenance": payload["fixed_split_provenance"],
        "sources": payload["sources"],
        "excluded_candidates": payload["excluded_candidates"],
        "card_b_route_overlay_audit": payload.get("card_b_route_overlay_audit"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "audit"))
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--results-csv", default="experiments/results.csv")
    parser.add_argument(
        "--output",
        default="experiments/artifacts/20260713_weak4_c_clean_fixed_panel.pt",
    )
    parser.add_argument(
        "--report-json",
        default="experiments/artifacts/20260713_weak4_c_clean_fixed_panel.json",
    )
    parser.add_argument("--card-b-dataset", default=DEFAULT_CARD_B_DATASET)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output = _resolve(repo_root, args.output)
    card_b_dataset = Path(args.card_b_dataset) if args.card_b_dataset else None
    if args.mode == "audit":
        result = audit_clean_panel_artifact(
            output,
            repo_root=repo_root,
            data_dir=Path(args.data_dir),
            results_csv=Path(args.results_csv),
            voter_specs=DEFAULT_VOTER_SPECS,
            card_b_dataset=card_b_dataset,
            expected_card_b_sha256=DEFAULT_CARD_B_DATASET_SHA256,
            expected_rescue_c0=218,
            expected_rescue_c_gt0=208,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    payload = build_clean_panel(
        repo_root=repo_root,
        data_dir=Path(args.data_dir),
        results_csv=Path(args.results_csv),
        voter_specs=DEFAULT_VOTER_SPECS,
        card_b_dataset=card_b_dataset,
        expected_card_b_sha256=DEFAULT_CARD_B_DATASET_SHA256,
        expected_rescue_c0=218,
        expected_rescue_c_gt0=208,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, output)
    report_path = _resolve(repo_root, args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = summary_payload(payload, Path(args.output))
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    audit = audit_clean_panel_artifact(
        output,
        repo_root=repo_root,
        data_dir=Path(args.data_dir),
        results_csv=Path(args.results_csv),
        voter_specs=DEFAULT_VOTER_SPECS,
        card_b_dataset=card_b_dataset,
        expected_card_b_sha256=DEFAULT_CARD_B_DATASET_SHA256,
        expected_rescue_c0=218,
        expected_rescue_c_gt0=208,
    )
    print(
        f"saved clean panel: {output} rows={payload['rows']} "
        f"voters={payload['voter_count']} histogram={payload['c_clean_histogram']}"
    )
    print(f"saved report: {report_path}")
    print(f"audit: {audit['audit']} sha256={audit['artifact_sha256']}")


if __name__ == "__main__":
    main()
