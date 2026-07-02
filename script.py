import csv
import json
import os
import pickle
import re

import torch


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

TOKEN_RE = re.compile(r"[A-Za-z0-9_./:+-]+|[가-힣]+")


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
    if parts and "." in parts[-1]:
        tokens.append(f"ext:{parts[-1].rsplit('.', 1)[-1].lower()}")
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
        ("intent_glob", r"\b(glob|pattern|files matching|all .* files|\*\.[a-z0-9]+)\b"),
        ("intent_list", r"\b(list|ls|directory|folder|tree|what files)\b|목록"),
        ("intent_edit", r"\b(change|fix|update|modify|edit|rename|refactor|patch|touch)\b|수정|고쳐|바꿔|패치"),
        ("intent_write", r"\b(create|new file|write a|add a file|scaffold)\b|새 파일|작성"),
        ("intent_plan", r"\b(plan|steps|break down|outline|roadmap|approach)\b|계획|단계|쪼개"),
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


def tokenize(text):
    return TOKEN_RE.findall(safe_text(text).lower())


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


def serialize_transformer_sample_current(sample):
    prompt = safe_text(sample.get("current_prompt", ""))
    history = sample.get("history") or []
    sm = sample.get("session_meta") or {}
    ws = sm.get("workspace") or {}
    action_names = []
    last_user = ""
    result_bits = []
    arg_bits = []
    for event in history:
        if event.get("role") == "user":
            last_user = safe_text(event.get("content", ""))
        elif event.get("role") == "assistant_action":
            name = safe_text(event.get("name"))
            action_names.append(name)
            result = safe_text(event.get("result_summary"))
            if result:
                result_bits.append(f"{name}:{result[:120]}")
            args = event.get("args") or {}
            if isinstance(args, dict):
                for key, value in list(args.items())[:4]:
                    arg_bits.append(f"{name}.{safe_text(key)}={safe_text(value)[:80]}")

    open_files = ws.get("open_files") or []
    language_mix = ws.get("language_mix") or {}
    if isinstance(language_mix, dict):
        langs = " ".join(f"{safe_text(k)}={float(v):.2f}" for k, v in list(language_mix.items())[:5])
    else:
        langs = ""

    parts = [
        f"current: {prompt}",
        f"meta: tier={safe_text(sm.get('user_tier'))} lang={safe_text(sm.get('language_pref'))} turn={safe_text(sm.get('turn_index'))} budget={safe_text(sm.get('budget_tokens_remaining'))} elapsed={safe_text(sm.get('elapsed_session_sec'))}",
        f"workspace: dirty={safe_text(ws.get('git_dirty'))} ci={safe_text(ws.get('last_ci_status'))} loc={safe_text(ws.get('loc'))} langs={langs} open={' | '.join(safe_text(x) for x in open_files[:6])}",
        f"actions: {' > '.join(action_names[-8:]) if action_names else 'none'}",
    ]
    if last_user:
        parts.append(f"last_user: {last_user}")
    if arg_bits:
        parts.append(f"args: {' | '.join(arg_bits[-10:])}")
    if result_bits:
        parts.append(f"results: {' | '.join(result_bits[-8:])}")
    return "\n".join(parts)


def history_user_action_pairs(history):
    pairs = []
    for idx, event in enumerate(history):
        if event.get("role") != "user":
            continue
        next_event = history[idx + 1] if idx + 1 < len(history) else {}
        if next_event.get("role") == "assistant_action":
            pairs.append((safe_text(event.get("content")), next_event))
    return pairs


def summarize_action_event(event):
    name = safe_text(event.get("name"))
    bits = [f"action={name}"]
    args = event.get("args") or {}
    if isinstance(args, dict):
        for key, value in list(args.items())[:4]:
            bits.append(f"{name}.{safe_text(key)}={safe_text(value)[:80]}")
    result = safe_text(event.get("result_summary"))
    if result:
        bits.append(f"result={result[:120]}")
    return " ".join(bits)


