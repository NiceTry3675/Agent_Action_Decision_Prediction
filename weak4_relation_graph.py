"""Deterministic Weak4 relation features for the live-pair CPU probe.

The module deliberately performs no label or logit access.  It compiles the
observable ``current_prompt``/``history``/``session_meta`` state into numeric
features and a JSON-safe audit graph.  Raw identities are retained only in the
audit graph; the feature dictionary contains typed relations and state bins so
the cheap probe cannot memorize a path or symbol spelling.

Candidate absence is fail-closed.  A missing or count-only candidate list is
encoded as ``unknown`` rather than as a negative membership observation.
"""

from __future__ import annotations

import math
import posixpath
import re
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from script import (
    ALL_CLASSES,
    route_arg_kind,
    route_candidate_pool,
    route_count_bucket_from_result,
    route_fields,
    route_open_relation,
    safe_text,
)
from weak4_pair_residual import LIVE_WEAK4_PAIRS


SCHEMA_VERSION = 1
SCHEMA_NAME = "weak4-relation-graph-v1"

WEAK4_TOOLS = tuple(ALL_CLASSES[:4])
SOURCE_VALUES = ("unknown", "sim", "au")
TURN_BIN_VALUES = ("unknown", "start", "early", "mid", "late", "long")
AGE_BIN_VALUES = ("missing", "current", "recent", "stale", "old")
COUNT_BIN_VALUES = ("unknown", "zero", "one", "few", "many")
RESULT_VALUES = ("na", "error", "zero", "one", "few", "many", "ok")
COMPLETENESS_VALUES = ("unknown", "none", "count_only", "partial", "exact")
PROVENANCE_VALUES = ("none", "args", "result", "args+result", "unknown")
PATH_RELATIONS = (
    "unknown",
    "exact",
    "normalized",
    "casefold",
    "basename",
    "stem",
    "ancestor",
    "descendant",
    "extension",
    "none",
)
SYMBOL_RELATIONS = (
    "unknown",
    "exact",
    "normalized",
    "casefold",
    "qualified_tail",
    "none",
)
TRISTATE_VALUES = ("unknown", "no", "yes")
OP_VALUES = ("unknown", "single_file_read", "content_search", "dir_children", "file_set")
TARGET_KIND_VALUES = (
    "none",
    "exact_path",
    "basename",
    "glob",
    "symbol",
    "dir",
    "pronoun",
    "other",
)
MULTIPLICITY_VALUES = ("unknown", "single", "occurrences", "children", "all_files")
SCOPE_VALUES = ("unknown", "open", "file", "dir", "directory", "candidate", "repo", "workspace")
ARG_KIND_VALUES = ("none", "cmd", "glob", "dir", "exact_path", "symbol", "other")
CANDIDATE_POOL_VALUES = (
    "none",
    "file_set",
    "content_hits",
    "dir_entries",
    "single_file",
    "diagnostic",
    "unknown",
)
LAST_TOOL_VALUES = ("none", *WEAK4_TOOLS)
CANDIDATE_COUNT_BINS = ("unknown", "none", "one", "few", "many")

_PATH_RE = re.compile(
    r"(?:[A-Za-z]:)?(?:[A-Za-z0-9_@+.,-]+[/\\])+[A-Za-z0-9_@+.,*?\[\]-]+"
    r"|(?:\*\*/)?\*\.[A-Za-z0-9]{1,12}"
    r"|\b[A-Za-z0-9_@+-]+\.[A-Za-z0-9]{1,12}\b"
)
_QUOTED_RE = re.compile(r"[`'\"]([^`'\"\n]{1,160})[`'\"]")
_SYMBOL_RE = re.compile(
    r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*\(\)"
    r"|\b(?:[A-Za-z_][A-Za-z0-9_]*::)+[A-Za-z_][A-Za-z0-9_]*\b"
    r"|\b[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+\b"
    r"|\b[A-Za-z]+[A-Z][A-Za-z0-9]*\b"
)
_COUNT_PATTERNS = (
    re.compile(
        r"\b(?:found\s+)?(\d{1,6})\s+"
        r"(?:matches?|occurrences?|files?\s+matched|matched\s+files?|entries|results?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(\d{1,6})\s+files?\b", re.IGNORECASE),
)
_ZERO_RE = re.compile(
    r"\b(?:no\s+(?:matches?|results?|relevant\s+results?)|not\s+found|"
    r"0\s+(?:matches?|occurrences?|files?|entries|results?))\b",
    re.IGNORECASE,
)
_SPECIAL_PATH_NAMES = frozenset(
    {
        "dockerfile",
        "makefile",
        "procfile",
        "vagrantfile",
        "gemfile",
        "rakefile",
        "jenkinsfile",
    }
)
_PATH_ARG_KEYS = frozenset(
    {"path", "paths", "file", "files", "filename", "target", "cwd", "scope", "directory", "dir"}
)
_QUERY_ARG_KEYS = frozenset(
    {"pattern", "query", "symbol", "target_symbol", "search", "needle", "literal"}
)


def scenario_group(sample_id: str) -> str:
    """Return a leakage-safe session/scenario group.

    Simulated sessions retain their full session id.  AU sibling variants share
    the ``sess_au_<primary>`` prefix and are grouped together.
    """

    session = safe_text(sample_id).split("-step_", 1)[0]
    parts = session.split("_")
    if len(parts) >= 4 and parts[:2] == ["sess", "au"]:
        return "_".join(parts[:3])
    return session


