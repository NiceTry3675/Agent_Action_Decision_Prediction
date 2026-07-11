#!/usr/bin/env python3
"""Reliability v2 assets: soft OOF consensus and OOF-hidden kNN agreement.

Stage ``soft`` stitches the same nine session-OOF fold logit files that built
the count-based consensus into per-row continuous reliability signals
(per-model p_true and their mean), and reports how much discrimination they
add inside each hard count bin — especially c=0, which the deployed sieve
currently zeroes as a single block.

Stage ``knn`` (after the fold hidden export) computes scenario-excluded
label-agreement rates among hidden-space neighbours, per OOF fold, as a
second refinement signal inside c=0.

Both stages are training-side only (usage scope full_data_refit_only); no
fixed-validation information is read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

CLASSES = [
    "read_file", "grep_search", "list_directory", "glob_pattern",
    "edit_file", "write_file", "apply_patch", "run_bash", "run_tests",
    "lint_or_typecheck", "ask_user", "plan_task", "web_search", "respond_only",
]
WEAK4 = set(CLASSES[:4])


def scenario_group(sample_id):
    session = str(sample_id).split("-step_")[0]
    parts = session.split("_")
    if len(parts) >= 4 and parts[:2] == ["sess", "au"]:
        return "_".join(parts[:3])
    return session


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model_ptrue(fold_paths):
    """Stitch one model's session-OOF folds into id -> (p_true, correct)."""

    by_id = {}
    for path in fold_paths:
        payload = torch.load(path, map_location="cpu", weights_only=False)
        ids = [str(value) for value in payload["ids"]]
        y_true = torch.as_tensor(payload["y_true"], dtype=torch.long)
        logits = payload["logits"].float()
        if list(payload.get("classes") or []) != CLASSES:
            raise ValueError(f"class order mismatch in {path}")
        probabilities = torch.softmax(logits, dim=-1)
        p_true = probabilities[torch.arange(len(ids)), y_true]
        predictions = logits.argmax(dim=-1)
        for row, sample_id in enumerate(ids):
            if sample_id in by_id:
                raise ValueError(f"overlapping OOF folds at id {sample_id!r}")
            by_id[sample_id] = (
                float(p_true[row]),
                bool(predictions[row] == y_true[row]),
                int(y_true[row]),
            )
    return by_id


def quantiles(values):
    if not len(values):
        return None
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": int(array.size),
        "mean": float(array.mean()),
        "p10": float(np.percentile(array, 10)),
        "p25": float(np.percentile(array, 25)),
        "p50": float(np.percentile(array, 50)),
        "p75": float(np.percentile(array, 75)),
        "p90": float(np.percentile(array, 90)),
    }


def stage_soft(args):
    consensus_report = json.load(open(args.consensus_report, encoding="utf-8"))
    consensus_payload = torch.load(
        args.consensus_artifact, map_location="cpu", weights_only=False
    )
    ids = [str(value) for value in consensus_payload["ids"]]
    counts = torch.as_tensor(consensus_payload["correct_counts"], dtype=torch.long)
    y_true = torch.as_tensor(consensus_payload["y_true"], dtype=torch.long)

    model_maps = []
    model_names = []
    for source in consensus_report["sources"]:
        fold_paths = [entry["path"] for entry in source["files"]]
        for entry in source["files"]:
            if sha256_file(entry["path"]) != entry["sha256"]:
                raise ValueError(f"fold logits changed on disk: {entry['path']}")
        model_maps.append(load_model_ptrue(fold_paths))
        model_names.append("+".join(source.get("base_models") or ["?"]))

    n = len(ids)
    p_true = torch.zeros((n, len(model_maps)), dtype=torch.float32)
    for column, mapping in enumerate(model_maps):
        if set(mapping) != set(ids):
            raise ValueError(f"model {column} OOF ids do not cover the artifact ids")
        for row, sample_id in enumerate(ids):
            value, correct, label = mapping[sample_id]
            if label != int(y_true[row]):
                raise ValueError(f"label mismatch at {sample_id!r}")
            p_true[row, column] = value
    p_mean = p_true.mean(dim=1)

    # Correct-count reconstruction cross-check against the deployed artifact.
    reconstructed = sum(
        torch.tensor([mapping[sample_id][1] for sample_id in ids], dtype=torch.long)
        for mapping in model_maps
    )
    if not bool((reconstructed == counts).all()):
        raise ValueError("reconstructed correct counts disagree with the artifact")

    report = {"per_bin_p_true_mean": {}, "weak4_c0_p_true_mean": None}
    for bin_value in range(4):
        mask = counts == bin_value
        report["per_bin_p_true_mean"][f"c={bin_value}"] = quantiles(
            p_mean[mask].numpy()
        )
    weak4_ids = torch.tensor(
        [CLASSES[int(label)] in WEAK4 for label in y_true], dtype=torch.bool
    )
    report["weak4_c0_p_true_mean"] = quantiles(
        p_mean[(counts == 0) & weak4_ids].numpy()
    )

    output = {
        "format": "soft-oof-consensus-asset-v1",
        "usage_scope": "full_data_refit_only",
        "classes": CLASSES,
        "ids": ids,
        "y_true": y_true,
        "correct_counts": counts,
        "p_true_per_model": p_true,
        "p_true_mean": p_mean,
        "model_names": model_names,
        "consensus_artifact_sha256": sha256_file(args.consensus_artifact),
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(output, output_path)
    report_path = output_path.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"soft consensus asset -> {output_path}")


