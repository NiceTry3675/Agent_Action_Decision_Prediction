import argparse
import csv
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.svm import LinearSVC


ALL_CLASSES = [
    "read_file",
    "grep_search",
    "list_directory",
    "glob_pattern",
    "edit_file",
    "write_file",
    "apply_patch",
    "run_bash",
    "run_tests",
    "lint_or_typecheck",
    "ask_user",
    "plan_task",
    "web_search",
    "respond_only",
]

TOKEN_PATTERN = r"(?u)[A-Za-z0-9_./:+-]+|[가-힣]+"


EXPERIMENTS = {
    "baseline_prompt_lr": {
        "model_family": "tfidf_logreg",
        "features": "current_prompt word 1-2grams",
        "channels": {
            "prompt": {
                "analyzer": "word",
                "ngram_range": (1, 2),
                "min_df": 2,
                "max_features": 80_000,
            },
        },
        "classifier": {
            "type": "logreg",
            "C": 2.0,
            "class_weight": "balanced",
            "max_iter": 500,
        },
    },
    "prompt_svc": {
        "model_family": "tfidf_linearsvc",
        "features": "current_prompt word+char ngrams",
        "channels": {
            "prompt": {
                "analyzer": "word",
                "ngram_range": (1, 3),
                "min_df": 2,
                "max_features": 180_000,
            },
            "prompt_char": {
                "analyzer": "char_wb",
                "ngram_range": (3, 5),
                "min_df": 2,
                "max_features": 160_000,
            },
            "intent_meta": {
                "analyzer": "word",
                "ngram_range": (1, 2),
                "min_df": 1,
                "max_features": 40_000,
            },
        },
        "classifier": {
            "type": "linearsvc",
            "C": 0.8,
            "class_weight": "balanced",
            "max_iter": 6000,
        },
    },
    "full_svc": {
        "model_family": "tfidf_linearsvc",
        "features": "prompt, history actions, args/results, workspace metadata",
        "channels": {
            "prompt": {
                "analyzer": "word",
                "ngram_range": (1, 3),
                "min_df": 2,
                "max_features": 180_000,
            },
            "prompt_char": {
                "analyzer": "char_wb",
                "ngram_range": (3, 5),
                "min_df": 2,
                "max_features": 160_000,
            },
            "history": {
                "analyzer": "word",
                "ngram_range": (1, 2),
                "min_df": 2,
                "max_features": 220_000,
            },
            "intent_meta": {
                "analyzer": "word",
                "ngram_range": (1, 2),
                "min_df": 1,
                "max_features": 80_000,
            },
        },
        "classifier": {
            "type": "linearsvc",
            "C": 0.7,
            "class_weight": "balanced",
            "max_iter": 7000,
        },
    },
    "full_svc_light": {
        "model_family": "tfidf_linearsvc",
        "features": "compact prompt/history/meta sparse ensemble",
        "channels": {
            "prompt": {
                "analyzer": "word",
                "ngram_range": (1, 2),
                "min_df": 2,
                "max_features": 120_000,
            },
            "prompt_char": {
                "analyzer": "char_wb",
                "ngram_range": (3, 5),
                "min_df": 2,
                "max_features": 100_000,
            },
            "history": {
                "analyzer": "word",
                "ngram_range": (1, 2),
                "min_df": 3,
                "max_features": 140_000,
            },
            "intent_meta": {
                "analyzer": "word",
                "ngram_range": (1, 2),
                "min_df": 1,
                "max_features": 50_000,
            },
        },
        "classifier": {
            "type": "linearsvc",
            "C": 0.8,
            "class_weight": "balanced",
            "max_iter": 6000,
        },
    },
}


def safe_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def load_jsonl(path):
    samples = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return samples


def load_labels(path):
    with open(path, encoding="utf-8", newline="") as f:
        return {row["id"]: row["action"] for row in csv.DictReader(f)}


def session_id(sample_id):
    return safe_text(sample_id).split("-step_")[0]