def compact_event_tokens(sample):
    prompt = safe_text(sample.get("current_prompt", ""))
    history = sample.get("history") or []
    sm = sample.get("session_meta") or {}
    ws = sm.get("workspace") or {}
    tokens = []

    tokens.extend(prompt_intent_tokens(prompt))
    tokens.extend(f"prompt_{token}" for token in tokenize(prompt)[:40])
    action_names = []
    for event in history:
        if event.get("role") != "assistant_action":
            continue
        name = safe_text(event.get("name"))
        if not name:
            continue
        action_names.append(name)
        tokens.append(f"hist_action_{name}")
        args = event.get("args") or {}
        if isinstance(args, dict):
            for key, value in list(args.items())[:4]:
                key_text = safe_text(key)
                value_text = safe_text(value)
                tokens.append(f"arg_{name}_{key_text}")
                for path_token in path_tokens(value_text)[:8]:
                    tokens.append(f"arg_{name}_{path_token}")
        result = safe_text(event.get("result_summary"))
        for result_token in tokenize(result)[:16]:
            tokens.append(f"result_{name}_{result_token}")

    for name in action_names[-10:]:
        tokens.append(f"recent_action_{name}")
    for a, b in zip(action_names[-6:], action_names[-5:]):
        tokens.append(f"action_bigram_{a}>{b}")

    open_files = ws.get("open_files") or []
    for path in open_files[:8]:
        tokens.extend(path_tokens(path))
    language_mix = ws.get("language_mix") or {}
    if isinstance(language_mix, dict):
        for lang, ratio in sorted(language_mix.items(), key=lambda kv: (-float(kv[1]), kv[0]))[:4]:
            tokens.append(f"code_lang_{safe_text(lang)}")
            try:
                tokens.append(f"code_lang_{safe_text(lang)}_{int(round(float(ratio) * 10))}")
            except (TypeError, ValueError):
                pass

    tokens.append(f"tier_{safe_text(sm.get('user_tier', 'missing'))}")
    tokens.append(f"lang_{safe_text(sm.get('language_pref', 'missing'))}")
    tokens.append(f"dirty_{safe_text(ws.get('git_dirty', 'missing')).lower()}")
    tokens.append(f"ci_{safe_text(ws.get('last_ci_status', 'missing'))}")
    tokens.append(bin_numeric("turn", sm.get("turn_index"), [("01", 1), ("02", 2), ("04", 4), ("08", 8), ("12", 12)]))
    tokens.append(bin_numeric("budget", sm.get("budget_tokens_remaining"), [("tiny", 6_000), ("low", 15_000), ("mid", 60_000), ("high", 130_000)]))
    tokens.append(bin_numeric("elapsed", sm.get("elapsed_session_sec"), [("fresh", 120), ("short", 600), ("mid", 1800), ("long", 3600)]))
    tokens.append(bin_numeric("loc", ws.get("loc"), [("tiny", 1_000), ("small", 5_000), ("mid", 20_000), ("large", 80_000)]))
    return " ".join(tokens[:700])


def serialize_transformer_sample_recent_pairs(sample, pair_count=3):
    prompt = safe_text(sample.get("current_prompt", ""))
    history = sample.get("history") or []
    pairs = history_user_action_pairs(history)[-pair_count:]
    parts = [f"current: {prompt}"]
    for idx, (user_text, action_event) in enumerate(pairs, 1):
        parts.append(f"pair_{idx}_user: {user_text}")
        parts.append(f"pair_{idx}_assistant: {summarize_action_event(action_event)}")
    sm = sample.get("session_meta") or {}
    ws = sm.get("workspace") or {}
    open_files = ws.get("open_files") or []
    parts.append(
        f"workspace: dirty={safe_text(ws.get('git_dirty'))} ci={safe_text(ws.get('last_ci_status'))} "
        f"open={' | '.join(safe_text(x) for x in open_files[:6])}"
    )
    return "\n".join(parts)


