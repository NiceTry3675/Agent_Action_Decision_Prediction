#!/usr/bin/env python3
"""Validate, join, and summarize blinded human annotations."""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "20260709_weak_qual_casebook" / "weak_stratified_cases.jsonl"
BLINDED = ROOT / "inputs" / "blinded_unique.jsonl"
ANNOTATION_DIR = ROOT / "annotations"
TRAIN_ROWS = ROOT.parents[2] / "open" / "data" / "train.jsonl"

LABELS = {
    "list_directory",
    "read_file",
    "grep_search",
    "glob_pattern",
    "other",
    "underdetermined",
}
INFORMATION_NEEDS = {
    "orient_layout",
    "inspect_known_file",
    "locate_content_occurrence",
    "enumerate_path_candidates",
    "other",
    "unclear",
}
IDENTIFIABILITY = {"clear", "plausible_multi", "underdetermined"}
CONFIDENCE = {"high", "medium", "low"}
REQUIRED_KEYS = {
    "id",
    "human_label",
    "acceptable_labels",
    "information_need",
    "identifiability",
    "confidence",
    "rationale_ko",
}


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def validate_annotation(row: dict, path: Path, line_no: int) -> None:
    missing = REQUIRED_KEYS - row.keys()
    if missing:
        raise ValueError(f"{path}:{line_no}: missing keys {sorted(missing)}")
    if row["human_label"] not in LABELS:
        raise ValueError(f"{path}:{line_no}: invalid human_label")
    acceptable = row["acceptable_labels"]
    if not isinstance(acceptable, list) or not acceptable:
        raise ValueError(f"{path}:{line_no}: acceptable_labels must be non-empty")
    if any(label not in LABELS for label in acceptable):
        raise ValueError(f"{path}:{line_no}: invalid acceptable label")
    if acceptable[0] != row["human_label"]:
        raise ValueError(f"{path}:{line_no}: primary label must be first")
    if len(set(acceptable)) != len(acceptable):
        raise ValueError(f"{path}:{line_no}: duplicate acceptable label")
    if row["information_need"] not in INFORMATION_NEEDS:
        raise ValueError(f"{path}:{line_no}: invalid information_need")
    if row["identifiability"] not in IDENTIFIABILITY:
        raise ValueError(f"{path}:{line_no}: invalid identifiability")
    if row["confidence"] not in CONFIDENCE:
        raise ValueError(f"{path}:{line_no}: invalid confidence")
    if not isinstance(row["rationale_ko"], str) or not row["rationale_ko"].strip():
        raise ValueError(f"{path}:{line_no}: empty rationale")


