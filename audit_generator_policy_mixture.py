import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from scipy.stats import chi2_contingency
from sklearn.metrics import adjusted_mutual_info_score

from script import ALL_CLASSES, load_jsonl
from train import CLASS_TO_ID, f1_metrics, load_labels, session_id


WEAK4_IDS = list(range(4))
START_ACTION = "<START>"
OTHER_PREDICTION = "<OTHER>"
POLICY_FEATURES = (
    "source",
    "last_action",
    "source_last_action",
    "prev_last_action",
    "source_prev_last_action",
)
SOURCE_COMPARISONS = (
    ("source", "raw_main"),
    ("source_last_action", "last_action"),
    ("source_prev_last_action", "prev_last_action"),
)
SUPPORT_THRESHOLDS = {
    "rows": 1000,
    "sessions": 200,
    "weak4_rows": 250,
    "each_weak4_label": 50,
    "fold_rows": 100,
    "fold_each_weak4_label": 5,
}
IMPROVEMENT_THRESHOLDS = {
    "macro_f1": 0.001,
    "weak4_macro_f1": 0.002,
    "positive_folds": 4,
}


def torch_load(path):
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def source_prefix(sample_id):
    parts = str(sample_id).split("_")
    if len(parts) < 3 or parts[0] != "sess" or not parts[1]:
        return "<UNPARSED>"
    return "_".join(parts[:2])


def action_context(sample):
    actions = [
        str(event.get("name") or "")
        for event in sample.get("history") or []
        if event.get("role") == "assistant_action" and event.get("name")
    ]
    last = actions[-1] if actions else START_ACTION
    prev = actions[-2] if len(actions) >= 2 else START_ACTION
    return prev, last