def bin_numeric(name, value, bins):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return f"{name}_missing"
    for label, upper in bins:
        if value <= upper:
            return f"{name}_{label}"
    return f"{name}_hi"


def path_tokens(path):
    path = safe_text(path).replace("\\", "/")
    if not path:
        return []
    parts = [p for p in path.split("/") if p]
    tokens = [f"path:{path}", f"base:{parts[-1]}"] if parts else [f"path:{path}"]
    for part in parts[:-1]:
        tokens.append(f"dir:{part}")
    if "." in parts[-1]:
        tokens.append(f"ext:{parts[-1].rsplit('.', 1)[-1].lower()}")
    return tokens


def flatten_value(value, prefix="", depth=0):
    if depth > 2:
        return []
    tokens = []
    if isinstance(value, dict):
        for key in sorted(value):
            key_text = safe_text(key)
            next_prefix = f"{prefix}.{key_text}" if prefix else key_text
            tokens.append(f"key:{next_prefix}")
            tokens.extend(flatten_value(value[key], next_prefix, depth + 1))
    elif isinstance(value, list):
        tokens.append(f"{prefix}_list_len_{min(len(value), 8)}")
        for item in value[:8]:
            tokens.extend(flatten_value(item, prefix, depth + 1))
    else:
        text = safe_text(value)
        if text:
            short = text[:160].replace("\n", " ")
            tokens.append(f"{prefix}={short}")
            if "/" in text or "\\" in text or "." in text:
                tokens.extend(path_tokens(text))
    return tokens


def prompt_intent_tokens(prompt):
    text = safe_text(prompt).lower()
    tokens = []
    patterns = [
        ("intent_run_tests", r"\b(test|tests|pytest|jest|vitest|happy path|specs?)\b|테스트|검증"),
        ("intent_lint", r"\b(lint|eslint|ruff|mypy|typecheck|type-check|tsc|typing)\b|타입|린트"),
        ("intent_run_bash", r"\b(run|execute|shell|command|terminal|build|install|pip|npm|yarn|pnpm|docker|server)\b|실행|빌드|터미널|명령"),
        ("intent_read", r"\b(open|show|read|inspect|look at|current impl|what'?s in)\b|열어|보여|읽어|확인"),
        ("intent_grep", r"\b(grep|search|find references|occurrences|look for|where is|찾아|검색)\b"),
        ("intent_glob", r"\b(glob|pattern|files matching|all .* files)\b"),
        ("intent_list", r"\b(list|ls|directory|folder|tree|what files)\b|목록"),
        ("intent_edit", r"\b(change|fix|update|modify|edit|rename|refactor|patch|touch)\b|수정|고쳐|바꿔|패치"),
        ("intent_write", r"\b(create|new file|write a|add a file|scaffold)\b|새 파일|작성"),
        ("intent_plan", r"\b(plan|steps|break down|outline|roadmap|approach)\b|계획|단계"),
        ("intent_ask", r"\b(ask me|confirm|clarify|question)\b|물어|확인해줘"),
        ("intent_web", r"\b(web|internet|search online|latest|docs|documentation|browser)\b|웹|인터넷|검색해"),
        ("intent_respond", r"\b(explain|summarize|tell me|what do you think|answer)\b|설명|요약|답변"),
    ]
    for name, pattern in patterns:
        if re.search(pattern, text):
            tokens.append(name)
    tokens.append(bin_numeric("prompt_len", len(text), [("xs", 20), ("s", 60), ("m", 140), ("l", 320)]))
    if "?" in text or "어?" in text or "까" in text:
        tokens.append("prompt_has_question")
    if any(ch in text for ch in ("ㅎ", "ㅋㅋ", "lol", "thanks", "cheers")):
        tokens.append("prompt_casual")
    return tokens