def ratio(numerator: int, denominator: int) -> dict:
    return {
        "count": numerator,
        "total": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def recover_generator_actions(source_by_id: dict[str, dict]) -> dict[str, dict]:
    """Recover the labeled target action from the following session row."""
    train_by_id = {row["id"]: row for row in read_jsonl(TRAIN_ROWS)}
    recovered = {}
    for row_id, source in source_by_id.items():
        session_id, step_text = row_id.rsplit("-step_", 1)
        next_id = f"{session_id}-step_{int(step_text) + 1:02d}"
        successor = train_by_id.get(next_id)
        if successor is None:
            continue
        history = successor.get("history") or []
        prompt = source.get("current_prompt")
        for index in range(len(history) - 1):
            user_event = history[index]
            action_event = history[index + 1]
            if (
                user_event.get("role") == "user"
                and user_event.get("content") == prompt
                and action_event.get("role") == "assistant_action"
            ):
                recovered[row_id] = {
                    "name": action_event.get("name"),
                    "args": action_event.get("args"),
                    "result_summary": action_event.get("result_summary"),
                    "observed_in_row": next_id,
                }
                break
    return recovered


def main() -> None:
    blinded = read_jsonl(BLINDED)
    expected_ids = [row["id"] for row in blinded]
    expected_set = set(expected_ids)
    if len(expected_set) != len(expected_ids):
        raise ValueError("blinded input contains duplicate ids")

    annotation_paths = sorted(ANNOTATION_DIR.glob("primary_*.jsonl"))
    if len(annotation_paths) != 6:
        raise ValueError(f"expected 6 primary files, found {len(annotation_paths)}")

    annotations: dict[str, dict] = {}
    file_counts: dict[str, int] = {}
    for path in annotation_paths:
        rows = read_jsonl(path)
        file_counts[path.name] = len(rows)
        for line_no, row in enumerate(rows, 1):
            validate_annotation(row, path, line_no)
            row_id = row["id"]
            if row_id in annotations:
                raise ValueError(f"duplicate annotation id: {row_id}")
            annotations[row_id] = row

    missing = expected_set - annotations.keys()
    extra = annotations.keys() - expected_set
    if missing or extra:
        raise ValueError(f"annotation id mismatch: missing={len(missing)}, extra={len(extra)}")

    source_by_id: dict[str, dict] = {}
    case_roles: defaultdict[str, list[str]] = defaultdict(list)
    for row in read_jsonl(SOURCE):
        source_by_id.setdefault(row["id"], row)
        role = row.get("case_role")
        if role and role not in case_roles[row["id"]]:
            case_roles[row["id"]].append(role)
    generator_actions = recover_generator_actions(source_by_id)

    joined = []
    for blinded_row in blinded:
        row_id = blinded_row["id"]
        src = source_by_id[row_id]
        ann = annotations[row_id]
        acceptable = set(ann["acceptable_labels"])
        joined.append(
            {
                "id": row_id,
                "row_pos": src.get("row_pos"),
                "train_index": src.get("train_index"),
                "case_roles": case_roles[row_id],
                "dataset_true": src.get("true"),
                "model_pred_raw": src.get("pred_raw"),
                "model_pred_bias2stage": src.get("pred_bias2stage"),
                "generator_target_action": generator_actions.get(row_id),
                "top5_raw": src.get("top5_raw"),
                "current_prompt": src.get("current_prompt"),
                "history": src.get("history_compact") or src.get("history_full") or [],
                "session_meta": src.get("session_meta"),
                **ann,
                "dataset_true_matches_human": src.get("true") == ann["human_label"],
                "dataset_true_is_acceptable": src.get("true") in acceptable,
                "model_raw_matches_human": src.get("pred_raw") == ann["human_label"],
                "model_raw_is_acceptable": src.get("pred_raw") in acceptable,
            }
        )

    write_jsonl(ROOT / "human_relabels_primary.jsonl", joined)

    human_counts = Counter(row["human_label"] for row in joined)
    ident_counts = Counter(row["identifiability"] for row in joined)
    confidence_counts = Counter(row["confidence"] for row in joined)
    need_counts = Counter(row["information_need"] for row in joined)

    clear = [row for row in joined if row["identifiability"] == "clear"]
    by_true = {}
    for true_label in sorted({row["dataset_true"] for row in joined}):
        group = [row for row in joined if row["dataset_true"] == true_label]
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
        "n_unique_rows": len(joined),
        "generator_target_action_recovery": ratio(
            sum(row["generator_target_action"] is not None for row in joined),
            len(joined),
        ),
        "recovered_action_matches_dataset_true": ratio(
            sum(
                row["generator_target_action"] is not None
                and row["generator_target_action"]["name"] == row["dataset_true"]
                for row in joined
            ),
            sum(row["generator_target_action"] is not None for row in joined),
        ),
        "annotation_file_counts": file_counts,
        "human_label_counts": dict(human_counts),
        "information_need_counts": dict(need_counts),
        "identifiability_counts": dict(ident_counts),
        "confidence_counts": dict(confidence_counts),
        "dataset_true_primary_agreement": ratio(
            sum(row["dataset_true_matches_human"] for row in joined), len(joined)
        ),
        "dataset_true_acceptable_agreement": ratio(
            sum(row["dataset_true_is_acceptable"] for row in joined), len(joined)
        ),
        "model_raw_primary_agreement": ratio(
            sum(row["model_raw_matches_human"] for row in joined), len(joined)
        ),
        "model_raw_acceptable_agreement": ratio(
            sum(row["model_raw_is_acceptable"] for row in joined), len(joined)
        ),
        "clear_rows": {
            "n": len(clear),
            "dataset_true_primary_agreement": ratio(
                sum(row["dataset_true_matches_human"] for row in clear), len(clear)
            ),
            "dataset_true_acceptable_agreement": ratio(
                sum(row["dataset_true_is_acceptable"] for row in clear), len(clear)
            ),
        },
        "by_dataset_true": by_true,
    }
    with (ROOT / "summary_primary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    priority = [
        row
        for row in joined
        if row["identifiability"] != "clear"
        or row["confidence"] == "low"
        or not row["dataset_true_is_acceptable"]
    ]
    priority_ids = {row["id"] for row in priority}
    remainder = [row for row in joined if row["id"] not in priority_ids]
    random.Random(20260710).shuffle(priority)
    random.Random(20260711).shuffle(remainder)
    selected = (priority + remainder)[:80]
    blinded_by_id = {row["id"]: row for row in blinded}
    audit_rows = [blinded_by_id[row["id"]] for row in selected]
    write_jsonl(ROOT / "inputs" / "audit_shared_80.jsonl", audit_rows)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"audit queue: {len(audit_rows)} rows ({min(len(priority), 80)} priority)")


if __name__ == "__main__":
    main()
