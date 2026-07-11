#!/usr/bin/env python3
"""T2 Stage F0: next-user recovery and counterfactual donor audit (CPU).

Recovers each training row's *next user turn* through strict same-session
evidence only (the step+1 row's own ``current_prompt`` and/or later rows'
positional history witnesses; any disagreement is fail-closed), then assigns
deterministic same-action counterfactual donors under the pre-registered
matching key ``true action x source x turn bin x language_pref`` with
same-session / same-AU-scenario / identical-text exclusions.

Emits the Stage F0 audit artifact and the frozen-encoding payload consumed by
the GPU teacher-gate stage.  No target event args/results and no action names
enter the privileged texts: the only privileged channel here is next-user.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from privileged_event_modes import _strict_history_pairs

AUDIT_FORMAT = "future-nextuser-donor-audit-v1"
PAYLOAD_FORMAT = "future-nextuser-donor-payload-v1"
DONOR_SEED_RULE = "sha256('{sample_id}|future-donor-v1')[:8] big-endian"

CLASSES = [
    "read_file", "grep_search", "list_directory", "glob_pattern",
    "edit_file", "write_file", "apply_patch", "run_bash", "run_tests",
    "lint_or_typecheck", "ask_user", "plan_task", "web_search", "respond_only",
]
C2I = {name: index for index, name in enumerate(CLASSES)}
WEAK4 = CLASSES[:4]


def scenario_group(sample_id):
    session = str(sample_id).split("-step_")[0]
    parts = session.split("_")
    if len(parts) >= 4 and parts[:2] == ["sess", "au"]:
        return "_".join(parts[:3])
    return session


def turn_bin_value(turn, fallback_step=None):
    if isinstance(turn, bool) or not isinstance(turn, (int, float)):
        turn = fallback_step
    if turn is None or isinstance(turn, bool) or not isinstance(turn, (int, float)):
        return "unknown"
    if not math.isfinite(float(turn)) or turn < 0:
        return "unknown"
    if turn <= 1:
        return "start"
    if turn <= 2:
        return "early"
    if turn <= 4:
        return "mid"
    if turn <= 6:
        return "late"
    return "long"


def donor_seed(sample_id):
    digest = hashlib.sha256(f"{sample_id}|future-donor-v1".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def load_rows(data_dir):
    """Stream train rows into compact records (history reduced to user texts)."""

    data_dir = Path(data_dir)
    import csv

    labels = {
        row["id"]: row["action"]
        for row in csv.DictReader((data_dir / "train_labels.csv").open(encoding="utf-8"))
    }
    rows = []
    with (data_dir / "train.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            sample = json.loads(line)
            rid = str(sample["id"])
            session, step_text = rid.rsplit("-step_", 1)
            meta = sample.get("session_meta") or {}
            pairs = _strict_history_pairs(sample.get("history", []))
            label = labels.get(rid)
            if label is None or label not in C2I:
                raise ValueError(f"missing or invalid label for {rid!r}")
            rows.append(
                {
                    "id": rid,
                    "session": session,
                    "scenario": scenario_group(rid),
                    "source": session.split("_")[1],
                    "step": int(step_text),
                    "label": label,
                    "prompt": sample.get("current_prompt"),
                    "turn_bin": turn_bin_value(
                        meta.get("turn_index"), fallback_step=int(step_text)
                    ),
                    "lang": str(meta.get("language_pref") or "unknown"),
                    "witness_users": None if pairs is None else tuple(p[0] for p in pairs),
                    "malformed_history": pairs is None,
                }
            )
    return rows


def recover_next_user(rows):
    """Return per-row (status, text) for the user turn at step+1, fail-closed."""

    by_session = defaultdict(dict)
    for index, row in enumerate(rows):
        steps = by_session[row["session"]]
        if row["step"] in steps:
            raise ValueError(f"duplicate session step for {row['id']!r}")
        steps[row["step"]] = index
    max_step = {session: max(steps) for session, steps in by_session.items()}

    # User turn observed at absolute step t (from later rows' histories).
    witnessed = defaultdict(set)
    for row in rows:
        users = row["witness_users"]
        if not users:
            continue
        first_step = row["step"] - len(users)
        for offset, text in enumerate(users):
            witnessed[(row["session"], first_step + offset)].add(text)

    statuses, texts = [], []
    for row in rows:
        target = row["step"] + 1
        candidates = set(witnessed.get((row["session"], target), ()))
        direct_index = by_session[row["session"]].get(target)
        if direct_index is not None:
            direct_prompt = rows[direct_index]["prompt"]
            if isinstance(direct_prompt, str):
                candidates.add(direct_prompt)
        if len(candidates) > 1:
            statuses.append("conflict")
            texts.append(None)
        elif candidates:
            statuses.append("recovered")
            texts.append(next(iter(candidates)))
        elif row["step"] == max_step[row["session"]]:
            statuses.append("terminal")
            texts.append(None)
        else:
            statuses.append("no_witness")
            texts.append(None)
    return statuses, texts


def build_donors(rows, statuses, texts, k=4, probe=64):
    """Deterministic per-row donors under the strict key; no backoff."""

    pool_by_key = defaultdict(list)
    for index, row in enumerate(rows):
        if statuses[index] != "recovered":
            continue
        key = (row["label"], row["source"], row["turn_bin"], row["lang"])
        pool_by_key[key].append(index)
    for indices in pool_by_key.values():
        indices.sort(key=lambda i: rows[i]["id"])

    donors = [None] * len(rows)
    for index, row in enumerate(rows):
        if statuses[index] != "recovered":
            continue
        key = (row["label"], row["source"], row["turn_bin"], row["lang"])
        pool = pool_by_key.get(key, [])
        rng = np.random.default_rng(donor_seed(row["id"]))

        def accept(candidate_index):
            candidate = rows[candidate_index]
            return (
                candidate["session"] != row["session"]
                and candidate["scenario"] != row["scenario"]
                and texts[candidate_index] != texts[index]
            )

        chosen = []
        seen = set()
        if len(pool) > probe:
            for candidate_pos in rng.choice(len(pool), size=probe, replace=False):
                candidate_index = pool[int(candidate_pos)]
                seen.add(candidate_index)
                if accept(candidate_index):
                    chosen.append(candidate_index)
                    if len(chosen) == k:
                        break
        if len(chosen) < k:
            chosen = []
            for candidate_pos in rng.permutation(len(pool)):
                candidate_index = pool[int(candidate_pos)]
                if accept(candidate_index):
                    chosen.append(candidate_index)
                    if len(chosen) == k:
                        break
        donors[index] = tuple(chosen)
    return donors


def _coverage(flags, mask=None):
    flags = np.asarray(flags, dtype=bool)
    if mask is not None:
        mask = np.asarray(mask, dtype=bool)
        if not mask.any():
            return None
        flags = flags[mask]
    return float(flags.mean())


def _length_stats(values):
    if not values:
        return None
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": int(array.size),
        "mean": float(array.mean()),
        "p50": float(np.percentile(array, 50)),
        "p90": float(np.percentile(array, 90)),
        "p99": float(np.percentile(array, 99)),
        "max": float(array.max()),
    }


def _rank_auc(positives, negatives):
    """AUC of separating the two samples by a scalar (Mann-Whitney)."""

    positives = np.asarray(positives, dtype=np.float64)
    negatives = np.asarray(negatives, dtype=np.float64)
    if positives.size == 0 or negatives.size == 0:
        return None
    pooled = np.concatenate([positives, negatives])
    order = pooled.argsort(kind="mergesort")
    ranks = np.empty(pooled.size, dtype=np.float64)
    ranks[order] = np.arange(1, pooled.size + 1)
    # average ranks for ties
    pooled_sorted = pooled[order]
    start = 0
    for end in range(1, pooled.size + 1):
        if end == pooled.size or pooled_sorted[end] != pooled_sorted[start]:
            ranks[order[start:end]] = 0.5 * (start + 1 + end)
            start = end
    rank_sum = ranks[: positives.size].sum()
    return float(
        (rank_sum - positives.size * (positives.size + 1) / 2)
        / (positives.size * negatives.size)
    )


def leakage_probe(rows, statuses, texts, seed=42, max_train=40000):
    """Next-user-only action classifier, scenario-grouped split (diagnostic)."""

    from sklearn.feature_extraction.text import HashingVectorizer
    from sklearn.linear_model import SGDClassifier

    def fold_of(scenario):
        digest = hashlib.sha256(f"{seed}:{scenario}".encode()).digest()
        return int.from_bytes(digest[:8], "big") % 5

    train_index, test_index = [], []
    for index, row in enumerate(rows):
        if statuses[index] != "recovered":
            continue
        (test_index if fold_of(row["scenario"]) == 0 else train_index).append(index)
    rng = np.random.default_rng(seed)
    if len(train_index) > max_train:
        train_index = [
            train_index[int(i)]
            for i in rng.choice(len(train_index), size=max_train, replace=False)
        ]
    vectorizer = HashingVectorizer(
        n_features=2**18, ngram_range=(1, 2), alternate_sign=False
    )
    x_train = vectorizer.transform([texts[i] for i in train_index])
    x_test = vectorizer.transform([texts[i] for i in test_index])
    y_train = np.asarray([C2I[rows[i]["label"]] for i in train_index])
    y_test = np.asarray([C2I[rows[i]["label"]] for i in test_index])
    model = SGDClassifier(loss="log_loss", alpha=1e-6, random_state=seed, max_iter=12)
    model.fit(x_train, y_train)
    predictions = model.predict(x_test)
    f1 = []
    for class_index in range(len(CLASSES)):
        tp = int(np.sum((y_test == class_index) & (predictions == class_index)))
        fp = int(np.sum((y_test != class_index) & (predictions == class_index)))
        fn = int(np.sum((y_test == class_index) & (predictions != class_index)))
        denominator = 2 * tp + fp + fn
        f1.append(0.0 if denominator == 0 else 2 * tp / denominator)
    return {
        "train_rows": len(train_index),
        "test_rows": len(test_index),
        "accuracy": float(np.mean(y_test == predictions)),
        "macro_f1": float(np.mean(f1)),
        "weak4_macro_f1": float(np.mean(f1[:4])),
        "note": (
            "diagnostic only: next-user predictiveness is the hypothesized signal; "
            "the donor arm (P2) absorbs the action-exposure component of it"
        ),
    }


def tokenizer_lengths(texts_actual, texts_donor, model_name, max_rows=20000):
    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
    except Exception as error:  # noqa: BLE001 - offline cache may be absent
        return {"available": False, "error": str(error)[:200]}
    rng = np.random.default_rng(0)

    def sample_lengths(texts):
        if len(texts) > max_rows:
            texts = [texts[int(i)] for i in rng.choice(len(texts), size=max_rows, replace=False)]
        return [len(ids) for ids in tokenizer(list(texts))["input_ids"]]

    return {
        "available": True,
        "model": model_name,
        "actual": _length_stats(sample_lengths(texts_actual)),
        "donor": _length_stats(sample_lengths(texts_donor)),
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        default="experiments/artifacts/20260711_future_recovery_donor_audit.json",
    )
    parser.add_argument(
        "--payload",
        default="experiments/privileged_targets/20260711_future_nextuser_donors.json.gz",
    )
    parser.add_argument("--tokenizer", default="naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B")
    parser.add_argument("--skip-leakage-probe", action="store_true")
    args = parser.parse_args()

    rows = load_rows(args.data_dir)
    statuses, texts = recover_next_user(rows)
    donors = build_donors(rows, statuses, texts, k=args.k)

    recovered = np.asarray([status == "recovered" for status in statuses])
    weak4_mask = np.asarray([row["label"] in WEAK4 for row in rows])
    full_k = np.asarray(
        [donor is not None and len(donor) == args.k for donor in donors]
    )

    # Post-hoc exclusion verification (belt and suspenders over accept()).
    violations = {"same_session": 0, "same_scenario": 0, "identical_text": 0}
    donor_index_count = Counter()
    for index, donor in enumerate(donors):
        if not donor:
            continue
        donor_index_count[len(donor)] += 1
        for candidate_index in donor:
            if rows[candidate_index]["session"] == rows[index]["session"]:
                violations["same_session"] += 1
            if rows[candidate_index]["scenario"] == rows[index]["scenario"]:
                violations["same_scenario"] += 1
            if texts[candidate_index] == texts[index]:
                violations["identical_text"] += 1

    per_class_recovery = {}
    for name in CLASSES:
        mask = np.asarray([row["label"] == name for row in rows])
        per_class_recovery[name] = _coverage(recovered, mask)

    actual_lengths = [len(texts[i]) for i in range(len(rows)) if recovered[i]]
    donor_lengths = [
        len(texts[j])
        for i, donor in enumerate(donors)
        if donor
        for j in donor
    ]
    length_auc = _rank_auc(actual_lengths, donor_lengths)

    recovered_texts = [texts[i] for i in range(len(rows)) if recovered[i]]
    duplicate_ratio = 1.0 - len(set(recovered_texts)) / max(1, len(recovered_texts))

    donor_coverage = {
        "full_k_over_all_rows": _coverage(full_k),
        "full_k_over_recovered": _coverage(full_k, recovered),
        "full_k_weak4_over_all_rows": _coverage(full_k, weak4_mask),
        "full_k_weak4_over_recovered": _coverage(full_k, weak4_mask & recovered),
        "donor_count_histogram": {
            str(count): int(total) for count, total in sorted(donor_index_count.items())
        },
    }

    gates = {
        "donor_coverage_recovered_ge_080": bool(
            (donor_coverage["full_k_over_recovered"] or 0.0) >= 0.80
        ),
        "donor_coverage_weak4_recovered_ge_080": bool(
            (donor_coverage["full_k_weak4_over_recovered"] or 0.0) >= 0.80
        ),
        "zero_same_session_donors": violations["same_session"] == 0,
        "zero_same_scenario_donors": violations["same_scenario"] == 0,
        "zero_identical_text_donors": violations["identical_text"] == 0,
        "length_auc_below_060": bool(length_auc is not None and abs(length_auc - 0.5) < 0.10),
    }

    report = {
        "format": AUDIT_FORMAT,
        "data_dir": str(args.data_dir),
        "k": args.k,
        "donor_seed_rule": DONOR_SEED_RULE,
        "matching_key": ["label", "source", "turn_bin", "language_pref"],
        "exclusions": ["same_session", "same_au_scenario_group", "identical_next_user_text"],
        "rows": len(rows),
        "recovery": {
            "status_counts": dict(sorted(Counter(statuses).items())),
            "coverage": _coverage(recovered),
            "weak4_coverage": _coverage(recovered, weak4_mask),
            "per_class": per_class_recovery,
            "per_source": {
                source: _coverage(
                    recovered, np.asarray([row["source"] == source for row in rows])
                )
                for source in sorted({row["source"] for row in rows})
            },
            "per_turn_bin": {
                bin_name: _coverage(
                    recovered, np.asarray([row["turn_bin"] == bin_name for row in rows])
                )
                for bin_name in sorted({row["turn_bin"] for row in rows})
            },
            "malformed_witness_histories": int(
                sum(row["malformed_history"] for row in rows)
            ),
            "duplicate_next_user_text_ratio": float(duplicate_ratio),
        },
        "donors": donor_coverage,
        "donor_exclusion_violations": violations,
        "lengths": {
            "actual_chars": _length_stats(actual_lengths),
            "donor_chars": _length_stats(donor_lengths),
            "actual_vs_donor_char_auc": length_auc,
            "tokenizer": tokenizer_lengths(
                recovered_texts,
                [texts[j] for donor in donors if donor for j in donor],
                args.tokenizer,
            ),
        },
        "gates": gates,
        "decision": "GO" if all(gates.values()) else "HOLD",
        "hashes": {
            "audit_future_donors.py": sha256_file(__file__),
            "train.jsonl": sha256_file(Path(args.data_dir) / "train.jsonl"),
            "train_labels.csv": sha256_file(Path(args.data_dir) / "train_labels.csv"),
        },
    }
    if not args.skip_leakage_probe:
        report["next_user_only_action_probe"] = leakage_probe(
            rows, statuses, texts, seed=args.seed
        )

    payload = {
        "format": PAYLOAD_FORMAT,
        "classes": CLASSES,
        "k": args.k,
        "donor_seed_rule": DONOR_SEED_RULE,
        "matching_key": ["label", "source", "turn_bin", "language_pref"],
        "ids": [row["id"] for row in rows],
        "status": statuses,
        "label": [row["label"] for row in rows],
        "session": [row["session"] for row in rows],
        "scenario": [row["scenario"] for row in rows],
        "source": [row["source"] for row in rows],
        "turn_bin": [row["turn_bin"] for row in rows],
        "language_pref": [row["lang"] for row in rows],
        "next_user": texts,
        "donor_ids": [
            [rows[j]["id"] for j in donor] if donor else None for donor in donors
        ],
    }
    payload_path = Path(args.payload)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(payload_path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    report["hashes"]["payload"] = sha256_file(payload_path)
    report["payload_path"] = str(payload_path)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("recovery", "donors", "gates", "decision")}, ensure_ascii=False, indent=2))
    print(f"audit -> {output_path}")
    print(f"payload -> {payload_path}")


if __name__ == "__main__":
    main()
