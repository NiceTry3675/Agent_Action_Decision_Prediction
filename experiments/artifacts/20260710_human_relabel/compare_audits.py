#!/usr/bin/env python3
"""Compare two blinded audits against the primary human annotations."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from merge_primary import read_jsonl, ratio, validate_annotation, write_jsonl


ROOT = Path(__file__).resolve().parent


def load_annotations(path: Path) -> dict[str, dict]:
    rows = read_jsonl(path)
    result = {}
    for line_no, row in enumerate(rows, 1):
        validate_annotation(row, path, line_no)
        if row["id"] in result:
            raise ValueError(f"{path}: duplicate id {row['id']}")
        result[row["id"]] = row
    return result


def main() -> None:
    primary_rows = read_jsonl(ROOT / "human_relabels_primary.jsonl")
    primary = {row["id"]: row for row in primary_rows}
    audit_input = read_jsonl(ROOT / "inputs" / "audit_shared_80.jsonl")
    expected_ids = [row["id"] for row in audit_input]
    expected_set = set(expected_ids)

    audit_a = load_annotations(ROOT / "annotations" / "audit_a.jsonl")
    audit_b = load_annotations(ROOT / "annotations" / "audit_b.jsonl")
    for name, rows in (("audit_a", audit_a), ("audit_b", audit_b)):
        if set(rows) != expected_set:
            raise ValueError(
                f"{name} id mismatch: missing={len(expected_set - set(rows))}, "
                f"extra={len(set(rows) - expected_set)}"
            )

    comparisons = []
    for row in audit_input:
        row_id = row["id"]
        p = primary[row_id]
        a = audit_a[row_id]
        b = audit_b[row_id]
        labels = [p["human_label"], a["human_label"], b["human_label"]]
        counts = Counter(labels)
        majority_label, majority_count = counts.most_common(1)[0]
        if majority_count < 2:
            majority_label = None
        comparisons.append(
            {
                "id": row_id,
                "current_prompt": row["current_prompt"],
                "history": row["history"],
                "dataset_true": p["dataset_true"],
                "model_pred_raw": p["model_pred_raw"],
                "primary": {
                    key: p[key]
                    for key in (
                        "human_label",
                        "acceptable_labels",
                        "information_need",
                        "identifiability",
                        "confidence",
                        "rationale_ko",
                    )
                },
                "audit_a": a,
                "audit_b": b,
                "unanimous_label": labels[0] if len(set(labels)) == 1 else None,
                "majority_label": majority_label,
                "all_three_different": len(set(labels)) == 3,
                "auditors_agree_against_primary": (
                    a["human_label"] == b["human_label"] != p["human_label"]
                ),
            }
        )

    write_jsonl(ROOT / "audit_comparison.jsonl", comparisons)
    adjudication = [
        row
        for row in comparisons
        if row["all_three_different"] or row["auditors_agree_against_primary"]
    ]
    write_jsonl(ROOT / "needs_adjudication.jsonl", adjudication)

    n = len(comparisons)
    summary = {
        "n_audited": n,
        "primary_vs_a": ratio(
            sum(
                row["primary"]["human_label"] == row["audit_a"]["human_label"]
                for row in comparisons
            ),
            n,
        ),
        "primary_vs_b": ratio(
            sum(
                row["primary"]["human_label"] == row["audit_b"]["human_label"]
                for row in comparisons
            ),
            n,
        ),
        "audit_a_vs_b": ratio(
            sum(
                row["audit_a"]["human_label"] == row["audit_b"]["human_label"]
                for row in comparisons
            ),
            n,
        ),
        "three_way_unanimous": ratio(
            sum(row["unanimous_label"] is not None for row in comparisons), n
        ),
        "majority_exists": ratio(
            sum(row["majority_label"] is not None for row in comparisons), n
        ),
        "all_three_different": ratio(
            sum(row["all_three_different"] for row in comparisons), n
        ),
        "auditors_agree_against_primary": ratio(
            sum(row["auditors_agree_against_primary"] for row in comparisons), n
        ),
        "needs_adjudication": len(adjudication),
    }
    with (ROOT / "summary_audit.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