def serialize_transformer_sample(sample, serializer_name="current_v1"):
    if serializer_name in ("current", "current_v1"):
        return serialize_transformer_sample_current(sample)
    if serializer_name == "recent_pairs_v1":
        return serialize_transformer_sample_recent_pairs(sample, pair_count=3)
    if serializer_name == "compact_events_v1":
        return compact_event_tokens(sample)
    if serializer_name == "hybrid_v1":
        return "\n".join([
            f"current: {safe_text(sample.get('current_prompt', ''))}",
            f"events: {compact_event_tokens(sample)}",
        ])
    raise ValueError(f"unknown serializer_name: {serializer_name}")


def rule_text_has(pattern, text):
    return bool(re.search(pattern, text))


def rule_path_like_tokens(text):
    text = safe_text(text).replace("\\", "/").lower()
    tokens = []
    if not text:
        return tokens
    if "/" in text or re.search(r"\.[a-z0-9]{1,6}\b", text):
        tokens.append("arg_has_path")
    for ext in ("py", "js", "ts", "tsx", "json", "md", "yml", "yaml", "toml", "sh", "txt", "csv"):
        if re.search(rf"\.{ext}\b", text):
            tokens.append(f"arg_ext:{ext}")
    if "*" in text:
        tokens.append("arg_has_glob")
    return tokens


def rule_base_feature_flags(sample):
    prompt = safe_text(sample.get("current_prompt", ""))
    prompt_l = prompt.lower()
    flags = set()
    for token in prompt_intent_tokens(prompt):
        flags.add(f"prompt_{token}")
    if "?" in prompt:
        flags.add("prompt_has_question")
    if rule_text_has(r"\b(open|show|read|inspect|look at|cat|view)\b|열어|보여|읽어", prompt_l):
        flags.add("prompt_read_words")
    if rule_text_has(r"\b(grep|search|find|lookup|occurrence|references?)\b|검색|찾아", prompt_l):
        flags.add("prompt_search_words")
    if rule_text_has(r"\b(list|ls|tree|directory|folder|files?)\b|목록", prompt_l):
        flags.add("prompt_list_words")
    if rule_text_has(r"\b(glob|pattern|\*\.[a-z0-9]+|\*)\b", prompt_l):
        flags.add("prompt_glob_words")
    if rule_text_has(r"\b(web|internet|browser|online|latest|docs?|documentation)\b|웹|인터넷", prompt_l):
        flags.add("prompt_web_words")
    if rule_text_has(r"\b(lint|typecheck|type-check|mypy|pyright|tsc|ruff|eslint)\b", prompt_l):
        flags.add("prompt_lint_words")
    if rule_text_has(r"\b(test|tests|pytest|jest|vitest|spec)\b", prompt_l):
        flags.add("prompt_test_words")
    if rule_text_has(r"\b(run|execute|shell|terminal|build|install|npm|pip|docker)\b", prompt_l):
        flags.add("prompt_run_words")
    for token in rule_path_like_tokens(prompt_l):
        flags.add(f"prompt_{token}")

    history = sample.get("history") or []
    action_names = []
    result_tokens = []
    arg_tokens = []
    for event in history:
        if event.get("role") != "assistant_action":
            continue
        name = safe_text(event.get("name"))
        if not name:
            continue
        action_names.append(name)
        args = event.get("args") or {}
        if isinstance(args, dict):
            for value in list(args.values())[:6]:
                arg_tokens.extend(rule_path_like_tokens(value))
        result = safe_text(event.get("result_summary", "")).lower()
        if result:
            if rule_text_has(r"\b(found|match|matches|occurrences?)\b", result):
                result_tokens.append("result_found")
            if rule_text_has(r"\b(no matches|not found|empty|0 matches)\b", result):
                result_tokens.append("result_none")
            if rule_text_has(r"\b(error|failed|traceback|exception)\b", result):
                result_tokens.append("result_failed")
            if rule_text_has(r"\b(pass|passed|success|ok)\b", result):
                result_tokens.append("result_passed")
            if rule_text_has(r"\b(exit code|return code)\b", result):
                result_tokens.append("result_exit_code")

    flags.add(f"hist_len:{min(len(history), 12)}")
    if action_names:
        flags.add(f"last_action:{action_names[-1]}")
        for name in action_names[-4:]:
            flags.add(f"recent_action:{name}")
        if len(action_names) >= 2:
            flags.add(f"last_pair:{action_names[-2]}>{action_names[-1]}")
    for token in set(arg_tokens):
        flags.add(token)
    for token in set(result_tokens):
        flags.add(token)

    sm = sample.get("session_meta") or {}
    ws = sm.get("workspace") or {}
    flags.add(bin_numeric("turn", sm.get("turn_index"), [("01", 1), ("02", 2), ("04", 4), ("08", 8), ("12", 12)]).replace("_", ":", 1))
    flags.add(f"dirty:{safe_text(ws.get('git_dirty', 'missing')).lower()}")
    flags.add(f"ci:{safe_text(ws.get('last_ci_status', 'missing')).lower()}")
    language_mix = ws.get("language_mix") or {}
    if isinstance(language_mix, dict) and language_mix:
        top_lang = sorted(language_mix.items(), key=lambda kv: (-float(kv[1]), kv[0]))[0][0]
        flags.add(f"top_lang:{safe_text(top_lang).lower()}")
    open_files = ws.get("open_files") or []
    if open_files:
        flags.add("has_open_files")
        for path in open_files[:6]:
            for token in rule_path_like_tokens(path):
                flags.add(f"open_{token}")
    return flags