def build_channels(samples):
    channels = {"prompt": [], "prompt_char": [], "history": [], "intent_meta": []}
    for sample in samples:
        prompt = safe_text(sample.get("current_prompt", ""))
        channels["prompt"].append(prompt)
        channels["prompt_char"].append(prompt)

        history_parts = []
        meta_tokens = []
        history = sample.get("history") or []
        action_names = []
        user_turns = []
        for i, event in enumerate(history):
            role = event.get("role")
            if role == "user":
                content = safe_text(event.get("content"))
                user_turns.append(content)
                history_parts.append(f"user_turn_{i} {content}")
            elif role == "assistant_action":
                name = safe_text(event.get("name"))
                action_names.append(name)
                history_parts.append(f"action_{i} name:{name}")
                history_parts.extend(flatten_value(event.get("args") or {}, f"args_{name}"))
                result = safe_text(event.get("result_summary"))
                if result:
                    history_parts.append(f"result_{name} {result}")

        channels["history"].append(" ".join(history_parts[-450:]))

        sm = sample.get("session_meta") or {}
        ws = sm.get("workspace") or {}
        meta_tokens.extend(prompt_intent_tokens(prompt))
        meta_tokens.append(f"tier_{safe_text(sm.get('user_tier', 'missing'))}")
        meta_tokens.append(f"lang_{safe_text(sm.get('language_pref', 'missing'))}")
        meta_tokens.append(f"dirty_{safe_text(ws.get('git_dirty', 'missing')).lower()}")
        meta_tokens.append(f"ci_{safe_text(ws.get('last_ci_status', 'missing'))}")
        meta_tokens.append(bin_numeric("turn", sm.get("turn_index"), [("01", 1), ("02", 2), ("04", 4), ("08", 8), ("12", 12)]))
        meta_tokens.append(bin_numeric("budget", sm.get("budget_tokens_remaining"), [("tiny", 6_000), ("low", 15_000), ("mid", 60_000), ("high", 130_000)]))
        meta_tokens.append(bin_numeric("elapsed", sm.get("elapsed_session_sec"), [("fresh", 120), ("short", 600), ("mid", 1800), ("long", 3600)]))
        meta_tokens.append(bin_numeric("loc", ws.get("loc"), [("tiny", 1_000), ("small", 5_000), ("mid", 20_000), ("large", 80_000)]))
        meta_tokens.append(f"history_len_{min(len(history), 12)}")

        language_mix = ws.get("language_mix") or {}
        if isinstance(language_mix, dict):
            for lang, ratio in sorted(language_mix.items(), key=lambda kv: (-float(kv[1]), kv[0]))[:4]:
                meta_tokens.append(f"code_lang_{safe_text(lang)}")
                try:
                    pct = int(round(float(ratio) * 10))
                    meta_tokens.append(f"code_lang_{safe_text(lang)}_{pct}")
                except (TypeError, ValueError):
                    pass

        open_files = ws.get("open_files") or []
        meta_tokens.append(f"open_files_{min(len(open_files), 6)}")
        for path in open_files[:8]:
            meta_tokens.extend(path_tokens(path))

        if action_names:
            meta_tokens.append(f"last_action_{action_names[-1]}")
            for name in action_names[-4:]:
                meta_tokens.append(f"recent_action_{name}")
            for a, b in zip(action_names[-5:], action_names[-4:]):
                meta_tokens.append(f"action_bigram_{a}>{b}")
            counts = Counter(action_names)
            for name, count in counts.items():
                meta_tokens.append(f"action_count_{name}_{min(count, 4)}")
        else:
            meta_tokens.append("no_history")

        if user_turns:
            meta_tokens.append("last_user " + user_turns[-1])
        channels["intent_meta"].append(" ".join(meta_tokens))
    return channels


def make_vectorizer(spec):
    kwargs = {
        "analyzer": spec["analyzer"],
        "ngram_range": tuple(spec["ngram_range"]),
        "min_df": spec["min_df"],
        "max_features": spec["max_features"],
        "sublinear_tf": True,
        "lowercase": True,
        "dtype": np.float32,
    }
    if spec["analyzer"] == "word":
        kwargs["token_pattern"] = TOKEN_PATTERN
    return TfidfVectorizer(**kwargs)


