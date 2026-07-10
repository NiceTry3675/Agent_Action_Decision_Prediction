"""Training-only event reconstruction and selective mode taxonomy utilities.

This module deliberately stops short of defining a model head.  It provides the
leak-safe inputs for the selective privileged-mode probe:

* recover a row's full next event only from a later row in the same session,
  using both step arithmetic and an exact prompt match;
* fit action-specific, args/intention-only modes from an explicit set of
  training indices;
* mask rare/unseen modes and omit actions with fewer than two supported modes;
* transform any split without mutating the fitted taxonomy, while reporting
  recovered/terminal/turn-bin diagnostics.

Raw paths and commands are inspected by the signature functions but are never
stored in a taxonomy.  ``result_summary`` is part of the strict witness hash,
but it is never used to define a mode.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


DEFAULT_SELECTED_ACTIONS = ("read_file", "edit_file", "run_bash")
UNKNOWN_MODE_ID = -100
RECONSTRUCTION_FORMAT = "privileged-event-reconstruction-v1"
TAXONOMY_FORMAT = "selective-privileged-mode-taxonomy-v1"

_STEP_RE = re.compile(r"^(?P<session>.+)-step_(?P<step>\d+)$")


@dataclass(frozen=True)
class ReconstructionResult:
    """Full-event targets aligned one-to-one with the input samples."""

    sample_ids: tuple[str, ...]
    events: tuple[dict[str, Any] | None, ...]
    statuses: tuple[str, ...]
    witness_counts: tuple[int, ...]
    witness_indices: tuple[tuple[int, ...], ...]
    metadata: dict[str, Any]

    def __len__(self) -> int:
        return len(self.events)

    def event_for(self, index: int) -> dict[str, Any] | None:
        return self.events[index]

    def is_recovered(self, index: int) -> bool:
        return self.statuses[index] == "recovered"

    @property
    def event_by_id(self) -> dict[str, dict[str, Any] | None]:
        return dict(zip(self.sample_ids, self.events))

    @property
    def status_by_id(self) -> dict[str, str]:
        return dict(zip(self.sample_ids, self.statuses))

    @property
    def stats(self) -> dict[str, Any]:
        return self.metadata


@dataclass(frozen=True)
class SelectiveModeTaxonomy:
    """A train-index-fitted conditional-mode vocabulary and its priors."""

    selected_actions: tuple[str, ...]
    min_support: int
    prior_smoothing: float
    modes_by_action: dict[str, tuple[str, ...]]
    support_by_action: dict[str, dict[str, int]]
    priors_by_action: dict[str, tuple[float, ...]]
    fit_metadata: dict[str, Any]
    format: str = TAXONOMY_FORMAT

    @property
    def active_actions(self) -> tuple[str, ...]:
        return tuple(
            action for action in self.selected_actions if action in self.modes_by_action
        )

    def has_branch(self, action: str) -> bool:
        """Return whether ``action`` has a genuine (K >= 2) mode branch."""

        return len(self.modes_by_action.get(action, ())) >= 2

    def mode_index(self, action: str, signature: str | None) -> int:
        """Return an action-local mode index, or ``UNKNOWN_MODE_ID``."""

        if signature is None or not self.has_branch(action):
            return UNKNOWN_MODE_ID
        try:
            return self.modes_by_action[action].index(signature)
        except ValueError:
            return UNKNOWN_MODE_ID

    def global_offset(self, action: str) -> int | None:
        """Return the packed-head offset for an active action."""

        offset = 0
        for candidate in self.active_actions:
            if candidate == action:
                return offset
            offset += len(self.modes_by_action[candidate])
        return None

    def prior_map(self, action: str) -> dict[str, float]:
        modes = self.modes_by_action.get(action, ())
        priors = self.priors_by_action.get(action, ())
        return dict(zip(modes, priors))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation containing no raw event identity."""

        return {
            "format": self.format,
            "selected_actions": list(self.selected_actions),
            "min_support": self.min_support,
            "prior_smoothing": self.prior_smoothing,
            "modes_by_action": {
                action: list(modes) for action, modes in self.modes_by_action.items()
            },
            "support_by_action": {
                action: dict(support)
                for action, support in self.support_by_action.items()
            },
            "priors_by_action": {
                action: list(priors)
                for action, priors in self.priors_by_action.items()
            },
            "fit_metadata": self.fit_metadata,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SelectiveModeTaxonomy":
        if payload.get("format") != TAXONOMY_FORMAT:
            raise ValueError(f"unsupported taxonomy format: {payload.get('format')!r}")
        taxonomy = cls(
            selected_actions=_validate_selected_actions(payload["selected_actions"]),
            min_support=_validate_min_support(payload["min_support"]),
            prior_smoothing=_validate_smoothing(payload["prior_smoothing"]),
            modes_by_action={
                str(action): tuple(str(mode) for mode in modes)
                for action, modes in dict(payload["modes_by_action"]).items()
            },
            support_by_action={
                str(action): {
                    str(mode): int(count) for mode, count in dict(support).items()
                }
                for action, support in dict(payload["support_by_action"]).items()
            },
            priors_by_action={
                str(action): tuple(float(value) for value in priors)
                for action, priors in dict(payload["priors_by_action"]).items()
            },
            fit_metadata=dict(payload.get("fit_metadata") or {}),
        )
        _validate_taxonomy(taxonomy)
        return taxonomy


@dataclass(frozen=True)
class ModeTargetResult:
    """Mode targets and split diagnostics in the requested index order."""

    indices: tuple[int, ...]
    actions: tuple[str, ...]
    signatures: tuple[str | None, ...]
    local_mode_ids: tuple[int, ...]
    global_mode_ids: tuple[int, ...]
    mode_mask: tuple[bool, ...]
    recovery_statuses: tuple[str, ...]
    metadata: dict[str, Any]

    @property
    def recovery_flags(self) -> tuple[bool, ...]:
        return tuple(status == "recovered" for status in self.recovery_statuses)


def parse_sample_id(sample_id: Any) -> tuple[str, int] | None:
    match = _STEP_RE.fullmatch(str(sample_id or ""))
    if match is None:
        return None
    return match.group("session"), int(match.group("step"))


def canonicalize_full_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize the three fields that define a full witness."""

    if not isinstance(event, Mapping):
        raise ValueError("assistant event must be a mapping")
    name = event.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("assistant event requires a non-empty string name")
    if "args" not in event or not isinstance(event.get("args"), Mapping):
        raise ValueError("assistant event requires a mapping args field")
    if "result_summary" not in event or not isinstance(event.get("result_summary"), str):
        raise ValueError("assistant event requires a string result_summary field")
    try:
        # The round-trip both deep-copies and rejects non-JSON event payloads.
        args = json.loads(
            json.dumps(event["args"], ensure_ascii=False, sort_keys=True, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("assistant event args must be finite JSON data") from exc
    return {
        "name": name,
        "args": args,
        "result_summary": event["result_summary"],
    }


def full_event_hash(event: Mapping[str, Any]) -> str:
    canonical = canonicalize_full_event(event)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _strict_history_pairs(
    history: Any,
) -> tuple[tuple[str, dict[str, Any]], ...] | None:
    """Return strict user/action pairs, or None for a malformed witness row."""

    if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
        return None
    if len(history) % 2:
        return None
    pairs: list[tuple[str, dict[str, Any]]] = []
    for offset in range(0, len(history), 2):
        user_event = history[offset]
        action_event = history[offset + 1]
        if not isinstance(user_event, Mapping) or user_event.get("role") != "user":
            return None
        content = user_event.get("content")
        if not isinstance(content, str):
            return None
        if not isinstance(action_event, Mapping) or action_event.get("role") != "assistant_action":
            return None
        try:
            canonical = canonicalize_full_event(action_event)
        except ValueError:
            return None
        pairs.append((content, canonical))
    return tuple(pairs)


def _aligned_labels(
    samples: Sequence[Mapping[str, Any]],
    labels: Mapping[str, str] | Sequence[str] | None,
) -> tuple[str | None, ...]:
    if labels is None:
        return (None,) * len(samples)
    if isinstance(labels, Mapping):
        aligned: list[str] = []
        for sample in samples:
            sample_id = str(sample.get("id") or "")
            if sample_id not in labels:
                raise ValueError(f"label missing for sample id: {sample_id!r}")
            label = labels[sample_id]
            if not isinstance(label, str) or not label:
                raise ValueError(f"invalid label for sample id: {sample_id!r}")
            aligned.append(label)
        return tuple(aligned)
    if isinstance(labels, (str, bytes)) or not isinstance(labels, Sequence):
        raise TypeError("labels must be an id mapping, an aligned sequence, or None")
    if len(labels) != len(samples):
        raise ValueError("aligned labels must have the same length as samples")
    if any(not isinstance(label, str) or not label for label in labels):
        raise ValueError("every aligned label must be a non-empty string")
    return tuple(labels)


def reconstruct_full_events(
    samples: Sequence[Mapping[str, Any]],
    labels: Mapping[str, str] | Sequence[str] | None = None,
) -> ReconstructionResult:
    """Recover full target events through strict positional+prompt witnesses.

    There is intentionally no prompt-only or ID-free fallback.  If multiple
    later rows witness a target, all canonical ``name/args/result_summary``
    payloads must agree.  Label disagreement, witness disagreement, malformed
    history, and prompt mismatch are all fail-closed conditions.
    """

    if isinstance(samples, (str, bytes)) or not isinstance(samples, Sequence):
        raise TypeError("samples must be an indexable sequence")
    aligned_labels = _aligned_labels(samples, labels)
    parsed_ids: list[tuple[str, int] | None] = []
    by_session: dict[str, dict[int, int]] = {}
    seen_ids: set[str] = set()
    for index, sample in enumerate(samples):
        if not isinstance(sample, Mapping):
            raise TypeError(f"sample {index} is not a mapping")
        sample_id = str(sample.get("id") or "")
        if not sample_id or sample_id in seen_ids:
            raise ValueError(f"missing or duplicate sample id: {sample_id!r}")
        seen_ids.add(sample_id)
        parsed = parse_sample_id(sample_id)
        parsed_ids.append(parsed)
        if parsed is None:
            continue
        session, step = parsed
        steps = by_session.setdefault(session, {})
        if step in steps:
            other = samples[steps[step]].get("id")
            raise ValueError(
                f"duplicate session step for {sample_id!r} and {other!r}"
            )
        steps[step] = index

    candidates: list[list[tuple[str, dict[str, Any], int, bool]]] = [
        [] for _ in samples
    ]
    prompt_mismatches = [0] * len(samples)
    malformed_histories = 0
    missing_positional_sources = 0
    valid_witness_rows = 0
    prompt_matches = 0
    prompt_mismatch_total = 0
    name_checked_witnesses = 0
    name_agreement_witnesses = 0

    for witness_index, (sample, parsed) in enumerate(zip(samples, parsed_ids)):
        if parsed is None:
            continue
        pairs = _strict_history_pairs(sample.get("history", []))
        if pairs is None:
            malformed_histories += 1
            continue
        if not pairs:
            continue
        valid_witness_rows += 1
        session, witness_step = parsed
        steps = by_session[session]
        first_step = witness_step - len(pairs)
        for offset, (prompt, event) in enumerate(pairs):
            source_index = steps.get(first_step + offset)
            if source_index is None:
                missing_positional_sources += 1
                continue
            source_prompt = samples[source_index].get("current_prompt")
            if not isinstance(source_prompt, str) or source_prompt != prompt:
                prompt_mismatches[source_index] += 1
                prompt_mismatch_total += 1
                continue
            prompt_matches += 1
            expected_name = aligned_labels[source_index]
            name_matches = expected_name is None or event["name"] == expected_name
            if expected_name is not None:
                name_checked_witnesses += 1
                if name_matches:
                    name_agreement_witnesses += 1
            candidates[source_index].append(
                (full_event_hash(event), event, witness_index, name_matches)
            )

    events: list[dict[str, Any] | None] = []
    statuses: list[str] = []
    witness_counts: list[int] = []
    witness_indices: list[tuple[int, ...]] = []
    max_step_by_session = {
        session: max(steps) for session, steps in by_session.items() if steps
    }
    for index, parsed in enumerate(parsed_ids):
        rows = candidates[index]
        witness_counts.append(len(rows))
        witness_indices.append(tuple(sorted({row[2] for row in rows})))
        unique_hashes = {row[0] for row in rows}
        if len(unique_hashes) > 1:
            events.append(None)
            statuses.append("conflict")
        elif rows and not all(row[3] for row in rows):
            events.append(None)
            statuses.append("name_mismatch")
        elif rows:
            events.append(rows[0][1])
            statuses.append("recovered")
        elif parsed is None:
            events.append(None)
            statuses.append("invalid_id")
        elif prompt_mismatches[index]:
            events.append(None)
            statuses.append("prompt_mismatch")
        elif parsed[1] == max_step_by_session[parsed[0]]:
            events.append(None)
            statuses.append("terminal")
        else:
            events.append(None)
            statuses.append("no_witness")

    status_counts = Counter(statuses)
    recovered = status_counts.get("recovered", 0)
    metadata = {
        "format": RECONSTRUCTION_FORMAT,
        "rows": len(samples),
        "recovered_rows": recovered,
        "coverage": recovered / len(samples) if samples else 0.0,
        "status_counts": dict(sorted(status_counts.items())),
        "conflicting_rows": status_counts.get("conflict", 0),
        "terminal_rows": status_counts.get("terminal", 0),
        "valid_witness_rows": valid_witness_rows,
        "malformed_witness_histories": malformed_histories,
        "missing_positional_sources": missing_positional_sources,
        "prompt_matches": prompt_matches,
        "prompt_mismatches": prompt_mismatch_total,
        "name_checked_witnesses": name_checked_witnesses,
        "name_agreement_witnesses": name_agreement_witnesses,
        "name_agreement_rate": (
            name_agreement_witnesses / name_checked_witnesses
            if name_checked_witnesses
            else None
        ),
        "method": "strict_positional_plus_exact_prompt",
    }
    return ReconstructionResult(
        sample_ids=tuple(str(sample.get("id") or "") for sample in samples),
        events=tuple(events),
        statuses=tuple(statuses),
        witness_counts=tuple(witness_counts),
        witness_indices=tuple(witness_indices),
        metadata=metadata,
    )


_DEPENDENCY_MANIFESTS = {
    "build.gradle",
    "build.gradle.kts",
    "cargo.lock",
    "cargo.toml",
    "composer.json",
    "composer.lock",
    "gemfile",
    "gemfile.lock",
    "go.mod",
    "go.sum",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "podfile",
    "podfile.lock",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
    "settings.gradle",
    "settings.gradle.kts",
    "setup.cfg",
    "setup.py",
    "yarn.lock",
}
_SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".dart",
    ".ex",
    ".exs",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ipynb",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".scss",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
}
_CONFIG_SUFFIXES = {
    ".cfg",
    ".conf",
    ".env",
    ".ini",
    ".json",
    ".properties",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}
_DOC_SUFFIXES = {".md", ".mdx", ".rst"}
_SCRIPT_SUFFIXES = {".bat", ".bash", ".cmd", ".ps1", ".sh", ".zsh"}


def read_file_signature(args: Mapping[str, Any]) -> str | None:
    """Map a path to a categorical role without retaining its identity."""

    if not isinstance(args, Mapping):
        return None
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    normalized = path.strip().lower().replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part and part != ".")
    if not parts:
        return None
    basename = parts[-1]
    suffix = "." + basename.rsplit(".", 1)[-1] if "." in basename else ""
    if (
        any(part in {"test", "tests", "spec", "specs", "__tests__"} for part in parts[:-1])
        or basename.startswith(("test_", "spec_"))
        or ".test." in basename
        or ".spec." in basename
    ):
        return "test_code"
    if basename in _DEPENDENCY_MANIFESTS:
        return "dependency_manifest"
    if (
        basename.startswith(("readme", "changelog", "contributing", "license"))
        or "docs" in parts[:-1]
        or suffix in _DOC_SUFFIXES
    ):
        return "documentation"
    if (
        basename in {"dockerfile", "makefile", "procfile", "vagrantfile"}
        or any(
            part in {".github", "k8s", "kubernetes", "terraform", "ansible"}
            for part in parts[:-1]
        )
        or suffix in {".tf", ".tfvars"}
    ):
        return "infra_build_config"
    if suffix in _SCRIPT_SUFFIXES:
        return "script"
    if suffix in _SOURCE_SUFFIXES:
        return "source_code"
    if suffix in _CONFIG_SUFFIXES or basename.startswith("."):
        return "configuration"
    return None


def edit_file_signature(args: Mapping[str, Any]) -> str | None:
    """Separate path-only edits from symbol-targeted edits."""

    if not isinstance(args, Mapping):
        return None
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    target = args.get("target_symbol")
    if isinstance(target, str) and target.strip():
        return "path_target_symbol"
    return "path_only"


_COMMAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "test",
        re.compile(
            r"(?:^|[;&|\s])(?:pytest|unittest|jest|vitest|mocha|rspec|ctest)(?:$|\s)"
            r"|\bgo\s+test\b|\bcargo\s+test\b|\bnpm\s+(?:run\s+)?test\b"
            r"|\b(?:pnpm|yarn)\s+(?:run\s+)?test\b|\bmvn\w*\s+test\b"
            r"|\bgradle\w*\s+test\b"
        ),
    ),
    (
        "lint",
        re.compile(
            r"\b(?:eslint|ruff|flake8|pylint|mypy|pyright|golangci-lint|"
            r"rubocop|biome|checkstyle)\b"
            r"|\bcargo\s+clippy\b|\bgo\s+vet\b|\btsc(?:\s|$)"
            r"|\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:lint|typecheck|check)\b"
        ),
    ),
    (
        "install",
        re.compile(
            r"\b(?:pip|pip3)\s+install\b|\bpoetry\s+install\b|\buv\s+sync\b"
            r"|\b(?:npm|pnpm|yarn)\s+(?:install|ci)\b|\bbundle\s+install\b"
            r"|\bcargo\s+install\b|\b(?:apt|apt-get|apk|dnf|yum|brew)\s+install\b"
        ),
    ),
    (
        "migration_db",
        re.compile(
            r"\b(?:alembic|flyway|liquibase)\b|\bmanage\.py\s+migrate\b"
            r"|\bprisma\s+migrate\b|\b(?:knex|sequelize)\b.*\b(?:migrate|db:)"
            r"|\b(?:migrate|migration)\b"
        ),
    ),
    (
        "infra",
        re.compile(
            r"\b(?:docker|docker-compose|podman|kubectl|helm|terraform|ansible|pulumi)\b"
            r"|\b(?:aws|gcloud|az)\s+"
        ),
    ),
    (
        "build",
        re.compile(
            r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?build\b|\bcargo\s+build\b"
            r"|\bgo\s+build\b|\b(?:mvn|mvnw)\b.*\b(?:package|compile)\b"
            r"|\bgradle\w*\b.*\bbuild\b|\bcmake\b|(?:^|[;&|\s])make(?:$|\s)"
        ),
    ),
    (
        "server_runtime",
        re.compile(
            r"\b(?:uvicorn|gunicorn|hypercorn)\b|\bflask\s+run\b"
            r"|\bmanage\.py\s+runserver\b|\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:start|dev|serve)\b"
            r"|\bcargo\s+run\b|\bgo\s+run\b|\bdotnet\s+run\b"
            r"|\brails\s+(?:server|s)\b|(?:^|[;&|\s])(?:python|python3|node)\s+[^;&|]+"
        ),
    ),
)


def run_bash_signature(args: Mapping[str, Any]) -> str | None:
    """Classify command intent; never return the command or its arguments."""

    if not isinstance(args, Mapping):
        return None
    command = args.get("cmd", args.get("command"))
    if not isinstance(command, str) or not command.strip():
        return None
    normalized = " ".join(command.lower().split())
    for mode, pattern in _COMMAND_PATTERNS:
        if pattern.search(normalized):
            return mode
    return "other"


def event_mode_signature(event: Mapping[str, Any]) -> str | None:
    """Return the args/intention-only signature for a selected action."""

    if not isinstance(event, Mapping):
        return None
    action = event.get("name")
    args = event.get("args")
    if action == "read_file":
        return read_file_signature(args)
    if action == "edit_file":
        return edit_file_signature(args)
    if action == "run_bash":
        return run_bash_signature(args)
    return None


def _validate_selected_actions(actions: Iterable[str]) -> tuple[str, ...]:
    if isinstance(actions, (str, bytes)):
        raise TypeError("selected_actions must be an iterable of action names")
    result = tuple(actions)
    if not result or any(not isinstance(action, str) or not action for action in result):
        raise ValueError("selected_actions must contain non-empty strings")
    if len(set(result)) != len(result):
        raise ValueError("selected_actions must not contain duplicates")
    unsupported = sorted(set(result) - set(DEFAULT_SELECTED_ACTIONS))
    if unsupported:
        raise ValueError(f"no signature extractor for selected actions: {unsupported}")
    return result


def _validate_min_support(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("min_support must be a positive integer")
    return value


def _validate_smoothing(value: Any) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("prior_smoothing must be finite and non-negative")
    return value


def _checked_indices(indices: Iterable[int], size: int, name: str) -> tuple[int, ...]:
    if isinstance(indices, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of integer indices")
    result = tuple(indices)
    if any(isinstance(index, bool) or not isinstance(index, int) for index in result):
        raise TypeError(f"{name} must contain integer indices")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicate indices")
    if any(index < 0 or index >= size for index in result):
        raise IndexError(f"{name} contains an out-of-range index")
    return result


def _index_scope_hash(samples: Sequence[Mapping[str, Any]], indices: Sequence[int]) -> str:
    digest = hashlib.sha256()
    for index in sorted(indices):
        digest.update(str(index).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(samples[index].get("id") or "").encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_reconstruction_alignment(
    samples: Sequence[Mapping[str, Any]], reconstruction: ReconstructionResult
) -> None:
    if len(reconstruction) != len(samples):
        raise ValueError("reconstruction and samples are not aligned")
    sample_ids = tuple(str(sample.get("id") or "") for sample in samples)
    if reconstruction.sample_ids != sample_ids:
        raise ValueError("reconstruction sample ids/order do not match samples")


def fit_selective_taxonomy(
    samples: Sequence[Mapping[str, Any]],
    reconstruction: ReconstructionResult,
    train_indices: Iterable[int],
    labels: Mapping[str, str] | Sequence[str] | None = None,
    *,
    selected_actions: Iterable[str] = DEFAULT_SELECTED_ACTIONS,
    min_support: int = 200,
    prior_smoothing: float = 1.0,
) -> SelectiveModeTaxonomy:
    """Fit modes and priors using only the explicitly supplied train indices."""

    _validate_reconstruction_alignment(samples, reconstruction)
    train_indices = _checked_indices(train_indices, len(samples), "train_indices")
    train_index_set = set(train_indices)
    selected_actions = _validate_selected_actions(selected_actions)
    min_support = _validate_min_support(min_support)
    prior_smoothing = _validate_smoothing(prior_smoothing)
    aligned_labels = _aligned_labels(samples, labels)
    selected = set(selected_actions)
    support: dict[str, Counter[str]] = {
        action: Counter() for action in selected_actions
    }
    recovered_fit_rows = 0
    selected_recovered_rows = 0
    fit_name_mismatches = 0
    cross_split_witness_rows = 0
    unrecognized_signature_rows = 0

    for index in train_indices:
        if not reconstruction.is_recovered(index):
            continue
        # The event payload is privileged data carried by witness rows.  Do
        # not let an incorrectly constructed (non-session-grouped) split pull
        # that payload across the train/validation boundary.
        if any(
            witness_index not in train_index_set
            for witness_index in reconstruction.witness_indices[index]
        ):
            cross_split_witness_rows += 1
            continue
        recovered_fit_rows += 1
        event = reconstruction.events[index]
        assert event is not None
        label = aligned_labels[index]
        if label is not None and label != event["name"]:
            fit_name_mismatches += 1
            continue
        action = event["name"]
        if action not in selected:
            continue
        selected_recovered_rows += 1
        signature = event_mode_signature(event)
        if signature is None:
            unrecognized_signature_rows += 1
            continue
        support[action][signature] += 1

    modes_by_action: dict[str, tuple[str, ...]] = {}
    priors_by_action: dict[str, tuple[float, ...]] = {}
    inactive_reasons: dict[str, str] = {}
    for action in selected_actions:
        supported = tuple(
            sorted(mode for mode, count in support[action].items() if count >= min_support)
        )
        if len(supported) < 2:
            inactive_reasons[action] = "fewer_than_two_supported_modes"
            continue
        modes_by_action[action] = supported
        denominator = sum(support[action][mode] for mode in supported)
        denominator += prior_smoothing * len(supported)
        priors_by_action[action] = tuple(
            (support[action][mode] + prior_smoothing) / denominator
            for mode in supported
        )

    fit_metadata = {
        "fit_scope": "explicit_train_indices_only",
        "fit_rows": len(train_indices),
        "fit_index_hash": _index_scope_hash(samples, train_indices),
        "fit_recovered_rows": recovered_fit_rows,
        "fit_selected_recovered_rows": selected_recovered_rows,
        "fit_name_mismatches": fit_name_mismatches,
        "cross_split_witness_rows_masked": cross_split_witness_rows,
        "unrecognized_signature_rows": unrecognized_signature_rows,
        "active_actions": [
            action for action in selected_actions if action in modes_by_action
        ],
        "inactive_actions": inactive_reasons,
        "support_pruned_rows_by_action": {
            action: sum(
                count
                for mode, count in support[action].items()
                if mode not in modes_by_action.get(action, ())
            )
            for action in selected_actions
        },
    }
    taxonomy = SelectiveModeTaxonomy(
        selected_actions=selected_actions,
        min_support=min_support,
        prior_smoothing=prior_smoothing,
        modes_by_action=modes_by_action,
        support_by_action={
            action: dict(sorted(counter.items())) for action, counter in support.items()
        },
        priors_by_action=priors_by_action,
        fit_metadata=fit_metadata,
    )
    _validate_taxonomy(taxonomy)
    return taxonomy


def _validate_taxonomy(taxonomy: SelectiveModeTaxonomy) -> None:
    selected = set(taxonomy.selected_actions)
    if set(taxonomy.modes_by_action) - selected:
        raise ValueError("taxonomy contains modes for an unselected action")
    if set(taxonomy.priors_by_action) != set(taxonomy.modes_by_action):
        raise ValueError("taxonomy mode and prior actions do not align")
    for action, modes in taxonomy.modes_by_action.items():
        if len(modes) < 2 or len(set(modes)) != len(modes):
            raise ValueError(f"active action {action!r} must have at least two unique modes")
        priors = taxonomy.priors_by_action[action]
        if len(priors) != len(modes):
            raise ValueError(f"prior length mismatch for {action!r}")
        if any(not math.isfinite(value) or value <= 0 for value in priors):
            raise ValueError(f"priors for {action!r} must be finite and positive")
        if not math.isclose(sum(priors), 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"priors for {action!r} must sum to one")


def turn_bin(sample: Mapping[str, Any]) -> str:
    """Return the fixed diagnostic bin: start/early/mid/late/long/unknown."""

    meta = sample.get("session_meta")
    turn: Any = meta.get("turn_index") if isinstance(meta, Mapping) else None
    if isinstance(turn, bool) or not isinstance(turn, (int, float)):
        parsed = parse_sample_id(sample.get("id"))
        turn = parsed[1] if parsed is not None else None
    if turn is None or not math.isfinite(float(turn)) or turn < 0:
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


def _increment_subset(
    table: dict[str, dict[str, int]],
    key: str,
    *,
    recovered: bool,
    selected: bool,
    supervised: bool,
) -> None:
    row = table.setdefault(
        key,
        {"rows": 0, "recovered": 0, "unrecovered": 0, "selected": 0, "mode_supervised": 0},
    )
    row["rows"] += 1
    row["recovered" if recovered else "unrecovered"] += 1
    row["selected"] += int(selected)
    row["mode_supervised"] += int(supervised)


def transform_mode_targets(
    samples: Sequence[Mapping[str, Any]],
    reconstruction: ReconstructionResult,
    taxonomy: SelectiveModeTaxonomy,
    labels: Mapping[str, str] | Sequence[str],
    *,
    indices: Iterable[int] | None = None,
) -> ModeTargetResult:
    """Apply a frozen taxonomy and return masked conditional-mode targets."""

    _validate_reconstruction_alignment(samples, reconstruction)
    _validate_taxonomy(taxonomy)
    if indices is None:
        requested = tuple(range(len(samples)))
    else:
        requested = _checked_indices(indices, len(samples), "indices")
    aligned_labels = _aligned_labels(samples, labels)
    selected = set(taxonomy.selected_actions)
    actions: list[str] = []
    signatures: list[str | None] = []
    local_ids: list[int] = []
    global_ids: list[int] = []
    masks: list[bool] = []
    statuses: list[str] = []
    masked_reasons: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    by_action: dict[str, dict[str, int]] = {}
    by_turn: dict[str, dict[str, int]] = {}

    for index in requested:
        action = aligned_labels[index]
        assert action is not None
        status = reconstruction.statuses[index]
        event = reconstruction.events[index]
        signature: str | None = None
        local_id = UNKNOWN_MODE_ID
        reason = "supervised"
        if action not in selected:
            reason = "action_not_selected"
        elif status != "recovered" or event is None:
            reason = f"event_{status}"
        elif event["name"] != action:
            reason = "event_name_mismatch"
        elif not taxonomy.has_branch(action):
            reason = "no_active_branch"
            signature = event_mode_signature(event)
        else:
            signature = event_mode_signature(event)
            if signature is None:
                reason = "unrecognized_signature"
            else:
                local_id = taxonomy.mode_index(action, signature)
                if local_id == UNKNOWN_MODE_ID:
                    reason = "pruned_or_unseen_signature"

        supervised = local_id != UNKNOWN_MODE_ID
        if supervised:
            offset = taxonomy.global_offset(action)
            assert offset is not None
            global_id = offset + local_id
        else:
            global_id = UNKNOWN_MODE_ID
            masked_reasons[reason] += 1
        recovered = status == "recovered"
        status_counts[status] += 1
        _increment_subset(
            by_action,
            action,
            recovered=recovered,
            selected=action in selected,
            supervised=supervised,
        )
        _increment_subset(
            by_turn,
            turn_bin(samples[index]),
            recovered=recovered,
            selected=action in selected,
            supervised=supervised,
        )
        actions.append(action)
        signatures.append(signature)
        local_ids.append(local_id)
        global_ids.append(global_id)
        masks.append(supervised)
        statuses.append(status)

    metadata = {
        "rows": len(requested),
        "taxonomy_format": taxonomy.format,
        "taxonomy_fit_index_hash": taxonomy.fit_metadata.get("fit_index_hash"),
        "active_actions": list(taxonomy.active_actions),
        "recovered_rows": status_counts.get("recovered", 0),
        "unrecovered_rows": len(requested) - status_counts.get("recovered", 0),
        "mode_supervised_rows": sum(masks),
        "recovery_status_counts": dict(sorted(status_counts.items())),
        "masked_reason_counts": dict(sorted(masked_reasons.items())),
        "by_action": dict(sorted(by_action.items())),
        "by_turn_bin": {
            key: by_turn[key]
            for key in ("start", "early", "mid", "late", "long", "unknown")
            if key in by_turn
        },
    }
    return ModeTargetResult(
        indices=requested,
        actions=tuple(actions),
        signatures=tuple(signatures),
        local_mode_ids=tuple(local_ids),
        global_mode_ids=tuple(global_ids),
        mode_mask=tuple(masks),
        recovery_statuses=tuple(statuses),
        metadata=metadata,
    )


__all__ = [
    "DEFAULT_SELECTED_ACTIONS",
    "UNKNOWN_MODE_ID",
    "ModeTargetResult",
    "ReconstructionResult",
    "SelectiveModeTaxonomy",
    "canonicalize_full_event",
    "edit_file_signature",
    "event_mode_signature",
    "fit_selective_taxonomy",
    "full_event_hash",
    "parse_sample_id",
    "read_file_signature",
    "reconstruct_full_events",
    "run_bash_signature",
    "transform_mode_targets",
    "turn_bin",
]