def rule_feature_flags(sample, base_scores_row, class_names=None):
    class_names = class_names or ALL_CLASSES
    flags = rule_base_feature_flags(sample)
    top_values, top_indices = torch.topk(base_scores_row.detach().cpu(), k=2)
    top = class_names[int(top_indices[0])]
    second = class_names[int(top_indices[1])]
    margin = float(top_values[0] - top_values[1])
    logit_flags = {
        f"base_top:{top}",
        f"base_second:{second}",
        f"base_pair:{top}>{second}",
    }
    for threshold in (0.25, 0.50, 1.00):
        if margin <= threshold:
            logit_flags.add(f"margin_le:{threshold:.2f}")
    composite_sources = [
        flag for flag in flags
        if flag.startswith(("prompt_", "last_action:", "recent_action:", "last_pair:", "arg_", "open_arg_", "top_lang:", "result_"))
    ]
    composite_flags = set()
    for flag in composite_sources:
        composite_flags.add(f"{flag}|top:{top}")
        composite_flags.add(f"{flag}|pair:{top}>{second}")
    return flags | logit_flags | composite_flags


def apply_rule_boosts_to_logits(logits, samples, rules, class_names):
    if not rules:
        return logits
    class_to_idx = {label: idx for idx, label in enumerate(class_names)}
    base_logits = logits.detach().cpu()
    boosted = logits.clone()
    for row_idx, sample in enumerate(samples):
        flags = rule_feature_flags(sample, base_logits[row_idx], class_names)
        for rule in rules:
            if rule.get("feature") not in flags:
                continue
            target_id = class_to_idx.get(rule.get("target"))
            if target_id is not None:
                boosted[row_idx, target_id] += float(rule.get("boost", 0.0))
    return boosted


def load_sparse_ensemble(model_dir, class_names):
    model_path = os.path.join(model_dir, "sparse_svc.pkl")
    meta_path = os.path.join(model_dir, "sparse_meta.json")
    if not os.path.exists(model_path):
        return None
    if not os.path.exists(meta_path):
        raise FileNotFoundError("Found sparse_svc.pkl but missing sparse_meta.json")
    with open(model_path, "rb") as f:
        payload = pickle.load(f)
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    sparse_classes = meta.get("classes", class_names)
    if list(sparse_classes) != list(class_names):
        raise ValueError("sparse ensemble class order does not match transformer class order")
    return {
        "vectorizer": payload["vectorizer"],
        "model": payload["model"],
        "meta": meta,
    }


