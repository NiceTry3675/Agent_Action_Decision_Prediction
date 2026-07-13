#!/usr/bin/env python3
"""Nested champion-OOF probe for the SIM trajectory KEEP/SWITCH router."""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression

from sim_policy_router import (
    CLASSES,
    DEFAULT_EXPERTS,
    C2I,
    SimPolicyTables,
    SimRow,
    aligned_probabilities,
    arg_schema,
    candidate_examples,
    label_probabilities_to_candidate_states,
    macro_f1,
    policy_row_examples,
    result_bucket,
    routed_predictions,
    state_utility_table,
)


FEATURE_FAMILIES = ("pair_policy", "pair_main", "full")
FOLLOWUP_FEATURE_FAMILIES = ("pair_main",)
C_GRID = (0.01, 0.03, 0.1, 0.3)
CLASS_WEIGHT_GRID = (
    (0.5, 1.0, 1.0),
    (0.25, 1.0, 1.0),
    (0.5, 1.0, 1.25),
)
THRESHOLD_GRID = (0.00, 0.03, 0.06, 0.10, 0.15)
THRESHOLD_AUDIT_GRID = (0.00, 0.03, 0.06, 0.10, 0.15, 0.20, 0.30, 0.50, 1.00)
ALPHA_GRID = (0.25, 0.50, 0.75, 1.00)


