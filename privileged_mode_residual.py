"""Centered action-specific residual head for privileged event modes."""

from collections.abc import Mapping

import torch
import torch.nn.functional as F


class SelectiveModeResidual(torch.nn.Module):
    """Add zero-initialized mixture corrections to selected parent logits.

    Each selected action must have at least two stable modes.  Non-selected
    actions remain byte-for-byte equal to the supplied parent logits.
    """

    def __init__(self, hidden_size, num_actions, priors_by_action):
        super().__init__()
        self.hidden_size = int(hidden_size)
        self.num_actions = int(num_actions)
        self.mode_heads = torch.nn.ModuleDict()
        self._action_ids = []

        if not isinstance(priors_by_action, Mapping):
            raise TypeError("priors_by_action must be a mapping")
        for raw_action_id, raw_prior in sorted(
            priors_by_action.items(), key=lambda item: int(item[0])
        ):
            action_id = int(raw_action_id)
            if action_id < 0 or action_id >= self.num_actions:
                raise ValueError(f"action id out of range: {action_id}")
            prior = torch.as_tensor(raw_prior, dtype=torch.float32)
            if prior.ndim != 1 or len(prior) < 2:
                raise ValueError(
                    f"selected action {action_id} needs at least two modes, got {tuple(prior.shape)}"
                )
            if not bool(torch.isfinite(prior).all()) or bool((prior <= 0).any()):
                raise ValueError(f"action {action_id} prior must be finite and positive")
            prior = prior / prior.sum()
            key = str(action_id)
            head = torch.nn.Linear(self.hidden_size, len(prior), bias=True)
            torch.nn.init.zeros_(head.weight)
            torch.nn.init.zeros_(head.bias)
            self.mode_heads[key] = head
            self.register_buffer(f"log_prior_{action_id}", prior.log())
            self._action_ids.append(action_id)

    @property
    def action_ids(self):
        return tuple(self._action_ids)

    def log_prior(self, action_id):
        action_id = int(action_id)
        if action_id not in self._action_ids:
            raise KeyError(f"action {action_id} has no mode branch")
        return getattr(self, f"log_prior_{action_id}")

    def mode_logits(self, hidden, action_id):
        action_id = int(action_id)
        residual = self.mode_heads[str(action_id)](hidden.float())
        return residual + self.log_prior(action_id)

    def corrections(self, hidden):
        hidden = hidden.float()
        correction = hidden.new_zeros((len(hidden), self.num_actions))
        for action_id in self._action_ids:
            log_prior = self.log_prior(action_id)
            residual = self.mode_heads[str(action_id)](hidden)
            # The centered pair is deliberately evaluated in float32.  At
            # zero initialization both logsumexp inputs are identical, making
            # the correction exactly zero rather than merely close to zero.
            mixed = torch.logsumexp(log_prior + residual, dim=-1)
            baseline = torch.logsumexp(log_prior, dim=-1)
            correction[:, action_id] = mixed - baseline
        return correction

    def forward(self, hidden, parent_logits):
        if parent_logits.ndim != 2 or parent_logits.shape[1] != self.num_actions:
            raise ValueError(
                f"parent_logits must be [N,{self.num_actions}], got {tuple(parent_logits.shape)}"
            )
        if hidden.ndim != 2 or hidden.shape != (len(parent_logits), self.hidden_size):
            raise ValueError(
                f"hidden must be [N,{self.hidden_size}], got {tuple(hidden.shape)}"
            )
        return parent_logits.float() + self.corrections(hidden)


def conditional_mode_loss_values(
    model,
    hidden,
    mode_action_ids,
    mode_local_ids,
    normalization="prior_entropy",
):
    """Return one conditional CE value per row; unknown rows remain zero."""

    action_ids = torch.as_tensor(mode_action_ids, dtype=torch.long, device=hidden.device)
    local_ids = torch.as_tensor(mode_local_ids, dtype=torch.long, device=hidden.device)
    if action_ids.shape != (len(hidden),) or local_ids.shape != (len(hidden),):
        raise ValueError("mode action/local target tensors must have one value per hidden row")
    values = hidden.new_zeros(len(hidden), dtype=torch.float32)
    known = (action_ids >= 0) & (local_ids >= 0)
    for action_id in model.action_ids:
        mask = known & (action_ids == action_id)
        if not bool(mask.any()):
            continue
        logits = model.mode_logits(hidden[mask], action_id)
        targets = local_ids[mask]
        if bool((targets >= logits.shape[1]).any()):
            raise ValueError(f"mode target out of range for action {action_id}")
        loss = F.cross_entropy(logits, targets, reduction="none")
        if normalization == "prior_entropy":
            log_prior = model.log_prior(action_id)
            entropy = (-(log_prior.exp() * log_prior).sum()).clamp_min(1e-6)
            loss = loss / entropy
        elif normalization == "log_k":
            loss = loss / torch.log(
                torch.tensor(logits.shape[1], dtype=torch.float32, device=hidden.device)
            )
        elif normalization != "none":
            raise ValueError(f"unknown mode-loss normalization: {normalization}")
        values[mask] = loss
    return values, known


def combine_kd_and_mode_loss(
    action_loss,
    kd_loss,
    alpha,
    mode_loss,
    mode_known,
    mode_weight,
    sample_weight=None,
):
    """Apply KD interpolation while keeping privileged mode CE outside it."""

    action_loss = action_loss.float()
    kd_loss = kd_loss.float()
    alpha = alpha.float()
    mode_loss = mode_loss.float()
    mode_known = mode_known.to(dtype=torch.float32)
    if not (
        action_loss.shape
        == kd_loss.shape
        == alpha.shape
        == mode_loss.shape
        == mode_known.shape
    ):
        raise ValueError("all loss inputs must have the same per-row shape")
    total = (1.0 - alpha) * action_loss + alpha * kd_loss
    total = total + float(mode_weight) * mode_known * mode_loss
    if sample_weight is None:
        return total
    sample_weight = sample_weight.float()
    if sample_weight.shape != total.shape:
        raise ValueError("sample_weight must have the same per-row shape")
    return total * sample_weight
