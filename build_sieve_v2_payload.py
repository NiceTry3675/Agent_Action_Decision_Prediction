#!/usr/bin/env python3
"""Soft/source-aware sieve v2 payload: histogram-preserving row reassignment.

Replaces the count-based row->tier assignment of the deployed consensus sieve
with a soft, source-weighted reliability score

    r_i = p_M7(y_i) + 2 * p_M8(y_i) + p_v6(y_i) + p_Xbase(y_i) + p_Xlarge(y_i)

(T=1 softmax gold-label probabilities from session-OOF fold logits).  Within
each true class, rows are ranked by (r desc, id asc) and re-binned into
pseudo-counts 0..3 so that the per-class tier histogram — and therefore the
weight mass and every class-normalization factor downstream — is exactly the
deployed artifact's.  Only *which rows* receive each weight changes.  The
output keeps the `oof_correct_consensus_reliability` schema, so the training
side needs no code change: it is a pure payload swap.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch

CLASSES = [
    "read_file", "grep_search", "list_directory", "glob_pattern",
    "edit_file", "write_file", "apply_patch", "run_bash", "run_tests",
    "lint_or_typecheck", "ask_user", "plan_task", "web_search", "respond_only",
]

XBASE_FOLDS = [
    "experiments/logits/20260701_190742_gpu_transformer_session_oof_current_v1_len192_fold0-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold0_val_logits.pt",
    "experiments/logits/20260701_194151_gpu_transformer_session_oof_current_v1_len192_fold1-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold1_val_logits.pt",
    "experiments/logits/20260701_201604_gpu_transformer_session_oof_current_v1_len192_fold2-of3_replay-last1_oof_xlmr_len192_ep5_replay_last1_cap10000_fold2_val_logits.pt",
]
XLARGE_FOLDS = {
    "p2_large384": [
        "experiments/logits/20260704_173012_gpu_transformer_session_oof_current_v1_len384_fold0-of3_replay-last1_p2_oof_large384_focal_ep5_replay_last1_fold0_val_logits.pt",
        "experiments/logits/20260704_200449_gpu_transformer_session_oof_current_v1_len384_fold1-of3_replay-last1_p2_oof_large384_focal_ep5_replay_last1_fold1_val_logits.pt",
        "experiments/logits/20260704_231701_gpu_transformer_session_oof_current_v1_len384_fold2-of3_replay-last1_p2_oof_large384_focal_ep5_replay_last1_fold2_val_logits.pt",
    ],
    "xlmr_large192": [
        "experiments/logits/20260703_112702_gpu_transformer_session_oof_current_v1_len192_fold0-of3_replay-last1_oof_xlmr_large_len192_ep5_replay_last1_fold0_val_logits.pt",
        "experiments/logits/20260703_124258_gpu_transformer_session_oof_current_v1_len192_fold1-of3_replay-last1_oof_xlmr_large_len192_ep5_replay_last1_fold1_val_logits.pt",
        "experiments/logits/20260703_135951_gpu_transformer_session_oof_current_v1_len192_fold2-of3_replay-last1_oof_xlmr_large_len192_ep5_replay_last1_fold2_val_logits.pt",
    ],
}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model_ptrue(fold_paths, expected_ids):
    p_true = {}
    for path in fold_paths:
        payload = torch.load(path, map_location="cpu", weights_only=False)
        if list(payload.get("classes") or []) != CLASSES:
            raise ValueError(f"class order mismatch in {path}")
        ids = [str(value) for value in payload["ids"]]
        y_true = torch.as_tensor(payload["y_true"], dtype=torch.long)
        probabilities = torch.softmax(payload["logits"].float(), dim=-1)
        rows = torch.arange(len(ids))
        values = probabilities[rows, y_true]
        for row, sample_id in enumerate(ids):
            if sample_id in p_true:
                raise ValueError(f"overlapping OOF folds at id {sample_id!r} in {path}")
            p_true[sample_id] = float(values[row])
    if set(p_true) != set(expected_ids):
        missing = len(set(expected_ids) - set(p_true))
        extra = len(set(p_true) - set(expected_ids))
        raise ValueError(f"OOF coverage mismatch: missing={missing} extra={extra}")
    return p_true


def rank_auc(scores, outcomes):
    scores = np.asarray(scores, dtype=np.float64)
    outcomes = np.asarray(outcomes, dtype=bool)
    order = scores.argsort(kind="mergesort")
    ranks = np.empty(len(scores))
    sorted_scores = scores[order]
    start = 0
    for end in range(1, len(scores) + 1):
        if end == len(scores) or sorted_scores[end] != sorted_scores[start]:
            ranks[order[start:end]] = 0.5 * (start + 1 + end)
            start = end
    n_pos = int(outcomes.sum())
    n_neg = len(outcomes) - n_pos
    if not n_pos or not n_neg:
        return None
    return float((ranks[outcomes].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--consensus-report",
        default="experiments/artifacts/20260710_m7_m8_v6_oof_consensus.json",
    )
    parser.add_argument(
        "--consensus-artifact",
        default="experiments/artifacts/20260710_m7_m8_v6_oof_consensus.pt",
    )
    parser.add_argument("--xlarge-variant", choices=sorted(XLARGE_FOLDS), default="p2_large384")
    parser.add_argument(
        "--student-cache",
        default="experiments/cache/kd_hcx_m8_screen_cache.pt",
        help="fixed-val student cache for the read-only AUC diagnostic ('' to skip)",
    )
    parser.add_argument(
        "--output",
        default="experiments/artifacts/20260711_sieve_v2_soft_reassign.pt",
    )
    args = parser.parse_args()

    base = torch.load(args.consensus_artifact, map_location="cpu", weights_only=False)
    if base.get("kind") != "oof_correct_consensus_reliability":
        raise ValueError("unexpected base artifact kind")
    ids = [str(value) for value in base["ids"]]
    y_true = torch.as_tensor(base["y_true"], dtype=torch.long)
    counts = torch.as_tensor(base["correct_counts"], dtype=torch.long)

    report = json.load(open(args.consensus_report, encoding="utf-8"))
    source_paths = {
        name: [entry["path"] for entry in source["files"]]
        for name, source in zip(("M7", "M8", "v6"), report["sources"])
    }
    source_paths["Xbase"] = XBASE_FOLDS
    source_paths["Xlarge"] = XLARGE_FOLDS[args.xlarge_variant]
    weights = {"M7": 1.0, "M8": 2.0, "v6": 1.0, "Xbase": 1.0, "Xlarge": 1.0}

    p_true = {
        name: load_model_ptrue(paths, ids) for name, paths in source_paths.items()
    }
    r = np.zeros(len(ids), dtype=np.float64)
    for name, mapping in p_true.items():
        r += weights[name] * np.asarray([mapping[sample_id] for sample_id in ids])

    # Histogram-preserving reassignment inside each true class.
    new_counts = torch.full_like(counts, -1)
    per_class_transitions = Counter()
    for class_id in range(len(CLASSES)):
        rows = [i for i in range(len(ids)) if int(y_true[i]) == class_id]
        if not rows:
            continue
        tier_sizes = Counter(int(counts[i]) for i in rows)
        order = sorted(rows, key=lambda i: (-r[i], ids[i]))
        cursor = 0
        for tier in (3, 2, 1, 0):
            take = tier_sizes.get(tier, 0)
            for i in order[cursor: cursor + take]:
                new_counts[i] = tier
                per_class_transitions[(int(counts[i]), tier)] += 1
            cursor += take
        if cursor != len(rows):
            raise AssertionError("tier sizes do not partition the class rows")
    if bool((new_counts < 0).any()):
        raise AssertionError("some rows were not assigned a tier")

    # Audit: per-class histograms must be identical.
    for class_id in range(len(CLASSES)):
        mask = y_true == class_id
        old_hist = Counter(counts[mask].tolist())
        new_hist = Counter(new_counts[mask].tolist())
        if old_hist != new_hist:
            raise AssertionError(f"histogram changed for class {CLASSES[class_id]}")

    moved = int((counts != new_counts).sum())
    transition_matrix = {
        f"{old}->{new}": count
        for (old, new), count in sorted(per_class_transitions.items())
        if old != new
    }

    diagnostics = {}
    if args.student_cache:
        cache = torch.load(args.student_cache, map_location="cpu", weights_only=False)
        cache_pos = {str(sample_id): i for i, sample_id in enumerate(cache["ids"])}
        val_rows = [int(v) for v in cache["val_indices"].tolist()]
        student_ok = (
            cache["parent_logits"].argmax(dim=-1) == cache["y_true"]
        ).numpy()
        artifact_pos = {sample_id: i for i, sample_id in enumerate(ids)}
        val_ids = [str(cache["ids"][i]) for i in val_rows]
        rows = [artifact_pos[v] for v in val_ids]
        outcomes = student_ok[val_rows]
        diagnostics = {
            "xlarge_variant": args.xlarge_variant,
            "auc_count": rank_auc(counts.numpy()[rows], outcomes),
            "auc_soft_r": rank_auc(r[rows], outcomes),
            "auc_new_tier": rank_auc(new_counts.numpy()[rows], outcomes),
        }

    output = {
        "schema_version": 1,
        "kind": "oof_correct_consensus_reliability",
        "usage_scope": "full_data_refit_only",
        "classes": CLASSES,
        "ids": ids,
        "y_true": y_true,
        "correct_counts": new_counts,
        "model_count": 3,
        "backbone_weights": [0.0, 0.25, 0.75, 1.0],
        "sources": [
            {
                "note": (
                    "sieve v2: pseudo-counts from histogram-preserving soft "
                    "reassignment; NOT literal correct counts"
                ),
                "formula": "r = pM7 + 2*pM8 + pv6 + pXbase + pXlarge (T=1 gold prob)",
                "tie_break": "(r desc, id asc) stable",
                "base_artifact_sha256": sha256_file(args.consensus_artifact),
                "fold_files": {
                    name: [
                        {"path": path, "sha256": sha256_file(path)}
                        for path in paths
                    ]
                    for name, paths in source_paths.items()
                },
                "moved_rows": moved,
                "transition_matrix": transition_matrix,
                "diagnostics": diagnostics,
            }
        ],
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(output, output_path)
    report_out = {
        "moved_rows": moved,
        "moved_fraction": moved / len(ids),
        "transition_matrix": transition_matrix,
        "diagnostics": diagnostics,
        "output": str(output_path),
        "output_sha256": sha256_file(output_path),
    }
    report_path = output_path.with_suffix(".report.json")
    report_path.write_text(json.dumps(report_out, indent=2), encoding="utf-8")
    print(json.dumps(report_out, indent=2))


if __name__ == "__main__":
    main()