def session_fold(sample_id, n_folds, seed):
    group = session_id(sample_id)
    digest = hashlib.sha256(f"{seed}:{group}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % n_folds


def feature_values(source, prev, last):
    return {
        "source": source,
        "last_action": last,
        "source_last_action": f"{source}|{last}",
        "prev_last_action": f"{prev}>{last}",
        "source_prev_last_action": f"{source}|{prev}>{last}",
    }


def build_records(samples, labels, n_folds, seed):
    records = []
    seen = set()
    for sample in samples:
        sample_id = str(sample.get("id") or "")
        if not sample_id or sample_id in seen:
            raise ValueError(f"missing or duplicate train id: {sample_id!r}")
        seen.add(sample_id)
        if sample_id not in labels:
            raise ValueError(f"label missing for train id: {sample_id}")
        label = labels[sample_id]
        if label not in CLASS_TO_ID:
            raise ValueError(f"unknown label for {sample_id}: {label}")
        source = source_prefix(sample_id)
        prev, last = action_context(sample)
        records.append(
            {
                "id": sample_id,
                "session": session_id(sample_id),
                "fold": session_fold(sample_id, n_folds, seed),
                "source": source,
                "prev_action": prev,
                "last_action": last,
                "features": feature_values(source, prev, last),
                "y": CLASS_TO_ID[label],
                "sample": sample,
            }
        )
    extra_labels = sorted(set(labels) - seen)
    if extra_labels:
        raise ValueError(f"labels contain ids absent from train JSONL: {extra_labels[:5]}")
    session_to_fold = {}
    for record in records:
        previous = session_to_fold.setdefault(record["session"], record["fold"])
        if previous != record["fold"]:
            raise AssertionError(f"session spans folds: {record['session']}")
    return records


def entropy_bits(values):
    counts = Counter(values)
    total = sum(counts.values())
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def mutual_information_bits(labels, features):
    if len(labels) != len(features) or not labels:
        raise ValueError("MI inputs must be non-empty and aligned")
    joint = Counter(zip(features, labels))
    feature_counts = Counter(features)
    label_counts = Counter(labels)
    total = len(labels)
    value = 0.0
    for (feature, label), count in joint.items():
        value += (count / total) * math.log2(
            (count * total) / (feature_counts[feature] * label_counts[label])
        )
    return value


def summarize_values(values):
    return {
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
    }


def mi_one(labels, features):
    labels = [int(value) for value in labels]
    features = [str(value) for value in features]
    label_entropy = entropy_bits(labels)
    mi = mutual_information_bits(labels, features)
    return {
        "rows": len(labels),
        "feature_cardinality": len(set(features)),
        "label_entropy_bits": label_entropy,
        "mi_bits": mi,
        "mi_over_label_entropy": mi / label_entropy if label_entropy else 0.0,
        "adjusted_mutual_information": float(adjusted_mutual_info_score(labels, features)),
    }


def mi_report(records, n_folds):
    features = {
        "source": [record["source"] for record in records],
        "last_action": [record["last_action"] for record in records],
        "source_x_last_action": [
            record["features"]["source_last_action"] for record in records
        ],
        "prev_action_x_last_action": [
            record["features"]["prev_last_action"] for record in records
        ],
        "source_x_prev_action_x_last_action": [
            record["features"]["source_prev_last_action"] for record in records
        ],
    }
    labels = [record["y"] for record in records]
    folds = [record["fold"] for record in records]
    report = {}
    for name, values in features.items():
        fold_rows = []
        for fold in range(n_folds):
            indices = [idx for idx, value in enumerate(folds) if value == fold]
            row = mi_one([labels[idx] for idx in indices], [values[idx] for idx in indices])
            row["fold"] = fold
            fold_rows.append(row)
        overall = mi_one(labels, values)
        overall["folds"] = fold_rows
        overall["fold_mi_summary"] = summarize_values([row["mi_bits"] for row in fold_rows])
        overall["fold_adjusted_mi_summary"] = summarize_values(
            [row["adjusted_mutual_information"] for row in fold_rows]
        )
        report[name] = overall
    report["incremental"] = {
        "source_last_minus_last_bits": (
            report["source_x_last_action"]["mi_bits"] - report["last_action"]["mi_bits"]
        ),
        "source_prev_last_minus_prev_last_bits": (
            report["source_x_prev_action_x_last_action"]["mi_bits"]
            - report["prev_action_x_last_action"]["mi_bits"]
        ),
        "fold_source_last_minus_last_bits": [
            report["source_x_last_action"]["folds"][fold]["mi_bits"]
            - report["last_action"]["folds"][fold]["mi_bits"]
            for fold in range(n_folds)
        ],
        "fold_source_prev_last_minus_prev_last_bits": [
            report["source_x_prev_action_x_last_action"]["folds"][fold]["mi_bits"]
            - report["prev_action_x_last_action"]["folds"][fold]["mi_bits"]
            for fold in range(n_folds)
        ],
    }
    return report


def source_support(records, n_folds):
    grouped = defaultdict(list)
    for record in records:
        grouped[record["source"]].append(record)
    report = {}
    for source, rows in sorted(grouped.items()):
        label_counts = Counter(record["y"] for record in rows)
        fold_rows = []
        for fold in range(n_folds):
            subset = [record for record in rows if record["fold"] == fold]
            counts = Counter(record["y"] for record in subset)
            fold_rows.append(
                {
                    "fold": fold,
                    "rows": len(subset),
                    "sessions": len({record["session"] for record in subset}),
                    "weak4_label_counts": {
                        ALL_CLASSES[class_id]: counts[class_id] for class_id in WEAK4_IDS
                    },
                }
            )
        weak4_rows = sum(label_counts[class_id] for class_id in WEAK4_IDS)
        checks = {
            "rows": len(rows) >= SUPPORT_THRESHOLDS["rows"],
            "sessions": len({record["session"] for record in rows})
            >= SUPPORT_THRESHOLDS["sessions"],
            "weak4_rows": weak4_rows >= SUPPORT_THRESHOLDS["weak4_rows"],
            "each_weak4_label": min(label_counts[class_id] for class_id in WEAK4_IDS)
            >= SUPPORT_THRESHOLDS["each_weak4_label"],
            "fold_rows": min(row["rows"] for row in fold_rows)
            >= SUPPORT_THRESHOLDS["fold_rows"],
            "fold_each_weak4_label": min(
                row["weak4_label_counts"][ALL_CLASSES[class_id]]
                for row in fold_rows
                for class_id in WEAK4_IDS
            )
            >= SUPPORT_THRESHOLDS["fold_each_weak4_label"],
        }
        report[source] = {
            "rows": len(rows),
            "sessions": len({record["session"] for record in rows}),
            "weak4_rows": weak4_rows,
            "label_counts": {
                ALL_CLASSES[class_id]: label_counts[class_id]
                for class_id in range(len(ALL_CLASSES))
            },
            "folds": fold_rows,
            "support_checks": checks,
            "sufficient": all(checks.values()),
        }
    return report


def normalized_counts(counts):
    total = sum(counts)
    if total == 0:
        return [0.0 for _ in counts]
    return [count / total for count in counts]


def transition_report(records):
    action_rows = [START_ACTION] + list(ALL_CLASSES)
    report = {}
    by_source = defaultdict(list)
    for record in records:
        by_source[record["source"]].append(record)
    for source, rows in sorted(by_source.items()):
        matrix = []
        for action in action_rows:
            counts = [0 for _ in ALL_CLASSES]
            for record in rows:
                if record["last_action"] == action:
                    counts[record["y"]] += 1
            matrix.append(
                {
                    "last_action": action,
                    "support": sum(counts),
                    "next_counts": dict(zip(ALL_CLASSES, counts)),
                    "next_probabilities": dict(zip(ALL_CLASSES, normalized_counts(counts))),
                }
            )
        pair_counts = defaultdict(lambda: [0 for _ in ALL_CLASSES])
        for record in rows:
            pair = f"{record['prev_action']}>{record['last_action']}"
            pair_counts[pair][record["y"]] += 1
        pair_rows = []
        for pair, counts in pair_counts.items():
            pair_rows.append(
                {
                    "prev_last": pair,
                    "support": sum(counts),
                    "next_counts": dict(zip(ALL_CLASSES, counts)),
                    "next_probabilities": dict(zip(ALL_CLASSES, normalized_counts(counts))),
                }
            )
        pair_rows.sort(key=lambda row: (-row["support"], row["prev_last"]))
        report[source] = {
            "last_to_next_matrix": matrix,
            "prev_last_to_next": pair_rows,
        }
    return report


def metric_summary(y_true, y_pred):
    y_true = [int(value) for value in y_true]
    y_pred = [int(value) for value in y_pred]
    metrics = f1_metrics(y_true, y_pred)
    return {
        "rows": len(y_true),
        "accuracy": sum(left == right for left, right in zip(y_true, y_pred)) / len(y_true),
        "error_rate": sum(left != right for left, right in zip(y_true, y_pred)) / len(y_true),
        "macro_f1": metrics["macro_f1"],
        "weak4_macro_f1": sum(
            metrics["per_class_f1"][ALL_CLASSES[class_id]] for class_id in WEAK4_IDS
        )
        / len(WEAK4_IDS),
        "per_class_f1": metrics["per_class_f1"],
    }


def weak_confusion(y_true, y_pred, indices):
    columns = list(ALL_CLASSES[:4]) + [OTHER_PREDICTION]
    matrix = {label: {column: 0 for column in columns} for label in ALL_CLASSES[:4]}
    for idx in indices:
        true_id = int(y_true[idx])
        if true_id not in WEAK4_IDS:
            continue
        pred_id = int(y_pred[idx])
        column = ALL_CLASSES[pred_id] if pred_id in WEAK4_IDS else OTHER_PREDICTION
        matrix[ALL_CLASSES[true_id]][column] += 1
    probabilities = {}
    for label, row in matrix.items():
        values = [row[column] for column in columns]
        probabilities[label] = dict(zip(columns, normalized_counts(values)))
    return {"columns": columns, "counts": matrix, "row_probabilities": probabilities}


def jensen_shannon_bits(left, right):
    left = np.asarray(left, dtype=np.float64)
    right = np.asarray(right, dtype=np.float64)
    middle = 0.5 * (left + right)

    def kl(values, reference):
        mask = values > 0
        return float(np.sum(values[mask] * np.log2(values[mask] / reference[mask])))

    return 0.5 * kl(left, middle) + 0.5 * kl(right, middle)


def contingency_test(source_values, correct_values):
    sources = sorted(set(source_values))
    if len(sources) != 2:
        return {"evaluated": False, "reason": f"requires exactly two sources, got {sources}"}
    table = []
    for source in sources:
        values = [correct for value, correct in zip(source_values, correct_values) if value == source]
        table.append([sum(values), len(values) - sum(values)])
    chi2, p_value, _, _ = chi2_contingency(table, correction=False)
    total = sum(sum(row) for row in table)
    return {
        "evaluated": True,
        "sources": sources,
        "table_correct_incorrect": table,
        "chi2": float(chi2),
        "p_value": float(p_value),
        "cramers_v": math.sqrt(float(chi2) / total),
    }


def main_logit_report(records, payload, n_folds):
    if list(payload.get("classes") or []) != ALL_CLASSES:
        raise ValueError("main logits class order mismatch")
    logits = torch.as_tensor(payload["logits"]).float().cpu().numpy()
    ids = [str(value) for value in payload["ids"]]
    y_true = np.asarray(payload["y_true"], dtype=np.int64)
    if logits.shape != (len(ids), len(ALL_CLASSES)) or len(ids) != len(y_true):
        raise ValueError("main logits payload row mismatch")
    by_id = {record["id"]: record for record in records}
    if len(ids) != len(set(ids)):
        raise ValueError("main logits payload contains duplicate ids")
    missing = [sample_id for sample_id in ids if sample_id not in by_id]
    if missing:
        raise ValueError(f"main logits ids missing from train rows: {missing[:5]}")
    anchor_records = [by_id[sample_id] for sample_id in ids]
    expected_y = np.asarray([record["y"] for record in anchor_records], dtype=np.int64)
    if not np.array_equal(expected_y, y_true):
        raise ValueError("main logits labels differ from train_labels.csv")
    pred = np.argmax(logits, axis=1)
    sources = np.asarray([record["source"] for record in anchor_records], dtype=object)
    folds = np.asarray([record["fold"] for record in anchor_records], dtype=np.int64)

    source_rows = {}
    for source in sorted(set(sources.tolist())):
        indices = np.where(sources == source)[0]
        weak_indices = indices[np.isin(y_true[indices], WEAK4_IDS)]
        fold_rows = []
        for fold in range(n_folds):
            fold_indices = indices[folds[indices] == fold]
            fold_weak = fold_indices[np.isin(y_true[fold_indices], WEAK4_IDS)]
            fold_rows.append(
                {
                    "fold": fold,
                    "all": metric_summary(y_true[fold_indices], pred[fold_indices]),
                    "weak_true_rows": {
                        "rows": len(fold_weak),
                        "error_rate": float(np.mean(pred[fold_weak] != y_true[fold_weak]))
                        if len(fold_weak)
                        else None,
                    },
                    "weak4_confusion": weak_confusion(y_true, pred, fold_indices),
                }
            )
        source_rows[source] = {
            "all": metric_summary(y_true[indices], pred[indices]),
            "weak_true_rows": {
                "rows": len(weak_indices),
                "error_rate": float(np.mean(pred[weak_indices] != y_true[weak_indices])),
            },
            "weak4_confusion": weak_confusion(y_true, pred, indices),
            "folds": fold_rows,
        }

    ordered_sources = sorted(source_rows, key=lambda value: -source_rows[value]["all"]["rows"])
    reproducibility = {"evaluated": len(ordered_sources) == 2, "sources": ordered_sources}
    if len(ordered_sources) == 2:
        left, right = ordered_sources
        fold_gaps = []
        weak_gaps = []
        fold_js = []
        for fold in range(n_folds):
            left_row = source_rows[left]["folds"][fold]
            right_row = source_rows[right]["folds"][fold]
            fold_gaps.append(left_row["all"]["error_rate"] - right_row["all"]["error_rate"])
            weak_gaps.append(
                left_row["weak_true_rows"]["error_rate"]
                - right_row["weak_true_rows"]["error_rate"]
            )
            class_js = []
            for label in ALL_CLASSES[:4]:
                columns = left_row["weak4_confusion"]["columns"]
                left_probs = [
                    left_row["weak4_confusion"]["row_probabilities"][label][column]
                    for column in columns
                ]
                right_probs = [
                    right_row["weak4_confusion"]["row_probabilities"][label][column]
                    for column in columns
                ]
                left_support = sum(
                    left_row["weak4_confusion"]["counts"][label].values()
                )
                right_support = sum(
                    right_row["weak4_confusion"]["counts"][label].values()
                )
                if min(left_support, right_support) >= SUPPORT_THRESHOLDS["fold_each_weak4_label"]:
                    class_js.append(jensen_shannon_bits(left_probs, right_probs))
            fold_js.append(float(np.mean(class_js)) if class_js else None)
        non_null_js = [value for value in fold_js if value is not None]
        all_sign = all(value > 0 for value in fold_gaps) or all(value < 0 for value in fold_gaps)
        weak_sign = all(value > 0 for value in weak_gaps) or all(value < 0 for value in weak_gaps)
        reproduced = all_sign and weak_sign and len(non_null_js) == n_folds
        reproducibility.update(
            {
                "all_error_rate_gap_left_minus_right": fold_gaps,
                "weak_error_rate_gap_left_minus_right": weak_gaps,
                "weak_confusion_js_bits": fold_js,
                "all_error_gap_same_sign_all_folds": all_sign,
                "weak_error_gap_same_sign_all_folds": weak_sign,
                "reproduced": reproduced,
            }
        )

    correct = pred == y_true
    weak_mask = np.isin(y_true, WEAK4_IDS)
    report = {
        "rows": len(ids),
        "payload_split": payload.get("split"),
        "payload_seed": payload.get("seed"),
        "raw_main": metric_summary(y_true, pred),
        "by_source": source_rows,
        "source_correctness_test": contingency_test(sources.tolist(), correct.tolist()),
        "source_weak_correctness_test": contingency_test(
            sources[weak_mask].tolist(), correct[weak_mask].tolist()
        ),
        "source_confusion_reproducibility": reproducibility,
    }
    return report, anchor_records, logits, y_true, pred


def fit_policy_table(records, indices, feature_name, strength):
    global_counts = np.bincount(
        [records[idx]["y"] for idx in indices], minlength=len(ALL_CLASSES)
    ).astype(np.float64)
    global_probability = (global_counts + 1.0) / (global_counts.sum() + len(ALL_CLASSES))
    counts = defaultdict(lambda: np.zeros(len(ALL_CLASSES), dtype=np.float64))
    for idx in indices:
        record = records[idx]
        counts[record["features"][feature_name]][record["y"]] += 1.0
    probabilities = {}
    log_bias = {}
    for key, values in counts.items():
        posterior = (values + strength * global_probability) / (values.sum() + strength)
        probabilities[key] = posterior
        log_bias[key] = np.log(np.clip(posterior, 1e-12, None)) - np.log(global_probability)
    return {
        "global_probability": global_probability,
        "probabilities": probabilities,
        "log_bias": log_bias,
    }


def table_bias(table, records, indices, feature_name):
    zero = np.zeros(len(ALL_CLASSES), dtype=np.float64)
    return np.stack(
        [
            table["log_bias"].get(records[idx]["features"][feature_name], zero)
            for idx in indices
        ]
    )


def table_prediction(table, records, indices, feature_name):
    fallback = int(np.argmax(table["global_probability"]))
    return np.asarray(
        [
            int(
                np.argmax(
                    table["probabilities"].get(
                        records[idx]["features"][feature_name],
                        table["global_probability"],
                    )
                )
            )
            if records[idx]["features"][feature_name] in table["probabilities"]
            else fallback
            for idx in indices
        ],
        dtype=np.int64,
    )


def grouped_policy_cv(records, anchor_records, logits, y_true, n_folds, strength, beta_grid):
    record_pos = {record["id"]: idx for idx, record in enumerate(records)}
    anchor_positions = np.asarray([record_pos[record["id"]] for record in anchor_records])
    anchor_folds = np.asarray([record["fold"] for record in anchor_records])
    all_folds = np.asarray([record["fold"] for record in records])
    raw_pred = np.argmax(logits, axis=1)

    tuned_predictions = {
        feature: np.full(len(anchor_records), -1, dtype=np.int64)
        for feature in POLICY_FEATURES
    }
    fixed_predictions = {
        feature: {
            beta: np.full(len(anchor_records), -1, dtype=np.int64) for beta in beta_grid
        }
        for feature in POLICY_FEATURES
    }
    policy_only_predictions = {
        feature: np.full(len(records), -1, dtype=np.int64) for feature in POLICY_FEATURES
    }
    folds_by_feature = {feature: [] for feature in POLICY_FEATURES}

    for fold in range(n_folds):
        fit_indices = np.where(all_folds != fold)[0].tolist()
        policy_val_indices = np.where(all_folds == fold)[0].tolist()
        anchor_train = np.where(anchor_folds != fold)[0]
        anchor_val = np.where(anchor_folds == fold)[0]
        anchor_train_positions = anchor_positions[anchor_train].tolist()
        anchor_val_positions = anchor_positions[anchor_val].tolist()
        for feature in POLICY_FEATURES:
            table = fit_policy_table(records, fit_indices, feature, strength)
            train_bias = table_bias(table, records, anchor_train_positions, feature)
            val_bias = table_bias(table, records, anchor_val_positions, feature)
            train_candidates = []
            for beta in beta_grid:
                candidate = np.argmax(logits[anchor_train] + beta * train_bias, axis=1)
                metrics = metric_summary(y_true[anchor_train], candidate)
                train_candidates.append((metrics["macro_f1"], -beta, beta))
            selected_beta = max(train_candidates)[2]
            tuned = np.argmax(logits[anchor_val] + selected_beta * val_bias, axis=1)
            tuned_predictions[feature][anchor_val] = tuned
            folds_by_feature[feature].append(
                {
                    "fold": fold,
                    "selected_beta": selected_beta,
                    "heldout": metric_summary(y_true[anchor_val], tuned),
                    "raw_main": metric_summary(y_true[anchor_val], raw_pred[anchor_val]),
                }
            )
            for beta in beta_grid:
                fixed_predictions[feature][beta][anchor_val] = np.argmax(
                    logits[anchor_val] + beta * val_bias, axis=1
                )
            policy_only_predictions[feature][policy_val_indices] = table_prediction(
                table, records, policy_val_indices, feature
            )

    for feature in POLICY_FEATURES:
        if bool((tuned_predictions[feature] < 0).any()):
            raise AssertionError(f"missing tuned OOF predictions for {feature}")
        if bool((policy_only_predictions[feature] < 0).any()):
            raise AssertionError(f"missing policy-only OOF predictions for {feature}")
        for beta in beta_grid:
            if bool((fixed_predictions[feature][beta] < 0).any()):
                raise AssertionError(f"missing fixed-beta OOF predictions for {feature}/{beta}")

    main_models = {
        "raw_main": {
            "oof": metric_summary(y_true, raw_pred),
            "folds": [
                {
                    "fold": fold,
                    "heldout": metric_summary(
                        y_true[anchor_folds == fold], raw_pred[anchor_folds == fold]
                    ),
                }
                for fold in range(n_folds)
            ],
        }
    }
    for feature in POLICY_FEATURES:
        main_models[feature] = {
            "selection": "beta chosen on the other four anchor folds by macro-F1",
            "oof": metric_summary(y_true, tuned_predictions[feature]),
            "folds": folds_by_feature[feature],
            "fixed_beta_grid": {
                str(beta): metric_summary(y_true, fixed_predictions[feature][beta])
                for beta in beta_grid
            },
        }

    policy_y = np.asarray([record["y"] for record in records], dtype=np.int64)
    policy_only = {
        feature: metric_summary(policy_y, policy_only_predictions[feature])
        for feature in POLICY_FEATURES
    }
    comparisons = {}
    for source_model, control_model in SOURCE_COMPARISONS:
        source_metrics = main_models[source_model]["oof"]
        control_metrics = main_models[control_model]["oof"]
        raw_metrics = main_models["raw_main"]["oof"]
        fold_deltas = []
        for fold in range(n_folds):
            source_fold = main_models[source_model]["folds"][fold]["heldout"]
            control_fold = main_models[control_model]["folds"][fold]["heldout"]
            raw_fold = main_models["raw_main"]["folds"][fold]["heldout"]
            fold_deltas.append(
                {
                    "fold": fold,
                    "vs_control_macro_f1": (
                        source_fold["macro_f1"] - control_fold["macro_f1"]
                    ),
                    "vs_control_weak4_macro_f1": (
                        source_fold["weak4_macro_f1"] - control_fold["weak4_macro_f1"]
                    ),
                    "vs_raw_main_macro_f1": (
                        source_fold["macro_f1"] - raw_fold["macro_f1"]
                    ),
                    "vs_raw_main_weak4_macro_f1": (
                        source_fold["weak4_macro_f1"] - raw_fold["weak4_macro_f1"]
                    ),
                }
            )
        control_macro_delta = source_metrics["macro_f1"] - control_metrics["macro_f1"]
        control_weak_delta = (
            source_metrics["weak4_macro_f1"] - control_metrics["weak4_macro_f1"]
        )
        raw_macro_delta = source_metrics["macro_f1"] - raw_metrics["macro_f1"]
        raw_weak_delta = source_metrics["weak4_macro_f1"] - raw_metrics["weak4_macro_f1"]
        incremental_reproduced = (
            (
                control_macro_delta >= IMPROVEMENT_THRESHOLDS["macro_f1"]
                and sum(row["vs_control_macro_f1"] > 0 for row in fold_deltas)
                >= IMPROVEMENT_THRESHOLDS["positive_folds"]
            )
            or (
                control_weak_delta >= IMPROVEMENT_THRESHOLDS["weak4_macro_f1"]
                and sum(row["vs_control_weak4_macro_f1"] > 0 for row in fold_deltas)
                >= IMPROVEMENT_THRESHOLDS["positive_folds"]
            )
        )
        beats_raw_reproduced = (
            (
                raw_macro_delta >= IMPROVEMENT_THRESHOLDS["macro_f1"]
                and sum(row["vs_raw_main_macro_f1"] > 0 for row in fold_deltas)
                >= IMPROVEMENT_THRESHOLDS["positive_folds"]
            )
            or (
                raw_weak_delta >= IMPROVEMENT_THRESHOLDS["weak4_macro_f1"]
                and sum(row["vs_raw_main_weak4_macro_f1"] > 0 for row in fold_deltas)
                >= IMPROVEMENT_THRESHOLDS["positive_folds"]
            )
        )
        comparisons[f"{source_model}_vs_{control_model}"] = {
            "vs_control_macro_f1_delta": control_macro_delta,
            "vs_control_weak4_macro_f1_delta": control_weak_delta,
            "vs_raw_main_macro_f1_delta": raw_macro_delta,
            "vs_raw_main_weak4_macro_f1_delta": raw_weak_delta,
            "fold_deltas": fold_deltas,
            "incremental_over_control_reproduced": incremental_reproduced,
            "beats_raw_main_reproduced": beats_raw_reproduced,
            "reproducible_improvement": incremental_reproduced and beats_raw_reproduced,
        }

    policy_comparisons = {}
    for source_model, control_model in SOURCE_COMPARISONS[1:]:
        source_metrics = policy_only[source_model]
        control_metrics = policy_only[control_model]
        policy_comparisons[f"{source_model}_vs_{control_model}"] = {
            "macro_f1_delta": source_metrics["macro_f1"] - control_metrics["macro_f1"],
            "weak4_macro_f1_delta": (
                source_metrics["weak4_macro_f1"] - control_metrics["weak4_macro_f1"]
            ),
        }

    return {
        "method": {
            "folding": "sha256(seed:session_id) modulo n_folds",
            "table_fit": "all train rows outside held-out session fold",
            "smoothing": "Dirichlet posterior toward fold-global label prior",
            "prior_strength": strength,
            "beta_grid": beta_grid,
            "beta_selection": "other four anchor folds, macro-F1, lower beta tie-break",
            "promotion_improvement_thresholds": IMPROVEMENT_THRESHOLDS,
        },
        "main_logit_models": main_models,
        "main_logit_source_comparisons": comparisons,
        "policy_only_models": policy_only,
        "policy_only_source_comparisons": policy_comparisons,
        "source_feature_reproducibly_improves_main": any(
            row["reproducible_improvement"] for row in comparisons.values()
        ),
    }


def test_prefix_report(test_path, known_sources):
    test_samples = load_jsonl(test_path)
    ids = [str(sample.get("id") or "") for sample in test_samples]
    sources = Counter(source_prefix(sample_id) for sample_id in ids)
    unknown = sorted(set(sources) - set(known_sources))
    return {
        "path": str(test_path),
        "rows": len(ids),
        "source_counts": dict(sorted(sources.items())),
        "known_train_sources": sorted(known_sources),
        "unknown_sources": unknown,
        "prefix_scheme_compatible": bool(ids) and not unknown and "<UNPARSED>" not in sources,
        "hidden_private_test_verifiable": False,
        "limitation": "Only the local stub is visible; hidden evaluation IDs cannot be audited locally.",
    }


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument(
        "--main-logits",
        default=(
            "experiments/logits/20260707_100521_gpu_transformer_session_current_v1_"
            "len384_replay-last1_kd_hcx_m8_screen_s42_val_logits.pt"
        ),
    )
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--prior-strength", type=float, default=50.0)
    parser.add_argument("--beta-grid", default="0,0.1,0.2,0.3,0.5,0.75,1.0")
    parser.add_argument(
        "--output",
        default="experiments/artifacts/20260710_generator_policy_mixture_audit.json",
    )
    args = parser.parse_args()
    if args.n_folds < 2:
        parser.error("--n-folds must be at least 2")
    if args.prior_strength <= 0:
        parser.error("--prior-strength must be positive")
    try:
        args.beta_grid = sorted({float(value) for value in args.beta_grid.split(",")})
    except ValueError as exc:
        parser.error(f"invalid --beta-grid: {exc}")
    if not args.beta_grid or args.beta_grid[0] < 0:
        parser.error("--beta-grid must contain non-negative values")
    return args


def main():
    args = parse_args()
    data_dir = Path(args.data_dir)
    train_path = data_dir / "train.jsonl"
    labels_path = data_dir / "train_labels.csv"
    test_path = data_dir / "test.jsonl"
    samples = load_jsonl(train_path)
    labels = load_labels(labels_path)
    records = build_records(samples, labels, args.n_folds, args.seed)
    support = source_support(records, args.n_folds)
    mi = mi_report(records, args.n_folds)
    transitions = transition_report(records)
    payload = torch_load(args.main_logits)
    main_report, anchor_records, logits, y_true, _ = main_logit_report(
        records, payload, args.n_folds
    )
    grouped_cv = grouped_policy_cv(
        records,
        anchor_records,
        logits,
        y_true,
        args.n_folds,
        args.prior_strength,
        args.beta_grid,
    )
    test_report = test_prefix_report(test_path, support)
    gate_checks = {
        "all_source_support_sufficient": all(row["sufficient"] for row in support.values()),
        "source_confusion_reproduced": bool(
            main_report["source_confusion_reproducibility"].get("reproduced")
        ),
        "group_cv_source_feature_improves": grouped_cv[
            "source_feature_reproducibly_improves_main"
        ],
        "local_test_stub_prefix_compatible": test_report["prefix_scheme_compatible"],
    }
    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "config": {
            "data_dir": str(data_dir),
            "main_logits": args.main_logits,
            "n_folds": args.n_folds,
            "seed": args.seed,
            "prior_strength": args.prior_strength,
            "beta_grid": args.beta_grid,
            "classes": ALL_CLASSES,
            "weak4_classes": ALL_CLASSES[:4],
            "source_extraction": "first two underscore-separated ID components",
            "support_thresholds": SUPPORT_THRESHOLDS,
        },
        "rows": len(records),
        "sessions": len({record["session"] for record in records}),
        "source_support": support,
        "mutual_information": mi,
        "source_transition_matrices": transitions,
        "current_v1_main": main_report,
        "session_grouped_cv": grouped_cv,
        "test_prefix_audit": test_report,
        "promotion_gate": {
            "checks": gate_checks,
            "passed": all(gate_checks.values()),
            "hidden_private_test_prefix_unverified": True,
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"rows={report['rows']} sessions={report['sessions']} sources={list(support)}")
    for name in (
        "source",
        "source_x_last_action",
        "source_x_prev_action_x_last_action",
    ):
        row = mi[name]
        print(
            f"MI(label; {name})={row['mi_bits']:.6f} bits "
            f"adjusted={row['adjusted_mutual_information']:.6f}"
        )
    for name, row in grouped_cv["main_logit_source_comparisons"].items():
        print(
            f"{name}: vs_control_macro={row['vs_control_macro_f1_delta']:+.6f} "
            f"vs_raw_macro={row['vs_raw_main_macro_f1_delta']:+.6f} "
            f"vs_raw_weak4={row['vs_raw_main_weak4_macro_f1_delta']:+.6f} "
            f"reproduced={row['reproducible_improvement']}"
        )
    print(f"promotion_gate={'PASS' if report['promotion_gate']['passed'] else 'FAIL'}")
    print(f"saved {output}")


if __name__ == "__main__":
    main()
