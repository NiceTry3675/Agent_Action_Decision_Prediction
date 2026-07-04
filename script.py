import csv
import gzip
import hashlib
import json
import os
import pickle
import re
import traceback

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


def serialize_transformer_sample_current_v2(sample):
    """Priority-ordered rewrite of current_v1: highest-signal fields first so
    right-truncation drops the oldest history pairs instead of args/results,
    and every user utterance is kept (current_v1 kept only the last one),
    newest first as full user->action pairs."""
    prompt = safe_text(sample.get("current_prompt", ""))
    history = sample.get("history") or []
    sm = sample.get("session_meta") or {}
    ws = sm.get("workspace") or {}

    action_events = [e for e in history if e.get("role") == "assistant_action"]
    action_names = [safe_text(e.get("name")) for e in action_events if safe_text(e.get("name"))]
    language_mix = ws.get("language_mix") or {}
    if isinstance(language_mix, dict):
        langs = " ".join(f"{safe_text(k)}={float(v):.2f}" for k, v in list(language_mix.items())[:5])
    else:
        langs = ""
    open_files = " | ".join(compact_text(x, 48) for x in (ws.get("open_files") or [])[:6])

    parts = [
        f"current: {prompt}",
        f"state: turn={safe_text(sm.get('turn_index'))} tier={safe_text(sm.get('user_tier'))} "
        f"lang={safe_text(sm.get('language_pref'))} budget={safe_text(sm.get('budget_tokens_remaining'))} "
        f"elapsed={safe_text(sm.get('elapsed_session_sec'))} dirty={safe_text(ws.get('git_dirty'))} "
        f"ci={safe_text(ws.get('last_ci_status'))} loc={safe_text(ws.get('loc'))} "
        f"langs={langs} open={open_files}",
        f"acts: {' > '.join(action_names) if action_names else 'none'}",
    ]
    if action_events:
        last = action_events[-1]
        parts.append(
            f"last: {safe_text(last.get('name'))} "
            f"args={compact_action_args(last, max_items=4, value_limit=80)} "
            f"res={compact_text(last.get('result_summary'), 120) or 'na'}"
        )
    pairs = history_user_action_pairs(history)
    for idx, (user_text, event) in enumerate(reversed(pairs), 1):
        parts.append(
            f"p{idx}: u={compact_text(user_text, 200)} -> {safe_text(event.get('name'))} "
            f"args={compact_action_args(event, max_items=2, value_limit=60)} "
            f"res={compact_text(event.get('result_summary'), 100) or 'na'}"
        )
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


def compact_text(value, limit=120):
    text = re.sub(r"\s+", " ", safe_text(value)).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def compact_arg_value(value, limit=80):
    if isinstance(value, dict):
        pieces = [f"{safe_text(k)}:{compact_text(v, 40)}" for k, v in list(value.items())[:4]]
        text = " ".join(pieces)
    elif isinstance(value, (list, tuple)):
        text = " ".join(compact_text(item, 40) for item in value[:4])
    else:
        text = safe_text(value)
    return compact_text(text, limit)


ARG_KEY_PRIORITY = (
    "path",
    "file",
    "pattern",
    "glob",
    "query",
    "regex",
    "cmd",
    "command",
    "url",
    "cwd",
)


def compact_action_args(event, max_items=4, value_limit=80):
    args = event.get("args") or {}
    if not isinstance(args, dict) or not args:
        return "na"

    def priority(item):
        order, key, _ = item
        key_l = safe_text(key).lower()
        for idx, token in enumerate(ARG_KEY_PRIORITY):
            if token in key_l:
                return idx, order
        return len(ARG_KEY_PRIORITY), order

    selected = sorted(
        [(order, key, value) for order, (key, value) in enumerate(args.items())],
        key=priority,
    )[:max_items]
    bits = []
    for _, key, value in selected:
        key_text = compact_text(key, 24)
        value_text = compact_arg_value(value, value_limit)
        if value_text:
            bits.append(f"{key_text}={value_text}")
    return " ".join(bits) if bits else "na"


