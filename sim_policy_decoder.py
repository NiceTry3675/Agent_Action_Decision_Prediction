"""Minimal SIM recorded-policy decoder specialist contracts.

The specialist is intentionally narrower than the main ``current_v1`` model:
it sees only the current prompt, the ordered names of preceding assistant
actions, and seven bounded features from the main model posterior.  It is
trained only on SIM rows for which the main model predicts one of Weak4.

No threshold or post-hoc router is part of this module.  Action zero is the
identity operation (KEEP); actions one through four select the corresponding
Weak4 class.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

import torch
import torch.nn.functional as F
from torch import nn

from script import ALL_CLASSES, safe_text


DATASET_FORMAT = "sim-recorded-policy-decoder-dataset-v1"
FOLD_FORMAT = "sim-recorded-policy-decoder-fold-v1"
MODEL_SCHEMA = "hcx-sim-recorded-policy-decoder-v1"
SERIALIZER_NAME = "sim_policy_decoder_v1"
FEATURE_SCHEMA = "main-rowz-weak4-posterior-7d-v1"
DIAGNOSTIC_USAGE_SCOPE = "diagnostic_stitched_oof"
STRICT_USAGE_SCOPE = "strict_nested_main_session_oof"
SUPPORTED_USAGE_SCOPES = (DIAGNOSTIC_USAGE_SCOPE, STRICT_USAGE_SCOPE)

WEAK4_CLASSES = tuple(ALL_CLASSES[:4])
OUTPUT_NAMES = ("KEEP", *WEAK4_CLASSES)
NUMERIC_FEATURE_DIM = 7
NUM_OUTPUTS = len(OUTPUT_NAMES)

# Locked diagnostic budget.  Every fold records a tokenizer-specific length
# audit; encode_sim_policy_sample preserves all action names and spends any
# remaining capacity on a head/tail view of the prompt.
DEFAULT_MAX_LENGTH = 128
DEFAULT_PROMPT_TOKEN_CAP = 192
DEFAULT_PROMPT_HEAD_TOKENS = 128


def session_id_from_sample_id(sample_id: Any) -> str:
    return safe_text(sample_id).split("-step_")[0]


def is_sim_id(sample_id: Any) -> bool:
    return safe_text(sample_id).startswith("sess_sim_")


def stable_inner_fold(session_id: Any, parent_fold: int, n_folds: int, seed: int) -> int:
    """Deterministic session-hash split used inside one held-out parent fold."""

    if n_folds < 2:
        raise ValueError("n_inner_folds must be at least 2")
    payload = f"sim-policy-inner:{seed}:{parent_fold}:{safe_text(session_id)}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % n_folds


def select_sim_weak4_routes(
    ids: Sequence[Any], main_logits: torch.Tensor
) -> torch.Tensor:
    """Return rows that are both SIM and main-top1 Weak4."""

    logits = _validated_main_logits(main_logits)
    if len(ids) != len(logits):
        raise ValueError("ids and main_logits have different row counts")
    pred = logits.argmax(dim=1)
    selected = [
        index
        for index, sample_id in enumerate(ids)
        if is_sim_id(sample_id) and int(pred[index]) < len(WEAK4_CLASSES)
    ]
    return torch.tensor(selected, dtype=torch.long)


def build_recorded_policy_targets(
    y_true: Sequence[int] | torch.Tensor,
    main_predictions: Sequence[int] | torch.Tensor,
) -> torch.Tensor:
    """Map official labels to KEEP/read/grep/list/glob specialist targets.

    The specialist never changes a main Weak4 prediction to a non-Weak4
    action.  A true label outside Weak4 is therefore an explicit KEEP target,
    as is a true label equal to the main prediction.
    """

    truth = torch.as_tensor(y_true, dtype=torch.long).view(-1)
    main = torch.as_tensor(main_predictions, dtype=torch.long).view(-1)
    if len(truth) != len(main):
        raise ValueError("y_true and main_predictions have different lengths")
    if len(main) and not bool(((main >= 0) & (main < len(WEAK4_CLASSES))).all()):
        raise ValueError("specialist targets require main predictions inside Weak4")
    if len(truth) and not bool(((truth >= 0) & (truth < len(ALL_CLASSES))).all()):
        raise ValueError("y_true contains an invalid class id")
    targets = torch.zeros(len(truth), dtype=torch.long)
    switch = (truth < len(WEAK4_CLASSES)) & (truth != main)
    targets[switch] = truth[switch] + 1
    return targets


def _validated_main_logits(main_logits: torch.Tensor) -> torch.Tensor:
    logits = torch.as_tensor(main_logits).detach().float().cpu()
    if logits.ndim != 2 or logits.shape[1] != len(ALL_CLASSES):
        raise ValueError(
            f"main logits must have shape [rows, {len(ALL_CLASSES)}], "
            f"got {tuple(logits.shape)}"
        )
    if not torch.isfinite(logits).all():
        raise ValueError("main logits contain non-finite values")
    return logits


def main_posterior_features(main_logits: torch.Tensor) -> torch.Tensor:
    """Build the fixed seven-dimensional, row-affine-invariant side channel.

    Columns are: four conditional Weak4 probabilities, total Weak4 mass,
    the conditional Weak4 top1-top2 probability margin, and the full-posterior
    top-Weak4 minus best-non-Weak4 probability margin.
    """

    logits = _validated_main_logits(main_logits)
    if not len(logits):
        return torch.empty((0, NUMERIC_FEATURE_DIM), dtype=torch.float32)
    centered = logits - logits.mean(dim=1, keepdim=True)
    scale = centered.square().mean(dim=1, keepdim=True).sqrt()
    normalized = centered / scale.clamp_min(1e-6)
    # A constant row has no relative preference and maps deterministically to
    # all-zero normalized logits rather than amplifying roundoff.
    normalized = torch.where(scale > 1e-6, normalized, torch.zeros_like(normalized))
    probabilities = normalized.softmax(dim=1)
    weak = probabilities[:, : len(WEAK4_CLASSES)]
    weak_mass = weak.sum(dim=1, keepdim=True)
    conditional = weak / weak_mass.clamp_min(torch.finfo(torch.float32).tiny)
    top2 = conditional.topk(2, dim=1).values
    weak_margin = (top2[:, 0] - top2[:, 1]).unsqueeze(1)
    cross_margin = (
        weak.max(dim=1).values - probabilities[:, len(WEAK4_CLASSES) :].max(dim=1).values
    ).unsqueeze(1)
    features = torch.cat(
        [conditional, weak_mass, weak_margin, cross_margin], dim=1
    ).float()
    if features.shape != (len(logits), NUMERIC_FEATURE_DIM):
        raise AssertionError(f"invalid side-channel shape: {tuple(features.shape)}")
    if not torch.isfinite(features).all():
        raise AssertionError("side-channel features are non-finite")
    return features


def action_trajectory(sample: Mapping[str, Any]) -> tuple[str, ...]:
    """Return every preceding assistant action name in chronological order."""

    return tuple(
        safe_text(event.get("name"))
        for event in (sample.get("history") or [])
        if isinstance(event, Mapping) and event.get("role") == "assistant_action"
    )


def _head_tail_ids(
    token_ids: Sequence[int], cap: int, configured_head: int
) -> list[int]:
    ids = [int(value) for value in token_ids]
    if cap < 0:
        raise ValueError("token cap must be nonnegative")
    if configured_head < 0 or configured_head > DEFAULT_PROMPT_TOKEN_CAP:
        raise ValueError("configured prompt head must be in [0, 192]")
    if len(ids) <= cap:
        return ids
    if cap == 0:
        return []
    # Preserve the fixed 128/64 ratio when the total prompt budget has to be
    # reduced further to make room for an unusually long action trajectory.
    head = min(configured_head, int(math.ceil(cap * (2.0 / 3.0))))
    tail = cap - head
    if tail == 0:
        return ids[:head]
    return ids[:head] + ids[-tail:]


def capped_prompt_text(
    prompt: Any,
    tokenizer,
    *,
    token_cap: int = DEFAULT_PROMPT_TOKEN_CAP,
    head_tokens: int = DEFAULT_PROMPT_HEAD_TOKENS,
) -> tuple[str, int, int]:
    """Return tokenizer-aware head/tail prompt text and token counts."""

    text = safe_text(prompt)
    original = tokenizer.encode(text, add_special_tokens=False)
    selected = _head_tail_ids(original, int(token_cap), int(head_tokens))
    if len(selected) == len(original):
        return text, len(original), len(selected)
    decoded = tokenizer.decode(
        selected,
        skip_special_tokens=False,
        clean_up_tokenization_spaces=False,
    )
    return safe_text(decoded), len(original), len(selected)


def serialize_sim_policy_decoder_v1(
    sample: Mapping[str, Any],
    tokenizer,
    *,
    prompt_token_cap: int = DEFAULT_PROMPT_TOKEN_CAP,
    prompt_head_tokens: int = DEFAULT_PROMPT_HEAD_TOKENS,
) -> tuple[str, dict[str, int | bool]]:
    """Serialize only current prompt and ordered action names."""

    prompt, original_tokens, kept_tokens = capped_prompt_text(
        sample.get("current_prompt"),
        tokenizer,
        token_cap=prompt_token_cap,
        head_tokens=prompt_head_tokens,
    )
    trajectory = action_trajectory(sample)
    actions = ">".join(trajectory) if trajectory else "none"
    text = f"<SIM_POLICY>\n<P>{prompt}</P>\n<A>{actions}</A>"
    return text, {
        "prompt_original_tokens": original_tokens,
        "prompt_kept_tokens": kept_tokens,
        "prompt_truncated": kept_tokens < original_tokens,
        "action_count": len(trajectory),
    }


def encode_sim_policy_sample(
    sample: Mapping[str, Any],
    tokenizer,
    *,
    max_length: int = DEFAULT_MAX_LENGTH,
    prompt_token_cap: int = DEFAULT_PROMPT_TOKEN_CAP,
    prompt_head_tokens: int = DEFAULT_PROMPT_HEAD_TOKENS,
) -> tuple[dict[str, list[int]], dict[str, int | bool]]:
    """Encode without ever truncating the ordered action trajectory.

    Prompt capacity is reduced below 192 only when the tags and full action
    trajectory need more room.  A trajectory that cannot fit on its own fails
    loudly rather than silently violating the serializer contract.
    """

    if max_length <= 0:
        raise ValueError("max_length must be positive")
    prompt_cap = int(prompt_token_cap)
    original_prompt_tokens = len(
        tokenizer.encode(safe_text(sample.get("current_prompt")), add_special_tokens=False)
    )
    while True:
        text, audit = serialize_sim_policy_decoder_v1(
            sample,
            tokenizer,
            prompt_token_cap=prompt_cap,
            prompt_head_tokens=prompt_head_tokens,
        )
        encoded = tokenizer(text, add_special_tokens=True, truncation=False)
        input_ids = [int(value) for value in encoded["input_ids"]]
        if len(input_ids) <= max_length:
            result = {key: [int(value) for value in values] for key, values in encoded.items()}
            if "attention_mask" not in result:
                result["attention_mask"] = [1] * len(input_ids)
            audit = dict(audit)
            audit.update(
                prompt_original_tokens=original_prompt_tokens,
                effective_prompt_cap=prompt_cap,
                encoded_tokens=len(input_ids),
                prompt_truncated=int(audit["prompt_kept_tokens"]) < original_prompt_tokens,
            )
            return result, audit
        if prompt_cap == 0:
            raise ValueError(
                "SIM policy tags and complete action trajectory exceed max_length; "
                "refusing to truncate actions"
            )
        overflow = len(input_ids) - max_length
        prompt_cap = max(0, prompt_cap - max(1, overflow))


def target_class_weights(
    targets: Sequence[int] | torch.Tensor, power: float = 0.5
) -> torch.Tensor:
    """Fold-local square-root inverse-frequency weights, mean one by row."""

    labels = torch.as_tensor(targets, dtype=torch.long).view(-1)
    if not 0.0 <= power <= 1.0:
        raise ValueError("class-weight power must be in [0, 1]")
    counts = torch.bincount(labels, minlength=NUM_OUTPUTS).float()
    if bool((counts == 0).any()):
        missing = [OUTPUT_NAMES[index] for index in (counts == 0).nonzero().view(-1).tolist()]
        raise ValueError(f"training fold is missing specialist targets: {missing}")
    raw = counts.pow(-float(power))
    return raw * (len(labels) / (raw * counts).sum())


def specialist_cross_entropy(
    logits: torch.Tensor,
    targets: torch.Tensor,
    class_weights: torch.Tensor,
    label_smoothing: float = 0.02,
) -> torch.Tensor:
    if logits.ndim != 2 or logits.shape[1] != NUM_OUTPUTS:
        raise ValueError(f"specialist logits must have shape [rows, {NUM_OUTPUTS}]")
    return F.cross_entropy(
        logits.float(),
        targets.long(),
        weight=class_weights.float().to(logits.device),
        label_smoothing=float(label_smoothing),
    )


def mask_impossible_main_alternative(
    alt_logits: torch.Tensor,
    main_predictions: Sequence[int] | torch.Tensor,
) -> torch.Tensor:
    """Mask the switch action that would redundantly select main again."""

    values = torch.as_tensor(alt_logits)
    main = torch.as_tensor(main_predictions, dtype=torch.long, device=values.device).view(-1)
    if values.ndim != 2 or values.shape[1] != len(WEAK4_CLASSES):
        raise ValueError("alternative logits must have shape [rows, 4]")
    if len(values) != len(main):
        raise ValueError("alternative logits and main predictions have different rows")
    if len(main) and not bool(((main >= 0) & (main < len(WEAK4_CLASSES))).all()):
        raise ValueError("main prediction is outside Weak4")
    masked = values.clone()
    if len(masked):
        masked[
            torch.arange(len(masked), device=masked.device), main
        ] = -torch.inf
    return masked


def hierarchical_policy_loss(
    raw_outputs: torch.Tensor,
    targets: Sequence[int] | torch.Tensor,
    main_predictions: Sequence[int] | torch.Tensor,
    *,
    conditional_loss_weight: float = 0.5,
    gate_pos_weight: float = 1.0,
    label_smoothing: float = 0.02,
) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
    """Binary KEEP/SWITCH gate plus switch-conditional valid-3 CE.

    Raw output zero is the gate logit; outputs one through four are conditional
    Weak4 alternative logits.  Label smoothing is distributed over exactly the
    three valid alternatives after masking the route's main class.
    """

    raw = torch.as_tensor(raw_outputs)
    target = torch.as_tensor(targets, dtype=torch.long, device=raw.device).view(-1)
    main = torch.as_tensor(
        main_predictions, dtype=torch.long, device=raw.device
    ).view(-1)
    if raw.ndim != 2 or raw.shape[1] != NUM_OUTPUTS:
        raise ValueError(f"raw_outputs must have shape [rows, {NUM_OUTPUTS}]")
    if not (len(raw) == len(target) == len(main)):
        raise ValueError("hierarchical loss inputs have different row counts")
    if conditional_loss_weight < 0:
        raise ValueError("conditional_loss_weight must be nonnegative")
    if gate_pos_weight <= 0:
        raise ValueError("gate_pos_weight must be positive")
    if not 0.0 <= label_smoothing < 1.0:
        raise ValueError("label_smoothing must be in [0, 1)")
    if len(target) and not bool(((target >= 0) & (target < NUM_OUTPUTS)).all()):
        raise ValueError("hierarchical target is out of range")

    switch = target != 0
    gate_target = switch.to(raw.dtype)
    gate_loss = F.binary_cross_entropy_with_logits(
        raw[:, 0],
        gate_target,
        pos_weight=torch.tensor(float(gate_pos_weight), dtype=raw.dtype, device=raw.device),
    )
    masked_alt = mask_impossible_main_alternative(raw[:, 1:], main)
    conditional_log_probs = F.log_softmax(masked_alt.float(), dim=1)
    if bool(switch.any()):
        switch_rows = switch.nonzero(as_tuple=False).view(-1)
        conditional_target = target[switch_rows] - 1
        if bool((conditional_target == main[switch_rows]).any()):
            raise ValueError("switch target redundantly selects the main class")
        selected = conditional_log_probs[switch_rows]
        nll = -selected[
            torch.arange(len(switch_rows), device=raw.device), conditional_target
        ]
        valid = torch.ones_like(selected, dtype=torch.bool)
        valid[
            torch.arange(len(switch_rows), device=raw.device), main[switch_rows]
        ] = False
        smooth = -(selected.masked_fill(~valid, 0.0).sum(dim=1) / 3.0)
        conditional_loss = (
            (1.0 - float(label_smoothing)) * nll
            + float(label_smoothing) * smooth
        ).mean()
    else:
        # Keep a differentiable exact zero when a small batch has no switches.
        conditional_loss = raw[:, 1:].sum() * 0.0
    total = gate_loss + float(conditional_loss_weight) * conditional_loss
    return total, {
        "gate_loss": gate_loss,
        "conditional_loss": conditional_loss,
        "masked_alt_logits": masked_alt,
        "switch_mask": switch,
    }


def hierarchical_joint_log_probabilities(
    raw_outputs: torch.Tensor,
    main_predictions: Sequence[int] | torch.Tensor,
) -> torch.Tensor:
    """Factorized joint log-probabilities for KEEP and four actions."""

    raw = torch.as_tensor(raw_outputs)
    if raw.ndim != 2 or raw.shape[1] != NUM_OUTPUTS:
        raise ValueError(f"raw_outputs must have shape [rows, {NUM_OUTPUTS}]")
    gate = raw[:, 0]
    masked_alt = mask_impossible_main_alternative(raw[:, 1:], main_predictions)
    conditional = F.log_softmax(masked_alt.float(), dim=1)
    keep = F.logsigmoid(-gate.float()).unsqueeze(1)
    switch = F.logsigmoid(gate.float()).unsqueeze(1) + conditional
    joint = torch.cat([keep, switch], dim=1)
    if torch.isnan(joint).any():
        raise AssertionError("hierarchical joint log-probabilities contain NaN")
    # The impossible same-main action has exact log-probability -inf during
    # the conditional softmax. Fold artifacts require finite logits, so use
    # a sentinel that stays an exact zero after float32 exponentiation and
    # can never win argmax.
    joint = torch.nan_to_num(joint, neginf=-1.0e9)
    if not torch.isfinite(joint).all():
        raise AssertionError("hierarchical joint log-probabilities are non-finite")
    return joint


def apply_specialist_actions(
    parent_logits: torch.Tensor,
    route_indices: Sequence[int] | torch.Tensor,
    specialist_actions: Sequence[int] | torch.Tensor,
) -> torch.Tensor:
    """Apply KEEP/Weak4 actions and leave every non-route row untouched."""

    logits = _validated_main_logits(parent_logits)
    baseline = logits.argmax(dim=1)
    rows = torch.as_tensor(route_indices, dtype=torch.long).view(-1)
    actions = torch.as_tensor(specialist_actions, dtype=torch.long).view(-1)
    if len(rows) != len(actions):
        raise ValueError("route_indices and specialist_actions have different lengths")
    if len(rows):
        if int(rows.min()) < 0 or int(rows.max()) >= len(logits):
            raise ValueError("route index is out of range")
        if len(set(rows.tolist())) != len(rows):
            raise ValueError("route_indices contains duplicates")
        if not bool(((actions >= 0) & (actions < NUM_OUTPUTS)).all()):
            raise ValueError("specialist action is out of range")
        if not bool((baseline[rows] < len(WEAK4_CLASSES)).all()):
            raise ValueError("specialist route contains a non-Weak4 main prediction")
    prediction = baseline.clone()
    switch = actions > 0
    prediction[rows[switch]] = actions[switch] - 1
    mask = torch.zeros(len(logits), dtype=torch.bool)
    mask[rows] = True
    if not torch.equal(prediction[~mask], baseline[~mask]):
        raise AssertionError("specialist changed a non-route prediction")
    return prediction


def project_predictions_to_logits(
    parent_logits: torch.Tensor, predictions: Sequence[int] | torch.Tensor
) -> torch.Tensor:
    """Promote changed predictions by one float32 ULP for OOF composition."""

    logits = _validated_main_logits(parent_logits)
    pred = torch.as_tensor(predictions, dtype=torch.long).view(-1)
    if len(pred) != len(logits):
        raise ValueError("predictions and parent_logits have different lengths")
    if len(pred) and not bool(((pred >= 0) & (pred < len(ALL_CLASSES))).all()):
        raise ValueError("predictions contains an invalid class id")
    adjusted = logits.clone()
    baseline = logits.argmax(dim=1)
    changed = (pred != baseline).nonzero(as_tuple=False).view(-1)
    positive_inf = torch.full_like(changed, float("inf"), dtype=torch.float32)
    for position, row in enumerate(changed.tolist()):
        maximum = adjusted[row].max()
        adjusted[row, int(pred[row])] = torch.nextafter(maximum, positive_inf[position])
    if not torch.equal(adjusted.argmax(dim=1), pred):
        raise AssertionError("projected logits do not reproduce specialist predictions")
    return adjusted


def pool_last_nonpadding(
    hidden_states: torch.Tensor, attention_mask: torch.Tensor
) -> torch.Tensor:
    if hidden_states.ndim != 3 or attention_mask.ndim != 2:
        raise ValueError("hidden_states/attention_mask ranks must be 3/2")
    if hidden_states.shape[:2] != attention_mask.shape:
        raise ValueError("hidden_states and attention_mask shapes do not align")
    mask = attention_mask.to(hidden_states.device).bool()
    if not bool(mask.any(dim=1).all()):
        raise ValueError("every input must contain at least one non-padding token")
    positions = torch.arange(hidden_states.shape[1], device=hidden_states.device)
    last = positions.unsqueeze(0).expand_as(mask).masked_fill(~mask, -1).max(dim=1).values
    return hidden_states[torch.arange(len(hidden_states), device=hidden_states.device), last]


class SimPolicyDecoderModel(nn.Module):
    """End-to-end decoder encoder plus a 7d posterior side-channel head."""

    def __init__(
        self,
        encoder: nn.Module,
        *,
        numeric_dim: int = NUMERIC_FEATURE_DIM,
        fusion_dim: int = 256,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.encoder = encoder
        hidden_size = int(getattr(encoder.config, "hidden_size"))
        self.numeric_mlp = nn.Sequential(
            nn.Linear(numeric_dim, 32),
            nn.GELU(),
            nn.LayerNorm(32),
        )
        self.fusion = nn.Sequential(
            nn.Linear(hidden_size + 32, fusion_dim),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.output = nn.Linear(fusion_dim, NUM_OUTPUTS)
        # Exact identity at initialization: tied zero logits use class zero,
        # which is KEEP under torch.argmax's stable lower-index tie break.
        nn.init.zeros_(self.output.weight)
        nn.init.zeros_(self.output.bias)

    def forward(
        self, *, numeric_features: torch.Tensor, attention_mask: torch.Tensor, **encoder_inputs
    ) -> torch.Tensor:
        outputs = self.encoder(
            attention_mask=attention_mask,
            **encoder_inputs,
            return_dict=True,
        )
        hidden = pool_last_nonpadding(outputs.last_hidden_state, attention_mask)
        numeric = self.numeric_mlp(numeric_features.float())
        return self.output(self.fusion(torch.cat([hidden, numeric], dim=1)))


def target_histogram(targets: Iterable[int]) -> dict[str, int]:
    counts = Counter(int(value) for value in targets)
    return {name: counts.get(index, 0) for index, name in enumerate(OUTPUT_NAMES)}


def ids_sha256(ids: Sequence[Any]) -> str:
    payload = "\n".join(safe_text(value) for value in ids).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
