#!/usr/bin/env python3
"""Leakage-aware CPU audit for generator FSM and template-memory residuals."""

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch


CLASSES = [
    "read_file", "grep_search", "list_directory", "glob_pattern",
    "edit_file", "write_file", "apply_patch", "run_bash", "run_tests",
    "lint_or_typecheck", "ask_user", "plan_task", "web_search", "respond_only",
]
C2I = {x: i for i, x in enumerate(CLASSES)}
START = "<START>"

URL_RE = re.compile(r"https?://\S+", re.I)
GLOB_RE = re.compile(r"(?<!\w)(?:\*\*/)?\*\.[A-Za-z0-9]{1,10}")
PATH_RE = re.compile(
    r"(?:(?:[A-Za-z]:)?[\w.@~+-]+[\\/])+(?:[\w.@~+*?-]+)(?:\.[A-Za-z0-9]{1,10})?"
)
FILE_RE = re.compile(r"(?<![\w.])([A-Za-z0-9_@+-]+)\.([A-Za-z0-9]{1,10})(?![\w.])")
QUOTE_RE = re.compile(r"([`'\"])([^`'\"\n]{1,80})\1")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
CAMEL_RE = re.compile(
    r"\b(?:[A-Za-z]+_[A-Za-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]*|[A-Za-z][A-Za-z0-9]*\(\))\b"
)


def bucket_count(n):
    n = int(float(n))
    if n == 0:
        return "0"
    if n == 1:
        return "1"
    if n <= 3:
        return "2-3"
    if n <= 10:
        return "4-10"
    if n <= 50:
        return "11-50"
    return "51+"


