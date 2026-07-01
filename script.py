import csv
import json
import os
import re
from collections import Counter

import joblib
import numpy as np
from scipy.sparse import hstack


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


def first_existing(paths):
    for path in paths:
        if os.path.exists(path):
            return path
    return paths[0]


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


def transform_feature_matrix(samples, vectorizers):
    channel_texts = build_channels(samples)
    matrices = []
    for name, vectorizer in vectorizers.items():
        matrices.append(vectorizer.transform(channel_texts[name]))
    return hstack(matrices, format="csr", dtype=np.float32)


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


def predict_from_scores(scores, classes, bias):
    if bias is not None:
        scores = scores + np.asarray(bias, dtype=np.float64).reshape(1, -1)
    best = np.argmax(scores, axis=1)
    return [str(classes[i]) for i in best]


def load_sample_submission(path, ids):
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)
        if fieldnames is None or fieldnames[:2] != ["id", "action"]:
            raise ValueError(f"sample_submission columns must start with id,action: {fieldnames}")
        return fieldnames, rows
    return ["id", "action"], [{"id": sample_id, "action": ALL_CLASSES[0]} for sample_id in ids]


def save_submission(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    data_dir = first_existing(["./data", "./open/data"])
    model_path = first_existing(["./model/model.joblib", "./open/baseline_submit/model/tfidf_logreg.pkl"])
    output_path = "./output/submission.csv"
    test_path = os.path.join(data_dir, "test.jsonl")
    sample_submission_path = os.path.join(data_dir, "sample_submission.csv")

    print(f"Load model: {model_path}")
    artifact = joblib.load(model_path)
    samples = load_jsonl(test_path)
    ids = [safe_text(sample.get("id", "")) for sample in samples]

    if isinstance(artifact, dict) and "vectorizers" in artifact:
        matrix = transform_feature_matrix(samples, artifact["vectorizers"])
        clf = artifact["classifier"]
        classes = list(artifact.get("classes") or clf.classes_)
        bias = artifact.get("bias")
        scores = decision_scores(clf, matrix)
        preds = predict_from_scores(scores, classes, bias)
    else:
        texts = [safe_text(sample.get("current_prompt", "")) for sample in samples]
        preds = [str(pred) for pred in artifact.predict(texts)]

    valid = set(ALL_CLASSES)
    bad = sorted(set(preds) - valid)
    if bad:
        raise ValueError(f"invalid predicted labels: {bad}")

    fieldnames, rows = load_sample_submission(sample_submission_path, ids)
    pred_map = dict(zip(ids, preds))
    missing = 0
    for row in rows:
        pred = pred_map.get(row["id"])
        if pred is None:
            missing += 1
        else:
            row["action"] = pred
    if missing:
        print(f"Warning: missing predictions for {missing} sample_submission ids")
    save_submission(output_path, fieldnames, rows)
    print(f"Saved {output_path} rows={len(rows)}")


if __name__ == "__main__":
    main()