def _strip_wrapping(value: Any) -> str:
    text = safe_text(value).strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"`":
        text = text[1:-1].strip()
    return text


def normalize_path(value: Any) -> str:
    """Lexically normalize a path without touching the filesystem."""

    text = _strip_wrapping(value).replace("\\", "/").strip()
    if not text:
        return ""
    if text.lower().startswith("file://"):
        text = text[7:]
    # Result summaries often place punctuation immediately after a path.
    text = text.rstrip(".,;:)]}")
    text = re.sub(r"/{2,}", "/", text)
    normalized = posixpath.normpath(text)
    if normalized in ("", "."):
        return ""
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _path_parts(value: Any) -> tuple[str, str, str, str]:
    normalized = normalize_path(value)
    if not normalized:
        return "", "", "", ""
    basename = normalized.rstrip("/").rsplit("/", 1)[-1]
    if "." in basename and not basename.startswith("."):
        stem, extension = basename.rsplit(".", 1)
        extension = extension.casefold()
    else:
        stem, extension = basename, ""
    return normalized, basename, stem, extension


def _is_ancestor(left: str, right: str) -> bool:
    left_parts = tuple(part for part in left.strip("/").split("/") if part)
    right_parts = tuple(part for part in right.strip("/").split("/") if part)
    return bool(left_parts) and len(left_parts) < len(right_parts) and right_parts[: len(left_parts)] == left_parts


def path_relation(left: Any, right: Any) -> str:
    """Return the strongest typed relation from ``left`` to ``right``."""

    raw_left = _strip_wrapping(left)
    raw_right = _strip_wrapping(right)
    if not raw_left or not raw_right:
        return "unknown"
    if raw_left == raw_right:
        return "exact"
    left_norm, left_base, left_stem, left_ext = _path_parts(raw_left)
    right_norm, right_base, right_stem, right_ext = _path_parts(raw_right)
    if not left_norm or not right_norm:
        return "unknown"
    if left_norm == right_norm:
        return "normalized"
    left_fold, right_fold = left_norm.casefold(), right_norm.casefold()
    if left_fold == right_fold:
        return "casefold"
    if left_base and left_base.casefold() == right_base.casefold():
        return "basename"
    if left_stem and left_stem.casefold() == right_stem.casefold():
        return "stem"
    if _is_ancestor(left_fold, right_fold):
        return "ancestor"
    if _is_ancestor(right_fold, left_fold):
        return "descendant"
    if left_ext and left_ext == right_ext:
        return "extension"
    return "none"


def _glob_regex(pattern: str) -> re.Pattern[str]:
    output = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    output.append("(?:.*/)?")
                    index += 1
                else:
                    output.append(".*")
                continue
            output.append("[^/]*")
        elif char == "?":
            output.append("[^/]")
        elif char == "[":
            end = pattern.find("]", index + 1)
            if end < 0:
                output.append(r"\[")
            else:
                body = pattern[index + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                elif body.startswith("^"):
                    body = "\\" + body
                output.append("[" + body.replace("\\", r"\\") + "]")
                index = end
        else:
            output.append(re.escape(char))
        index += 1
    output.append("$")
    return re.compile("".join(output))


def glob_matches(pattern: Any, path: Any) -> bool:
    """Deterministically match a normalized path with ``**`` support."""

    pattern_text = normalize_path(pattern)
    path_text = normalize_path(path)
    if not pattern_text or not path_text:
        return False
    target = path_text if "/" in pattern_text else path_text.rsplit("/", 1)[-1]
    try:
        return bool(_glob_regex(pattern_text).fullmatch(target))
    except re.error:
        return False


def _normalize_symbol(value: Any) -> str:
    text = _strip_wrapping(value).strip()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"\(\)$", "", text)
    text = text.replace("::", ".")
    return text.strip(".&")


def symbol_relation(left: Any, right: Any) -> str:
    raw_left = _strip_wrapping(left)
    raw_right = _strip_wrapping(right)
    if not raw_left or not raw_right:
        return "unknown"
    if raw_left == raw_right:
        return "exact"
    left_norm = _normalize_symbol(raw_left)
    right_norm = _normalize_symbol(raw_right)
    if not left_norm or not right_norm:
        return "unknown"
    if left_norm == right_norm:
        return "normalized"
    if left_norm.casefold() == right_norm.casefold():
        return "casefold"
    if left_norm.rsplit(".", 1)[-1].casefold() == right_norm.rsplit(".", 1)[-1].casefold():
        return "qualified_tail"
    return "none"


def _source(sample_id: str) -> str:
    if safe_text(sample_id).startswith("sess_sim_"):
        return "sim"
    if safe_text(sample_id).startswith("sess_au_"):
        return "au"
    return "unknown"


def _turn_bin(value: Any) -> str:
    try:
        turn = int(float(value))
    except (TypeError, ValueError):
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