def fit_feature_matrix(samples, config):
    channel_texts = build_channels(samples)
    vectorizers = {}
    matrices = []
    for name, spec in config["channels"].items():
        vectorizer = make_vectorizer(spec)
        matrix = vectorizer.fit_transform(channel_texts[name])
        vectorizers[name] = vectorizer
        matrices.append(matrix)
        print(f"  channel={name:12s} shape={matrix.shape} nnz={matrix.nnz}")
    return hstack(matrices, format="csr", dtype=np.float32), vectorizers


def transform_feature_matrix(samples, vectorizers):
    channel_texts = build_channels(samples)
    matrices = []
    for name, vectorizer in vectorizers.items():
        matrices.append(vectorizer.transform(channel_texts[name]))
    return hstack(matrices, format="csr", dtype=np.float32)


def make_classifier(config, override_c=None, override_class_weight=None):
    spec = config["classifier"]
    class_weight = spec.get("class_weight")
    if override_class_weight == "none":
        class_weight = None
    elif override_class_weight and override_class_weight != "same":
        class_weight = override_class_weight
    c_value = spec["C"] if override_c is None else override_c
    if spec["type"] == "logreg":
        return LogisticRegression(
            C=c_value,
            class_weight=class_weight,
            max_iter=spec.get("max_iter", 500),
            n_jobs=-1,
            random_state=42,
        )
    if spec["type"] == "linearsvc":
        return LinearSVC(
            C=c_value,
            class_weight=class_weight,
            max_iter=spec.get("max_iter", 6000),
            dual="auto",
            random_state=42,
        )
    raise ValueError(f"unknown classifier type: {spec['type']}")


def split_indices(samples, y, split_type):
    indices = np.arange(len(samples))
    if split_type == "random":
        train_idx, val_idx = train_test_split(
            indices,
            test_size=0.2,
            stratify=y,
            random_state=42,
        )
        return train_idx, val_idx
    if split_type == "session":
        groups = np.array([session_id(s.get("id", "")) for s in samples])
        splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
        train_idx, val_idx = next(splitter.split(indices, y, groups))
        return train_idx, val_idx
    raise ValueError(f"unknown split type: {split_type}")


def predict_from_scores(scores, classes, bias=None):
    if bias is not None:
        scores = scores + np.asarray(bias, dtype=np.float64).reshape(1, -1)
    best = np.argmax(scores, axis=1)
    return np.asarray(classes, dtype=object)[best]


def decision_scores(clf, matrix):
    if hasattr(clf, "decision_function"):
        scores = clf.decision_function(matrix)
    elif hasattr(clf, "predict_proba"):
        scores = clf.predict_proba(matrix)
    else:
        pred = clf.predict(matrix)
        classes = list(clf.classes_)
        scores = np.zeros((len(pred), len(classes)), dtype=np.float64)
        for i, label in enumerate(pred):
            scores[i, classes.index(label)] = 1.0
    if scores.ndim == 1:
        scores = np.vstack([-scores, scores]).T
    return np.asarray(scores, dtype=np.float64)


def tune_class_bias(scores, y_true, classes, rounds=3):
    classes = list(classes)
    bias = np.zeros(len(classes), dtype=np.float64)
    candidates = np.linspace(-2.4, 2.4, 49)
    best_pred = predict_from_scores(scores, classes, bias)
    best = f1_score(y_true, best_pred, labels=ALL_CLASSES, average="macro", zero_division=0)
    for round_no in range(rounds):
        improved = False
        for class_idx, class_name in enumerate(classes):
            current = bias[class_idx]
            local_best = best
            local_value = current
            for candidate in candidates:
                trial = bias.copy()
                trial[class_idx] = candidate
                pred = predict_from_scores(scores, classes, trial)
                score = f1_score(y_true, pred, labels=ALL_CLASSES, average="macro", zero_division=0)
                if score > local_best + 1e-7:
                    local_best = score
                    local_value = candidate
            if local_best > best + 1e-7:
                bias[class_idx] = local_value
                best = local_best
                improved = True
                print(f"  bias round={round_no + 1} class={class_name} value={local_value:.2f} macro_f1={best:.5f}")
            else:
                bias[class_idx] = current
        if not improved:
            break
    return bias, best


