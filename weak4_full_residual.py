"""Independent KEEP/SWITCH Weak4 residual specialist primitives.

This module is deliberately isolated from ``script.py``'s deployed Weak4 LoRA
path.  It contains only the typed, test-observable input contract, target
construction, a small pure-PyTorch model, and identity-preserving prediction
helpers.  Packaging/inference integration is intentionally out of scope until
the clean session-CV gate passes.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import Counter
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any, Iterable, Mapping, Sequence

import torch
import torch.nn as nn
import torch.nn.functional as F

from script import ALL_CLASSES, safe_text


WEAK4_CLASSES = tuple(ALL_CLASSES[:4])
WEAK4_IDS = tuple(range(4))
WEAK4_ID_SET = frozenset(WEAK4_IDS)
MAIN_FEATURE_DIM = 13
MAX_EVENTS = 4

BYTE_PAD = 0
BYTE_BOS = 1
BYTE_EOS = 2
BYTE_OFFSET = 3
BYTE_VOCAB_SIZE = 259

ACTION_NONE_ID = len(ALL_CLASSES)
ACTION_TO_ID = {name: idx for idx, name in enumerate(ALL_CLASSES)}

RESULT_BUCKETS = ("na", "error", "zero", "one", "few", "many", "ok")
RESULT_TO_ID = {name: idx for idx, name in enumerate(RESULT_BUCKETS)}
ARG_KEYS = (
    "path",
    "paths",
    "pattern",
    "scope",
    "target",
    "target_symbol",
    "cmd",
    "command",
    "cwd",
    "n_files",
    "query",
    "other",
)
ARG_KEY_TO_BIT = {name: idx for idx, name in enumerate(ARG_KEYS)}
ARG_KINDS = ("none", "path", "glob", "dir", "symbol", "cmd", "count", "text", "other")
ARG_KIND_TO_ID = {name: idx for idx, name in enumerate(ARG_KINDS)}

SOURCE_VALUES = ("unknown", "sim", "au")
TURN_BIN_VALUES = ("unknown", "start", "early", "mid", "late", "long")
CI_VALUES = ("unknown", "passed", "failed", "running", "canceled", "other")
STATE_CAT_SIZES = (len(SOURCE_VALUES), 17, len(TURN_BIN_VALUES), 8, 5, 3, len(CI_VALUES))

OP_VALUES = ("unknown", "single_file_read", "content_search", "dir_children", "file_set")
TARGET_KIND_VALUES = ("none", "exact_path", "glob", "symbol", "directory", "basename", "pronoun", "other")
SCOPE_VALUES = ("unknown", "file", "directory", "repo", "workspace")
MULTIPLICITY_VALUES = ("unknown", "single", "occurrences", "children", "all_files")
CANDIDATE_POOL_VALUES = ("none", "one", "few", "many")
OPEN_RELATION_VALUES = ("none", "exact_open", "basename_open", "history_only")
TASK_CAT_SIZES = (
    len(OP_VALUES),
    len(TARGET_KIND_VALUES),
    len(SCOPE_VALUES),
    len(MULTIPLICITY_VALUES),
    len(CANDIDATE_POOL_VALUES),
    len(OPEN_RELATION_VALUES),
)

PATH_RE = re.compile(
    r"(?:[A-Za-z0-9_.-]+[/\\])+[A-Za-z0-9_.-]+|"
    r"\*\.[A-Za-z0-9]{1,10}|"
    r"\b[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,10}\b"
)
COUNT_RE = re.compile(r"\b(\d+)\b")


class ResidualAction(IntEnum):
    KEEP_MAIN = 0
    SWITCH_READ = 1
    SWITCH_GREP = 2
    SWITCH_LIST = 3
    SWITCH_GLOB = 4


@dataclass(frozen=True)
class Weak4TypedInput:
    prompt_bytes: tuple[int, ...]
    event_text_bytes: tuple[tuple[int, ...], ...]
    event_action_ids: tuple[int, ...]
    event_result_ids: tuple[int, ...]
    event_arg_key_masks: tuple[int, ...]
    event_arg_kind_ids: tuple[int, ...]
    state_cat_ids: tuple[int, ...]
    task_cat_ids: tuple[int, ...]
    workspace_bytes: tuple[int, ...]
    main_features: tuple[float, ...]
    main_pred: int


@dataclass(frozen=True)
class Weak4RouteRow:
    sample_id: str
    session_id: str
    full_index: int
    typed: Weak4TypedInput
    y_true: int
    target_action: int
    change_target: int
    alt_target: int
    identity_weight: float


@dataclass(frozen=True)
class FeatureStats:
    mean: tuple[float, ...]
    std: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.mean) != MAIN_FEATURE_DIM or len(self.std) != MAIN_FEATURE_DIM:
            raise ValueError("main feature stats must have 13 entries")
        if any(not math.isfinite(value) for value in (*self.mean, *self.std)):
            raise ValueError("main feature stats must be finite")
        if any(value <= 0 for value in self.std):
            raise ValueError("main feature standard deviations must be positive")


@dataclass(frozen=True)
class Weak4ModelConfig:
    byte_embed_dim: int = 96
    byte_hidden_dim: int = 256
    byte_conv_layers: int = 6
    event_dim: int = 384
    event_layers: int = 4
    event_heads: int = 8
    fusion_dim: int = 512
    dropout: float = 0.15


def session_id_from_sample_id(sample_id: str) -> str:
    return str(sample_id).split("-step_")[0]


def source_from_sample_id(sample_id: str) -> str:
    sample_id = str(sample_id)
    if sample_id.startswith("sess_sim_"):
        return "sim"
    if sample_id.startswith("sess_au_"):
        return "au"
    return "unknown"


def encode_utf8_bytes(value: Any, max_content_bytes: int) -> tuple[int, ...]:
    """Encode text without a learned tokenizer; preserve both ends if capped."""
    if max_content_bytes < 0:
        raise ValueError("max_content_bytes must be nonnegative")
    raw = safe_text(value).encode("utf-8", errors="replace")
    if len(raw) > max_content_bytes:
        head = (max_content_bytes + 1) // 2
        raw = raw[:head] + raw[-(max_content_bytes - head):] if max_content_bytes else b""
    return (BYTE_BOS, *(byte + BYTE_OFFSET for byte in raw), BYTE_EOS)


def _first_int(text: str) -> int | None:
    match = COUNT_RE.search(text)
    return int(match.group(1)) if match else None


def result_bucket(result: Any) -> str:
    lower = safe_text(result).lower()
    if not lower:
        return "na"
    if re.search(r"error|failed|failure|traceback|exception|denied|timeout|conflict", lower):
        return "error"
    if re.search(r"no matches?|not found|\b0 (?:matches?|files?|entries|results?)\b|empty", lower):
        return "zero"
    count = _first_int(lower)
    if count is not None and re.search(r"matches?|occurrences?|files?|entries|results?", lower):
        if count == 0:
            return "zero"
        if count == 1:
            return "one"
        if count <= 5:
            return "few"
        return "many"
    if re.search(r"\b(?:pass(?:ed)?|success(?:ful)?|succeeded|ok|clean|read|patched)\b", lower):
        return "ok"
    return "na"


def arg_key_mask(args: Any) -> int:
    if not isinstance(args, Mapping):
        return 0
    mask = 0
    for raw_key in args:
        key = safe_text(raw_key).lower()
        bit = ARG_KEY_TO_BIT.get(key, ARG_KEY_TO_BIT["other"])
        mask |= 1 << bit
    return mask


def arg_kind(args: Any) -> str:
    if not isinstance(args, Mapping) or not args:
        return "none"
    items = [(safe_text(key).lower(), safe_text(value)) for key, value in args.items()]
    for key, value in items:
        lower = value.lower()
        if key in {"cmd", "command"}:
            return "cmd"
        if "*" in value or key == "pattern" and ("/" in value or "." in value):
            return "glob"
        if key in {"scope", "cwd", "directory", "dir"}:
            return "dir"
        if key in {"path", "paths", "file", "filename"} or "/" in value or PATH_RE.search(value):
            return "path"
        if key in {"symbol", "target_symbol"} or "_" in value or re.search(r"[a-z][A-Z]", value):
            return "symbol"
        if key.startswith("n_") or isinstance(args.get(key), (int, float)):
            return "count"
        if lower:
            return "text"
    return "other"


def _turn_exact_id(value: Any) -> int:
    try:
        turn = int(float(value))
    except (TypeError, ValueError):
        return 0
    if turn < 0:
        return 0
    return min(turn, 15) + 1 if turn < 15 else 16


def _turn_bin_id(value: Any) -> int:
    try:
        turn = int(float(value))
    except (TypeError, ValueError):
        return 0
    if turn <= 1:
        return 1
    if turn <= 2:
        return 2
    if turn <= 4:
        return 3
    if turn <= 6:
        return 4
    return 5


def _bool_id(value: Any) -> int:
    if value is None:
        return 0
    return 2 if bool(value) else 1


def _ci_id(value: Any) -> int:
    lower = safe_text(value).lower()
    if not lower:
        return 0
    aliases = {"pass": "passed", "success": "passed", "failure": "failed", "cancelled": "canceled"}
    lower = aliases.get(lower, lower)
    try:
        return CI_VALUES.index(lower)
    except ValueError:
        return CI_VALUES.index("other")


def _repeat_count(actions: Sequence[str]) -> int:
    if not actions:
        return 0
    last = actions[-1]
    count = 0
    for action in reversed(actions):
        if action != last:
            break
        count += 1
    return min(count, 4)


def _index(values: Sequence[str], value: str, fallback: str) -> int:
    try:
        return values.index(value)
    except ValueError:
        return values.index(fallback)


def _path_values(sample: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    ws = ((sample.get("session_meta") or {}).get("workspace") or {})
    values.extend(safe_text(value).replace("\\", "/") for value in ws.get("open_files") or [])
    for event in sample.get("history") or []:
        if event.get("role") != "assistant_action":
            continue
        args = event.get("args") or {}
        if not isinstance(args, Mapping):
            continue
        for key, value in args.items():
            if safe_text(key).lower() in {"path", "paths", "scope", "cwd", "directory", "dir", "file", "filename", "target"}:
                values.append(safe_text(value).replace("\\", "/"))
    return [value for value in values if value]


def derive_task_categories(sample: Mapping[str, Any]) -> tuple[int, ...]:
    prompt = safe_text(sample.get("current_prompt"))
    lower = prompt.lower()
    path_mentions = [value.replace("\\", "/") for value in PATH_RE.findall(prompt)]

    if re.search(r"\bglob\b|files? matching|matching files?|\*\.[a-z0-9]+|모든 .*파일", lower):
        op = "file_set"
    elif re.search(r"\bgrep\b|\bsearch\b|\bfind\b|references?|occurrences?|where is|찾아|검색", lower):
        op = "content_search"
    elif re.search(r"\b(?:list|ls|tree)\b|directory contents?|folder contents?|목록", lower):
        op = "dir_children"
    elif re.search(r"\b(?:read|open|show|inspect|current impl)\b|읽어|열어|보여|확인", lower):
        op = "single_file_read"
    else:
        op = "unknown"

    if "*" in prompt:
        target_kind = "glob"
    elif any("/" in value for value in path_mentions):
        target_kind = "exact_path"
    elif path_mentions:
        target_kind = "basename"
    elif re.search(r"\b(?:that file|this file|same file|it|there)\b|그\s*파일|그거|거기|방금", lower):
        target_kind = "pronoun"
    elif re.search(r"\b(?:directory|folder|repo|repository|workspace)\b|디렉토리|폴더", lower):
        target_kind = "directory"
    elif re.search(r"[`'\"]?[A-Za-z_][A-Za-z0-9_]*(?:\(\))?[`'\"]?", prompt):
        target_kind = "symbol" if op == "content_search" else "other"
    else:
        target_kind = "none"

    if re.search(r"\bworkspace\b", lower):
        scope = "workspace"
    elif re.search(r"\brepo(?:sitory)?\b|codebase|project", lower):
        scope = "repo"
    elif target_kind == "exact_path" or op == "single_file_read":
        scope = "file"
    elif op == "dir_children" or target_kind == "directory":
        scope = "directory"
    else:
        scope = "unknown"

    multiplicity = {
        "single_file_read": "single",
        "content_search": "occurrences",
        "dir_children": "children",
        "file_set": "all_files",
    }.get(op, "unknown")

    paths = list(dict.fromkeys(_path_values(sample)))
    candidate_pool = "none" if not paths else "one" if len(paths) == 1 else "few" if len(paths) <= 4 else "many"
    open_files = [
        safe_text(value).replace("\\", "/")
        for value in (((sample.get("session_meta") or {}).get("workspace") or {}).get("open_files") or [])
    ]
    open_relation = "none"
    if path_mentions and any(mention in open_files for mention in path_mentions):
        open_relation = "exact_open"
    elif path_mentions and any(
        mention.rsplit("/", 1)[-1] == opened.rsplit("/", 1)[-1]
        for mention in path_mentions for opened in open_files
    ):
        open_relation = "basename_open"
    elif target_kind == "pronoun" and paths:
        open_relation = "history_only"

    return (
        _index(OP_VALUES, op, "unknown"),
        _index(TARGET_KIND_VALUES, target_kind, "other"),
        _index(SCOPE_VALUES, scope, "unknown"),
        _index(MULTIPLICITY_VALUES, multiplicity, "unknown"),
        _index(CANDIDATE_POOL_VALUES, candidate_pool, "none"),
        _index(OPEN_RELATION_VALUES, open_relation, "none"),
    )


def main_numeric_features(parent_logits: Sequence[float] | torch.Tensor) -> tuple[float, ...]:
    logits = torch.as_tensor(parent_logits, dtype=torch.float32).flatten()
    if logits.numel() != len(ALL_CLASSES):
        raise ValueError(f"parent logits must contain {len(ALL_CLASSES)} values")
    if not bool(torch.isfinite(logits).all()):
        raise ValueError("parent logits must be finite")
    weak = logits[:4]
    centered = weak - weak.mean()
    weak_probs = torch.softmax(weak, dim=0)
    full_probs = torch.softmax(logits, dim=0)
    weak_mass = full_probs[:4].sum()
    top2 = torch.topk(weak, 2).values
    weak_gap = top2[0] - top2[1]
    weak_entropy = -(weak_probs * weak_probs.clamp_min(1e-12).log()).sum() / math.log(4)
    full_entropy = -(full_probs * full_probs.clamp_min(1e-12).log()).sum() / math.log(len(ALL_CLASSES))
    nonweak_gap = logits[4:].max() - weak.max()
    features = torch.cat([
        centered,
        weak_probs,
        torch.stack([weak_mass, weak_gap, weak_entropy, full_entropy, nonweak_gap]),
    ])
    if features.numel() != MAIN_FEATURE_DIM:
        raise AssertionError("internal main feature width mismatch")
    return tuple(float(value) for value in features)


def _stable_args_text(args: Any) -> str:
    if not isinstance(args, Mapping):
        return safe_text(args)
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=safe_text)


def serialize_weak4_full(
    sample: Mapping[str, Any],
    parent_logits: Sequence[float] | torch.Tensor,
    *,
    prompt_cap: int = 384,
    event_cap: int = 512,
    workspace_cap: int = 384,
) -> Weak4TypedInput:
    logits = torch.as_tensor(parent_logits, dtype=torch.float32).flatten()
    main_pred = int(logits.argmax())
    if main_pred not in WEAK4_ID_SET:
        raise ValueError("serialize_weak4_full requires a Weak4 parent argmax")

    last_user = ""
    events: list[tuple[str, Mapping[str, Any], str, str]] = []
    actions: list[str] = []
    for event in sample.get("history") or []:
        role = event.get("role")
        if role == "user":
            last_user = safe_text(event.get("content"))
        elif role == "assistant_action":
            name = safe_text(event.get("name"))
            args = event.get("args") if isinstance(event.get("args"), Mapping) else {}
            result = safe_text(event.get("result_summary"))
            events.append((name, args, result, last_user))
            actions.append(name)
    events = events[-MAX_EVENTS:]

    event_text: list[tuple[int, ...]] = []
    event_actions: list[int] = []
    event_results: list[int] = []
    event_masks: list[int] = []
    event_kinds: list[int] = []
    for name, args, result, user_text in events:
        rendered = f"user\0{user_text}\0args\0{_stable_args_text(args)}\0result\0{result}"
        event_text.append(encode_utf8_bytes(rendered, event_cap))
        event_actions.append(ACTION_TO_ID.get(name, ACTION_NONE_ID))
        event_results.append(RESULT_TO_ID[result_bucket(result)])
        event_masks.append(arg_key_mask(args))
        event_kinds.append(ARG_KIND_TO_ID[arg_kind(args)])

    sm = sample.get("session_meta") or {}
    ws = sm.get("workspace") or {}
    source = source_from_sample_id(safe_text(sample.get("id")))
    state = (
        SOURCE_VALUES.index(source),
        _turn_exact_id(sm.get("turn_index")),
        _turn_bin_id(sm.get("turn_index")),
        min(len(actions), 7),
        _repeat_count(actions),
        _bool_id(ws.get("git_dirty")),
        _ci_id(ws.get("last_ci_status")),
    )
    open_files = sorted({safe_text(value) for value in ws.get("open_files") or [] if safe_text(value)})
    workspace_text = "\0".join(open_files)
    return Weak4TypedInput(
        prompt_bytes=encode_utf8_bytes(sample.get("current_prompt"), prompt_cap),
        event_text_bytes=tuple(event_text),
        event_action_ids=tuple(event_actions),
        event_result_ids=tuple(event_results),
        event_arg_key_masks=tuple(event_masks),
        event_arg_kind_ids=tuple(event_kinds),
        state_cat_ids=state,
        task_cat_ids=derive_task_categories(sample),
        workspace_bytes=encode_utf8_bytes(workspace_text, workspace_cap),
        main_features=main_numeric_features(logits),
        main_pred=main_pred,
    )


def build_residual_target(
    parent_logits: Sequence[float] | torch.Tensor,
    y_true: int,
) -> tuple[int, int, int]:
    logits = torch.as_tensor(parent_logits, dtype=torch.float32).flatten()
    if logits.numel() != len(ALL_CLASSES) or not bool(torch.isfinite(logits).all()):
        raise ValueError("invalid parent logits")
    y_true = int(y_true)
    if y_true < 0 or y_true >= len(ALL_CLASSES):
        raise ValueError("y_true is outside the canonical class range")
    main_pred = int(logits.argmax())
    if main_pred not in WEAK4_ID_SET:
        raise ValueError("build_residual_target requires a Weak4 parent argmax")
    if y_true in WEAK4_ID_SET and y_true != main_pred:
        return y_true + 1, 1, y_true
    return int(ResidualAction.KEEP_MAIN), 0, -100


def route_row_to_payload(row: Weak4RouteRow) -> dict[str, Any]:
    return asdict(row)


def fit_feature_stats(rows: Sequence[Weak4RouteRow | Mapping[str, Any]]) -> FeatureStats:
    if not rows:
        raise ValueError("cannot fit feature stats on zero rows")
    matrix = torch.tensor([_typed(row)["main_features"] for row in rows], dtype=torch.float32)
    mean = matrix.mean(dim=0)
    std = matrix.std(dim=0, unbiased=False).clamp_min(1e-6)
    return FeatureStats(tuple(float(x) for x in mean), tuple(float(x) for x in std))


def _typed(row: Weak4RouteRow | Mapping[str, Any]) -> Mapping[str, Any]:
    typed = row.typed if isinstance(row, Weak4RouteRow) else row["typed"]
    return asdict(typed) if isinstance(typed, Weak4TypedInput) else typed


def _field(row: Weak4RouteRow | Mapping[str, Any], name: str) -> Any:
    return getattr(row, name) if isinstance(row, Weak4RouteRow) else row[name]


def _pad_sequences(sequences: Sequence[Sequence[int]], pad_value: int = 0) -> tuple[torch.Tensor, torch.Tensor]:
    width = max((len(sequence) for sequence in sequences), default=1)
    values = torch.full((len(sequences), width), pad_value, dtype=torch.long)
    mask = torch.zeros((len(sequences), width), dtype=torch.bool)
    for idx, sequence in enumerate(sequences):
        if sequence:
            values[idx, :len(sequence)] = torch.tensor(sequence, dtype=torch.long)
            mask[idx, :len(sequence)] = True
    return values, mask


def collate_weak4_full(
    rows: Sequence[Weak4RouteRow | Mapping[str, Any]],
    feature_stats: FeatureStats,
) -> dict[str, torch.Tensor]:
    if not rows:
        raise ValueError("cannot collate zero rows")
    typed = [_typed(row) for row in rows]
    prompt_ids, prompt_mask = _pad_sequences([value["prompt_bytes"] for value in typed])
    workspace_ids, workspace_mask = _pad_sequences([value["workspace_bytes"] for value in typed])

    flat_events: list[Sequence[int]] = []
    event_mask = torch.zeros((len(rows), MAX_EVENTS), dtype=torch.bool)
    event_action = torch.full((len(rows), MAX_EVENTS), ACTION_NONE_ID, dtype=torch.long)
    event_result = torch.zeros((len(rows), MAX_EVENTS), dtype=torch.long)
    event_kind = torch.zeros((len(rows), MAX_EVENTS), dtype=torch.long)
    event_distance = torch.zeros((len(rows), MAX_EVENTS), dtype=torch.long)
    event_arg_keys = torch.zeros((len(rows), MAX_EVENTS, len(ARG_KEYS)), dtype=torch.float32)
    event_sequences: list[list[Sequence[int]]] = [[()] * MAX_EVENTS for _ in rows]
    for row_idx, value in enumerate(typed):
        count = min(len(value["event_text_bytes"]), MAX_EVENTS)
        offset = MAX_EVENTS - count
        for local_idx in range(count):
            slot = offset + local_idx
            event_sequences[row_idx][slot] = value["event_text_bytes"][local_idx]
            event_mask[row_idx, slot] = True
            event_action[row_idx, slot] = int(value["event_action_ids"][local_idx])
            event_result[row_idx, slot] = int(value["event_result_ids"][local_idx])
            event_kind[row_idx, slot] = int(value["event_arg_kind_ids"][local_idx])
            event_distance[row_idx, slot] = count - local_idx
            bitmask = int(value["event_arg_key_masks"][local_idx])
            for bit in range(len(ARG_KEYS)):
                event_arg_keys[row_idx, slot, bit] = float(bool(bitmask & (1 << bit)))
        if count == 0:
            # TransformerEncoder does not support an all-padding row.  A masked
            # semantic NONE event is explicit and contains no private fields.
            slot = MAX_EVENTS - 1
            event_sequences[row_idx][slot] = (BYTE_BOS, BYTE_EOS)
            event_mask[row_idx, slot] = True
    for sequences in event_sequences:
        flat_events.extend(sequences)
    event_text_ids, event_text_mask = _pad_sequences(flat_events)
    event_text_ids = event_text_ids.view(len(rows), MAX_EVENTS, -1)
    event_text_mask = event_text_mask.view(len(rows), MAX_EVENTS, -1)

    main = torch.tensor([value["main_features"] for value in typed], dtype=torch.float32)
    mean = torch.tensor(feature_stats.mean, dtype=torch.float32)
    std = torch.tensor(feature_stats.std, dtype=torch.float32)
    main = (main - mean) / std
    return {
        "prompt_ids": prompt_ids,
        "prompt_mask": prompt_mask,
        "workspace_ids": workspace_ids,
        "workspace_mask": workspace_mask,
        "event_text_ids": event_text_ids,
        "event_text_mask": event_text_mask,
        "event_mask": event_mask,
        "event_action_ids": event_action,
        "event_result_ids": event_result,
        "event_arg_kind_ids": event_kind,
        "event_distance_ids": event_distance,
        "event_arg_keys": event_arg_keys,
        "state_cat_ids": torch.tensor([value["state_cat_ids"] for value in typed], dtype=torch.long),
        "task_cat_ids": torch.tensor([value["task_cat_ids"] for value in typed], dtype=torch.long),
        "main_features": main,
        "main_pred": torch.tensor([value["main_pred"] for value in typed], dtype=torch.long),
        "target_action": torch.tensor([_field(row, "target_action") for row in rows], dtype=torch.long),
        "change_target": torch.tensor([_field(row, "change_target") for row in rows], dtype=torch.float32),
        "alt_target": torch.tensor([_field(row, "alt_target") for row in rows], dtype=torch.long),
        "identity_weight": torch.tensor([_field(row, "identity_weight") for row in rows], dtype=torch.float32),
        "y_true": torch.tensor([_field(row, "y_true") for row in rows], dtype=torch.long),
        "full_index": torch.tensor([_field(row, "full_index") for row in rows], dtype=torch.long),
    }


class ByteConvBlock(nn.Module):
    def __init__(self, hidden: int, dilation: int, dropout: float):
        super().__init__()
        self.norm = nn.GroupNorm(1, hidden)
        self.depthwise = nn.Conv1d(hidden, hidden, 3, padding=dilation, dilation=dilation, groups=hidden)
        self.expand = nn.Conv1d(hidden, hidden * 2, 1)
        self.project = nn.Conv1d(hidden, hidden, 1)
        self.dropout = nn.Dropout(dropout)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        residual = value
        value = self.depthwise(self.norm(value))
        value, gate = self.expand(value).chunk(2, dim=1)
        value = self.project(F.gelu(value) * torch.sigmoid(gate))
        return residual + self.dropout(value)


class ByteSegmentEncoder(nn.Module):
    def __init__(self, config: Weak4ModelConfig):
        super().__init__()
        self.embedding = nn.Embedding(BYTE_VOCAB_SIZE, config.byte_embed_dim, padding_idx=BYTE_PAD)
        self.input_projection = nn.Linear(config.byte_embed_dim, config.byte_hidden_dim)
        dilations = (1, 2, 4, 8)
        self.blocks = nn.ModuleList([
            ByteConvBlock(config.byte_hidden_dim, dilations[idx % len(dilations)], config.dropout)
            for idx in range(config.byte_conv_layers)
        ])
        self.pool_score = nn.Linear(config.byte_hidden_dim, 1)
        self.output_norm = nn.LayerNorm(config.byte_hidden_dim)

    def forward(self, ids: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        value = self.input_projection(self.embedding(ids)).transpose(1, 2)
        for block in self.blocks:
            value = block(value)
        value = value.transpose(1, 2)
        score = self.pool_score(value).squeeze(-1).masked_fill(~mask, -1e4)
        weight = torch.softmax(score, dim=-1) * mask.float()
        weight = weight / weight.sum(dim=-1, keepdim=True).clamp_min(1e-6)
        pooled = (value * weight.unsqueeze(-1)).sum(dim=1)
        return self.output_norm(pooled)


class Weak4FullResidualNet(nn.Module):
    def __init__(self, config: Weak4ModelConfig | None = None):
        super().__init__()
        self.config = config or Weak4ModelConfig()
        cfg = self.config
        self.byte_encoder = ByteSegmentEncoder(cfg)
        self.action_embedding = nn.Embedding(len(ALL_CLASSES) + 1, 24)
        self.result_embedding = nn.Embedding(len(RESULT_BUCKETS), 12)
        self.arg_kind_embedding = nn.Embedding(len(ARG_KINDS), 12)
        self.distance_embedding = nn.Embedding(MAX_EVENTS + 1, 8)
        self.arg_key_projection = nn.Linear(len(ARG_KEYS), 20)
        event_in = cfg.byte_hidden_dim + 24 + 12 + 12 + 8 + 20
        self.event_projection = nn.Linear(event_in, cfg.event_dim)
        self.event_position = nn.Embedding(MAX_EVENTS, cfg.event_dim)
        event_layer = nn.TransformerEncoderLayer(
            d_model=cfg.event_dim,
            nhead=cfg.event_heads,
            dim_feedforward=cfg.event_dim * 3,
            dropout=cfg.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.event_encoder = nn.TransformerEncoder(event_layer, cfg.event_layers)
        self.event_pool = nn.Linear(cfg.event_dim, 1)

        self.state_embeddings = nn.ModuleList([nn.Embedding(size, 8) for size in STATE_CAT_SIZES])
        self.task_embeddings = nn.ModuleList([nn.Embedding(size, 8) for size in TASK_CAT_SIZES])
        categorical_dim = 8 * (len(STATE_CAT_SIZES) + len(TASK_CAT_SIZES))
        self.categorical_tower = nn.Sequential(
            nn.Linear(categorical_dim, 96), nn.GELU(), nn.Dropout(cfg.dropout), nn.LayerNorm(96)
        )
        self.main_tower = nn.Sequential(
            nn.Linear(MAIN_FEATURE_DIM, 96), nn.GELU(), nn.Dropout(cfg.dropout),
            nn.Linear(96, 96), nn.GELU(), nn.LayerNorm(96),
        )
        fusion_in = cfg.byte_hidden_dim * 2 + cfg.event_dim + 96 + 96
        self.fusion = nn.Sequential(
            nn.Linear(fusion_in, cfg.fusion_dim), nn.GELU(), nn.Dropout(cfg.dropout),
            nn.Linear(cfg.fusion_dim, cfg.fusion_dim), nn.GELU(), nn.LayerNorm(cfg.fusion_dim),
        )
        self.change_head = nn.Linear(cfg.fusion_dim, 1)
        self.alt_head = nn.Linear(cfg.fusion_dim, 4)

    def forward(self, batch: Mapping[str, torch.Tensor]) -> dict[str, torch.Tensor]:
        prompt = self.byte_encoder(batch["prompt_ids"], batch["prompt_mask"])
        workspace = self.byte_encoder(batch["workspace_ids"], batch["workspace_mask"])
        bsz, event_count, text_len = batch["event_text_ids"].shape
        event_text = self.byte_encoder(
            batch["event_text_ids"].reshape(bsz * event_count, text_len),
            batch["event_text_mask"].reshape(bsz * event_count, text_len),
        ).reshape(bsz, event_count, -1)
        event = torch.cat([
            event_text,
            self.action_embedding(batch["event_action_ids"]),
            self.result_embedding(batch["event_result_ids"]),
            self.arg_kind_embedding(batch["event_arg_kind_ids"]),
            self.distance_embedding(batch["event_distance_ids"]),
            self.arg_key_projection(batch["event_arg_keys"]),
        ], dim=-1)
        positions = torch.arange(event_count, device=event.device)
        event = self.event_projection(event) + self.event_position(positions)[None, :, :]
        event = self.event_encoder(event, src_key_padding_mask=~batch["event_mask"])
        score = self.event_pool(event).squeeze(-1).masked_fill(~batch["event_mask"], -1e4)
        weight = torch.softmax(score, dim=-1)
        policy = (event * weight.unsqueeze(-1)).sum(dim=1)

        categorical_parts = [
            embedding(batch["state_cat_ids"][:, idx])
            for idx, embedding in enumerate(self.state_embeddings)
        ] + [
            embedding(batch["task_cat_ids"][:, idx])
            for idx, embedding in enumerate(self.task_embeddings)
        ]
        categorical = self.categorical_tower(torch.cat(categorical_parts, dim=-1))
        main = self.main_tower(batch["main_features"])
        fused = self.fusion(torch.cat([prompt, workspace, policy, categorical, main], dim=-1))
        change_logit = self.change_head(fused).squeeze(-1)
        alt_logits = self.alt_head(fused)
        alt_logits = alt_logits.scatter(1, batch["main_pred"].view(-1, 1), -1e4)
        return {"change_logit": change_logit, "alt_logits": alt_logits}


def direction_weights(rows: Sequence[Weak4RouteRow | Mapping[str, Any]]) -> torch.Tensor:
    counts = Counter()
    for row in rows:
        if int(_field(row, "change_target")):
            counts[(int(_typed(row)["main_pred"]), int(_field(row, "alt_target")))] += 1
    weights = torch.ones((4, 4), dtype=torch.float32)
    raw = {pair: 1.0 / math.sqrt(count) for pair, count in counts.items() if count > 0}
    if raw:
        mean = sum(raw.values()) / len(raw)
        for (main_pred, alt), value in raw.items():
            weights[main_pred, alt] = min(2.0, max(0.5, value / mean))
    return weights


def weak4_full_loss(
    outputs: Mapping[str, torch.Tensor],
    batch: Mapping[str, torch.Tensor],
    pair_weights: torch.Tensor | None = None,
    *,
    pos_weight: float = 1.0,
    alt_lambda: float = 0.5,
    bce_lambda: float = 0.5,
    rank_lambda: float = 0.1,
    rank_margin: float = 0.5,
) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
    change = batch["change_target"].float()
    change_logit = outputs["change_logit"].float()
    alt_logits = outputs["alt_logits"].float()
    log_alt = F.log_softmax(alt_logits, dim=-1)
    positive = change.bool()
    safe_alt = batch["alt_target"].clamp_min(0)
    selected_log_alt = log_alt.gather(1, safe_alt.view(-1, 1)).squeeze(1)
    log_joint = torch.where(
        positive,
        F.logsigmoid(change_logit) + selected_log_alt,
        F.logsigmoid(-change_logit),
    )
    joint = -log_joint
    bce = F.binary_cross_entropy_with_logits(
        change_logit,
        change,
        pos_weight=torch.tensor(float(pos_weight), device=change_logit.device),
        reduction="none",
    )
    alt_values = F.cross_entropy(alt_logits, safe_alt, reduction="none")
    if pair_weights is not None:
        pair_weights = pair_weights.to(alt_values.device)
        alt_values = alt_values * pair_weights[
            batch["main_pred"], safe_alt
        ]
    alt = torch.where(positive, alt_values, torch.zeros_like(alt_values))
    switch_scores = change_logit[:, None] + log_alt
    keep_rank = F.relu(rank_margin + switch_scores.max(dim=1).values)
    switch_rank = F.relu(
        rank_margin - switch_scores.gather(1, safe_alt.view(-1, 1)).squeeze(1)
    )
    rank = torch.where(positive, switch_rank, keep_rank)
    per_row = joint + bce_lambda * bce + alt_lambda * alt + rank_lambda * rank
    per_row = per_row * batch["identity_weight"].float()
    loss = per_row.mean()
    return loss, {
        "joint": joint.mean().detach(),
        "bce": bce.mean().detach(),
        "alt": alt[positive].mean().detach() if bool(positive.any()) else alt.sum().detach(),
        "rank": rank.mean().detach(),
    }


def action_probabilities(
    outputs: Mapping[str, torch.Tensor],
    main_pred: torch.Tensor | None = None,
) -> torch.Tensor:
    change = torch.sigmoid(outputs["change_logit"].float())
    alt_logits = outputs["alt_logits"].float()
    if main_pred is not None:
        alt_logits = alt_logits.clone().scatter(1, main_pred.view(-1, 1), -1e4)
    alt = torch.softmax(alt_logits, dim=-1)
    return torch.cat([(1.0 - change).unsqueeze(1), change.unsqueeze(1) * alt], dim=1)


def apply_residual_actions(
    parent_logits: torch.Tensor,
    routed_indices: Sequence[int] | torch.Tensor,
    action_probs: torch.Tensor,
    thresholds: float | torch.Tensor = 0.5,
) -> torch.Tensor:
    logits = torch.as_tensor(parent_logits).float()
    routed = torch.as_tensor(routed_indices, dtype=torch.long)
    if logits.ndim != 2 or logits.shape[1] != len(ALL_CLASSES):
        raise ValueError("parent_logits must have shape [N, 14]")
    if action_probs.shape != (len(routed), 5):
        raise ValueError("action_probs must have shape [R, 5]")
    if len(set(int(value) for value in routed.tolist())) != len(routed):
        raise ValueError("routed_indices contains duplicates")
    if len(routed) and (int(routed.min()) < 0 or int(routed.max()) >= len(logits)):
        raise ValueError("routed index is out of range")
    pred = logits.argmax(dim=1)
    if len(routed) == 0:
        return pred
    main = pred[routed]
    if bool((main >= 4).any()):
        raise ValueError("all routed rows must have a Weak4 parent argmax")
    proposed_alt = action_probs[:, 1:].argmax(dim=1)
    if bool((proposed_alt == main).any()):
        raise ValueError("action probabilities propose switching to the parent class")
    p_keep = action_probs[:, 0]
    p_switch = action_probs[torch.arange(len(routed)), proposed_alt + 1]
    confidence = p_switch / (p_switch + p_keep).clamp_min(1e-12)
    if isinstance(thresholds, torch.Tensor):
        if thresholds.shape != (4, 4):
            raise ValueError("threshold tensor must have shape [4, 4]")
        table = thresholds.to(confidence.device)
        cutoff = table[main.to(confidence.device), proposed_alt.to(confidence.device)]
    else:
        cutoff = torch.full_like(confidence, float(thresholds))
    changed = confidence >= cutoff
    pred[routed[changed]] = proposed_alt[changed].to(pred.device)
    return pred


def fit_direction_thresholds(
    main_pred: Sequence[int] | torch.Tensor,
    y_true: Sequence[int] | torch.Tensor,
    action_probs: torch.Tensor,
    *,
    global_threshold: float = 0.5,
    min_support: int = 40,
    grid: Sequence[float] | None = None,
) -> tuple[torch.Tensor, dict[str, Any]]:
    main = torch.as_tensor(main_pred, dtype=torch.long)
    truth = torch.as_tensor(y_true, dtype=torch.long)
    probs = torch.as_tensor(action_probs, dtype=torch.float32)
    if probs.shape != (len(main), 5) or len(truth) != len(main):
        raise ValueError("threshold calibration row mismatch")
    proposed = probs[:, 1:].argmax(dim=1)
    p_switch = probs[torch.arange(len(main)), proposed + 1]
    confidence = p_switch / (p_switch + probs[:, 0]).clamp_min(1e-12)
    thresholds = torch.full((4, 4), float(global_threshold), dtype=torch.float32)
    thresholds.fill_diagonal_(1.0)
    candidates = tuple(grid or [step / 20 for step in range(4, 20)])
    details: dict[str, Any] = {}
    for source in range(4):
        for alt in range(4):
            if source == alt:
                continue
            rows = (main == source) & (proposed == alt)
            support = int(rows.sum())
            key = f"{WEAK4_CLASSES[source]}->{WEAK4_CLASSES[alt]}"
            if support < min_support:
                details[key] = {"support": support, "threshold": global_threshold, "fallback": True}
                continue
            best = None
            for threshold in candidates:
                switched = rows & (confidence >= threshold)
                rescue = int((switched & (truth == alt) & (truth != main)).sum())
                harm = int((switched & (truth == main)).sum())
                score = rescue - harm
                candidate = (score, -harm, threshold, rescue)
                if best is None or candidate > best:
                    best = candidate
            assert best is not None
            threshold = float(best[2])
            thresholds[source, alt] = threshold
            details[key] = {
                "support": support,
                "threshold": threshold,
                "fallback": False,
                "net": int(best[0]),
                "harm": int(-best[1]),
                "rescue": int(best[3]),
            }
    return thresholds, {
        "global_threshold": global_threshold,
        "min_support": min_support,
        "grid": list(candidates),
        "directions": details,
    }


def stratified_group_fold_ids(
    group_ids: Sequence[str],
    labels: Sequence[int],
    n_folds: int,
    seed: int,
) -> list[int]:
    """Greedy deterministic group split balanced on labels and row count."""
    if len(group_ids) != len(labels) or not group_ids:
        raise ValueError("group_ids and labels must be nonempty and aligned")
    if n_folds < 2:
        raise ValueError("n_folds must be at least 2")
    groups: dict[str, list[int]] = {}
    for idx, group in enumerate(group_ids):
        groups.setdefault(str(group), []).append(idx)
    if len(groups) < n_folds:
        raise ValueError("fewer groups than folds")
    rng = random.Random(seed)
    ordered = list(groups)
    rng.shuffle(ordered)
    ordered.sort(key=lambda group: len(groups[group]), reverse=True)
    total = Counter(int(value) for value in labels)
    targets = {label: count / n_folds for label, count in total.items()}
    target_size = len(labels) / n_folds
    fold_counts = [Counter() for _ in range(n_folds)]
    fold_sizes = [0] * n_folds
    group_fold: dict[str, int] = {}
    for group in ordered:
        indices = groups[group]
        group_count = Counter(int(labels[idx]) for idx in indices)
        best_fold = min(
            range(n_folds),
            key=lambda fold: (
                sum(
                    ((fold_counts[fold][label] + group_count[label] - target) ** 2) / max(target, 1.0)
                    - ((fold_counts[fold][label] - target) ** 2) / max(target, 1.0)
                    for label, target in targets.items()
                )
                + 0.2 * (
                    ((fold_sizes[fold] + len(indices) - target_size) ** 2)
                    - ((fold_sizes[fold] - target_size) ** 2)
                ) / max(target_size, 1.0),
                fold_sizes[fold],
                fold,
            ),
        )
        group_fold[group] = best_fold
        fold_counts[best_fold].update(group_count)
        fold_sizes[best_fold] += len(indices)
    return [group_fold[str(group)] for group in group_ids]


def split_fit_calibration_sessions(
    rows: Sequence[Weak4RouteRow | Mapping[str, Any]],
    candidate_indices: Sequence[int],
    fraction: float,
    seed: int,
) -> tuple[list[int], list[int]]:
    if not 0.0 < fraction < 0.5:
        raise ValueError("calibration fraction must be in (0, 0.5)")
    by_session: dict[str, list[int]] = {}
    for idx in candidate_indices:
        by_session.setdefault(str(_field(rows[idx], "session_id")), []).append(int(idx))
    if len(by_session) < 2:
        raise ValueError("need at least two sessions for fit/calibration split")
    sessions = sorted(
        by_session,
        key=lambda value: hashlib.sha256(f"{seed}:{value}".encode()).digest(),
    )
    target = max(1, round(len(candidate_indices) * fraction))
    calibration_sessions: set[str] = set()
    count = 0
    for session in sessions:
        if count >= target and calibration_sessions:
            break
        calibration_sessions.add(session)
        count += len(by_session[session])
    fit = [idx for idx in candidate_indices if str(_field(rows[idx], "session_id")) not in calibration_sessions]
    calibration = [idx for idx in candidate_indices if str(_field(rows[idx], "session_id")) in calibration_sessions]
    if not fit or not calibration:
        raise ValueError("fit/calibration split produced an empty partition")
    return fit, calibration


def tensor_dict_to_device(batch: Mapping[str, torch.Tensor], device: torch.device) -> dict[str, torch.Tensor]:
    return {key: value.to(device) for key, value in batch.items()}