def _count_bin(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value <= 0:
        return "zero"
    if value == 1:
        return "one"
    if value <= 5:
        return "few"
    return "many"


def _age_bin(value: int | None) -> str:
    if value is None:
        return "missing"
    if value <= 0:
        return "current"
    if value <= 2:
        return "recent"
    if value <= 5:
        return "stale"
    return "old"


def _candidate_count_bin(count: int, completeness: Iterable[str], any_tool: bool) -> str:
    if count > 5:
        return "many"
    if count > 1:
        return "few"
    if count == 1:
        return "one"
    values = set(completeness)
    if "count_only" in values or "partial" in values or "unknown" in values or not any_tool:
        return "unknown"
    return "none"


def _parse_result_count(result: Any, tool: str) -> int | None:
    text = safe_text(result)
    if _ZERO_RE.search(text):
        return 0
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    if tool == "read_file" and route_count_bucket_from_result(text) == "ok":
        return 1
    return None


def _flatten_values(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        output: list[str] = []
        for nested in value.values():
            output.extend(_flatten_values(nested))
        return output
    if isinstance(value, (list, tuple, set)):
        output = []
        for nested in value:
            output.extend(_flatten_values(nested))
        return output
    text = safe_text(value).strip()
    return [text] if text else []


def _looks_path_like(value: str, key_hint: str = "") -> bool:
    text = _strip_wrapping(value)
    lower = text.casefold()
    if key_hint.casefold() in _PATH_ARG_KEYS:
        return bool(text) and not re.fullmatch(r"\d+(?:\.\d+)?", text)
    return bool(
        "/" in text
        or "\\" in text
        or any(char in text for char in "*?[")
        or re.search(r"\.[A-Za-z0-9]{1,12}(?:$|[:#]\d+$)", text)
        or lower in _SPECIAL_PATH_NAMES
    )


def _unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = safe_text(value).strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _extract_paths_from_text(value: Any) -> list[str]:
    text = safe_text(value)
    values = [match.group(0) for match in _PATH_RE.finditer(text)]
    for quoted in _QUOTED_RE.findall(text):
        if _looks_path_like(quoted):
            values.append(quoted)
    return _unique(normalize_path(item) for item in values if normalize_path(item))


def _extract_arg_paths(args: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for raw_key, raw_value in args.items():
        key = safe_text(raw_key).casefold()
        for item in _flatten_values(raw_value):
            if _looks_path_like(item, key):
                normalized = normalize_path(item)
                if normalized:
                    values.append(normalized)
    return _unique(values)


def _extract_globs(values: Iterable[str]) -> list[str]:
    return _unique(normalize_path(value) for value in values if any(char in safe_text(value) for char in "*?[") and normalize_path(value))


def _extract_symbols(value: Any) -> list[str]:
    text = safe_text(value)
    candidates = [match.group(0) for match in _SYMBOL_RE.finditer(text)]
    for quoted in _QUOTED_RE.findall(text):
        normalized = _normalize_symbol(quoted)
        if normalized and not _looks_path_like(quoted) and re.fullmatch(
            r"(?:[A-Za-z_][A-Za-z0-9_]*[.:]{1,2})*[A-Za-z_][A-Za-z0-9_]*(?:\(\))?",
            quoted.strip(),
        ):
            candidates.append(quoted)
    return _unique(_normalize_symbol(value) for value in candidates if _normalize_symbol(value))


def _event_records(sample: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    last_user = ""
    for raw_event in sample.get("history") or []:
        if not isinstance(raw_event, Mapping):
            continue
        role = raw_event.get("role")
        if role == "user":
            last_user = safe_text(raw_event.get("content"))
            continue
        if role != "assistant_action":
            continue
        args = raw_event.get("args") if isinstance(raw_event.get("args"), Mapping) else {}
        records.append(
            {
                "index": len(records),
                "name": safe_text(raw_event.get("name")),
                "args": dict(args),
                "result": safe_text(raw_event.get("result_summary")),
                "user": last_user,
            }
        )
    return records


def _event_query_values(event: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    args = event.get("args") or {}
    if isinstance(args, Mapping):
        for raw_key, raw_value in args.items():
            if safe_text(raw_key).casefold() in _QUERY_ARG_KEYS:
                values.extend(_flatten_values(raw_value))
    return _unique(values)


def _candidate_state(event: Mapping[str, Any]) -> dict[str, Any]:
    tool = safe_text(event.get("name"))
    args = event.get("args") if isinstance(event.get("args"), Mapping) else {}
    result = safe_text(event.get("result"))
    arg_paths = _extract_arg_paths(args)
    result_paths = _extract_paths_from_text(result)
    queries = _event_query_values(event)
    patterns = _extract_globs(queries + arg_paths)

    # Do not mistake an echoed glob/query/scope for a returned candidate.
    exclusions = {
        normalize_path(value)
        for value in (*queries, *arg_paths)
        if normalize_path(value)
    }

    def is_echoed_query_fragment(value: str) -> bool:
        normalized = normalize_path(value)
        if not normalized:
            return True
        for excluded in exclusions:
            if normalized == excluded:
                return True
            # The path regex may split one echoed glob into fragments. Only
            # use containment for two glob-like values; an ordinary scope such
            # as src must not erase a real returned child such as src/foo.py.
            if (
                any(char in normalized for char in "*?[")
                and any(char in excluded for char in "*?[")
                and (normalized in excluded or excluded in normalized)
            ):
                return True
        return False

    result_candidates = [
        value for value in result_paths if not is_echoed_query_fragment(value)
    ]
    count = _parse_result_count(result, tool)
    bucket = route_count_bucket_from_result(result)

    if tool == "read_file":
        candidates = arg_paths
        completeness = "exact" if candidates else "unknown"
        provenance = "args" if candidates else "unknown"
    else:
        candidates = result_candidates
        if count == 0:
            completeness = "none"
        elif candidates and count is not None and len(candidates) >= count:
            completeness = "exact"
        elif candidates:
            completeness = "partial"
        elif count is not None:
            completeness = "count_only"
        else:
            completeness = "unknown"
        provenance = "result" if candidates else "none" if completeness in {"none", "count_only"} else "unknown"

    return {
        "tool": tool,
        "arg_paths": arg_paths,
        "result_paths": result_paths,
        "candidate_paths": _unique(candidates),
        "queries": queries,
        "symbols": _unique(symbol for value in queries for symbol in _extract_symbols(value)),
        "patterns": patterns,
        "result_bucket": bucket if bucket in RESULT_VALUES else "na",
        "count": count,
        "count_bin": _count_bin(count),
        "completeness": completeness,
        "provenance": provenance,
    }


def _tool_states(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    total_actions = len(records)
    for tool in WEAK4_TOOLS:
        events = [record for record in records if record.get("name") == tool]
        if not events:
            states[tool] = {
                "present": False,
                "age": None,
                "age_bin": "missing",
                "result_bucket": "na",
                "count": None,
                "count_bin": "unknown",
                "completeness": "unknown",
                "provenance": "unknown",
                "arg_paths": [],
                "candidate_paths": [],
                "queries": [],
                "symbols": [],
                "patterns": [],
                "event_count": 0,
                "history_states": [],
            }
            continue
        latest = events[-1]
        latest_state = _candidate_state(latest)
        age = total_actions - 1 - int(latest["index"])
        history_states = []
        for event in events:
            event_state = _candidate_state(event)
            event_age = total_actions - 1 - int(event["index"])
            history_states.append(
                {
                    **event_state,
                    "age": event_age,
                    "age_bin": _age_bin(event_age),
                    "event_index": int(event["index"]),
                }
            )
        states[tool] = {
            **latest_state,
            "present": True,
            "age": age,
            "age_bin": _age_bin(age),
            "event_count": len(events),
            "history_states": history_states,
            "latest_event_index": int(latest["index"]),
            "latest_user": safe_text(latest.get("user")),
        }
    return states


def _validate_pair(pair_id: int, main_pred: int) -> tuple[tuple[int, int], int]:
    pair_id = int(pair_id)
    main_pred = int(main_pred)
    if not 0 <= pair_id < len(LIVE_WEAK4_PAIRS):
        raise ValueError(f"invalid pair_id: {pair_id}")
    pair = LIVE_WEAK4_PAIRS[pair_id]
    if main_pred not in pair:
        raise ValueError(f"main_pred {main_pred} is outside live pair {pair}")
    alt_pred = pair[1] if main_pred == pair[0] else pair[0]
    return pair, alt_pred


def _one_hot(features: dict[str, float], prefix: str, value: str, choices: Sequence[str]) -> None:
    selected = value if value in choices else "unknown"
    for choice in choices:
        features[f"{prefix}={choice}"] = float(selected == choice)


def _tristate(features: dict[str, float], prefix: str, value: str) -> None:
    _one_hot(features, prefix, value, TRISTATE_VALUES)


def _best_relation(left_values: Sequence[str], right_values: Sequence[str]) -> str:
    if not left_values or not right_values:
        return "unknown"
    priority = {
        "exact": 0,
        "normalized": 1,
        "casefold": 2,
        "basename": 3,
        "stem": 4,
        "ancestor": 5,
        "descendant": 5,
        "extension": 6,
        "none": 7,
        "unknown": 8,
    }
    relations = [path_relation(left, right) for left in left_values for right in right_values]
    return min(relations, key=lambda value: priority[value])


def _best_symbol_relation(left_values: Sequence[str], right_values: Sequence[str]) -> str:
    if not left_values or not right_values:
        return "unknown"
    priority = {
        "exact": 0,
        "normalized": 1,
        "casefold": 2,
        "qualified_tail": 3,
        "none": 4,
        "unknown": 5,
    }
    relations = [symbol_relation(left, right) for left in left_values for right in right_values]
    return min(relations, key=lambda value: priority[value])


def _membership(relation: str, completeness: str, *, has_target: bool) -> str:
    if not has_target:
        return "unknown"
    if relation in {"exact", "normalized", "casefold", "basename"}:
        return "yes"
    if relation in {"stem", "ancestor", "descendant", "extension"}:
        return "unknown"
    if completeness == "none":
        return "no"
    if relation == "none" and completeness in {"exact", "none"}:
        return "no"
    return "unknown"


def _glob_relation(patterns: Sequence[str], paths: Sequence[str], completeness: str) -> str:
    if not patterns or (not paths and completeness not in {"exact", "none"}):
        return "unknown"
    if any(glob_matches(pattern, path) for pattern in patterns for path in paths):
        return "yes"
    if completeness in {"exact", "none"}:
        return "no"
    return "unknown"


def _current_entities(sample: Mapping[str, Any]) -> dict[str, Any]:
    prompt = safe_text(sample.get("current_prompt"))
    family, operation, target_kind, target_value, multiplicity, scope, cue = route_fields(sample)
    prompt_paths = _extract_paths_from_text(prompt)
    if target_kind in {"exact_path", "basename", "glob", "dir"} and target_value not in {"", "na"}:
        normalized = normalize_path(target_value)
        if normalized:
            prompt_paths = _unique([normalized, *prompt_paths])
    prompt_globs = _extract_globs(prompt_paths + [target_value])
    prompt_symbols = _extract_symbols(prompt)
    if target_kind == "symbol" and target_value not in {"", "na"}:
        prompt_symbols = _unique([_normalize_symbol(target_value), *prompt_symbols])
    return {
        "prompt": prompt,
        "family": family,
        "operation": operation if operation in OP_VALUES else "unknown",
        "target_kind": target_kind if target_kind in TARGET_KIND_VALUES else "other",
        "target_value": target_value,
        "multiplicity": multiplicity if multiplicity in MULTIPLICITY_VALUES else "unknown",
        "scope": scope if scope in SCOPE_VALUES else "unknown",
        "cue": cue,
        "paths": prompt_paths,
        "globs": prompt_globs,
        "symbols": prompt_symbols,
    }


def _common_context(sample: Mapping[str, Any], pair_id: int, main_pred: int) -> dict[str, Any]:
    pair, alt_pred = _validate_pair(pair_id, main_pred)
    sample_id = safe_text(sample.get("id"))
    session_meta = sample.get("session_meta") if isinstance(sample.get("session_meta"), Mapping) else {}
    workspace = session_meta.get("workspace") if isinstance(session_meta.get("workspace"), Mapping) else {}
    open_known = "open_files" in workspace
    raw_open_files = workspace.get("open_files")
    if not isinstance(raw_open_files, (list, tuple, set)):
        raw_open_files = []
    open_files = _unique(
        normalized
        for value in raw_open_files
        if (normalized := normalize_path(value))
    )
    records = _event_records(sample)
    tools = _tool_states(records)
    current = _current_entities(sample)
    source = _source(sample_id)
    turn_bin = _turn_bin(session_meta.get("turn_index"))
    all_candidates = _unique(
        value
        for tool in WEAK4_TOOLS
        for event_state in tools[tool]["history_states"]
        for value in event_state["candidate_paths"]
    )
    completeness = [tools[tool]["completeness"] for tool in WEAK4_TOOLS]
    any_tool = any(tools[tool]["present"] for tool in WEAK4_TOOLS)
    present_completeness = [
        tools[tool]["completeness"] for tool in WEAK4_TOOLS if tools[tool]["present"]
    ]
    candidate_count_bin = _candidate_count_bin(
        len(all_candidates), present_completeness, any_tool
    )
    return {
        "sample_id": sample_id,
        "scenario_group": scenario_group(sample_id),
        "source": source,
        "turn_bin": turn_bin,
        "pair_id": int(pair_id),
        "pair": pair,
        "main_pred": int(main_pred),
        "alt_pred": alt_pred,
        "records": records,
        "tools": tools,
        "current": current,
        "open_files": open_files,
        "open_files_known": open_known,
        "all_candidates": all_candidates,
        "candidate_count_bin": candidate_count_bin,
        "has_current_path": bool(current["paths"]),
        "has_history_candidate": bool(all_candidates),
        "completeness": completeness,
        "completeness_histogram": dict(sorted(Counter(completeness).items())),
    }


def _base_pair_features(context: Mapping[str, Any], prefix: str) -> dict[str, float]:
    features: dict[str, float] = {}
    for pair_index in range(len(LIVE_WEAK4_PAIRS)):
        features[f"{prefix}.pair_id={pair_index}"] = float(int(context["pair_id"]) == pair_index)
    for class_id, tool in enumerate(WEAK4_TOOLS):
        features[f"{prefix}.main_tool={tool}"] = float(int(context["main_pred"]) == class_id)
        features[f"{prefix}.alt_tool={tool}"] = float(int(context["alt_pred"]) == class_id)
    _one_hot(features, f"{prefix}.source", str(context["source"]), SOURCE_VALUES)
    _one_hot(features, f"{prefix}.turn_bin", str(context["turn_bin"]), TURN_BIN_VALUES)
    return features


def extract_coarse_features(
    sample: Mapping[str, Any], pair_id: int, main_pred: int
) -> tuple[dict[str, float], dict[str, Any]]:
    """Return the current-v10-style coarse control with a fixed schema."""

    context = _common_context(sample, pair_id, main_pred)
    features = _base_pair_features(context, "coarse")
    current = context["current"]
    records = context["records"]
    last = records[-1] if records else None
    previous = records[-2] if len(records) > 1 else None
    last_name = safe_text(last.get("name")) if last else "none"
    previous_name = safe_text(previous.get("name")) if previous else "none"
    result_bucket = route_count_bucket_from_result(last.get("result") if last else "")
    candidate_pool = route_candidate_pool(
        {
            "name": last_name,
            "args": last.get("args") if last else {},
            "result_summary": last.get("result") if last else "",
        }
        if last
        else None
    )
    arg_kind = route_arg_kind(
        {"name": last_name, "args": last.get("args") if last else {}} if last else None
    )
    session_meta = sample.get("session_meta")
    if not isinstance(session_meta, Mapping):
        session_meta = {}
    workspace = session_meta.get("workspace")
    if not isinstance(workspace, Mapping):
        workspace = {}
    n_open, target_open, dir_overlap, ext_overlap = route_open_relation(
        current["prompt"], workspace, current["target_kind"], current["target_value"]
    )

    _one_hot(features, "coarse.operation", current["operation"], OP_VALUES)
    _one_hot(features, "coarse.target_kind", current["target_kind"], TARGET_KIND_VALUES)
    _one_hot(features, "coarse.multiplicity", current["multiplicity"], MULTIPLICITY_VALUES)
    _one_hot(features, "coarse.scope", current["scope"], SCOPE_VALUES)
    _one_hot(features, "coarse.last_action", last_name if last_name in LAST_TOOL_VALUES else "none", LAST_TOOL_VALUES)
    _one_hot(features, "coarse.previous_action", previous_name if previous_name in LAST_TOOL_VALUES else "none", LAST_TOOL_VALUES)
    _one_hot(features, "coarse.last_result", result_bucket if result_bucket in RESULT_VALUES else "na", RESULT_VALUES)
    _one_hot(features, "coarse.last_arg_kind", arg_kind if arg_kind in ARG_KIND_VALUES else "other", ARG_KIND_VALUES)
    _one_hot(
        features,
        "coarse.candidate_pool",
        candidate_pool if candidate_pool in CANDIDATE_POOL_VALUES else "unknown",
        CANDIDATE_POOL_VALUES,
    )
    _one_hot(features, "coarse.target_open", target_open, ("unknown", "no", "same", "overlap"))
    _one_hot(features, "coarse.dir_overlap", dir_overlap, ("no", "yes"))
    _one_hot(features, "coarse.ext_overlap", ext_overlap, ("no", "yes"))
    features["coarse.open_count"] = float(n_open)
    features["coarse.open_count_log1p"] = math.log1p(float(n_open))
    features["coarse.action_count"] = float(len(records))
    features["coarse.action_count_log1p"] = math.log1p(float(len(records)))
    features["coarse.has_current_path"] = float(context["has_current_path"])
    features["coarse.has_history_candidate"] = float(context["has_history_candidate"])

    metadata = {
        "schema": "weak4-coarse-control-v1",
        "schema_version": SCHEMA_VERSION,
        "sample_id": context["sample_id"],
        "scenario_group": context["scenario_group"],
        "source": context["source"],
        "turn_bin": context["turn_bin"],
        "pair_id": context["pair_id"],
        "pair": [WEAK4_TOOLS[index] for index in context["pair"]],
        "main_pred": context["main_pred"],
        "main_tool": WEAK4_TOOLS[context["main_pred"]],
        "alt_pred": context["alt_pred"],
        "alt_tool": WEAK4_TOOLS[context["alt_pred"]],
        "candidate_count_bin": context["candidate_count_bin"],
        "has_current_path": context["has_current_path"],
        "has_history_candidate": context["has_history_candidate"],
        "completeness": context["completeness"],
        "completeness_histogram": context["completeness_histogram"],
        "operation": current["operation"],
        "target_kind": current["target_kind"],
        "last_action": last_name,
        "previous_action": previous_name,
        "last_result": result_bucket,
        "candidate_pool": candidate_pool,
        "feature_names": sorted(features),
    }
    return features, metadata


def _tool_relation_state(
    current: Mapping[str, Any], state: Mapping[str, Any], open_files: Sequence[str]
) -> dict[str, Any]:
    target_vs_arg = _best_relation(current["paths"], state["arg_paths"])
    target_vs_candidate = _best_relation(current["paths"], state["candidate_paths"])
    membership = _membership(
        target_vs_candidate,
        str(state["completeness"]),
        has_target=bool(current["paths"]),
    )
    current_glob_vs_candidate = _glob_relation(
        current["globs"], state["candidate_paths"], str(state["completeness"])
    )
    tool_glob_vs_target = _glob_relation(
        state["patterns"], current["paths"], "exact" if current["paths"] else "unknown"
    )
    target_symbol_vs_query = _best_symbol_relation(current["symbols"], state["symbols"])
    candidate_vs_open = _best_relation(state["candidate_paths"], list(open_files))
    historical = []
    # Latest state is represented above. Historical features use only earlier
    # executions so age-0 evidence is not duplicated under two names.
    for event_state in (state.get("history_states") or [])[:-1]:
        event_relation = _best_relation(
            current["paths"], event_state["candidate_paths"]
        )
        historical.append(
            {
                "relation": event_relation,
                "membership": _membership(
                    event_relation,
                    str(event_state["completeness"]),
                    has_target=bool(current["paths"]),
                ),
                "age": int(event_state["age"]),
                "candidate_paths": list(event_state["candidate_paths"]),
            }
        )
    historical_candidates = _unique(
        value for item in historical for value in item["candidate_paths"]
    )
    historical_relation = _best_relation(current["paths"], historical_candidates)
    yes_ages = [
        item["age"] for item in historical if item["membership"] == "yes"
    ]
    historical_memberships = [item["membership"] for item in historical]
    if yes_ages:
        historical_membership = "yes"
        historical_match_age = min(yes_ages)
    elif historical_memberships and all(
        value == "no" for value in historical_memberships
    ):
        historical_membership = "no"
        historical_match_age = None
    else:
        historical_membership = "unknown"
        historical_match_age = None
    return {
        "target_vs_arg": target_vs_arg,
        "target_vs_candidate": target_vs_candidate,
        "membership": membership,
        "current_glob_vs_candidate": current_glob_vs_candidate,
        "tool_glob_vs_target": tool_glob_vs_target,
        "target_symbol_vs_query": target_symbol_vs_query,
        "candidate_vs_open": candidate_vs_open,
        "historical_target_vs_candidate": historical_relation,
        "historical_membership": historical_membership,
        "historical_match_age": historical_match_age,
        "historical_candidate_count": len(historical_candidates),
    }


def _emit_tool_features(
    features: dict[str, float], prefix: str, state: Mapping[str, Any], relation: Mapping[str, Any]
) -> None:
    features[f"{prefix}.present"] = float(bool(state["present"]))
    features[f"{prefix}.age_known"] = float(state["age"] is not None)
    features[f"{prefix}.age_log1p"] = math.log1p(float(state["age"] or 0))
    features[f"{prefix}.event_count_log1p"] = math.log1p(float(state["event_count"]))
    features[f"{prefix}.count_known"] = float(state["count"] is not None)
    features[f"{prefix}.count_log1p"] = math.log1p(float(state["count"] or 0))
    features[f"{prefix}.candidate_count_log1p"] = math.log1p(len(state["candidate_paths"]))
    features[f"{prefix}.arg_path_count_log1p"] = math.log1p(len(state["arg_paths"]))
    _one_hot(features, f"{prefix}.age", str(state["age_bin"]), AGE_BIN_VALUES)
    _one_hot(features, f"{prefix}.result", str(state["result_bucket"]), RESULT_VALUES)
    _one_hot(features, f"{prefix}.count", str(state["count_bin"]), COUNT_BIN_VALUES)
    _one_hot(features, f"{prefix}.completeness", str(state["completeness"]), COMPLETENESS_VALUES)
    _one_hot(features, f"{prefix}.provenance", str(state["provenance"]), PROVENANCE_VALUES)
    _one_hot(features, f"{prefix}.target_vs_arg", relation["target_vs_arg"], PATH_RELATIONS)
    _one_hot(features, f"{prefix}.target_vs_candidate", relation["target_vs_candidate"], PATH_RELATIONS)
    _tristate(features, f"{prefix}.membership", relation["membership"])
    _tristate(features, f"{prefix}.current_glob_vs_candidate", relation["current_glob_vs_candidate"])
    _tristate(features, f"{prefix}.tool_glob_vs_target", relation["tool_glob_vs_target"])
    _one_hot(
        features,
        f"{prefix}.target_symbol_vs_query",
        relation["target_symbol_vs_query"],
        SYMBOL_RELATIONS,
    )
    _one_hot(features, f"{prefix}.candidate_vs_open", relation["candidate_vs_open"], PATH_RELATIONS)
    _one_hot(
        features,
        f"{prefix}.historical_target_vs_candidate",
        relation["historical_target_vs_candidate"],
        PATH_RELATIONS,
    )
    _tristate(
        features,
        f"{prefix}.historical_membership",
        relation["historical_membership"],
    )
    historical_age = relation["historical_match_age"]
    features[f"{prefix}.historical_match_age_known"] = float(
        historical_age is not None
    )
    features[f"{prefix}.historical_match_age_log1p"] = math.log1p(
        float(historical_age or 0)
    )
    features[f"{prefix}.historical_candidate_count_log1p"] = math.log1p(
        float(relation["historical_candidate_count"])
    )
    _one_hot(
        features,
        f"{prefix}.historical_match_age",
        _age_bin(historical_age),
        AGE_BIN_VALUES,
    )


def _transition_state(
    context: Mapping[str, Any], relations: Mapping[str, Mapping[str, Any]]
) -> dict[str, str]:
    records = context["records"]
    weak_records = [record for record in records if record.get("name") in WEAK4_TOOLS]
    last = weak_records[-1] if weak_records else None
    if not last:
        return {
            "last_weak_tool": "none",
            "candidate_selected": "unknown",
            "file_set_to_single_file": "unknown",
            "directory_to_child": "unknown",
            "search_to_inspect": "unknown",
            "retry_after_zero": "unknown",
            "broaden_after_zero": "unknown",
            "narrow_to_read": "unknown",
            "repeat_same_tool": "unknown",
            "tool_switch": "unknown",
        }

    last_tool = safe_text(last.get("name"))
    state = context["tools"][last_tool]
    relation = relations[last_tool]
    current_op = context["current"]["operation"]
    current_tool = {
        "single_file_read": "read_file",
        "content_search": "grep_search",
        "dir_children": "list_directory",
        "file_set": "glob_pattern",
    }.get(current_op)
    membership = relation["membership"]
    candidate_selected = membership
    if candidate_selected == "unknown" and relation["target_vs_arg"] in {
        "exact",
        "normalized",
        "casefold",
        "basename",
    }:
        candidate_selected = "yes"
    last_result = str(state["result_bucket"])

    def known_flag(condition: bool, known: bool = True) -> str:
        return "yes" if condition else "no" if known else "unknown"

    repeat_known = current_tool is not None
    inspect_known = current_op != "unknown"

    def selection_transition(expected_tool: str) -> str:
        if not inspect_known:
            return "unknown"
        if last_tool != expected_tool or current_op != "single_file_read":
            return "no"
        return candidate_selected

    return {
        "last_weak_tool": last_tool,
        "candidate_selected": candidate_selected,
        "file_set_to_single_file": selection_transition("glob_pattern"),
        "directory_to_child": selection_transition("list_directory"),
        "search_to_inspect": known_flag(
            last_tool == "grep_search" and current_op == "single_file_read",
            inspect_known,
        ),
        "retry_after_zero": known_flag(
            last_result == "zero" and current_tool == last_tool,
            repeat_known and last_result != "na",
        ),
        "broaden_after_zero": known_flag(
            last_result == "zero" and current_tool in {"glob_pattern", "list_directory"} and current_tool != last_tool,
            repeat_known and last_result != "na",
        ),
        "narrow_to_read": known_flag(
            last_tool in {"glob_pattern", "grep_search", "list_directory"}
            and current_op == "single_file_read",
            inspect_known,
        ),
        "repeat_same_tool": known_flag(current_tool == last_tool, repeat_known),
        "tool_switch": known_flag(current_tool != last_tool, repeat_known),
    }


def extract_relation_features(
    sample: Mapping[str, Any], pair_id: int, main_pred: int
) -> tuple[dict[str, float], dict[str, Any]]:
    """Compile full-history pair-aware relation features and audit metadata."""

    context = _common_context(sample, pair_id, main_pred)
    features = _base_pair_features(context, "rel")
    current = context["current"]
    tools = context["tools"]

    _one_hot(features, "rel.current.operation", current["operation"], OP_VALUES)
    _one_hot(features, "rel.current.target_kind", current["target_kind"], TARGET_KIND_VALUES)
    _one_hot(features, "rel.current.multiplicity", current["multiplicity"], MULTIPLICITY_VALUES)
    _one_hot(features, "rel.current.scope", current["scope"], SCOPE_VALUES)
    _one_hot(
        features,
        "rel.candidate_count",
        context["candidate_count_bin"],
        CANDIDATE_COUNT_BINS,
    )
    features["rel.has_current_path"] = float(context["has_current_path"])
    features["rel.has_current_glob"] = float(bool(current["globs"]))
    features["rel.has_current_symbol"] = float(bool(current["symbols"]))
    features["rel.has_history_candidate"] = float(context["has_history_candidate"])
    features["rel.current_path_count_log1p"] = math.log1p(len(current["paths"]))
    features["rel.current_symbol_count_log1p"] = math.log1p(len(current["symbols"]))
    features["rel.history_candidate_count_log1p"] = math.log1p(len(context["all_candidates"]))

    relations: dict[str, dict[str, Any]] = {}
    for tool in WEAK4_TOOLS:
        relation = _tool_relation_state(current, tools[tool], context["open_files"])
        relations[tool] = relation

    main_tool = WEAK4_TOOLS[context["main_pred"]]
    alt_tool = WEAK4_TOOLS[context["alt_pred"]]
    _emit_tool_features(features, "rel.main", tools[main_tool], relations[main_tool])
    _emit_tool_features(features, "rel.alt", tools[alt_tool], relations[alt_tool])

    open_relation = _best_relation(current["paths"], context["open_files"])
    if not context["has_current_path"] or not context["open_files_known"]:
        target_open = "unknown"
    elif open_relation in {"exact", "normalized", "casefold", "basename"}:
        target_open = "yes"
    else:
        target_open = "no"
    _one_hot(features, "rel.current.target_vs_open", open_relation, PATH_RELATIONS)
    _tristate(features, "rel.current.target_open", target_open)
    features["rel.open_files_known"] = float(context["open_files_known"])
    features["rel.open_file_count_log1p"] = math.log1p(len(context["open_files"]))

    transition = _transition_state(context, relations)
    _one_hot(features, "rel.transition.last_weak_tool", transition["last_weak_tool"], LAST_TOOL_VALUES)
    for name in (
        "candidate_selected",
        "file_set_to_single_file",
        "directory_to_child",
        "search_to_inspect",
        "retry_after_zero",
        "broaden_after_zero",
        "narrow_to_read",
        "repeat_same_tool",
        "tool_switch",
    ):
        _tristate(features, f"rel.transition.{name}", transition[name])

    metadata = {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "sample_id": context["sample_id"],
        "scenario_group": context["scenario_group"],
        "source": context["source"],
        "turn_bin": context["turn_bin"],
        "pair_id": context["pair_id"],
        "pair": [WEAK4_TOOLS[index] for index in context["pair"]],
        "main_pred": context["main_pred"],
        "main_tool": main_tool,
        "alt_pred": context["alt_pred"],
        "alt_tool": alt_tool,
        "candidate_count_bin": context["candidate_count_bin"],
        "has_current_path": context["has_current_path"],
        "has_history_candidate": context["has_history_candidate"],
        "completeness": context["completeness"],
        "completeness_histogram": context["completeness_histogram"],
        "current": {
            "operation": current["operation"],
            "target_kind": current["target_kind"],
            "target_value": current["target_value"],
            "multiplicity": current["multiplicity"],
            "scope": current["scope"],
            "paths": list(current["paths"]),
            "globs": list(current["globs"]),
            "symbols": list(current["symbols"]),
        },
        "open_files_known": context["open_files_known"],
        "open_files": list(context["open_files"]),
        "tools": {
            tool: {
                key: value
                for key, value in tools[tool].items()
                if key not in {"latest_user"}
            }
            for tool in WEAK4_TOOLS
        },
        "relations": relations,
        "open_relation": open_relation,
        "target_open": target_open,
        "transition": transition,
        "feature_names": sorted(features),
    }
    return features, metadata


def vectorize_feature_dicts(
    rows: Sequence[Mapping[str, float]], feature_names: Sequence[str] | None = None
) -> tuple[np.ndarray, list[str]]:
    """Convert numeric feature dictionaries to a deterministic dense matrix."""

    materialized = list(rows)
    if feature_names is None:
        names = sorted({safe_text(key) for row in materialized for key in row})
    else:
        names = [safe_text(value) for value in feature_names]
        if any(not value for value in names) or len(names) != len(set(names)):
            raise ValueError("feature_names must be nonempty and unique")
    position = {name: index for index, name in enumerate(names)}
    matrix = np.zeros((len(materialized), len(names)), dtype=np.float32)
    for row_index, row in enumerate(materialized):
        for raw_name, raw_value in row.items():
            name = safe_text(raw_name)
            column = position.get(name)
            if column is None:
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"feature {name!r} is not numeric") from exc
            if not math.isfinite(value):
                raise ValueError(f"feature {name!r} is not finite")
            matrix[row_index, column] = value
    return matrix, names


__all__ = [
    "extract_coarse_features",
    "extract_relation_features",
    "glob_matches",
    "normalize_path",
    "path_relation",
    "scenario_group",
    "symbol_relation",
    "vectorize_feature_dicts",
]