def stable_fold(value: str, n_folds: int, seed: int) -> int:
    digest = hashlib.sha256(f"{seed}:{value}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % n_folds


def torch_load(path: str | Path):
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def load_champion_oof(oof_glob: str) -> tuple[dict[str, np.ndarray], dict[str, object]]:
    paths = sorted(glob.glob(oof_glob))
    if len(paths) != 3:
        raise ValueError(f"expected exactly three champion folds, found {len(paths)}: {paths}")
    logits_by_id: dict[str, np.ndarray] = {}
    y_by_id: dict[str, int] = {}
    fold_by_id: dict[str, int] = {}
    classes = None
    payload_rows = []
    for fold, path in enumerate(paths):
        payload = torch_load(path)
        if list(payload["classes"]) != CLASSES:
            raise ValueError(f"class order mismatch in {path}")
        if classes is None:
            classes = list(payload["classes"])
        rows = 0
        for sample_id, y_true, logits in zip(
            payload["ids"], payload["y_true"], np.asarray(payload["logits"], dtype=np.float64)
        ):
            sample_id = str(sample_id)
            if sample_id in logits_by_id:
                raise ValueError(f"duplicate OOF id: {sample_id}")
            logits_by_id[sample_id] = logits
            y_by_id[sample_id] = int(y_true)
            fold_by_id[sample_id] = fold
            rows += 1
        payload_rows.append({"path": path, "rows": rows})
    return {
        "logits": logits_by_id,
        "y": y_by_id,
        "fold": fold_by_id,
    }, {"payloads": payload_rows, "classes": classes}


def global_oof_arrays(oof: dict[str, dict]):
    ids = list(oof["logits"])
    return {
        "ids": ids,
        "position": {sample_id: index for index, sample_id in enumerate(ids)},
        "y": np.asarray([oof["y"][sample_id] for sample_id in ids], dtype=np.int64),
        "logits": np.stack([oof["logits"][sample_id] for sample_id in ids]),
        "fold": np.asarray([oof["fold"][sample_id] for sample_id in ids], dtype=np.int64),
    }


def load_sim_rows(
    data_dir: str | Path,
    oof: dict[str, dict],
    global_position: dict[str, int],
) -> tuple[list[SimRow], np.ndarray, np.ndarray, np.ndarray]:
    data_dir = Path(data_dir)
    labels = {
        row["id"]: C2I[row["action"]]
        for row in csv.DictReader((data_dir / "train_labels.csv").open(encoding="utf-8"))
    }
    rows = []
    logits = []
    folds = []
    positions = []
    session_fold: dict[str, int] = {}
    with (data_dir / "train.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            sample = json.loads(line)
            sample_id = str(sample["id"])
            if not sample_id.startswith("sess_sim_"):
                continue
            if sample_id not in oof["logits"]:
                raise ValueError(f"SIM row missing from champion OOF: {sample_id}")
            y_true = labels[sample_id]
            if y_true != oof["y"][sample_id]:
                raise ValueError(f"label mismatch for {sample_id}")
            session = sample_id.rsplit("-step_", 1)[0]
            fold = int(oof["fold"][sample_id])
            old_fold = session_fold.setdefault(session, fold)
            if old_fold != fold:
                raise ValueError(f"champion OOF leaks session across folds: {session}")
            actions = []
            events = []
            last_result = ""
            for event in sample.get("history") or []:
                if event.get("role") != "assistant_action":
                    continue
                action = str(event.get("name") or "")
                actions.append(action)
                last_result = str(event.get("result_summary") or "")
                events.append(
                    f"{action}|{arg_schema(event.get('args'))}|{result_bucket(last_result)}"
                )
            meta = sample.get("session_meta") or {}
            workspace = meta.get("workspace") or {}
            rows.append(SimRow(
                sample_id=sample_id,
                session=session,
                y=y_true,
                turn=int(meta.get("turn_index", len(actions) + 1)),
                actions=tuple(actions),
                events=tuple(events),
                last_result=result_bucket(last_result),
                git_dirty=str(workspace.get("git_dirty", "na")),
                ci_status=str(workspace.get("last_ci_status", "na")),
                open_files=len(workspace.get("open_files") or []),
            ))
            logits.append(oof["logits"][sample_id])
            folds.append(fold)
            positions.append(global_position[sample_id])
    if len(rows) != 64_975:
        raise ValueError(f"expected 64,975 SIM rows, found {len(rows)}")
    return (
        rows,
        np.stack(logits),
        np.asarray(folds, dtype=np.int64),
        np.asarray(positions, dtype=np.int64),
    )


def table_outputs(rows, train_indices, query_indices):
    return SimPolicyTables(rows, train_indices).query(np.asarray(query_indices, dtype=np.int64))


def evaluation(y, main, prediction) -> dict[str, object]:
    y = np.asarray(y)
    main = np.asarray(main)
    prediction = np.asarray(prediction)
    flip = prediction != main
    rescue = (main != y) & (prediction == y)
    harm = (main == y) & (prediction != y)
    neutral = flip & ~rescue & ~harm
    per_class = {}
    for label, name in enumerate(CLASSES):
        tp = int(np.sum((y == label) & (prediction == label)))
        fp = int(np.sum((y != label) & (prediction == label)))
        fn = int(np.sum((y == label) & (prediction != label)))
        denominator = 2 * tp + fp + fn
        per_class[name] = 0.0 if denominator == 0 else 2 * tp / denominator
    return {
        "macro_f1": macro_f1(y, prediction),
        "macro_delta": macro_f1(y, prediction) - macro_f1(y, main),
        "weak4_macro_f1": macro_f1(y, prediction, 4),
        "weak4_delta": macro_f1(y, prediction, 4) - macro_f1(y, main, 4),
        "accuracy": float(np.mean(y == prediction)),
        "accuracy_delta": float(np.mean(y == prediction) - np.mean(y == main)),
        "flips": int(flip.sum()),
        "rescue": int(rescue.sum()),
        "harm": int(harm.sum()),
        "neutral": int(neutral.sum()),
        "per_class_f1": per_class,
    }


def candidate_inventory(
    rows,
    y,
    logits,
    folds,
    global_y=None,
    global_main=None,
    global_positions=None,
) -> dict[str, object]:
    main = logits.argmax(axis=1)
    outer_outputs = {}
    for outer in range(3):
        train = np.where(folds != outer)[0]
        validation = np.where(folds == outer)[0]
        outer_outputs[outer] = (validation, table_outputs(rows, train, validation))
    report = {}
    union_rescue = np.zeros(len(rows), dtype=bool)
    for expert in DEFAULT_EXPERTS:
        candidate = main.copy()
        covered = np.zeros(len(rows), dtype=bool)
        for validation, outputs in outer_outputs.values():
            values = outputs[expert.name]
            route = (
                (main[validation] < 4)
                & (values["pred"] != main[validation])
                & (values["depth"] > 0)
            )
            candidate[validation[route]] = values["pred"][route]
            covered[validation[route]] = True
        rescue = covered & (main != y) & (candidate == y)
        harm = covered & (main == y) & (candidate != y)
        union_rescue |= rescue
        oracle = main.copy()
        oracle[rescue] = candidate[rescue]
        report[expert.name] = {
            "disagreements": int(covered.sum()),
            "rescue": int(rescue.sum()),
            "harm": int(harm.sum()),
            "hard_replace": evaluation(y, main, candidate),
            "candidate_oracle": evaluation(y, main, oracle),
        }
        if global_y is not None:
            global_oracle = np.asarray(global_main).copy()
            global_oracle[np.asarray(global_positions)[rescue]] = np.asarray(global_y)[
                np.asarray(global_positions)[rescue]
            ]
            report[expert.name]["candidate_oracle_full"] = evaluation(
                global_y, global_main, global_oracle
            )
    union_oracle = main.copy()
    # The correct Weak4 label is the only possible rescued alternative.
    union_oracle[union_rescue] = y[union_rescue]
    output = {
        "experts": report,
        "union_rescues": int(union_rescue.sum()),
        "union_oracle": evaluation(y, main, union_oracle),
    }
    if global_y is not None:
        global_union_oracle = np.asarray(global_main).copy()
        global_union_oracle[np.asarray(global_positions)[union_rescue]] = np.asarray(global_y)[
            np.asarray(global_positions)[union_rescue]
        ]
        output["union_oracle_full"] = evaluation(
            global_y, global_main, global_union_oracle
        )
    return output


def fit_model(matrix, target, c_value, class_weights):
    counts = Counter(map(int, target))
    if set(counts) != {0, 1, 2}:
        raise ValueError(f"router train split lacks a state: {counts}")
    weight = {state: float(class_weights[state]) for state in range(3)}
    return LogisticRegression(
        C=float(c_value),
        class_weight=weight,
        max_iter=500,
        solver="lbfgs",
        random_state=42,
    ).fit(matrix, target)


def fit_label_model(matrix, target, c_value):
    counts = Counter(map(int, target))
    if set(counts) != {0, 1, 2, 3, 4}:
        raise ValueError(f"label-posterior train split lacks a class: {counts}")
    return LogisticRegression(
        C=float(c_value),
        max_iter=500,
        solver="lbfgs",
        random_state=42,
    ).fit(matrix, target)


def aligned_label_probabilities(model, matrix):
    raw = model.predict_proba(matrix)
    output = np.zeros((raw.shape[0], 5), dtype=np.float64)
    for column, label in enumerate(model.classes_):
        output[:, int(label)] = raw[:, column]
    return output


def prepare_nested_table_outputs(rows, outer_train, inner_group):
    """Build strict router-fold table features once, independent of router recipe."""
    prepared = []
    for router_fold in range(3):
        validation = outer_train[inner_group == router_fold]
        router_train = outer_train[inner_group != router_fold]
        train_groups = inner_group[inner_group != router_fold]

        # Router-training candidate features are themselves table-OOF after
        # excluding the current router-validation fold.
        train_output = None
        for subfold in sorted(set(train_groups.tolist())):
            sub_validation = router_train[train_groups == subfold]
            table_train = router_train[train_groups != subfold]
            output = table_outputs(rows, table_train, sub_validation)
            if train_output is None:
                train_output = {
                    name: {
                        key: np.empty((len(router_train),) + value.shape[1:], dtype=value.dtype)
                        for key, value in values.items()
                    }
                    for name, values in output.items()
                }
            locations = np.where(train_groups == subfold)[0]
            for name, values in output.items():
                for key, value in values.items():
                    train_output[name][key][locations] = value
        validation_output = table_outputs(rows, router_train, validation)
        prepared.append((router_train, validation, train_output, validation_output))
    return prepared


def vectorize_router_folds(rows, logits, prepared, feature_family):
    folds = []
    for router_train, validation, train_output, validation_output in prepared:
        train_examples = candidate_examples(
            rows, router_train, logits, train_output, feature_family=feature_family
        )
        validation_examples = candidate_examples(
            rows, validation, logits, validation_output, feature_family=feature_family
        )
        vectorizer = DictVectorizer(sparse=True)
        train_matrix = vectorizer.fit_transform(train_examples["features"])
        validation_matrix = vectorizer.transform(validation_examples["features"])
        folds.append({
            "train_examples": train_examples,
            "validation_examples": validation_examples,
            "train_matrix": train_matrix,
            "validation_matrix": validation_matrix,
            "validation": validation,
        })
    return folds


def fit_router_cv(vectorized_folds, c_value, class_weights):
    probability_parts = []
    examples_parts = []
    validation_parts = []
    for fold in vectorized_folds:
        model = fit_model(
            fold["train_matrix"],
            fold["train_examples"]["target"],
            c_value,
            class_weights,
        )
        probability_parts.append(aligned_probabilities(model, fold["validation_matrix"]))
        examples_parts.append(fold["validation_examples"])
        validation_parts.append(fold["validation"])
    return probability_parts, examples_parts, validation_parts


def vectorize_label_folds(rows, logits, prepared, feature_family):
    folds = []
    for router_train, validation, train_output, validation_output in prepared:
        train_examples = policy_row_examples(
            rows, router_train, logits, train_output, feature_family=feature_family
        )
        validation_examples = policy_row_examples(
            rows, validation, logits, validation_output, feature_family=feature_family
        )
        vectorizer = DictVectorizer(sparse=True)
        train_matrix = vectorizer.fit_transform(train_examples["features"])
        validation_matrix = vectorizer.transform(validation_examples["features"])
        folds.append({
            "train_examples": train_examples,
            "validation_examples": validation_examples,
            "train_matrix": train_matrix,
            "validation_matrix": validation_matrix,
            "validation": validation,
        })
    return folds


def fit_label_router_cv_base(vectorized_folds, c_value):
    model_probability_parts = []
    row_examples_parts = []
    validation_parts = []
    for fold in vectorized_folds:
        model = fit_label_model(
            fold["train_matrix"], fold["train_examples"]["target"], c_value
        )
        model_probability = aligned_label_probabilities(model, fold["validation_matrix"])
        model_probability_parts.append(model_probability)
        row_examples_parts.append(fold["validation_examples"])
        validation_parts.append(fold["validation"])
    return model_probability_parts, row_examples_parts, validation_parts


def blend_label_router_cv(base_parts, alpha):
    model_probability_parts, row_examples_parts, validation_parts = base_parts
    probability_parts = []
    examples_parts = []
    for model_probability, row_examples in zip(
        model_probability_parts, row_examples_parts
    ):
        label_probability = (
            (1.0 - alpha) * row_examples["main_prior"]
            + alpha * model_probability
        )
        candidate, state_probability = label_probabilities_to_candidate_states(
            row_examples, label_probability
        )
        probability_parts.append(state_probability)
        examples_parts.append(candidate)
    return probability_parts, examples_parts, validation_parts


def combine_partition_predictions(
    y,
    logits,
    probability_parts,
    examples_parts,
    validation_parts,
    threshold,
    utility_parts,
    global_y,
    global_main,
    global_positions,
    metric_indices,
):
    main = logits.argmax(axis=1)
    prediction = main.copy()
    if not (
        len(probability_parts)
        == len(examples_parts)
        == len(validation_parts)
        == len(utility_parts)
    ):
        raise ValueError("partition predictions and utility tables must align")
    for probability, examples, validation, utility in zip(
        probability_parts, examples_parts, validation_parts, utility_parts
    ):
        local_prediction, _, _ = routed_predictions(
            main[validation], examples, probability, utility, threshold
        )
        prediction[validation] = local_prediction
    global_prediction = global_main.copy()
    covered = np.concatenate(validation_parts)
    global_prediction[global_positions[covered]] = prediction[covered]
    return prediction, evaluation(
        global_y[metric_indices],
        global_main[metric_indices],
        global_prediction[metric_indices],
    )


def inner_utility_tables(
    validation_parts,
    metric_indices,
    global_positions,
    global_y,
    global_main,
):
    """Fit each inner-fold utility without that fold's validation labels."""

    metric_indices = np.asarray(metric_indices, dtype=np.int64)
    global_positions = np.asarray(global_positions, dtype=np.int64)
    utilities = []
    for validation in validation_parts:
        heldout = global_positions[np.asarray(validation, dtype=np.int64)]
        fit_indices = metric_indices[~np.isin(metric_indices, heldout)]
        if len(fit_indices) + len(heldout) != len(metric_indices):
            raise ValueError("inner validation rows are not contained in metric scope")
        utilities.append(
            state_utility_table(global_y[fit_indices], global_main[fit_indices])
        )
    return utilities


def select_router(
    rows,
    y,
    logits,
    outer_train,
    inner_group,
    global_y,
    global_main,
    global_positions,
    metric_indices,
    objective_mode="both",
    label_feature_families=("pair_main",),
):
    candidates = []
    prepared = prepare_nested_table_outputs(rows, outer_train, inner_group)
    validation_parts = [validation for _, validation, _, _ in prepared]
    utility_parts = inner_utility_tables(
        validation_parts,
        metric_indices,
        global_positions,
        global_y,
        global_main,
    )
    # The completed state3 ablation selected pair_main in all three outer
    # folds.  This adaptive follow-up compares objectives on that compact
    # family; the first-run artifact preserves the broader ablation.
    if objective_mode in {"both", "state3"}:
        for feature_family in FOLLOWUP_FEATURE_FAMILIES:
            vectorized = vectorize_router_folds(rows, logits, prepared, feature_family)
            for c_value in C_GRID:
                for class_weights in CLASS_WEIGHT_GRID:
                    parts = fit_router_cv(vectorized, c_value, class_weights)
                    for threshold in THRESHOLD_GRID:
                        _prediction, metric = combine_partition_predictions(
                            y,
                            logits,
                            *parts,
                            threshold,
                            utility_parts,
                            global_y,
                            global_main,
                            global_positions,
                            metric_indices,
                        )
                        candidates.append({
                            "objective": "state3",
                            "feature_family": feature_family,
                            "C": c_value,
                            "class_weights": class_weights,
                            "threshold": threshold,
                            "metric": metric,
                        })
    if objective_mode in {"both", "label5"}:
        for feature_family in label_feature_families:
            label_vectorized = vectorize_label_folds(
                rows, logits, prepared, feature_family
            )
            for c_value in C_GRID:
                base_parts = fit_label_router_cv_base(label_vectorized, c_value)
                for alpha in ALPHA_GRID:
                    parts = blend_label_router_cv(base_parts, alpha)
                    for threshold in THRESHOLD_GRID:
                        _prediction, metric = combine_partition_predictions(
                            y,
                            logits,
                            *parts,
                            threshold,
                            utility_parts,
                            global_y,
                            global_main,
                            global_positions,
                            metric_indices,
                        )
                        candidates.append({
                            "objective": "label5",
                            "feature_family": feature_family,
                            "C": c_value,
                            "alpha": alpha,
                            "threshold": threshold,
                            "metric": metric,
                        })
    if not candidates:
        raise ValueError(f"unknown or empty objective mode: {objective_mode}")
    candidates.sort(
        key=lambda row: (row["metric"]["macro_delta"], -row["metric"]["flips"]),
        reverse=True,
    )
    return candidates[0], candidates[:10]


def final_outer_fit(
    rows,
    y,
    logits,
    outer_train,
    outer_validation,
    inner_group,
    selected,
    utility,
):
    # OOF table features for final router training.
    train_output = None
    for inner_fold in range(3):
        validation = outer_train[inner_group == inner_fold]
        table_train = outer_train[inner_group != inner_fold]
        output = table_outputs(rows, table_train, validation)
        if train_output is None:
            train_output = {
                name: {
                    key: np.empty((len(outer_train),) + value.shape[1:], dtype=value.dtype)
                    for key, value in values.items()
                }
                for name, values in output.items()
            }
        locations = np.where(inner_group == inner_fold)[0]
        for name, values in output.items():
            for key, value in values.items():
                train_output[name][key][locations] = value
    validation_output = table_outputs(rows, outer_train, outer_validation)
    if selected["objective"] == "state3":
        train_examples = candidate_examples(
            rows, outer_train, logits, train_output,
            feature_family=selected["feature_family"],
        )
        validation_examples = candidate_examples(
            rows, outer_validation, logits, validation_output,
            feature_family=selected["feature_family"],
        )
        vectorizer = DictVectorizer(sparse=True)
        train_matrix = vectorizer.fit_transform(train_examples["features"])
        validation_matrix = vectorizer.transform(validation_examples["features"])
        model = fit_model(
            train_matrix,
            train_examples["target"],
            selected["C"],
            selected["class_weights"],
        )
        probability = aligned_probabilities(model, validation_matrix)
        route_examples = validation_examples
    elif selected["objective"] == "label5":
        train_examples = policy_row_examples(
            rows, outer_train, logits, train_output,
            feature_family=selected["feature_family"],
        )
        validation_examples = policy_row_examples(
            rows, outer_validation, logits, validation_output,
            feature_family=selected["feature_family"],
        )
        vectorizer = DictVectorizer(sparse=True)
        train_matrix = vectorizer.fit_transform(train_examples["features"])
        validation_matrix = vectorizer.transform(validation_examples["features"])
        model = fit_label_model(train_matrix, train_examples["target"], selected["C"])
        model_probability = aligned_label_probabilities(model, validation_matrix)
        label_probability = (
            (1.0 - selected["alpha"]) * validation_examples["main_prior"]
            + selected["alpha"] * model_probability
        )
        route_examples, probability = label_probabilities_to_candidate_states(
            validation_examples, label_probability
        )
    else:
        raise ValueError(f"unknown selected objective: {selected['objective']}")
    main = logits.argmax(axis=1)
    prediction, selected_examples, scores = routed_predictions(
        main[outer_validation], route_examples, probability, utility,
        selected["threshold"],
    )
    threshold_predictions = {
        str(threshold): routed_predictions(
            main[outer_validation], route_examples, probability, utility, threshold
        )[0]
        for threshold in THRESHOLD_AUDIT_GRID
    }
    return prediction, {
        "train_candidate_examples": len(train_examples["features"]),
        "validation_candidate_examples": len(route_examples["alternative"]),
        "selected_examples": int(len(selected_examples)),
        "feature_count": int(train_matrix.shape[1]),
        "score_summary": {
            "min": float(scores.min()) if len(scores) else None,
            "mean": float(scores.mean()) if len(scores) else None,
            "max": float(scores.max()) if len(scores) else None,
        },
    }, threshold_predictions


def run(args):
    start = time.time()
    selection_report = (
        json.loads(Path(args.selection_report).read_text(encoding="utf-8"))
        if args.selection_report else None
    )
    fixed_recipe = json.loads(args.fixed_recipe) if args.fixed_recipe else None
    if fixed_recipe is not None:
        effective_objectives = {fixed_recipe["objective"]}
        analysis_status = "adaptive_posthoc_fixed_recipe_diagnostic"
        adaptivity = (
            "A fixed recipe was supplied instead of nested hyperparameter selection. "
            "For this artifact it was chosen after inspecting the same outer OOF, so the "
            "result is a post-hoc diagnostic and not promotion evidence."
        )
    elif selection_report is not None:
        effective_objectives = {
            fold["selected"]["objective"] for fold in selection_report["folds"]
        }
        analysis_status = "adaptive_selected_recipe_diagnostic_on_reused_outer_oof"
        adaptivity = (
            "Per-outer recipes are reused from a prior selection report on the same OOF; "
            "this is a diagnostic replay rather than independent promotion evidence."
        )
    else:
        effective_objectives = (
            {"state3", "label5"} if args.objective == "both" else {args.objective}
        )
        if args.label_feature_families != "pair_main":
            analysis_status = "adaptive_feature_family_followup_on_reused_outer_oof"
            adaptivity = (
                "The feature-family sweep is nested inside each outer fold, but the families "
                "were proposed after earlier results on the same outer OOF; treat it as an "
                "adaptive research follow-up, not an independent prospective estimate."
            )
        else:
            analysis_status = "adaptive_nested_oof_probe_on_reused_champion_oof"
            adaptivity = (
                "pair_main was retained after a prior feature-family ablation on the same OOF; "
                "this is an adaptive follow-up estimate."
            )
    oof, provenance = load_champion_oof(args.oof_glob)
    global_oof = global_oof_arrays(oof)
    rows, logits, outer_folds, global_positions = load_sim_rows(
        args.data_dir, oof, global_oof["position"]
    )
    y = np.asarray([row.y for row in rows], dtype=np.int64)
    main = logits.argmax(axis=1)
    global_main = global_oof["logits"].argmax(axis=1)
    report = {
        "schema_version": 1,
        "analysis_status": analysis_status,
        "methodology": {
            "scope": "sess_sim only; main-top1 Weak4 route",
            "outer": "provided three champion session-OOF folds",
            "inner": (
                "three session-hash folds; candidate table, router and Macro-F1 utility "
                "are nested-crossfit"
            ),
            "identity": "keep-main is always available and is the default",
            "router_target": (
                "five labels (read/grep/list/glob/other), projected to candidate "
                "neutral/rescue/harm states"
                if effective_objectives == {"label5"}
                else (
                    "three states: neutral/rescue/harm"
                    if effective_objectives == {"state3"}
                    else "inner selection between three-state and five-label targets"
                )
            ),
            "router_score": (
                "expected one-row Macro-F1 utility under confusion counts that exclude "
                "the scored inner/outer validation partition"
            ),
            "main_features": "row-normalized probabilities, margins and ranks; no raw logit scale",
            "adaptivity": adaptivity,
            "caveat": (
                "The supplied main OOF is fixed. Router-train main predictions may come from "
                "base models trained on outer-router validation sessions; a fully nested main "
                "refit is required for a strict second-order stacking estimate. Table posterior "
                "and support features also see different fit-set sizes across inner selection, "
                "outer evaluation and eventual full-data deployment."
            ),
        },
        "config": vars(args),
        "provenance": provenance,
        "rows": len(rows),
        "sessions": len({row.session for row in rows}),
        "outer_rows": dict(Counter(map(int, outer_folds))),
        "baseline_full": evaluation(global_oof["y"], global_main, global_main),
        "baseline_sim": evaluation(y, main, main),
        "candidate_inventory": candidate_inventory(
            rows,
            y,
            logits,
            outer_folds,
            global_oof["y"],
            global_main,
            global_positions,
        ),
        "folds": [],
    }
    combined = main.copy()
    threshold_combined = {
        str(threshold): main.copy() for threshold in THRESHOLD_AUDIT_GRID
    }
    for outer in range(3):
        outer_train = np.where(outer_folds != outer)[0]
        outer_validation = np.where(outer_folds == outer)[0]
        outer_global_train = np.where(global_oof["fold"] != outer)[0]
        outer_global_validation = np.where(global_oof["fold"] == outer)[0]
        inner_group = np.asarray([
            stable_fold(rows[index].session, 3, args.inner_seed + outer)
            for index in outer_train
        ])
        if selection_report is None:
            selected, leaderboard = select_router(
                rows,
                y,
                logits,
                outer_train,
                inner_group,
                global_oof["y"],
                global_main,
                global_positions,
                outer_global_train,
                args.objective,
                tuple(args.label_feature_families.split(",")),
            )
        else:
            source_fold = selection_report["folds"][outer]
            if int(source_fold["fold"]) != outer:
                raise ValueError("selection report fold order mismatch")
            selected = fixed_recipe or source_fold["selected"]
            leaderboard = source_fold.get("inner_top10", [])
        utility = state_utility_table(
            global_oof["y"][outer_global_train],
            global_main[outer_global_train],
        )
        prediction, diagnostic, threshold_predictions = final_outer_fit(
            rows,
            y,
            logits,
            outer_train,
            outer_validation,
            inner_group,
            selected,
            utility,
        )
        combined[outer_validation] = prediction
        for threshold, threshold_prediction in threshold_predictions.items():
            threshold_combined[threshold][outer_validation] = threshold_prediction
        fold_global_prediction = global_main.copy()
        fold_global_prediction[global_positions[outer_validation]] = prediction
        report["folds"].append({
            "fold": outer,
            "selected": selected,
            "inner_top10": leaderboard,
            "heldout_full": evaluation(
                global_oof["y"][outer_global_validation],
                global_main[outer_global_validation],
                fold_global_prediction[outer_global_validation],
            ),
            "heldout_sim": evaluation(
                y[outer_validation], main[outer_validation], prediction
            ),
            "threshold_audit_full": {},
            "diagnostic": diagnostic,
        })
        for threshold, threshold_prediction in threshold_predictions.items():
            threshold_global_prediction = global_main.copy()
            threshold_global_prediction[global_positions[outer_validation]] = threshold_prediction
            report["folds"][-1]["threshold_audit_full"][threshold] = evaluation(
                global_oof["y"][outer_global_validation],
                global_main[outer_global_validation],
                threshold_global_prediction[outer_global_validation],
            )
        print(json.dumps({
            "fold": outer,
            "selected": selected,
            "heldout_full": report["folds"][-1]["heldout_full"],
            "elapsed_seconds": time.time() - start,
        }, ensure_ascii=False), flush=True)
    global_combined = global_main.copy()
    global_combined[global_positions] = combined
    report["combined_full"] = evaluation(
        global_oof["y"], global_main, global_combined
    )
    report["combined_sim"] = evaluation(y, main, combined)
    report["threshold_audit_combined_full"] = {}
    for threshold, threshold_prediction in threshold_combined.items():
        global_threshold_prediction = global_main.copy()
        global_threshold_prediction[global_positions] = threshold_prediction
        report["threshold_audit_combined_full"][threshold] = evaluation(
            global_oof["y"], global_main, global_threshold_prediction
        )
    report["positive_macro_folds"] = sum(
        fold["heldout_full"]["macro_delta"] > 0 for fold in report["folds"]
    )
    report["elapsed_seconds"] = time.time() - start
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "combined_full": report["combined_full"],
        "combined_sim": report["combined_sim"],
        "positive_macro_folds": report["positive_macro_folds"],
        "elapsed_seconds": report["elapsed_seconds"],
    }, ensure_ascii=False, indent=2))
    return report


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument(
        "--oof-glob",
        default="/tmp/champ_oof_3fold/champ_oof_f*_val_logits.pt",
    )
    parser.add_argument(
        "--output",
        default="experiments/artifacts/20260713_sim_policy_router_nested_oof.json",
    )
    parser.add_argument("--inner-seed", type=int, default=20260713)
    parser.add_argument(
        "--objective", choices=["both", "state3", "label5"], default="both"
    )
    parser.add_argument(
        "--label-feature-families",
        default="pair_main",
        help=(
            "comma-separated label5 families: pair_policy,pair_main,"
            "pair_main_nocount,main_context,full"
        ),
    )
    parser.add_argument(
        "--selection-report",
        default="",
        help="reuse per-outer selected recipes and run only final-fit/threshold diagnostics",
    )
    parser.add_argument(
        "--fixed-recipe",
        default="",
        help="JSON recipe applied to every outer fold; requires --selection-report",
    )
    args = parser.parse_args()
    valid_families = {
        "pair_policy", "pair_main", "pair_main_nocount", "main_context", "full"
    }
    requested_families = args.label_feature_families.split(",")
    if not requested_families or any(
        not family or family not in valid_families for family in requested_families
    ):
        parser.error("invalid --label-feature-families")
    if args.fixed_recipe and not args.selection_report:
        parser.error("--fixed-recipe requires --selection-report")
    return args


if __name__ == "__main__":
    run(parse_args())
