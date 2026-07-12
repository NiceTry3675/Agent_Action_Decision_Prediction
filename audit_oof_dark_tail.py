#!/usr/bin/env python3
"""Static, no-training audit for an OOF dark-tail KD target transplant.

The primary contract is deliberately narrow and rejection-only:

* M8 raw train logits and the stored OOF log-probability-style blend are both
  interpreted through the champion KD temperature (T=3 by default).
* The M8 probability assigned to the official class is preserved exactly.
* Only the conditional distribution over the other 13 classes is blended.
* Only canonical consensus c=0 rows are changed; c>0 rows remain exact clones.

This script never writes a teacher payload.  It records the size and semantics
of the proposed intervention so a harmful target can be rejected before a
full refit/Public-only experiment.  A T=1 OOF interpretation is included only
as a caution arm because the OOF payload already stores log-probability-style
blend scores.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shlex
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import torch
import torch.nn.functional as F

from script import ALL_CLASSES


REPORT_FORMAT = "oof-dark-tail-static-audit-v1"
EXPECTED_ROWS = 70_000
EXPECTED_C_HISTOGRAM = {0: 12_776, 1: 3_806, 2: 4_811, 3: 48_607}
EXPECTED_C0_ID_DIGEST = "746841ab1ee9cb8e7ecc5eea2b4748d528a71617de9e76a38ba781c1b5f54070"
WEAK4_IDS = set(range(4))


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pt(path: Path):
    return torch.load(path, map_location="cpu", weights_only=False)


def id_digest(ids: list[str]) -> str:
    digest = hashlib.sha256()
    for sample_id in ids:
        digest.update(sample_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def validate_logit_payload(payload, path: Path) -> dict:
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: payload must be a dict")
    classes = list(payload.get("classes") or [])
    if classes != ALL_CLASSES:
        raise ValueError(f"{path}: class order mismatch")
    ids = [str(value) for value in payload.get("ids") or []]
    if len(ids) != EXPECTED_ROWS:
        raise ValueError(f"{path}: expected {EXPECTED_ROWS} ids, found {len(ids)}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate ids")
    logits = payload.get("logits")
    if not torch.is_tensor(logits) or tuple(logits.shape) != (len(ids), len(classes)):
        raise ValueError(
            f"{path}: logits must have shape {(len(ids), len(classes))}, "
            f"got {getattr(logits, 'shape', None)}"
        )
    logits = logits.float()
    if not bool(torch.isfinite(logits).all()):
        raise ValueError(f"{path}: non-finite logits")
    y_true = torch.as_tensor(payload.get("y_true"), dtype=torch.long).view(-1)
    if len(y_true) != len(ids):
        raise ValueError(f"{path}: y_true length mismatch")
    if bool(((y_true < 0) | (y_true >= len(classes))).any()):
        raise ValueError(f"{path}: y_true outside class range")
    labels = payload.get("labels")
    if labels is not None:
        label_ids = torch.tensor(
            [classes.index(str(label)) if str(label) in classes else -1 for label in labels],
            dtype=torch.long,
        )
        if len(label_ids) != len(ids) or not torch.equal(label_ids, y_true):
            raise ValueError(f"{path}: labels/y_true mismatch")
    return {"classes": classes, "ids": ids, "logits": logits, "y_true": y_true}


def align_logit_payload(payload: dict, target_ids: list[str], target_y: torch.Tensor, path: Path) -> dict:
    source_ids = payload["ids"]
    if len(source_ids) != len(target_ids) or set(source_ids) != set(target_ids):
        raise ValueError(f"{path}: id coverage mismatch")
    positions = {sample_id: idx for idx, sample_id in enumerate(source_ids)}
    order = torch.tensor([positions[sample_id] for sample_id in target_ids], dtype=torch.long)
    aligned_y = payload["y_true"][order]
    if not torch.equal(aligned_y, target_y):
        raise ValueError(f"{path}: ID-aligned labels differ from primary payload")
    return {**payload, "ids": list(target_ids), "logits": payload["logits"][order], "y_true": aligned_y}


def validate_consensus(payload, path: Path, target_ids: list[str], target_y: torch.Tensor) -> torch.Tensor:
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: consensus payload must be a dict")
    if list(payload.get("classes") or []) != ALL_CLASSES:
        raise ValueError(f"{path}: consensus class order mismatch")
    if payload.get("kind") != "oof_correct_consensus_reliability":
        raise ValueError(f"{path}: unexpected consensus kind")
    if payload.get("usage_scope") != "full_data_refit_only":
        raise ValueError(f"{path}: consensus must be full_data_refit_only")
    ids = [str(value) for value in payload.get("ids") or []]
    if len(ids) != EXPECTED_ROWS or len(ids) != len(set(ids)):
        raise ValueError(f"{path}: invalid or duplicate consensus ids")
    if set(ids) != set(target_ids):
        raise ValueError(f"{path}: consensus id coverage mismatch")
    y = torch.as_tensor(payload.get("y_true"), dtype=torch.long).view(-1)
    counts = torch.as_tensor(payload.get("correct_counts"), dtype=torch.long).view(-1)
    if len(y) != len(ids) or len(counts) != len(ids):
        raise ValueError(f"{path}: consensus row length mismatch")
    positions = {sample_id: idx for idx, sample_id in enumerate(ids)}
    order = torch.tensor([positions[sample_id] for sample_id in target_ids], dtype=torch.long)
    y = y[order]
    counts = counts[order]
    if not torch.equal(y, target_y):
        raise ValueError(f"{path}: ID-aligned consensus labels differ")
    histogram = Counter(int(value) for value in counts.tolist())
    observed = {key: int(histogram.get(key, 0)) for key in EXPECTED_C_HISTOGRAM}
    if observed != EXPECTED_C_HISTOGRAM:
        raise ValueError(f"{path}: consensus histogram drifted: {observed}")
    return counts


def conditional_tail(probabilities: torch.Tensor, y_true: torch.Tensor) -> torch.Tensor:
    if probabilities.ndim != 2 or probabilities.shape[1] < 2:
        raise ValueError("probabilities must have shape [rows,classes>=2]")
    rows = torch.arange(len(y_true))
    tail = probabilities.clone()
    tail[rows, y_true] = 0.0
    mass = tail.sum(dim=1, keepdim=True)
    if bool((mass <= 0).any()) or not bool(torch.isfinite(mass).all()):
        raise ValueError("conditional tail has zero or non-finite mass")
    return tail / mass


def transplant_probabilities(
    full_p: torch.Tensor,
    oof_p: torch.Tensor,
    y_true: torch.Tensor,
    c0_mask: torch.Tensor,
    rho: float,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    if not math.isfinite(rho) or not 0.0 <= rho <= 1.0:
        raise ValueError("rho must be finite and in [0,1]")
    if full_p.shape != oof_p.shape or full_p.shape[0] != len(y_true):
        raise ValueError("probability inputs are not aligned")
    if c0_mask.dtype != torch.bool or len(c0_mask) != len(y_true):
        raise ValueError("c0_mask must be an aligned bool tensor")
    rows = torch.arange(len(y_true))
    full_tail = conditional_tail(full_p, y_true)
    oof_tail = conditional_tail(oof_p, y_true)
    official = full_p[rows, y_true]
    mixed_tail = (1.0 - rho) * full_tail + rho * oof_tail
    changed = mixed_tail * (1.0 - official).unsqueeze(1)
    changed[rows, y_true] = official
    output = full_p.clone()
    output[c0_mask] = changed[c0_mask]
    if not torch.equal(output[~c0_mask], full_p[~c0_mask]):
        raise AssertionError("c>0 rows changed")
    official_error = float((output[rows, y_true] - official).abs().max())
    if official_error > 1e-7:
        raise AssertionError(f"official probability was not preserved: {official_error}")
    sum_error = float((output.sum(dim=1) - 1.0).abs().max())
    if sum_error > 2e-6 or not bool(torch.isfinite(output).all()):
        raise AssertionError(f"invalid output probabilities: sum_error={sum_error}")
    return output, full_tail, oof_tail


def entropy(probabilities: torch.Tensor) -> torch.Tensor:
    values = probabilities.clamp_min(1e-12)
    return -(values * values.log()).sum(dim=1)


def distribution_metrics(
    full_p: torch.Tensor,
    output_p: torch.Tensor,
    full_tail: torch.Tensor,
    oof_tail: torch.Tensor,
    y_true: torch.Tensor,
    mask: torch.Tensor,
) -> dict:
    if not bool(mask.any()):
        return {"rows": 0}
    rows = torch.where(mask)[0]
    full = full_p[rows]
    output = output_p[rows]
    labels = y_true[rows]
    local_rows = torch.arange(len(rows))
    full_argmax = full.argmax(dim=1)
    output_argmax = output.argmax(dim=1)
    full_values = full.clamp_min(1e-12)
    output_values = output.clamp_min(1e-12)
    midpoint = (0.5 * (full + output)).clamp_min(1e-12)
    kl_q_full = (output * (output_values.log() - full_values.log())).sum(dim=1)
    js = 0.5 * (
        (full * (full_values.log() - midpoint.log())).sum(dim=1)
        + (output * (output_values.log() - midpoint.log())).sum(dim=1)
    )
    official_full = full[local_rows, labels]
    official_output = output[local_rows, labels]
    return {
        "rows": int(len(rows)),
        "mean_official_probability": float(official_full.mean()),
        "max_official_probability_error": float((official_output - official_full).abs().max()),
        "original_official_argmax_rate": float((full_argmax == labels).float().mean()),
        "new_official_argmax_rate": float((output_argmax == labels).float().mean()),
        "original_argmax_retention_rate": float((output_argmax == full_argmax).float().mean()),
        "argmax_change_rate": float((output_argmax != full_argmax).float().mean()),
        "mean_l1": float((output - full).abs().sum(dim=1).mean()),
        "mean_kl_q_to_full": float(kl_q_full.mean()),
        "mean_js": float(js.mean()),
        "mean_entropy_full": float(entropy(full).mean()),
        "mean_entropy_output": float(entropy(output).mean()),
        "mean_entropy_delta": float((entropy(output) - entropy(full)).mean()),
        "mean_conditional_tail_entropy_full": float(entropy(full_tail[rows]).mean()),
        "mean_conditional_tail_entropy_oof": float(entropy(oof_tail[rows]).mean()),
        "conditional_tail_top_agreement": float(
            (full_tail[rows].argmax(dim=1) == oof_tail[rows].argmax(dim=1)).float().mean()
        ),
    }


def source_name(sample_id: str) -> str:
    if sample_id.startswith("sess_sim_"):
        return "sim"
    if sample_id.startswith("sess_au_"):
        return "au"
    return "other"


def sliced_report(
    full_p: torch.Tensor,
    output_p: torch.Tensor,
    full_tail: torch.Tensor,
    oof_tail: torch.Tensor,
    y_true: torch.Tensor,
    c0: torch.Tensor,
    ids: list[str],
) -> dict:
    weak = torch.tensor([int(value) in WEAK4_IDS for value in y_true], dtype=torch.bool)
    report = {
        "all_c0": distribution_metrics(full_p, output_p, full_tail, oof_tail, y_true, c0),
        "weak4_c0": distribution_metrics(full_p, output_p, full_tail, oof_tail, y_true, c0 & weak),
        "nonweak_c0": distribution_metrics(full_p, output_p, full_tail, oof_tail, y_true, c0 & ~weak),
        "per_class_c0": {},
        "per_source_c0": {},
    }
    for class_id, label in enumerate(ALL_CLASSES):
        report["per_class_c0"][label] = distribution_metrics(
            full_p, output_p, full_tail, oof_tail, y_true, c0 & (y_true == class_id)
        )
    sources = sorted(set(source_name(sample_id) for sample_id in ids))
    for source in sources:
        source_mask = torch.tensor(
            [source_name(sample_id) == source for sample_id in ids], dtype=torch.bool
        )
        report["per_source_c0"][source] = distribution_metrics(
            full_p, output_p, full_tail, oof_tail, y_true, c0 & source_mask
        )
    return report


def parse_rhos(value: str) -> list[float]:
    values = [float(part.strip()) for part in value.split(",") if part.strip()]
    if not values or any(not math.isfinite(value) or value < 0 or value > 1 for value in values):
        raise ValueError("--rhos must contain finite comma-separated values in [0,1]")
    return values


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m8", default="experiments/logits/m8_qwen35_refit_train70k_fp16.pt")
    parser.add_argument("--oof", default="experiments/logits/teacher_m7m8v6_train70k_fp16.pt")
    parser.add_argument(
        "--consensus",
        default="experiments/artifacts/20260710_m7_m8_v6_oof_consensus.pt",
    )
    parser.add_argument("--temperature", type=float, default=3.0)
    parser.add_argument("--caution-oof-temperature", type=float, default=1.0)
    parser.add_argument("--rhos", default="0,0.05,0.1,0.25,0.5,1")
    parser.add_argument("--primary-rho", type=float, default=0.5)
    parser.add_argument(
        "--output",
        default="experiments/artifacts/20260712_oof_dark_tail_static_audit.json",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not math.isfinite(args.temperature) or args.temperature <= 0:
        raise ValueError("--temperature must be finite and > 0")
    if not math.isfinite(args.caution_oof_temperature) or args.caution_oof_temperature <= 0:
        raise ValueError("--caution-oof-temperature must be finite and > 0")
    rhos = parse_rhos(args.rhos)
    if args.primary_rho not in rhos:
        raise ValueError("--primary-rho must appear in --rhos")

    root = Path(__file__).resolve().parent
    m8_path = root / args.m8
    oof_path = root / args.oof
    consensus_path = root / args.consensus
    m8 = validate_logit_payload(load_pt(m8_path), m8_path)
    oof = validate_logit_payload(load_pt(oof_path), oof_path)
    oof = align_logit_payload(oof, m8["ids"], m8["y_true"], oof_path)
    counts = validate_consensus(
        load_pt(consensus_path), consensus_path, m8["ids"], m8["y_true"]
    )
    c0 = counts == 0
    c0_ids = [sample_id for sample_id, selected in zip(m8["ids"], c0.tolist()) if selected]
    c0_digest = id_digest(c0_ids)
    if c0_digest != EXPECTED_C0_ID_DIGEST:
        raise ValueError(f"canonical c0 ID digest drifted: {c0_digest}")

    full_p = F.softmax(m8["logits"] / args.temperature, dim=1)
    arms = {}
    for arm_name, oof_temperature in (
        ("temperature_matched", args.temperature),
        ("caution_untempered_oof", args.caution_oof_temperature),
    ):
        oof_p = F.softmax(oof["logits"] / oof_temperature, dim=1)
        rho_reports = {}
        for rho in rhos:
            output, full_tail, oof_tail = transplant_probabilities(
                full_p, oof_p, m8["y_true"], c0, rho
            )
            rho_reports[str(rho)] = sliced_report(
                full_p,
                output,
                full_tail,
                oof_tail,
                m8["y_true"],
                c0,
                m8["ids"],
            )
            rho_reports[str(rho)]["contract_checks"] = {
                "c0_rows": int(c0.sum()),
                "c_gt_0_rows_unchanged_exact": bool(
                    torch.equal(output[~c0], full_p[~c0])
                ),
                "max_probability_sum_error": float(
                    (output.sum(dim=1) - 1.0).abs().max()
                ),
            }
        arms[arm_name] = {
            "oof_temperature": oof_temperature,
            "interpretation": (
                "primary: stored OOF log-probability-style scores divided by champion KD T"
                if arm_name == "temperature_matched"
                else "caution only: exp-like interpretation of stored OOF log-probability scores"
            ),
            "rho_reports": rho_reports,
        }

    primary = arms["temperature_matched"]["rho_reports"][str(args.primary_rho)][
        "all_c0"
    ]
    caution = arms["caution_untempered_oof"]["rho_reports"][str(args.primary_rho)][
        "all_c0"
    ]
    report = {
        "format": REPORT_FORMAT,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "command": shlex.join(sys.argv),
        "scope": "static rejection-only audit; no teacher payload or model is produced",
        "contract": {
            "p_full": "softmax(M8_raw_logits / T)",
            "p_oof_primary": "softmax(stored_OOF_logp_scores / T)",
            "official_probability": "preserved from M8 exactly; not asserted to be argmax",
            "tail": "convex mixture of normalized 13-class non-official distributions",
            "changed_rows": "canonical consensus c=0 only",
            "primary_rho": args.primary_rho,
        },
        "config": vars(args),
        "inputs": {
            "m8": {"path": str(m8_path), "sha256": sha256_file(m8_path)},
            "oof": {"path": str(oof_path), "sha256": sha256_file(oof_path)},
            "consensus": {
                "path": str(consensus_path),
                "sha256": sha256_file(consensus_path),
                "usage_scope": "full_data_refit_only",
            },
        },
        "rows": EXPECTED_ROWS,
        "correct_count_histogram": {str(key): value for key, value in EXPECTED_C_HISTOGRAM.items()},
        "c0_id_digest": c0_digest,
        "arms": arms,
        "primary_summary": {
            "temperature": args.temperature,
            "rho": args.primary_rho,
            **primary,
        },
        "caution_summary": {
            "oof_temperature": args.caution_oof_temperature,
            "rho": args.primary_rho,
            **caution,
        },
        "decision": (
            "static_contract_valid; use row-specific frozen/permutation rejection gate before any payload/refit"
        ),
    }
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"saved {output} c0={int(c0.sum())} "
        f"primary_l1={primary['mean_l1']:.6f} "
        f"primary_retention={primary['original_argmax_retention_rate']:.6f} "
        f"caution_l1={caution['mean_l1']:.6f}"
    )


if __name__ == "__main__":
    main()