def top_confusions(y_true, y_pred, labels):
    matrix = confusion_matrix(y_true, y_pred, labels=labels)
    pairs = []
    for i, true_label in enumerate(labels):
        for j, pred_label in enumerate(labels):
            if i != j and matrix[i, j]:
                pairs.append((int(matrix[i, j]), true_label, pred_label))
    return sorted(pairs, reverse=True)[:20]


def evaluate(y_true, y_pred):
    macro = f1_score(y_true, y_pred, labels=ALL_CLASSES, average="macro", zero_division=0)
    per_class = classification_report(
        y_true,
        y_pred,
        labels=ALL_CLASSES,
        output_dict=True,
        zero_division=0,
    )
    pred_dist = Counter(y_pred)
    return {
        "macro_f1": macro,
        "per_class_f1": {label: per_class[label]["f1-score"] for label in ALL_CLASSES},
        "prediction_distribution": dict(sorted(pred_dist.items())),
        "top_confusions": top_confusions(y_true, y_pred, ALL_CLASSES),
    }


def append_results_csv(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["experiment_id", "model_family", "features", "split_type", "macro_f1", "notes", "artifact_path"]
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in fieldnames})


def append_research_log(path, experiment_id, config, split_type, metrics, notes, decision):
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    weak = sorted(metrics["per_class_f1"].items(), key=lambda kv: kv[1])[:5]
    strong = sorted(metrics["per_class_f1"].items(), key=lambda kv: kv[1], reverse=True)[:5]
    lines = [
        f"## {experiment_id}",
        "",
        f"- Date/time: {now}",
        f"- Hypothesis: {notes}",
        f"- Code/config changes: `{config['model_family']}` with {config['features']}.",
        f"- Validation setup: {split_type}",
        f"- Overall Macro-F1: {metrics['macro_f1']:.6f}",
        "- Per-class observations:",
        f"  - Weakest: {', '.join(f'{k}={v:.3f}' for k, v in weak)}",
        f"  - Strongest: {', '.join(f'{k}={v:.3f}' for k, v in strong)}",
        f"- Top confusions: {metrics['top_confusions'][:8]}",
        f"- Prediction distribution: {metrics['prediction_distribution']}",
        "- Runtime or package-size concerns: sparse sklearn model; expected offline CPU inference within limits.",
        f"- Decision: {decision}",
        "- Next suggested experiment: compare random vs session split and inspect weak classes before widening features.",
        "",
    ]
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def train_and_evaluate(args):
    config = EXPERIMENTS[args.experiment]
    samples = load_jsonl(Path(args.data_dir) / "train.jsonl")
    labels_by_id = load_labels(Path(args.data_dir) / "train_labels.csv")
    y = np.array([labels_by_id[s["id"]] for s in samples], dtype=object)
    train_idx, val_idx = split_indices(samples, y, args.split)
    train_samples = [samples[i] for i in train_idx]
    val_samples = [samples[i] for i in val_idx]
    y_train = y[train_idx]
    y_val = y[val_idx]

    print(f"experiment={args.experiment} split={args.split} train={len(train_samples)} val={len(val_samples)}")
    x_train, vectorizers = fit_feature_matrix(train_samples, config)
    x_val = transform_feature_matrix(val_samples, vectorizers)
    print(f"  train_matrix={x_train.shape} val_matrix={x_val.shape}")
    clf = make_classifier(config, args.override_c, args.class_weight)
    clf.fit(x_train, y_train)
    classes = list(clf.classes_)
    scores = decision_scores(clf, x_val)
    bias = np.zeros(len(classes), dtype=np.float64)
    pred = predict_from_scores(scores, classes, bias)
    metrics = evaluate(y_val, pred)
    notes = args.notes or config["features"]
    decision = "keep as candidate" if metrics["macro_f1"] >= args.keep_threshold else "discard or revisit"
    experiment_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{args.experiment}_{args.split}"

    print(f"  macro_f1={metrics['macro_f1']:.6f}")
    if args.tune_bias:
        print("  tuning class bias")
        bias, tuned_score = tune_class_bias(scores, y_val, classes)
        pred = predict_from_scores(scores, classes, bias)
        metrics = evaluate(y_val, pred)
        notes += "; class bias tuned on validation scores"
    if args.override_c is not None:
        notes += f"; C override={args.override_c}"
    if args.class_weight != "same":
        notes += f"; class_weight={args.class_weight}"
        decision = "keep as candidate" if metrics["macro_f1"] >= args.keep_threshold else "discard or revisit"
        print(f"  tuned_macro_f1={metrics['macro_f1']:.6f}")

    artifact_path = ""
    if args.save_split_artifact:
        artifact_path = str(Path(args.artifact_dir) / f"{experiment_id}.joblib")
        Path(args.artifact_dir).mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "classes": classes,
                "vectorizers": vectorizers,
                "classifier": clf,
                "bias": bias,
                "experiment": args.experiment,
                "config": config,
                "valid_labels": ALL_CLASSES,
            },
            artifact_path,
            compress=3,
        )

    append_results_csv(
        Path("experiments/results.csv"),
        {
            "experiment_id": experiment_id,
            "model_family": config["model_family"],
            "features": config["features"],
            "split_type": args.split,
            "macro_f1": f"{metrics['macro_f1']:.6f}",
            "notes": notes,
            "artifact_path": artifact_path,
        },
    )
    append_research_log(Path("research_log.md"), experiment_id, config, args.split, metrics, notes, decision)

    metrics_path = Path(args.artifact_dir) / f"{experiment_id}_metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "experiment_id": experiment_id,
                "experiment": args.experiment,
                "split": args.split,
                "metrics": metrics,
                "bias": dict(zip(classes, [float(x) for x in bias])),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    if args.final_model:
        print("  fitting final model on all training rows")
        x_all, vectorizers_all = fit_feature_matrix(samples, config)
        clf_all = make_classifier(config, args.override_c, args.class_weight)
        clf_all.fit(x_all, y)
        classes_all = list(clf_all.classes_)
        bias_map = dict(zip(classes, bias))
        final_bias = np.array([bias_map.get(c, 0.0) for c in classes_all], dtype=np.float64)
        output_model = Path(args.output_model)
        output_model.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "classes": classes_all,
                "vectorizers": vectorizers_all,
                "classifier": clf_all,
                "bias": final_bias,
                "experiment": args.experiment,
                "config": config,
                "valid_labels": ALL_CLASSES,
            },
            output_model,
            compress=3,
        )
        print(f"  saved final model: {output_model}")

    print("  weakest classes:")
    for label, score in sorted(metrics["per_class_f1"].items(), key=lambda kv: kv[1])[:8]:
        print(f"    {label:18s} {score:.4f}")
    print("  top confusions:")
    for count, true_label, pred_label in metrics["top_confusions"][:10]:
        print(f"    {true_label:18s} -> {pred_label:18s} {count}")
    return metrics


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--experiment", choices=sorted(EXPERIMENTS), default="full_svc")
    parser.add_argument("--split", choices=["random", "session"], default="session")
    parser.add_argument("--tune-bias", action="store_true")
    parser.add_argument("--final-model", action="store_true")
    parser.add_argument("--output-model", default="model/model.joblib")
    parser.add_argument("--artifact-dir", default="experiments/artifacts")
    parser.add_argument("--save-split-artifact", action="store_true")
    parser.add_argument("--keep-threshold", type=float, default=0.70)
    parser.add_argument("--override-c", type=float, default=None)
    parser.add_argument("--class-weight", choices=["same", "balanced", "none"], default="same")
    parser.add_argument("--notes", default="")
    return parser.parse_args()


if __name__ == "__main__":
    train_and_evaluate(parse_args())