def sparse_ensemble_scores(sparse_ensemble, samples):
    texts = [serialize_transformer_sample_current(sample) for sample in samples]
    features = sparse_ensemble["vectorizer"].transform(texts)
    scores = sparse_ensemble["model"].decision_function(features)
    if getattr(scores, "ndim", 1) != 2:
        raise ValueError("expected multiclass sparse SVC decision scores")
    model_classes = [int(value) for value in getattr(sparse_ensemble["model"], "classes_", range(len(ALL_CLASSES)))]
    expected = list(range(len(ALL_CLASSES)))
    if model_classes != expected:
        order = [model_classes.index(class_id) for class_id in expected]
        scores = scores[:, order]
    return torch.tensor(scores, dtype=torch.float32)


def run_hf_inference(model_dir, data_dir, output_path, device):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    hf_dir = os.path.join(model_dir, "hf_model")
    with open(os.path.join(model_dir, "hf_meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    tokenizer = AutoTokenizer.from_pretrained(hf_dir, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(hf_dir, local_files_only=True).to(device)
    if device.type == "cuda":
        model.half()
    else:
        model.float()
    model.eval()

    test_path = os.path.join(data_dir, "test.jsonl")
    sample_submission_path = os.path.join(data_dir, "sample_submission.csv")
    samples = load_jsonl(test_path)
    ids = [safe_text(sample.get("id", "")) for sample in samples]
    serializer_name = meta.get("serializer_name", "current_v1")
    texts = [serialize_transformer_sample(sample, serializer_name) for sample in samples]
    class_bias = torch.tensor(meta.get("class_bias", [0.0] * len(meta["classes"])), dtype=torch.float32, device=device)
    rule_boosts = meta.get("rule_boosts") or []
    sparse_ensemble = load_sparse_ensemble(model_dir, meta["classes"])
    sparse_scores = None
    sparse_weight = 0.0
    sparse_bias = None
    if sparse_ensemble is not None:
        sparse_meta = sparse_ensemble["meta"]
        sparse_scores = sparse_ensemble_scores(sparse_ensemble, samples)
        sparse_weight = float(sparse_meta.get("sparse_weight", 0.0))
        sparse_bias = torch.tensor(
            sparse_meta.get("class_bias", [0.0] * len(meta["classes"])),
            dtype=torch.float32,
            device=device,
        )
        print(f"Loaded sparse SVC ensemble weight={sparse_weight}")

    preds = []
    batch_size = int(meta.get("batch_size", 32))
    max_length = int(meta.get("max_length", 192))
    with torch.inference_mode():
        for start in range(0, len(texts), batch_size):
            batch_texts = texts[start:start + batch_size]
            encoded = tokenizer(
                batch_texts,
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            logits = model(**encoded).logits.float() + class_bias
            logits = apply_rule_boosts_to_logits(logits, samples[start:start + batch_size], rule_boosts, meta["classes"])
            if sparse_scores is not None:
                sparse_batch = sparse_scores[start:start + len(batch_texts)].to(device)
                logits = logits + sparse_weight * sparse_batch + sparse_bias
            pred_ids = torch.argmax(logits, dim=1).detach().cpu().tolist()
            preds.extend(meta["classes"][i] for i in pred_ids)

    fieldnames, rows = load_sample_submission(sample_submission_path, ids)
    pred_map = dict(zip(ids, preds))
    for row in rows:
        if row["id"] in pred_map:
            row["action"] = pred_map[row["id"]]
    save_submission(output_path, fieldnames, rows)
    print(f"Saved {output_path} rows={len(rows)}")


def main():
    data_dir = first_existing(["./data", "./open/data"])
    model_dir = "./model"
    output_path = "./output/submission.csv"
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    if not os.path.isdir(model_dir):
        raise FileNotFoundError("Required ./model directory is missing. Place the trained model artifacts at ./model before inference.")

    hf_config_path = os.path.join(model_dir, "hf_model", "config.json")
    if not os.path.exists(hf_config_path):
        raise FileNotFoundError("Expected HuggingFace model artifacts at ./model/hf_model/config.json.")

    print(f"Load transformer model from {model_dir}; device={device}")
    run_hf_inference(model_dir, data_dir, output_path, device)


if __name__ == "__main__":
    main()