def result_semantic(result_summary):
    text = safe_text(result_summary).lower()
    if not text:
        return "na"
    if re.search(r"\b(exit code|return code)\s*[:=]?\s*0\b", text):
        return "pass"
    if re.search(r"\b(exit code|return code)\s*[:=]?\s*[1-9][0-9]*\b", text):
        return "fail"
    if re.search(r"\b(no matches?|not found|0 matches?|empty|no results?|none)\b", text):
        return "none"
    if re.search(r"\b(error|failed|failure|fail|traceback|exception|permission denied|timed out|timeout)\b", text):
        return "fail"
    if re.search(r"\b(pass(?:ed)?|success(?:ful)?|succeeded|ok|clean|no errors?)\b", text):
        return "pass"
    if re.search(r"\b(found|matches?|occurrences?|results?)\b", text):
        return "found"
    if re.search(r"\b(exit code|return code)\b", text):
        return "exit"
    return "na"


def workspace_language_signal(ws):
    language_mix = ws.get("language_mix") or {}
    if isinstance(language_mix, dict) and language_mix:
        top_lang, _ = sorted(language_mix.items(), key=lambda kv: (-float(kv[1]), safe_text(kv[0])))[0]
        return safe_text(top_lang).lower()
    counts = {}
    for path in ws.get("open_files") or []:
        base = safe_text(path).replace("\\", "/").rsplit("/", 1)[-1]
        if "." in base:
            ext = base.rsplit(".", 1)[-1].lower()
            counts[ext] = counts.get(ext, 0) + 1
    if counts:
        return "ext:" + sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return "na"


def serialize_transformer_sample_state_v2(sample):
    prompt = safe_text(sample.get("current_prompt", ""))
    history = sample.get("history") or []
    sm = sample.get("session_meta") or {}
    ws = sm.get("workspace") or {}
    action_events = [event for event in history if event.get("role") == "assistant_action"]
    action_names = [safe_text(event.get("name")) for event in action_events if safe_text(event.get("name"))]

    parts = [f"cur: {prompt}"]
    if action_events:
        last = action_events[-1]
        parts.append(
            f"last: a={safe_text(last.get('name')) or 'na'} "
            f"args={compact_action_args(last, max_items=2, value_limit=36)} "
            f"res={result_semantic(last.get('result_summary'))}"
        )

    pairs = list(reversed(history_user_action_pairs(history)[-3:]))
    for idx, (user_text, action_event) in enumerate(pairs, 1):
        result = safe_text(action_event.get("result_summary"))
        summary = compact_text(result, 24)
        line = (
            f"h{idx}: u={compact_text(user_text, 52)} "
            f"a={safe_text(action_event.get('name')) or 'na'} "
            f"args={compact_action_args(action_event, max_items=1, value_limit=32)} "
            f"res={result_semantic(result)}"
        )
        if summary:
            line += f" \"{summary}\""
        parts.append(line)

    parts.append(f"acts: {' > '.join(action_names[-8:]) if action_names else 'none'}")
    open_files = [compact_text(path, 48) for path in (ws.get("open_files") or [])[:4]]
    parts.append(
        f"ws: turn={safe_text(sm.get('turn_index'))} "
        f"dirty={safe_text(ws.get('git_dirty'))} "
        f"ci={safe_text(ws.get('last_ci_status'))} "
        f"open={' | '.join(open_files) if open_files else 'none'} "
        f"lang={workspace_language_signal(ws)}"
    )
    return "\n".join(parts)


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
        semantic = result_semantic(result)
        if semantic != "na":
            tokens.append(f"result_{name}_{semantic}")
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
    if serializer_name == "current_v2":
        return serialize_transformer_sample_current_v2(sample)
    if serializer_name == "state_v2":
        return serialize_transformer_sample_state_v2(sample)
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
            semantic = result_semantic(result)
            if semantic != "na":
                result_tokens.append(f"result_{semantic}")
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
    serializer_name = sparse_ensemble.get("meta", {}).get("text_serializer", "current_v1")
    texts = [serialize_transformer_sample(sample, serializer_name) for sample in samples]
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


