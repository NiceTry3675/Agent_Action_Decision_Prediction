#!/usr/bin/env python3
"""T2 Stages F1/F2 (CPU half): counterfactual-future residual frozen gates.

Consumes the current-hidden cache (leak-free screen checkpoint, fixed
validation rows only) and the future-hidden cache, and runs the
pre-registered teacher/student gates:

* ``T2-T1`` actual next-user privileged oracle vs ``T2-T2`` same-action
  matched-donor oracle.  The primary comparison is symmetric: both arms use
  the same leave-one-donor-out counterfactual means, so neither side gets a
  lower-variance baseline.
* ``T2-S1`` current-only student trained on the actual-minus-donor residual
  vs ``T2-SPERM`` strata-permuted-residual students.

The privileged head and the student only ever train on fold-train rows;
lambda and clip thresholds come from inner tunes, never from held-out folds.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import platform
import shlex
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch

CLASSES = [
    "read_file", "grep_search", "list_directory", "glob_pattern",
    "edit_file", "write_file", "apply_patch", "run_bash", "run_tests",
    "lint_or_typecheck", "ask_user", "plan_task", "web_search", "respond_only",
]
WEAK4 = list(range(4))
MID3 = [CLASSES.index(name) for name in ("ask_user", "plan_task", "lint_or_typecheck")]


def scenario_group(sample_id):
    session = str(sample_id).split("-step_")[0]
    parts = session.split("_")
    if len(parts) >= 4 and parts[:2] == ["sess", "au"]:
        return "_".join(parts[:3])
    return session


def hash_fold(group, n_folds, seed):
    digest = hashlib.sha256(f"{seed}:{group}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % n_folds


def log_softmax_rows(scores):
    scores = np.asarray(scores, dtype=np.float64)
    shifted = scores - scores.max(axis=1, keepdims=True)
    return shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True))


def center_rows(values):
    values = np.asarray(values, dtype=np.float64)
    return values - values.mean(axis=1, keepdims=True)


def clip_rows(delta, threshold):
    delta = np.asarray(delta, dtype=np.float64)
    norms = np.linalg.norm(delta, axis=1, keepdims=True)
    scale = np.minimum(1.0, threshold / np.maximum(norms, 1e-12))
    return delta * scale


def permute_within_strata(values, strata, seed):
    values = np.asarray(values)
    permuted = values.copy()
    rng = np.random.default_rng(seed)
    buckets = defaultdict(list)
    for index, stratum in enumerate(strata):
        buckets[stratum].append(index)
    for indices in buckets.values():
        indices = np.asarray(indices)
        permuted[indices] = values[indices[rng.permutation(len(indices))]]
    return permuted


def f1_summary(y_true, predictions):
    y_true = np.asarray(y_true, dtype=np.int64)
    predictions = np.asarray(predictions, dtype=np.int64)
    f1 = []
    for class_index in range(len(CLASSES)):
        tp = int(np.sum((y_true == class_index) & (predictions == class_index)))
        fp = int(np.sum((y_true != class_index) & (predictions == class_index)))
        fn = int(np.sum((y_true == class_index) & (predictions != class_index)))
        denominator = 2 * tp + fp + fn
        f1.append(0.0 if denominator == 0 else 2 * tp / denominator)
    return {
        "rows": int(len(y_true)),
        "accuracy": float(np.mean(y_true == predictions)),
        "macro_f1": float(np.mean(f1)),
        "weak4_macro_f1": float(np.mean([f1[i] for i in WEAK4])),
        "mid3_f1_sum": float(sum(f1[i] for i in MID3)),
        "per_class_f1": {CLASSES[i]: float(f1[i]) for i in WEAK4 + MID3},
    }


def rescue_harm(y_true, base_pred, arm_pred):
    base_ok = base_pred == y_true
    arm_ok = arm_pred == y_true
    return {
        "rescued": int(np.sum(~base_ok & arm_ok)),
        "harmed": int(np.sum(base_ok & ~arm_ok)),
        "changed": int(np.sum(base_pred != arm_pred)),
    }


class PrivilegedHead:
    """L2 multinomial logistic on phi(h0, hf) with fold-local scaling."""

    def __init__(self, c=0.1, max_iter=300, seed=42):
        self.c = c
        self.max_iter = max_iter
        self.seed = seed
        self.scaler_mean = None
        self.scaler_scale = None
        self.model = None

    @staticmethod
    def phi(h0, hf):
        return np.concatenate([h0, hf, h0 * hf, np.abs(h0 - hf)], axis=1)

    def fit(self, h0, hf, labels):
        from sklearn.linear_model import LogisticRegression

        features = self.phi(h0, hf).astype(np.float32)
        self.scaler_mean = features.mean(axis=0)
        scale = features.std(axis=0)
        self.scaler_scale = np.where(scale < 1e-8, 1.0, scale)
        features = (features - self.scaler_mean) / self.scaler_scale
        self.model = LogisticRegression(
            C=self.c,
            max_iter=self.max_iter,
            random_state=self.seed,
        )
        self.model.fit(features, labels)
        self.present_classes = [int(value) for value in self.model.classes_]
        if any(c not in range(len(CLASSES)) for c in self.present_classes):
            raise ValueError("head classes are outside the canonical 14-way range")
        return self

    def scores(self, h0, hf, chunk=4096, absent_fill=-30.0):
        # Classes absent from fold-train (e.g. respond_only, which is always
        # terminal and hence never covered) get a constant score.  The fill
        # cancels exactly in the row-centered delta: q1-q2 shifts every class
        # by the same per-row log-partition difference, which centering
        # removes, and the absent class's own score difference is zero.
        outputs = np.full((len(h0), len(CLASSES)), absent_fill, dtype=np.float64)
        for start in range(0, len(h0), chunk):
            end = min(start + chunk, len(h0))
            features = self.phi(h0[start:end], hf[start:end]).astype(np.float32)
            features = (features - self.scaler_mean) / self.scaler_scale
            decision = self.model.decision_function(features)
            if decision.ndim == 1:
                raise ValueError("binary head is not supported")
            outputs[start:end, self.present_classes] = decision
        return outputs


def donor_scores(head, h0, future_hidden, donor_uidx):
    """Return per-donor log-softmax scores [rows, K, classes]."""

    rows, k = donor_uidx.shape
    q = np.empty((rows, k, len(CLASSES)), dtype=np.float64)
    for donor_position in range(k):
        hf = future_hidden[donor_uidx[:, donor_position]]
        q[:, donor_position] = log_softmax_rows(head.scores(h0, hf))
    return q


def rotation_deltas(q1, q2):
    """Symmetric LOO deltas: (t1_j, t2_j) per left-out donor j, plus full T1."""

    rows, k, _ = q2.shape
    total = q2.sum(axis=1)
    t1_rotations, t2_rotations = [], []
    for j in range(k):
        cf_mean = (total - q2[:, j]) / (k - 1)
        t1_rotations.append(center_rows(q1 - cf_mean))
        t2_rotations.append(center_rows(q2[:, j] - cf_mean))
    t1_full = center_rows(q1 - total / k)
    return t1_full, t1_rotations, t2_rotations


def fit_student(h0, delta, alpha, seed=0):
    from sklearn.linear_model import Ridge

    mean = h0.mean(axis=0)
    scale = h0.std(axis=0)
    scale = np.where(scale < 1e-8, 1.0, scale)
    model = Ridge(alpha=alpha, random_state=seed)
    model.fit(((h0 - mean) / scale).astype(np.float32), delta.astype(np.float32))

    def predict(h0_new):
        raw = model.predict(((h0_new - mean) / scale).astype(np.float32))
        return center_rows(raw)

    return predict


def git_provenance():
    def run(*cmd):
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, check=True
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            return None

    diff = run("git", "diff", "HEAD")
    return {
        "commit": run("git", "rev-parse", "HEAD"),
        "uncommitted_diff_sha256": (
            hashlib.sha256(diff.encode()).hexdigest() if diff else None
        ),
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--future-cache", required=True)
    parser.add_argument(
        "--payload",
        default="experiments/privileged_targets/20260711_future_nextuser_donors.json.gz",
    )
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--head-c", type=float, default=0.1)
    parser.add_argument("--ridge-alpha", type=float, default=10.0)
    parser.add_argument("--lambda-grid", default="0.25,0.5,1.0,2.0")
    parser.add_argument("--clip-percentile", type=float, default=99.0)
    parser.add_argument("--perm-seeds", default="101,102,103")
    parser.add_argument("--stage", choices=["teacher", "both"], default="both")
    parser.add_argument(
        "--output-teacher",
        default="experiments/artifacts/20260711_future_p1_p2_teacher_gate.json",
    )
    parser.add_argument(
        "--output-student",
        default="experiments/artifacts/20260711_future_student_recovery_gate.json",
    )
    args = parser.parse_args()

    lambda_grid = [float(v) for v in args.lambda_grid.split(",")]
    perm_seeds = [int(v) for v in args.perm_seeds.split(",")]

    cache = torch.load(args.cache, map_location="cpu", weights_only=False)
    future = torch.load(args.future_cache, map_location="cpu", weights_only=False)
    if future.get("format") != "future-nextuser-hidden-cache-v1":
        raise ValueError("unsupported future cache format")
    if future.get("current_cache_sha256") != sha256_file(args.cache):
        raise ValueError("future cache was built against a different current cache")

    val_indices = future["val_indices"].numpy()
    actual_uidx = future["actual_uidx"].numpy()
    donor_uidx = future["donor_uidx"].numpy()
    covered = (actual_uidx >= 0) & (donor_uidx >= 0).all(axis=1)

    row_indices = val_indices[covered]
    ids = [str(cache["ids"][index]) for index in row_indices]
    y_true = cache["y_true"].numpy()[row_indices]
    z0 = cache["parent_logits"].numpy()[row_indices].astype(np.float64)
    h0 = cache["hidden"].numpy()[row_indices].astype(np.float32)
    future_hidden = future["unique_hidden"].numpy().astype(np.float32)
    actual_uidx = actual_uidx[covered]
    donor_uidx = donor_uidx[covered]

    with gzip.open(args.payload, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload_ids = payload["ids"]
    strata = [
        f"{payload['label'][index]}|{payload['source'][index]}|{payload['turn_bin'][index]}"
        for index in row_indices
    ]
    for local_index, row_index in enumerate(row_indices):
        if payload_ids[row_index] != ids[local_index]:
            raise ValueError("payload/cache id order mismatch")

    scenarios = np.asarray([scenario_group(sample_id) for sample_id in ids])
    folds = np.asarray(
        [hash_fold(scenario, args.folds, args.seed) for scenario in scenarios]
    )
    inner_folds = np.asarray(
        [hash_fold(scenario, 5, args.seed * 31 + 7) for scenario in scenarios]
    )

    q0 = log_softmax_rows(z0)
    p0_pred = q0.argmax(axis=1)

    pooled = {
        arm: np.full(len(ids), -1, dtype=np.int64)
        for arm in ("p0", "t1_full", "t1_sym", "t2_loo", "s1")
    }
    pooled_perm = {seed: np.full(len(ids), -1, dtype=np.int64) for seed in perm_seeds}
    fold_reports = []

    for fold in range(args.folds):
        held = folds == fold
        train = ~held
        inner_val = train & (inner_folds == 0)
        inner_train = train & (inner_folds != 0)

        hf_actual = future_hidden[actual_uidx]

        # -- inner tune: lambda on the inner-val arm of the actual oracle
        inner_head = PrivilegedHead(c=args.head_c, seed=args.seed).fit(
            h0[inner_train], hf_actual[inner_train], y_true[inner_train]
        )
        q1_inner = log_softmax_rows(
            inner_head.scores(h0[inner_val], hf_actual[inner_val])
        )
        q2_inner = donor_scores(
            inner_head, h0[inner_val], future_hidden, donor_uidx[inner_val]
        )
        t1_inner_full, _, _ = rotation_deltas(q1_inner, q2_inner)
        delta_inner_train_norms = np.linalg.norm(
            rotation_deltas(
                log_softmax_rows(
                    inner_head.scores(h0[inner_train], hf_actual[inner_train])
                ),
                donor_scores(
                    inner_head, h0[inner_train], future_hidden, donor_uidx[inner_train]
                ),
            )[0],
            axis=1,
        )
        clip_threshold_inner = float(
            np.percentile(delta_inner_train_norms, args.clip_percentile)
        )
        t1_inner_clipped = clip_rows(t1_inner_full, clip_threshold_inner)
        best_lambda, best_macro = None, -1.0
        for lam in lambda_grid:
            macro = f1_summary(
                y_true[inner_val], (q0[inner_val] + lam * t1_inner_clipped).argmax(axis=1)
            )["macro_f1"]
            if macro > best_macro:
                best_lambda, best_macro = lam, macro

        # -- student lambda via inner split (targets from the inner head)
        student_lambda = best_lambda
        if args.stage == "both":
            delta_targets_inner = clip_rows(
                rotation_deltas(
                    log_softmax_rows(
                        inner_head.scores(h0[inner_train], hf_actual[inner_train])
                    ),
                    donor_scores(
                        inner_head,
                        h0[inner_train],
                        future_hidden,
                        donor_uidx[inner_train],
                    ),
                )[0],
                clip_threshold_inner,
            )
            inner_student = fit_student(
                h0[inner_train], delta_targets_inner, args.ridge_alpha
            )
            delta_hat_inner = inner_student(h0[inner_val])
            best_student_macro = -1.0
            for lam in lambda_grid:
                macro = f1_summary(
                    y_true[inner_val],
                    (q0[inner_val] + lam * delta_hat_inner).argmax(axis=1),
                )["macro_f1"]
                if macro > best_student_macro:
                    student_lambda, best_student_macro = lam, macro

        # -- final head on the full fold-train
        head = PrivilegedHead(c=args.head_c, seed=args.seed).fit(
            h0[train], hf_actual[train], y_true[train]
        )
        q1_train = log_softmax_rows(head.scores(h0[train], hf_actual[train]))
        q2_train = donor_scores(head, h0[train], future_hidden, donor_uidx[train])
        t1_train_full, _, _ = rotation_deltas(q1_train, q2_train)
        clip_threshold = float(
            np.percentile(
                np.linalg.norm(t1_train_full, axis=1), args.clip_percentile
            )
        )

        q1_held = log_softmax_rows(head.scores(h0[held], hf_actual[held]))
        q2_held = donor_scores(head, h0[held], future_hidden, donor_uidx[held])
        t1_full, t1_rotations, t2_rotations = rotation_deltas(q1_held, q2_held)

        lam = best_lambda
        arms_pred = {
            "p0": q0[held].argmax(axis=1),
            "t1_full": (q0[held] + lam * clip_rows(t1_full, clip_threshold)).argmax(axis=1),
        }
        t1_sym_macros, t2_loo_macros = [], []
        t1_sym_votes = np.zeros((held.sum(), len(CLASSES)))
        t2_loo_votes = np.zeros((held.sum(), len(CLASSES)))
        for t1_rotation, t2_rotation in zip(t1_rotations, t2_rotations):
            z_t1 = q0[held] + lam * clip_rows(t1_rotation, clip_threshold)
            z_t2 = q0[held] + lam * clip_rows(t2_rotation, clip_threshold)
            t1_sym_macros.append(
                f1_summary(y_true[held], z_t1.argmax(axis=1))["macro_f1"]
            )
            t2_loo_macros.append(
                f1_summary(y_true[held], z_t2.argmax(axis=1))["macro_f1"]
            )
            t1_sym_votes += z_t1
            t2_loo_votes += z_t2
        arms_pred["t1_sym"] = t1_sym_votes.argmax(axis=1)
        arms_pred["t2_loo"] = t2_loo_votes.argmax(axis=1)

        held_report = {
            "fold": fold,
            "rows": int(held.sum()),
            "lambda": lam,
            "clip_threshold": clip_threshold,
            "arms": {
                name: f1_summary(y_true[held], pred) for name, pred in arms_pred.items()
            },
            "t1_sym_rotation_macros": t1_sym_macros,
            "t2_loo_rotation_macros": t2_loo_macros,
        }

        if args.stage == "both":
            delta_targets = clip_rows(t1_train_full, clip_threshold)
            student = fit_student(h0[train], delta_targets, args.ridge_alpha)
            delta_hat = student(h0[held])
            arms_pred["s1"] = (q0[held] + student_lambda * delta_hat).argmax(axis=1)
            held_report["student_lambda"] = student_lambda
            held_report["arms"]["s1"] = f1_summary(y_true[held], arms_pred["s1"])
            held_report["perm"] = {}
            train_strata = [strata[i] for i in np.where(train)[0]]
            for seed in perm_seeds:
                permuted_targets = permute_within_strata(
                    delta_targets, train_strata, seed
                )
                perm_student = fit_student(
                    h0[train], permuted_targets, args.ridge_alpha
                )
                perm_pred = (
                    q0[held] + student_lambda * perm_student(h0[held])
                ).argmax(axis=1)
                pooled_perm[seed][held] = perm_pred
                held_report["perm"][str(seed)] = f1_summary(y_true[held], perm_pred)

        for name, pred in arms_pred.items():
            pooled[name][held] = pred
        held_report["rescue_harm_t1_vs_p0"] = rescue_harm(
            y_true[held], arms_pred["p0"], arms_pred["t1_full"]
        )
        fold_reports.append(held_report)
        print(
            f"fold {fold}: lambda={lam} "
            f"t1_sym={np.mean(t1_sym_macros):.6f} t2_loo={np.mean(t2_loo_macros):.6f} "
            f"p0={held_report['arms']['p0']['macro_f1']:.6f}"
        )

    aggregate = {
        name: f1_summary(y_true, pred)
        for name, pred in pooled.items()
        if (pred >= 0).all()
    }
    per_fold_sign = [
        np.mean(report["t1_sym_rotation_macros"])
        - np.mean(report["t2_loo_rotation_macros"])
        for report in fold_reports
    ]
    t1_minus_t2_macro = (
        aggregate["t1_sym"]["macro_f1"] - aggregate["t2_loo"]["macro_f1"]
    )
    t1_minus_t2_weak4 = (
        aggregate["t1_sym"]["weak4_macro_f1"] - aggregate["t2_loo"]["weak4_macro_f1"]
    )
    teacher_gates = {
        "t1_minus_t2_macro_ge_0002": bool(t1_minus_t2_macro >= 0.002),
        "t1_minus_t2_weak4_ge_0006": bool(t1_minus_t2_weak4 >= 0.006),
        "folds_positive_ge_2of3": bool(sum(v > 0 for v in per_fold_sign) >= 2),
    }
    teacher_decision = "GO" if all(teacher_gates.values()) else "REJECT"

    provenance = {
        **git_provenance(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "torch": torch.__version__,
        "command": shlex.join(sys.argv),
        "cache_sha256": sha256_file(args.cache),
        "future_cache_sha256": sha256_file(args.future_cache),
        "payload_sha256": sha256_file(args.payload),
        "rows_covered": int(covered.sum()),
        "rows_total_val": int(len(val_indices)),
        "folds": args.folds,
        "seed": args.seed,
        "head_c": args.head_c,
        "lambda_grid": lambda_grid,
        "clip_percentile": args.clip_percentile,
    }

    teacher_report = {
        "format": "future-p1-p2-teacher-gate-v1",
        "provenance": provenance,
        "aggregate": aggregate,
        "fold_reports": fold_reports,
        "t1_minus_t2_macro": t1_minus_t2_macro,
        "t1_minus_t2_weak4": t1_minus_t2_weak4,
        "per_fold_t1_minus_t2": [float(v) for v in per_fold_sign],
        "prediction_distribution": {
            name: dict(
                sorted(Counter(CLASSES[p] for p in pred).items())
            )
            for name, pred in pooled.items()
            if (pred >= 0).all()
        },
        "gates": teacher_gates,
        "decision": teacher_decision,
    }
    output_teacher = Path(args.output_teacher)
    output_teacher.parent.mkdir(parents=True, exist_ok=True)
    output_teacher.write_text(
        json.dumps(teacher_report, indent=2), encoding="utf-8"
    )
    print(f"teacher gate -> {output_teacher} decision={teacher_decision}")
    print(
        f"T1-T2 macro={t1_minus_t2_macro:+.6f} weak4={t1_minus_t2_weak4:+.6f} "
        f"folds={['+' if v > 0 else '-' for v in per_fold_sign]}"
    )

    if args.stage == "both":
        sperm_macros = [
            f1_summary(y_true, pooled_perm[seed])["macro_f1"] for seed in perm_seeds
        ]
        teacher_excess = t1_minus_t2_macro
        student_excess = aggregate["s1"]["macro_f1"] - float(np.mean(sperm_macros))
        recovery_ratio = (
            student_excess / teacher_excess if teacher_excess > 0 else None
        )
        s1_minus_p0 = aggregate["s1"]["macro_f1"] - aggregate["p0"]["macro_f1"]
        per_fold_student = [
            report["arms"]["s1"]["macro_f1"]
            - float(np.mean([p["macro_f1"] for p in report["perm"].values()]))
            for report in fold_reports
        ]
        student_gates = {
            "teacher_gate_passed": teacher_decision == "GO",
            "recovery_ratio_ge_030": bool(
                recovery_ratio is not None and recovery_ratio >= 0.30
            ),
            "s1_minus_p0_positive": bool(s1_minus_p0 > 0),
            "s1_minus_sperm_ge_0001": bool(student_excess >= 0.001),
            "folds_positive_ge_2of3": bool(
                sum(v > 0 for v in per_fold_student) >= 2
            ),
            "weak4_direction_positive": bool(
                aggregate["s1"]["weak4_macro_f1"]
                > aggregate["p0"]["weak4_macro_f1"]
            ),
        }
        student_report = {
            "format": "future-student-recovery-gate-v1",
            "provenance": provenance,
            "teacher_excess": teacher_excess,
            "student_excess": student_excess,
            "recovery_ratio": recovery_ratio,
            "s1_minus_p0": s1_minus_p0,
            "sperm_macros": {
                str(seed): macro for seed, macro in zip(perm_seeds, sperm_macros)
            },
            "sperm_macro_mean": float(np.mean(sperm_macros)),
            "sperm_macro_max": float(np.max(sperm_macros)),
            "per_fold_s1_minus_sperm": [float(v) for v in per_fold_student],
            "rescue_harm_s1_vs_p0": rescue_harm(y_true, pooled["p0"], pooled["s1"]),
            "gates": student_gates,
            "decision": "GO" if all(student_gates.values()) else "REJECT",
        }
        output_student = Path(args.output_student)
        output_student.parent.mkdir(parents=True, exist_ok=True)
        output_student.write_text(
            json.dumps(student_report, indent=2), encoding="utf-8"
        )
        print(
            f"student gate -> {output_student} decision={student_report['decision']} "
            f"recovery_ratio={recovery_ratio}"
        )


if __name__ == "__main__":
    main()
