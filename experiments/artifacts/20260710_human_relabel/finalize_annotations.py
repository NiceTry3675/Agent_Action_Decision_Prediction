#!/usr/bin/env python3
"""Apply human adjudications and produce the final relabel artifact."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from merge_primary import read_jsonl, ratio, validate_annotation, write_jsonl


ROOT = Path(__file__).resolve().parent
ANNOTATION_KEYS = (
    "human_label",
    "acceptable_labels",
    "information_need",
    "identifiability",
    "confidence",
    "rationale_ko",
)


def main() -> None:
    primary_rows = read_jsonl(ROOT / "human_relabels_primary.jsonl")
    comparisons = {
        row["id"]: row for row in read_jsonl(ROOT / "audit_comparison.jsonl")
    }
    adjudications = {}
    adjudication_path = ROOT / "adjudications.jsonl"
    for line_no, row in enumerate(read_jsonl(adjudication_path), 1):
        validate_annotation(row, adjudication_path, line_no)
        adjudications[row["id"]] = row

    expected_adjudications = {
        row_id
        for row_id, row in comparisons.items()
        if row["auditors_agree_against_primary"] or row["all_three_different"]
    }
    if set(adjudications) != expected_adjudications:
        raise ValueError(
            "adjudication ids do not match the audit disagreement queue: "
            f"missing={expected_adjudications - set(adjudications)}, "
            f"extra={set(adjudications) - expected_adjudications}"
        )

    final_rows = []
    for source in primary_rows:
        row = dict(source)
        row_id = row["id"]
        comparison = comparisons.get(row_id)
        if comparison is None:
            review_status = "not_audited"
            votes = None
        else:
            votes = {
                "primary": comparison["primary"]["human_label"],
                "audit_a": comparison["audit_a"]["human_label"],
                "audit_b": comparison["audit_b"]["human_label"],
            }
            if row_id in adjudications:
                review_status = "manually_adjudicated"
                for key in ANNOTATION_KEYS:
                    row[key] = adjudications[row_id][key]
            elif comparison["unanimous_label"] is not None:
                review_status = "three_way_unanimous"
            else:
                review_status = "two_of_three_primary"

        acceptable = set(row["acceptable_labels"])
        row["dataset_true_matches_human"] = row["dataset_true"] == row["human_label"]
        row["dataset_true_is_acceptable"] = row["dataset_true"] in acceptable
        row["model_raw_matches_human"] = row["model_pred_raw"] == row["human_label"]
        row["model_raw_is_acceptable"] = row["model_pred_raw"] in acceptable
        row["review_status"] = review_status
        row["review_votes"] = votes
        final_rows.append(row)

    write_jsonl(ROOT / "human_relabels_final.jsonl", final_rows)

    clear = [row for row in final_rows if row["identifiability"] == "clear"]
    by_true = {}
    for true_label in sorted({row["dataset_true"] for row in final_rows}):
        group = [row for row in final_rows if row["dataset_true"] == true_label]
        by_true[true_label] = {
            "n": len(group),
            "human_label_counts": dict(Counter(row["human_label"] for row in group)),
            "identifiability_counts": dict(Counter(row["identifiability"] for row in group)),
            "primary_agreement": ratio(
                sum(row["dataset_true_matches_human"] for row in group), len(group)
            ),
            "acceptable_agreement": ratio(
                sum(row["dataset_true_is_acceptable"] for row in group), len(group)
            ),
        }

    summary = {
        "n_unique_rows": len(final_rows),
        "sampling_note": (
            "Error-enriched stratified weak-class casebook; these rates do not "
            "estimate label quality over the full training or validation set."
        ),
        "human_label_counts": dict(Counter(row["human_label"] for row in final_rows)),
        "information_need_counts": dict(
            Counter(row["information_need"] for row in final_rows)
        ),
        "identifiability_counts": dict(
            Counter(row["identifiability"] for row in final_rows)
        ),
        "confidence_counts": dict(Counter(row["confidence"] for row in final_rows)),
        "review_status_counts": dict(
            Counter(row["review_status"] for row in final_rows)
        ),
        "dataset_true_primary_agreement": ratio(
            sum(row["dataset_true_matches_human"] for row in final_rows),
            len(final_rows),
        ),
        "dataset_true_acceptable_agreement": ratio(
            sum(row["dataset_true_is_acceptable"] for row in final_rows),
            len(final_rows),
        ),
        "model_raw_primary_agreement": ratio(
            sum(row["model_raw_matches_human"] for row in final_rows), len(final_rows)
        ),
        "model_raw_acceptable_agreement": ratio(
            sum(row["model_raw_is_acceptable"] for row in final_rows), len(final_rows)
        ),
        "clear_rows": {
            "n": len(clear),
            "dataset_true_primary_agreement": ratio(
                sum(row["dataset_true_matches_human"] for row in clear), len(clear)
            ),
            "dataset_true_acceptable_agreement": ratio(
                sum(row["dataset_true_is_acceptable"] for row in clear), len(clear)
            ),
            "model_raw_primary_agreement": ratio(
                sum(row["model_raw_matches_human"] for row in clear), len(clear)
            ),
        },
        "by_dataset_true": by_true,
        "audit_reliability": json.loads((ROOT / "summary_audit.json").read_text()),
    }
    with (ROOT / "summary_final.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