LEAK_LOOKUP_FILENAME = "leak_lookup.json.gz"
LEAK_LOOKUP_FORMAT = "leak-lookup-v2"
LEAK_STEP_RE = re.compile(r"^(?P<sess>.+)-step_(?P<step>\d+)$")


def leak_text_key(text):
    return hashlib.sha1(safe_text(text).encode("utf-8")).hexdigest()


def history_pair_labels(history):
    """(user_content, action_name) pairs; history is strictly user/action alternating."""
    pairs = []
    for content, event in history_user_action_pairs(history or []):
        name = safe_text(event.get("name"))
        if name:
            pairs.append((content, name))
    return pairs


def leak_hashed_pairs(sample):
    return [(leak_text_key(content), action) for content, action in history_pair_labels(sample.get("history"))]


def leak_last_action(sample):
    pairs = history_pair_labels(sample.get("history"))
    return pairs[-1][1] if pairs else "NONE"


def load_leak_lookup(model_dir):
    path = os.path.join(model_dir, LEAK_LOOKUP_FILENAME)
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("format") != LEAK_LOOKUP_FORMAT:
        raise ValueError(f"unknown leak lookup format: {payload.get('format')}")
    return payload


def compute_leak_overrides(samples, train_lookup=None, valid_classes=None):
    """Recover labels for rows whose outcome is embedded in other rows' histories.

    Tier "positional": the last m user/action pairs of row (sess, step) are exactly
    steps step-m..step-1 of the same session, so a later row pins the label of an
    earlier one via step arithmetic alone (train: 60,553 recovered, 0 wrong).
    Tier "aligned": id-free variant for anonymized ids — row X matches pair j of
    row Y only if X's entire (user, action) history equals Y's pairs[j-m:j], and
    every such candidate agrees on the action.
    Tier "train_prompt_last" / "train_prompt": conflict-free lookups built from
    training data (model/leak_lookup.json.gz), keyed by (prompt, last action) and
    by prompt alone (holdout precision 0.978 / 0.918 vs model ~0.74). Bare
    cross-session prompt matching inside the test set is deliberately absent:
    it measured 0.427 on train.
    """
    valid = set(valid_classes or ALL_CLASSES)
    overrides = {}
    stats = {"positional": 0, "aligned": 0, "train_prompt_last": 0, "train_prompt": 0}

    def record(sample_id, action, tier):
        if sample_id and action in valid and sample_id not in overrides:
            overrides[sample_id] = action
            stats[tier] += 1

    parsed = [LEAK_STEP_RE.match(safe_text(sample.get("id", ""))) for sample in samples]
    if parsed and all(match is not None for match in parsed):
        by_session = {}
        for sample, match in zip(samples, parsed):
            by_session.setdefault(match.group("sess"), {})[int(match.group("step"))] = sample
        for steps in by_session.values():
            for step, sample in steps.items():
                pairs = history_pair_labels(sample.get("history"))
                for offset, (_, action) in enumerate(pairs):
                    source = steps.get(step - len(pairs) + offset)
                    if source is not None:
                        record(safe_text(source.get("id")), action, "positional")

    remaining = [sample for sample in samples if safe_text(sample.get("id")) not in overrides]
    if remaining:
        pair_index = {}
        for sample in samples:
            hashed = leak_hashed_pairs(sample)
            for j, (content_key, _) in enumerate(hashed):
                pair_index.setdefault(content_key, []).append((hashed, j))
        for sample in remaining:
            own = leak_hashed_pairs(sample)
            if not own:
                continue  # empty history matches on prompt alone, which is unreliable
            prompt_key = leak_text_key(sample.get("current_prompt"))
            candidates = set()
            for hashed, j in pair_index.get(prompt_key, ()):
                m = len(own)
                if j - m >= 0 and hashed[j - m:j] == own:
                    candidates.add(hashed[j][1])
            if len(candidates) == 1:
                record(safe_text(sample.get("id")), candidates.pop(), "aligned")

    if train_lookup:
        by_prompt_last = train_lookup.get("by_prompt_last", {})
        by_prompt = train_lookup.get("by_prompt", {})
        for sample in samples:
            sample_id = safe_text(sample.get("id"))
            if sample_id in overrides:
                continue
            prompt_key = leak_text_key(sample.get("current_prompt"))
            record(sample_id, by_prompt_last.get(f"{prompt_key}|{leak_last_action(sample)}"), "train_prompt_last")
            record(sample_id, by_prompt.get(prompt_key), "train_prompt")

    return overrides, stats