def stage_knn(args):
    asset = torch.load(args.soft_asset, map_location="cpu", weights_only=False)
    ids = asset["ids"]
    id_pos = {sample_id: row for row, sample_id in enumerate(ids)}
    counts = asset["correct_counts"]
    y_true = asset["y_true"]
    scenarios = np.asarray([scenario_group(sample_id) for sample_id in ids])

    agree = torch.full((len(ids),), float("nan"), dtype=torch.float32)
    for cache_path in args.hidden_caches:
        payload = torch.load(cache_path, map_location="cpu", weights_only=False)
        fold_ids = [str(value) for value in payload["ids"]]
        hidden = payload["hidden"].float()
        hidden = hidden / hidden.norm(dim=1, keepdim=True).clamp_min(1e-6)
        rows = np.asarray([id_pos[sample_id] for sample_id in fold_ids])
        fold_scenarios = scenarios[rows]
        fold_labels = y_true[rows]
        similarity = hidden @ hidden.T
        # exclude self and same-scenario neighbours
        scenario_codes = {name: i for i, name in enumerate(set(fold_scenarios))}
        codes = torch.tensor([scenario_codes[s] for s in fold_scenarios])
        same_scenario = codes.view(-1, 1) == codes.view(1, -1)
        similarity.masked_fill_(same_scenario, -2.0)
        top = similarity.topk(args.k, dim=1).indices
        neighbour_labels = fold_labels[top]
        agree[rows] = (
            (neighbour_labels == fold_labels.view(-1, 1)).float().mean(dim=1)
        )

    covered = ~torch.isnan(agree)
    if int(covered.sum()) != len(ids):
        raise ValueError(
            f"kNN coverage incomplete: {int(covered.sum())}/{len(ids)}"
        )
    report = {"per_bin_knn_agreement": {}, "k": args.k}
    for bin_value in range(4):
        mask = counts == bin_value
        report["per_bin_knn_agreement"][f"c={bin_value}"] = quantiles(
            agree[mask].numpy()
        )
    asset["knn_agreement"] = agree
    asset["knn_k"] = args.k
    asset["knn_hidden_caches"] = [
        {"path": str(path), "sha256": sha256_file(path)}
        for path in args.hidden_caches
    ]
    torch.save(asset, args.soft_asset)
    report_path = Path(args.soft_asset).with_suffix(".knn_report.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"knn agreement merged into -> {args.soft_asset}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["soft", "knn"], required=True)
    parser.add_argument(
        "--consensus-report",
        default="experiments/artifacts/20260710_m7_m8_v6_oof_consensus.json",
    )
    parser.add_argument(
        "--consensus-artifact",
        default="experiments/artifacts/20260710_m7_m8_v6_oof_consensus.pt",
    )
    parser.add_argument(
        "--output",
        default="experiments/artifacts/20260711_soft_consensus_asset.pt",
    )
    parser.add_argument(
        "--soft-asset",
        default="experiments/artifacts/20260711_soft_consensus_asset.pt",
    )
    parser.add_argument("--hidden-caches", nargs="*", default=[])
    parser.add_argument("--k", type=int, default=20)
    args = parser.parse_args()
    if args.stage == "soft":
        stage_soft(args)
    else:
        if not args.hidden_caches:
            raise SystemExit("--hidden-caches is required for stage knn")
        stage_knn(args)


if __name__ == "__main__":
    main()