def normalize_text(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = URL_RE.sub("<url>", text)
    text = GLOB_RE.sub(lambda m: f"<glob:{m.group(0).rsplit('.', 1)[-1].lower()}>", text)
    text = PATH_RE.sub("<path>", text)
    text = FILE_RE.sub(lambda m: f"<file:{m.group(2).lower()}>", text)
    text = CAMEL_RE.sub("<symbol>", text)

    def replace_quoted(match):
        content = match.group(2)
        if any(marker in content.lower() for marker in ("<path>", "<file:", "<glob:", "<url>")):
            return content
        return "<quoted>"

    text = QUOTE_RE.sub(replace_quoted, text)
    text = NUMBER_RE.sub("<num>", text)
    return re.sub(r"\s+", " ", text).lower().strip()


def normalize_result(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = URL_RE.sub("<url>", text)
    text = GLOB_RE.sub("<glob>", text)
    text = PATH_RE.sub("<path>", text)
    text = FILE_RE.sub(lambda m: f"<file:{m.group(2).lower()}>", text)
    text = QUOTE_RE.sub("<quoted>", text)
    text = CAMEL_RE.sub("<symbol>", text)
    text = NUMBER_RE.sub(lambda m: f"<n:{bucket_count(m.group(0))}>", text)
    return re.sub(r"\s+", " ", text).lower().strip()


def value_kind(key, value):
    text = str(value or "")
    lower = text.lower().strip()
    if key == "path":
        ext = lower.rsplit(".", 1)[-1] if "." in lower.rsplit("/", 1)[-1] else "none"
        shape = "nested" if "/" in lower or "\\" in lower else "base"
        return f"{shape}:{ext[:10]}"
    if key == "scope":
        return "repo" if lower in {"", ".", "repo", "all", "workspace"} else "path"
    if key == "pattern":
        if "*" in lower or "?" in lower:
            return "glob"
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text.strip()):
            return "symbol"
        return "text"
    if key == "cmd":
        if re.search(r"\b(test|pytest|jest|vitest|rspec|go test|cargo test)\b", lower):
            return "test"
        if re.search(r"\b(lint|eslint|mypy|ruff|checkstyle|tsc)\b", lower):
            return "lint"
        if re.search(r"\b(build|compile|package|docker)\b", lower):
            return "build"
        if re.search(r"\b(install|pip|npm|yarn|pnpm|mvn|gradle)\b", lower):
            return "install"
        return "other"
    if key == "n_files":
        try:
            return bucket_count(value)
        except (TypeError, ValueError):
            return "na"
    if key in {"target", "target_symbol", "goal", "question", "query"}:
        return "glob" if "*" in lower else "text"
    return "text"


def arg_schema(args):
    if not isinstance(args, dict) or not args:
        return "none"
    return ",".join(f"{k}:{value_kind(k, v)}" for k, v in sorted(args.items()))


def turn_bucket(turn):
    turn = int(turn)
    if turn <= 1:
        return "1"
    if turn <= 3:
        return "2-3"
    if turn <= 6:
        return "4-6"
    if turn <= 10:
        return "7-10"
    return "11+"


def hash_fold(group, n_folds, seed):
    d = hashlib.sha256(f"{seed}:{group}".encode()).digest()
    return int.from_bytes(d[:8], "big") % n_folds


def scenario_group(sample_id):
    session = str(sample_id).split("-step_")[0]
    parts = session.split("_")
    if len(parts) >= 4 and parts[:2] == ["sess", "au"]:
        return "_".join(parts[:3])
    return session


def f1_summary(y, pred):
    y = np.asarray(y, dtype=np.int64)
    pred = np.asarray(pred, dtype=np.int64)
    f1 = []
    for c in range(len(CLASSES)):
        tp = int(np.sum((y == c) & (pred == c)))
        fp = int(np.sum((y != c) & (pred == c)))
        fn = int(np.sum((y == c) & (pred != c)))
        den = 2 * tp + fp + fn
        f1.append(0.0 if den == 0 else 2 * tp / den)
    return {
        "rows": int(len(y)),
        "accuracy": float(np.mean(y == pred)),
        "macro_f1": float(np.mean(f1)),
        "weak4_macro_f1": float(np.mean(f1[:4])),
        "per_class_f1": dict(zip(CLASSES, map(float, f1))),
    }


def load_records(data_dir, n_folds, seed):
    data_dir = Path(data_dir)
    labels = {
        row["id"]: row["action"]
        for row in csv.DictReader((data_dir / "train_labels.csv").open(encoding="utf-8"))
    }
    samples = [json.loads(line) for line in (data_dir / "train.jsonl").open(encoding="utf-8")]
    records = []
    for sample in samples:
        rid = sample["id"]
        sess, step_text = rid.rsplit("-step_", 1)
        parts = sess.split("_")
        source = parts[1]
        scenario = scenario_group(rid)
        turn = int(sample["session_meta"]["turn_index"])
        events = []
        actions = []
        for event in sample.get("history") or []:
            if event.get("role") != "assistant_action":
                continue
            action = str(event.get("name") or "")
            actions.append(action)
            events.append(
                f"{action}|{arg_schema(event.get('args'))}|{normalize_result(event.get('result_summary'))}"
            )
        if source == "sim":
            date = parts[2]
            primary = int(parts[3])
            variant = 0
            id_bin = primary // 5000
        else:
            date = "na"
            primary = int(parts[2])
            variant = int(parts[3])
            id_bin = primary // 100000
        rec = {
            "id": rid,
            "session": sess,
            "scenario": scenario,
            "source": source,
            "turn": turn,
            "turn_bucket": turn_bucket(turn),
            "y": C2I[labels[rid]],
            "actions": actions,
            "events": events,
            "prompt_template": normalize_text(sample.get("current_prompt")),
            "date": date,
            "primary": primary,
            "variant": variant,
            "id_bin": id_bin,
            "session_fold": hash_fold(sess, n_folds, seed),
            "scenario_fold": hash_fold(scenario, n_folds, seed),
            "sample": sample,
        }
        records.append(rec)
    return records


def trajectory_report(records):
    by_session = defaultdict(list)
    for rec in records:
        by_session[rec["session"]].append(rec)
    conflicts = 0
    row_nodes = 0
    recovered = 0
    missing = 0
    full = 0
    for sess, rows in by_session.items():
        nodes = {}
        rows.sort(key=lambda r: r["turn"])
        for rec in rows:
            step = rec["turn"]
            key = (rec["sample"].get("current_prompt"), CLASSES[rec["y"]])
            if step in nodes and nodes[step] != key:
                conflicts += 1
            nodes[step] = key
            history = rec["sample"].get("history") or []
            n = len(history) // 2
            for j in range(n):
                prior = step - n + j
                key = (history[2 * j].get("content"), history[2 * j + 1].get("name"))
                if prior in nodes and nodes[prior] != key:
                    conflicts += 1
                else:
                    nodes[prior] = key
        row_steps = {r["turn"] for r in rows}
        row_nodes += len(row_steps)
        recovered += len(set(nodes) - row_steps)
        absent = set(range(1, max(row_steps) + 1)) - set(nodes)
        missing += len(absent)
        full += int(not absent)
    au_scenarios = {r["scenario"] for r in records if r["source"] == "au"}
    return {
        "rows": len(records),
        "sessions": len(by_session),
        "scenario_groups": len({r["scenario"] for r in records}),
        "au_sessions": len({r["session"] for r in records if r["source"] == "au"}),
        "au_scenario_groups": len(au_scenarios),
        "row_nodes": row_nodes,
        "history_only_nodes_recovered": recovered,
        "union_nodes": row_nodes + recovered,
        "conflicts": conflicts,
        "unrecovered_gaps_through_last_row": missing,
        "fully_reconstructed_sessions": full,
    }


class Tables:
    def __init__(self, records, indices, strength=20.0):
        self.records = records
        self.strength = strength
        self.global_counts = np.bincount([records[i]["y"] for i in indices], minlength=14).astype(float)
        self.global_prob = (self.global_counts + 1) / (self.global_counts.sum() + 14)
        self.base = defaultdict(lambda: np.zeros(14, dtype=float))
        self.exact = {name: defaultdict(lambda: np.zeros(14, dtype=float)) for name in (
            "coarse2", "prompt_template", "prompt_action", "id_bin", "id_mod8", "date"
        )}
        self.action = [defaultdict(lambda: np.zeros(14, dtype=float)) for _ in range(7)]
        self.event = [defaultdict(lambda: np.zeros(14, dtype=float)) for _ in range(4)]
        for i in indices:
            r = records[i]
            y = r["y"]
            bk = self.base_key(r)
            self.base[bk][y] += 1
            last2 = tuple(([START, START] + r["actions"])[-2:])
            self.exact["coarse2"][(bk, last2)][y] += 1
            self.exact["prompt_template"][(r["source"], r["prompt_template"])][y] += 1
            self.exact["prompt_action"][(r["source"], r["prompt_template"], last2)][y] += 1
            self.exact["id_bin"][(bk, r["id_bin"])][y] += 1
            self.exact["id_mod8"][(bk, r["primary"] % 8, r["variant"] if r["source"] == "au" else 0)][y] += 1
            self.exact["date"][(bk, r["date"])][y] += 1
            if not r["actions"]:
                self.action[1][(bk, (START,))][y] += 1
            for k in range(1, min(6, len(r["actions"])) + 1):
                self.action[k][(bk, tuple(r["actions"][-k:]))][y] += 1
            if not r["events"]:
                self.event[1][(bk, (START,))][y] += 1
            for k in range(1, min(3, len(r["events"])) + 1):
                self.event[k][(bk, tuple(r["events"][-k:]))][y] += 1

    @staticmethod
    def base_key(r):
        return (r["source"], r["turn_bucket"])

    def base_prob(self, r):
        counts = self.base.get(self.base_key(r))
        if counts is None or not counts.sum():
            return self.global_prob
        return (counts + 1) / (counts.sum() + 14)

    def posterior(self, counts, base):
        support = float(counts.sum())
        prob = (counts + self.strength * base) / (support + self.strength)
        purity = float(counts.max() / support) if support else 0.0
        return prob, int(support), purity

    def exact_pred(self, r, name, min_support):
        bk = self.base_key(r)
        last2 = tuple(([START, START] + r["actions"])[-2:])
        keys = {
            "coarse2": (bk, last2),
            "prompt_template": (r["source"], r["prompt_template"]),
            "prompt_action": (r["source"], r["prompt_template"], last2),
            "id_bin": (bk, r["id_bin"]),
            "id_mod8": (bk, r["primary"] % 8, r["variant"] if r["source"] == "au" else 0),
            "date": (bk, r["date"]),
        }
        base = self.base_prob(r)
        counts = self.exact[name].get(keys[name])
        if counts is None or counts.sum() < min_support:
            return base, base, 0, 0.0, 0
        prob, support, purity = self.posterior(counts, base)
        return prob, base, support, purity, 1

    def suffix_pred(self, r, kind, min_support, exact_k=None):
        base = self.base_prob(r)
        seq = r["actions"] if kind == "action" else r["events"]
        maps = self.action if kind == "action" else self.event
        max_k = 6 if kind == "action" else 3
        if not seq:
            seq = [START]
        candidates = [exact_k] if exact_k is not None else range(min(max_k, len(seq)), 0, -1)
        for k in candidates:
            if k is None or k > len(seq):
                continue
            counts = maps[k].get((self.base_key(r), tuple(seq[-k:])))
            if counts is not None and counts.sum() >= min_support:
                prob, support, purity = self.posterior(counts, base)
                return prob, base, support, purity, k
        return base, base, 0, 0.0, 0

    def predict(self, r, model, min_support):
        if model.startswith("action_k"):
            return self.suffix_pred(r, "action", min_support, int(model[-1]))
        if model.startswith("event_k"):
            return self.suffix_pred(r, "event", min_support, int(model[-1]))
        if model == "action_vorder":
            return self.suffix_pred(r, "action", min_support)
        if model == "event_vorder":
            return self.suffix_pred(r, "event", min_support)
        if model == "fsm_memory":
            for name in ("prompt_action", "prompt_template"):
                out = self.exact_pred(r, name, min_support)
                if out[2] >= min_support:
                    return out
            out = self.suffix_pred(r, "event", min_support)
            if out[2] >= min_support:
                return out
            return self.suffix_pred(r, "action", min_support)
        return self.exact_pred(r, model, min_support)


DESCRIPTIVE_MODELS = [
    "date", "id_bin", "id_mod8", "coarse2",
    "action_k1", "action_k2", "action_k3", "action_k4", "action_k5", "action_k6",
    "action_vorder", "event_k1", "event_k2", "event_k3", "event_vorder",
    "prompt_template", "prompt_action", "fsm_memory",
]
RESIDUAL_MODELS = ["coarse2", "action_vorder", "event_vorder", "prompt_template", "fsm_memory"]


def predict_rows(tables, records, indices, model, min_support):
    probs = np.empty((len(indices), 14), dtype=float)
    bases = np.empty_like(probs)
    support = np.empty(len(indices), dtype=np.int32)
    purity = np.empty(len(indices), dtype=float)
    depth = np.empty(len(indices), dtype=np.int8)
    for j, i in enumerate(indices):
        probs[j], bases[j], support[j], purity[j], depth[j] = tables.predict(records[i], model, min_support)
    return probs, bases, support, purity, depth


def grouped_descriptive(records, fold_key, n_folds, min_support=5):
    folds = np.asarray([r[fold_key] for r in records])
    y = np.asarray([r["y"] for r in records])
    state = {
        model: {
            "pred": np.empty(len(records), dtype=np.int64),
            "support": np.zeros(len(records), dtype=np.int32),
            "purity": np.zeros(len(records), dtype=float),
            "depth": np.zeros(len(records), dtype=np.int8),
        }
        for model in DESCRIPTIVE_MODELS
    }
    for fold in range(n_folds):
        train = np.where(folds != fold)[0].tolist()
        val = np.where(folds == fold)[0].tolist()
        tables = Tables(records, train)
        for model in DESCRIPTIVE_MODELS:
            prob, _, sup, pur, dep = predict_rows(tables, records, val, model, min_support)
            state[model]["pred"][val] = np.argmax(prob, axis=1)
            state[model]["support"][val] = sup
            state[model]["purity"][val] = pur
            state[model]["depth"][val] = dep
    outputs = {}
    for model in DESCRIPTIVE_MODELS:
        pred = state[model]["pred"]
        support = state[model]["support"]
        purity = state[model]["purity"]
        depth = state[model]["depth"]
        covered = support >= min_support
        high = covered & (purity >= 0.65)
        weak = y < 4
        metrics = f1_summary(y, pred)
        outputs[model] = {
            "policy_only": {k: metrics[k] for k in ("accuracy", "macro_f1", "weak4_macro_f1")},
            "coverage": float(np.mean(covered)),
            "weak4_coverage": float(np.mean(covered[weak])),
            "high_purity_coverage": float(np.mean(high)),
            "covered_accuracy": float(np.mean(pred[covered] == y[covered])) if covered.any() else None,
            "high_purity_accuracy": float(np.mean(pred[high] == y[high])) if high.any() else None,
            "weak4_high_purity_rows": int(np.sum(high & weak)),
            "weak4_high_purity_accuracy": float(np.mean(pred[high & weak] == y[high & weak])) if (high & weak).any() else None,
            "mean_depth_covered": float(np.mean(depth[covered])) if covered.any() else None,
        }
    return outputs


def torch_load(path):
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def apply_residual(logits, prob, base, support, purity, lam, min_support, min_purity):
    raw = np.argmax(logits, axis=1)
    soft = np.exp(logits - logits.max(axis=1, keepdims=True))
    soft /= soft.sum(axis=1, keepdims=True)
    order = np.argsort(-logits, axis=1)
    strict = (
        (order[:, 0] < 4) & (order[:, 1] < 4) & (soft[:, :4].sum(axis=1) >= 0.5)
        & (support >= min_support) & (purity >= min_purity)
    )
    residual = np.log(np.clip(prob[:, :4], 1e-12, None)) - np.log(np.clip(base[:, :4], 1e-12, None))
    residual -= residual.mean(axis=1, keepdims=True)
    candidate_logits = logits.copy()
    candidate_logits[:, :4] += lam * residual
    candidate = np.argmax(candidate_logits, axis=1)
    candidate_in_top3 = np.asarray(
        [candidate[i] in order[i, :3] for i in range(len(logits))]
    )
    accept = strict & (candidate < 4) & candidate_in_top3
    pred = raw.copy()
    pred[accept] = candidate[accept]
    return pred, accept


def nested_residual(records, payload, n_folds):
    by_id = {r["id"]: i for i, r in enumerate(records)}
    ids = list(map(str, payload["ids"]))
    anchor_pos = np.asarray([by_id[x] for x in ids])
    logits = np.asarray(payload["logits"], dtype=np.float64)
    y = np.asarray(payload["y_true"], dtype=np.int64)
    if not np.array_equal(y, np.asarray([records[i]["y"] for i in anchor_pos])):
        raise ValueError("logit labels do not align")
    all_folds = np.asarray([r["scenario_fold"] for r in records])
    afolds = all_folds[anchor_pos]
    raw = np.argmax(logits, axis=1)
    lambdas = [0.0, 0.05, 0.1, 0.2, 0.3, 0.5]
    supports = [2, 5, 10, 20]
    purities = [0.55, 0.65, 0.75]
    anchor_set = set(anchor_pos.tolist())
    train_scenarios = {
        records[i]["scenario"] for i in range(len(records)) if i not in anchor_set
    }
    anchor_overlap = np.asarray(
        [records[i]["scenario"] in train_scenarios for i in anchor_pos], dtype=bool
    )
    anchor_au = np.asarray([records[i]["source"] == "au" for i in anchor_pos], dtype=bool)
    result = {
        "raw_main": f1_summary(y, raw),
        "main_split_scenario_overlap": {
            "diagnostic_available": len(anchor_pos) < len(records),
            "anchor_rows_with_training_scenario_sibling": int(anchor_overlap.sum()),
            "au_anchor_rows": int(anchor_au.sum()),
            "au_anchor_rows_with_training_scenario_sibling": int(
                np.sum(anchor_overlap & anchor_au)
            ),
            "note": (
                "The policy tables are scenario-group cross-fit. The supplied main logits "
                "may still come from a session-only split; this diagnostic counts that overlap."
            ),
        },
        "models": {},
    }
    final_preds = {model: np.full(len(ids), -1, dtype=np.int64) for model in RESIDUAL_MODELS}
    fold_rows = {model: [] for model in RESIDUAL_MODELS}
    for outer in range(n_folds):
        inner_mask = afolds != outer
        inner_indices = np.where(inner_mask)[0]
        offset = {anchor_i: j for j, anchor_i in enumerate(inner_indices.tolist())}
        inner_cache = {
            model: {
                support_min: (
                    np.empty((len(inner_indices), 14)),
                    np.empty((len(inner_indices), 14)),
                    np.empty(len(inner_indices), dtype=np.int32),
                    np.empty(len(inner_indices)),
                )
                for support_min in supports
            }
            for model in RESIDUAL_MODELS
        }
        for inner in range(n_folds):
            if inner == outer:
                continue
            aval = np.where(afolds == inner)[0]
            train = np.where((all_folds != outer) & (all_folds != inner))[0].tolist()
            tables = Tables(records, train)
            loc = [offset[x] for x in aval.tolist()]
            positions = anchor_pos[aval].tolist()
            for model in RESIDUAL_MODELS:
                for support_min in supports:
                    pp, bb, ss, qq, _ = predict_rows(tables, records, positions, model, support_min)
                    p, b, s, q = inner_cache[model][support_min]
                    p[loc], b[loc], s[loc], q[loc] = pp, bb, ss, qq
        outer_train = np.where(all_folds != outer)[0].tolist()
        outer_tables = Tables(records, outer_train)
        outer_aval = np.where(afolds == outer)[0]
        for model in RESIDUAL_MODELS:
            raw_inner = raw[inner_indices]
            y_inner = y[inner_indices]
            raw_score = f1_summary(y_inner, raw_inner)["macro_f1"]
            candidates = []
            for support_min in supports:
                p, b, s, q = inner_cache[model][support_min]
                for purity_min in purities:
                    for lam in lambdas:
                        pred, accept = apply_residual(
                            logits[inner_indices], p, b, s, q, lam, support_min, purity_min
                        )
                        score = f1_summary(y_inner, pred)["macro_f1"]
                        candidates.append((score, -lam, support_min, purity_min, lam, int(accept.sum())))
            best = max(candidates)
            _, _, support_min, purity_min, lam, inner_accepted = best
            p, b, s, q, _ = predict_rows(
                outer_tables, records, anchor_pos[outer_aval].tolist(), model, support_min
            )
            pred, accept = apply_residual(
                logits[outer_aval], p, b, s, q, lam, support_min, purity_min
            )
            final_preds[model][outer_aval] = pred
            r0 = raw[outer_aval]
            yy = y[outer_aval]
            rescue = int(np.sum((r0 != yy) & (pred == yy)))
            harm = int(np.sum((r0 == yy) & (pred != yy)))
            fold_metric = f1_summary(yy, pred)
            raw_metric = f1_summary(yy, r0)
            fold_rows[model].append({
                "fold": outer,
                "selected": {"lambda": lam, "min_support": support_min, "min_purity": purity_min},
                "inner_macro_delta": float(best[0] - raw_score),
                "inner_accepted": inner_accepted,
                "heldout_macro_delta": float(fold_metric["macro_f1"] - raw_metric["macro_f1"]),
                "heldout_weak4_delta": float(fold_metric["weak4_macro_f1"] - raw_metric["weak4_macro_f1"]),
                "accepted": int(accept.sum()), "flips": int(np.sum(pred != r0)),
                "rescue": rescue, "harm": harm,
            })
    for model in RESIDUAL_MODELS:
        final_pred = final_preds[model]
        if np.any(final_pred < 0):
            raise AssertionError("unfilled nested prediction")
        metric = f1_summary(y, final_pred)
        result["models"][model] = {
            "nested_oof": metric,
            "macro_delta": metric["macro_f1"] - result["raw_main"]["macro_f1"],
            "weak4_delta": metric["weak4_macro_f1"] - result["raw_main"]["weak4_macro_f1"],
            "positive_macro_folds": sum(x["heldout_macro_delta"] > 0 for x in fold_rows[model]),
            "total_flips": int(np.sum(final_pred != raw)),
            "total_rescue": int(np.sum((raw != y) & (final_pred == y))),
            "total_harm": int(np.sum((raw == y) & (final_pred != y))),
            "folds": fold_rows[model],
        }
    return result


def template_report(records):
    groups = defaultdict(lambda: {"sessions": set(), "scenarios": set(), "counts": Counter(), "rows": 0})
    for r in records:
        g = groups[r["prompt_template"]]
        g["sessions"].add(r["session"]); g["scenarios"].add(r["scenario"])
        g["counts"][CLASSES[r["y"]]] += 1; g["rows"] += 1
    out = {"templates": len(groups)}
    for group_name in ("sessions", "scenarios"):
        repeated = [g for g in groups.values() if len(g[group_name]) >= 2]
        rows = sum(g["rows"] for g in repeated)
        weak_rows = sum(sum(g["counts"][c] for c in CLASSES[:4]) for g in repeated)
        high_rows = sum(g["rows"] for g in repeated if max(g["counts"].values()) / g["rows"] >= 0.8)
        high_weak = sum(
            sum(g["counts"][c] for c in CLASSES[:4])
            for g in repeated if max(g["counts"].values()) / g["rows"] >= 0.8
        )
        out[f"cross_{group_name}"] = {
            "repeated_templates": len(repeated), "covered_rows": rows,
            "covered_fraction": rows / len(records), "weak4_covered_rows": weak_rows,
            "purity_ge_0.8_rows": high_rows, "weak4_purity_ge_0.8_rows": high_weak,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="open/data")
    ap.add_argument("--logits", required=True)
    ap.add_argument("--output", default="/tmp/generator_fsm_probe.json")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()
    records = load_records(args.data_dir, args.folds, args.seed)
    payload = torch_load(args.logits)
    report = {
        "schema_version": 2,
        "config": vars(args),
        "methodology": {
            "policy_validation": (
                "nested hash folds grouped by AU primary scenario (session elsewhere)"
            ),
            "selection": (
                "each outer fold uses only the other folds for hyperparameter selection"
            ),
            "template_report_caveat": (
                "prompt_templates is an in-sample inventory only; transfer evidence is in "
                "scenario_grouped_descriptive and scenario_grouped_nested_main_residual"
            ),
            "available_inputs": (
                "current_prompt, past history, session_meta, IDs, labels and supplied logits"
            ),
        },
        "trajectory": trajectory_report(records),
        "fold_rows": {
            "session": dict(Counter(r["session_fold"] for r in records)),
            "scenario": dict(Counter(r["scenario_fold"] for r in records)),
        },
        "prompt_templates": template_report(records),
        "session_grouped_descriptive": grouped_descriptive(records, "session_fold", args.folds),
        "scenario_grouped_descriptive": grouped_descriptive(records, "scenario_fold", args.folds),
        "scenario_grouped_nested_main_residual": nested_residual(records, payload, args.folds),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    residual = report["scenario_grouped_nested_main_residual"]
    concise = {
        "output": str(output),
        "rows": report["trajectory"]["rows"],
        "raw_macro_f1": residual["raw_main"]["macro_f1"],
        "models": {
            name: {
                "macro_delta": row["macro_delta"],
                "weak4_delta": row["weak4_delta"],
                "positive_macro_folds": row["positive_macro_folds"],
                "flips": row["total_flips"],
            }
            for name, row in residual["models"].items()
        },
    }
    print(json.dumps(concise, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