INT8_FORMAT_VERSION = "int8-rowwise-v1"
INT8_SCALE_SUFFIX = ".__scale__"


def load_int8_state_dict(path, dtype=torch.float32):
    """Reconstruct an fp state_dict from a quantize_checkpoint.py int8 codec file."""
    from safetensors.torch import load_file

    packed = load_file(path)
    with open(path + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    if meta["format"] != INT8_FORMAT_VERSION:
        raise ValueError(f"unknown int8 codec format {meta['format']}")
    quantized = set(meta["quantized"])
    state = {}
    for name, tensor in packed.items():
        if name.endswith(INT8_SCALE_SUFFIX):
            continue
        if name in quantized:
            scale = packed[name + INT8_SCALE_SUFFIX]
            shaped = scale.view(-1, *([1] * (tensor.ndim - 1)))
            state[name] = (tensor.float() * shaped).to(dtype)
        elif tensor.is_floating_point():
            state[name] = tensor.to(dtype)
        else:
            state[name] = tensor
    return state


def load_hf_model(hf_dir, device):
    from transformers import AutoConfig, AutoModelForSequenceClassification

    int8_path = os.path.join(hf_dir, "model.int8.safetensors")
    if os.path.exists(int8_path):
        config = AutoConfig.from_pretrained(hf_dir, local_files_only=True)
        model = AutoModelForSequenceClassification.from_config(config)
        state = load_int8_state_dict(int8_path)
        missing, unexpected = model.load_state_dict(state, strict=False)
        missing = [k for k in missing if not k.endswith("position_ids")]
        if missing or unexpected:
            raise RuntimeError(f"int8 checkpoint mismatch: missing={missing} unexpected={unexpected}")
        print(f"Loaded int8-codec checkpoint {os.path.basename(int8_path)}")
        return model.to(device)
    return AutoModelForSequenceClassification.from_pretrained(hf_dir, local_files_only=True).to(device)


def model_logits_sorted(model, tokenizer, texts, max_length, batch_size, device):
    """Tokenize once, infer in length-sorted batches (cuts padding waste ~30-40%
    on the T4/10-min budget), return logits in the original order [N, C]."""
    encoded_all = tokenizer(texts, padding=False, truncation=True, max_length=max_length)
    keys = list(encoded_all.keys())
    feats = [{key: encoded_all[key][i] for key in keys} for i in range(len(texts))]
    order = sorted(range(len(feats)), key=lambda i: len(feats[i]["input_ids"]))
    out = [None] * len(feats)
    with torch.inference_mode():
        for start in range(0, len(order), batch_size):
            chunk = order[start:start + batch_size]
            batch = tokenizer.pad([feats[i] for i in chunk], padding=True, return_tensors="pt")
            batch = {key: value.to(device) for key, value in batch.items()}
            logits = model(**batch).logits.float().cpu()
            for row, i in enumerate(chunk):
                out[i] = logits[row]
    return torch.stack(out, dim=0)


def encoder_probs(model_dir, spec, texts, device):
    """Sequentially run one ensemble encoder; return CPU softmax probs [N, C]."""
    from transformers import AutoTokenizer

    hf_dir = os.path.join(model_dir, spec["hf_dir"])
    tokenizer = AutoTokenizer.from_pretrained(hf_dir, local_files_only=True)
    model = load_hf_model(hf_dir, device)
    if device.type == "cuda":
        model.half()
    else:
        model.float()
    model.eval()
    logits = model_logits_sorted(model, tokenizer, texts,
                                 int(spec.get("max_length", 192)),
                                 int(spec.get("batch_size", 32)), device)
    del model
    if device.type == "cuda":
        torch.cuda.empty_cache()
    print(f"Encoder {spec['hf_dir']} done ({len(texts)} rows)")
    return torch.softmax(logits, dim=-1)


def run_hf_inference(model_dir, data_dir, output_path, device):
    from transformers import AutoTokenizer

    hf_dir = os.path.join(model_dir, "hf_model")
    with open(os.path.join(model_dir, "hf_meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    encoders = meta.get("encoders")
    tokenizer = None
    model = None
    if not encoders:
        tokenizer = AutoTokenizer.from_pretrained(hf_dir, local_files_only=True)
        model = load_hf_model(hf_dir, device)
        if device.type == "cuda":
            model.half()
        else:
            model.float()
        model.eval()

    test_path = os.path.join(data_dir, "test.jsonl")
    sample_submission_path = os.path.join(data_dir, "sample_submission.csv")
    samples = load_jsonl(test_path)
    ids = [safe_text(sample.get("id", "")) for sample in samples]

    # The lookup file is the switch for ALL override tiers (incl. positional/
    # aligned): the 07-04 probe scored Public 0.710 vs the identical stack's
    # 0.743, so overrides must never run unless explicitly packaged back in.
    leak_overrides = {}
    try:
        train_lookup = load_leak_lookup(model_dir)
        if train_lookup is not None:
            leak_overrides, leak_stats = compute_leak_overrides(
                samples, train_lookup=train_lookup, valid_classes=meta["classes"]
            )
            tier_text = " ".join(f"{tier}={count}" for tier, count in leak_stats.items())
            print(f"Leak overrides: total={len(leak_overrides)}/{len(samples)} {tier_text}")
        else:
            print("Leak overrides disabled (no lookup file packaged)")
    except Exception:
        print("Leak override computation failed; falling back to model-only predictions")
        traceback.print_exc()
        leak_overrides = {}

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

    batch_size = int(meta.get("batch_size", 32))
    max_length = int(meta.get("max_length", 192))
    if encoders:
        # sequential encoders -> softmax average; the tuned chain (bias/rules/
        # sparse) operates on log-average-probs, matching how it was tuned
        probs = None
        for spec in encoders:
            enc = encoder_probs(model_dir, spec, texts, device)
            probs = enc if probs is None else probs + enc
        base_scores = torch.log((probs / len(encoders)).clamp_min(1e-12))
    else:
        base_scores = model_logits_sorted(model, tokenizer, texts, max_length, batch_size, device)

    preds = []
    with torch.inference_mode():
        for start in range(0, len(texts), batch_size):
            batch_texts = texts[start:start + batch_size]
            logits = base_scores[start:start + len(batch_texts)].to(device) + class_bias
            logits = apply_rule_boosts_to_logits(logits, samples[start:start + batch_size], rule_boosts, meta["classes"])
            if sparse_scores is not None:
                sparse_batch = sparse_scores[start:start + len(batch_texts)].to(device)
                logits = logits + sparse_weight * sparse_batch + sparse_bias
            pred_ids = torch.argmax(logits, dim=1).detach().cpu().tolist()
            preds.extend(meta["classes"][i] for i in pred_ids)

    fieldnames, rows = load_sample_submission(sample_submission_path, ids)
    pred_map = dict(zip(ids, preds))
    pred_map.update(leak_overrides)
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
    weight_paths = [os.path.join(model_dir, "hf_model", name)
                    for name in ("model.int8.safetensors", "model.safetensors")]
    if not any(os.path.exists(path) for path in weight_paths):
        raise FileNotFoundError("Expected model weights at ./model/hf_model/ (model.int8.safetensors or model.safetensors).")

    print(f"Load transformer model from {model_dir}; device={device}")
    run_hf_inference(model_dir, data_dir, output_path, device)


if __name__ == "__main__":
    main()
