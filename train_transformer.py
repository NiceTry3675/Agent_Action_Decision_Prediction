import argparse
import copy
import hashlib
import json
import math
import random
import re
import shutil
import shlex
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import torch
import torch.nn.functional as F
from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

from script import ALL_CLASSES, load_jsonl, safe_text, serialize_transformer_sample
from train import (
    CLASS_TO_ID,
    append_results_csv,
    f1_metrics,
    load_labels,
    predict_with_bias,
    session_id,
    split_indices,
    tune_class_bias,
    tune_class_bias_two_stage,
)

EXPLORER4_CLASSES = [
    "list_directory",
    "read_file",
    "grep_search",
    "glob_pattern",
]
EXPLORER4_CLASS_IDS = [ALL_CLASSES.index(label) for label in EXPLORER4_CLASSES]
EXPLORER4_CLASS_ID_SET = set(EXPLORER4_CLASS_IDS)
WEAK4_CLASSES = ALL_CLASSES[:4]
WEAK4_CLASS_IDS = list(range(4))
WEAK4_CLASS_ID_SET = set(WEAK4_CLASS_IDS)


def safe_slug(value):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-")
    return value or "none"


def torch_load(path):
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def class_weights(y, device, power):
    counts = Counter(y)
    total = len(y)
    present = sorted(class_id for class_id, count in counts.items() if count > 0)
    if not present:
        raise ValueError("cannot compute class weights from an empty label set")
    weights = [0.0] * len(ALL_CLASSES)
    for class_id in present:
        weights[class_id] = (total / (len(present) * counts[class_id])) ** power
    mean = sum(weights[class_id] for class_id in present) / len(present)
    for class_id in present:
        weights[class_id] /= mean
    return torch.tensor(weights, dtype=torch.float32, device=device)


def classification_loss_values(
    logits,
    labels,
    weights,
    label_smoothing,
    loss_name,
    focal_gamma,
    class_ids=None,
):
    loss_logits = logits.float()
    if class_ids is not None:
        loss_logits = loss_logits[:, class_ids]
    loss_values = F.cross_entropy(
        loss_logits,
        labels,
        weight=weights,
        label_smoothing=label_smoothing,
        reduction="none",
    )
    if loss_name == "focal":
        pt = F.log_softmax(loss_logits, dim=-1).gather(1, labels.view(-1, 1)).squeeze(1).exp()
        loss_values = (1.0 - pt) ** focal_gamma * loss_values
    elif loss_name != "ce":
        raise ValueError(f"unknown loss: {loss_name}")
    return loss_values


def select_balanced_subset(indices, y, size, seed):
    if not size or size >= len(indices):
        return indices
    rng = random.Random(seed)
    by_label = {}
    for idx in indices:
        by_label.setdefault(y[idx], []).append(idx)
    for label_indices in by_label.values():
        rng.shuffle(label_indices)

    selected = []
    labels = list(by_label)
    cursor = 0
    while len(selected) < size and labels:
        label = labels[cursor % len(labels)]
        if by_label[label]:
            selected.append(by_label[label].pop())
        labels = [label_id for label_id in labels if by_label[label_id]]
        cursor += 1
    rng.shuffle(selected)
    return selected


def session_oof_split_indices(samples, y, n_folds, fold_id, seed):
    if n_folds < 2:
        raise ValueError("--n-folds must be at least 2 for session_oof")
    if fold_id < 0 or fold_id >= n_folds:
        raise ValueError(f"--fold-id must be in [0, {n_folds - 1}] for session_oof")

    groups = {}
    for idx, sample in enumerate(samples):
        groups.setdefault(session_id(sample.get("id", "")), []).append(idx)

    rng = random.Random(seed)
    group_ids = list(groups)
    rng.shuffle(group_ids)
    group_ids.sort(key=lambda group_id: len(groups[group_id]), reverse=True)

    total_label_counts = Counter(y)
    target_label_counts = {
        label_id: count / float(n_folds)
        for label_id, count in total_label_counts.items()
    }
    target_size = len(samples) / float(n_folds)
    fold_label_counts = [Counter() for _ in range(n_folds)]
    fold_sizes = [0 for _ in range(n_folds)]
    fold_groups = [set() for _ in range(n_folds)]

    for group_id in group_ids:
        indices = groups[group_id]
        group_counts = Counter(y[idx] for idx in indices)
        group_size = len(indices)
        best_fold = None
        best_score = None
        for fold in range(n_folds):
            size_before = fold_sizes[fold]
            size_after = fold_sizes[fold] + group_size
            size_before_score = ((size_before - target_size) ** 2) / max(1.0, target_size)
            size_after_score = ((size_after - target_size) ** 2) / max(1.0, target_size)
            label_before_score = 0.0
            label_after_score = 0.0
            for label_id, target in target_label_counts.items():
                before = fold_label_counts[fold][label_id]
                after = fold_label_counts[fold][label_id] + group_counts[label_id]
                label_before_score += ((before - target) ** 2) / max(1.0, target)
                label_after_score += ((after - target) ** 2) / max(1.0, target)
            score = (label_after_score - label_before_score) + 0.2 * (size_after_score - size_before_score)
            if best_score is None or score < best_score:
                best_score = score
                best_fold = fold
        fold_groups[best_fold].add(group_id)
        fold_sizes[best_fold] += group_size
        fold_label_counts[best_fold].update(group_counts)

    val_groups = fold_groups[fold_id]
    train_idx = []
    val_idx = []
    for group_id, indices in groups.items():
        if group_id in val_groups:
            val_idx.extend(indices)
        else:
            train_idx.extend(indices)
    rng.shuffle(train_idx)
    rng.shuffle(val_idx)
    return train_idx, val_idx


def split_for_args(samples, y, args):
    if args.split == "session_oof":
        return session_oof_split_indices(samples, y, args.n_folds, args.fold_id, args.seed)
    return split_indices(samples, y, args.split, args.seed)


def balanced_cap_replay(examples, max_count, seed):
    if not max_count or len(examples) <= max_count:
        return examples
    rng = random.Random(seed)
    by_label = {}
    for label_id, replay_sample in examples:
        by_label.setdefault(label_id, []).append((label_id, replay_sample))
    for label_examples in by_label.values():
        rng.shuffle(label_examples)

    selected = []
    labels = list(by_label)
    cursor = 0
    while len(selected) < max_count and labels:
        label = labels[cursor % len(labels)]
        if by_label[label]:
            selected.append(by_label[label].pop())
        labels = [label_id for label_id in labels if by_label[label_id]]
        cursor += 1
    rng.shuffle(selected)
    return selected


def replay_examples_for_sample(sample, pair_limit):
    history = sample.get("history") or []
    candidates = []
    for idx, event in enumerate(history[:-1]):
        if event.get("role") != "user":
            continue
        next_event = history[idx + 1]
        if next_event.get("role") == "assistant_action":
            label = safe_text(next_event.get("name"))
            if label in CLASS_TO_ID:
                candidates.append((idx, event, next_event, label))

    replay_samples = []
    for idx, user_event, target_event, label in candidates[-pair_limit:]:
        replay_samples.append(
            (
                CLASS_TO_ID[label],
                {
                    "id": f"{safe_text(sample.get('id'))}::replay_{idx}_{label}",
                    "_is_replay": True,
                    "session_meta": sample.get("session_meta") or {},
                    "history": history[:idx],
                    "current_prompt": safe_text(user_event.get("content")),
                    # Training-only target metadata.  Serializers intentionally
                    # ignore private keys; only the privileged mode label
                    # builder may read this event.
                    "_privileged_target_event": {
                        "name": label,
                        "args": copy.deepcopy(target_event.get("args") or {}),
                        "result_summary": safe_text(target_event.get("result_summary")),
                    },
                },
            )
        )
    return replay_samples


def add_replay_examples(samples, y, train_idx, args):
    sample_weights = [1.0] * len(samples)
    if args.replay_mode == "none":
        return samples, y, train_idx, sample_weights, 0
    if args.split not in ("session", "session_oof"):
        raise ValueError("Replay augmentation is only enabled for session-aware splits to avoid session leakage.")

    pair_limit = {"last1": 1, "last2": 2}[args.replay_mode]
    replay_examples = []
    for sample_idx in train_idx:
        replay_examples.extend(replay_examples_for_sample(samples[sample_idx], pair_limit))
    replay_examples = balanced_cap_replay(replay_examples, args.max_replay_samples, args.seed + 101)

    start_idx = len(samples)
    replay_samples = [sample for _, sample in replay_examples]
    replay_y = [label_id for label_id, _ in replay_examples]
    new_samples = samples + replay_samples
    new_y = y + replay_y
    replay_idx = list(range(start_idx, start_idx + len(replay_samples)))
    new_train_idx = train_idx + replay_idx
    sample_weights.extend([args.replay_sample_weight] * len(replay_samples))
    print(
        f"replay mode={args.replay_mode} generated={len(replay_samples)} "
        f"cap={args.max_replay_samples} weight={args.replay_sample_weight}"
    )
    return new_samples, new_y, new_train_idx, sample_weights, len(replay_samples)


def filter_train_indices(train_idx, y, mode):
    if mode == "none":
        return list(train_idx)
    if mode != "weak4":
        raise ValueError(f"unknown train label filter: {mode}")
    filtered = [idx for idx in train_idx if y[idx] in WEAK4_CLASS_ID_SET]
    if not filtered:
        raise ValueError("--train-label-filter weak4 selected zero training rows")
    counts = Counter(y[idx] for idx in filtered)
    missing = [label for label_id, label in enumerate(WEAK4_CLASSES) if counts[label_id] == 0]
    if missing:
        raise ValueError(f"weak4 training split is missing labels: {missing}")
    print(
        "train label filter=weak4 "
        f"kept={len(filtered)}/{len(train_idx)} "
        f"counts={{{', '.join(f'{WEAK4_CLASSES[i]}:{counts[i]}' for i in WEAK4_CLASS_IDS)}}}"
    )
    return filtered


def specialist_optimizer_groups(model, weight_decay, train_label_filter):
    named = [(name, param) for name, param in model.named_parameters() if param.requires_grad]
    if train_label_filter != "weak4":
        return [param for _, param in named]
    score_params = [param for name, param in named if ".score." in f".{name}."]
    other_params = [param for name, param in named if ".score." not in f".{name}."]
    if not score_params:
        raise ValueError("weak4 specialist found no trainable score parameters")
    groups = []
    if other_params:
        groups.append({"params": other_params, "weight_decay": weight_decay})
    groups.append({"params": score_params, "weight_decay": 0.0})
    return groups


def validate_specialist_warmstart(args):
    if args.train_label_filter != "weak4":
        return
    if not args.resume_from:
        raise ValueError("Weak4 specialist training requires --resume-from")
    resume_dir = Path(args.resume_from)
    meta_path = resume_dir.parent / "hf_meta.json" if resume_dir.name == "hf_model" else resume_dir / "hf_meta.json"
    if not meta_path.is_file():
        raise ValueError(f"Weak4 warm-start metadata is missing: {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if list(meta.get("classes") or []) != ALL_CLASSES:
        raise ValueError("Weak4 warm-start class order does not match ALL_CLASSES")
    if meta.get("base_model") != args.base_model:
        raise ValueError(
            f"Weak4 warm-start base_model mismatch: {meta.get('base_model')!r} != {args.base_model!r}"
        )
    if not bool(meta.get("saved_fp16", False)):
        raise ValueError("Weak4 warm-start must be a saved fp16 artifact")
    is_final_refit = bool(meta.get("final_refit", False))
    if args.final_only:
        if not is_final_refit:
            raise ValueError("Weak4 final-only training must warm-start from the final refit")
    else:
        if is_final_refit:
            raise ValueError("Weak4 screen must not warm-start from a final refit that saw validation")
        if meta.get("validation_split") != "session":
            raise ValueError("Weak4 screen warm-start must use the fixed session validation split")
    print(
        f"Weak4 warm-start OK: {resume_dir} final_refit={is_final_refit} "
        f"saved_fp16={meta.get('saved_fp16')}"
    )


def assert_validation_anchor(samples, y, train_idx, val_idx, args):
    if not args.assert_val_ids:
        return
    payload = torch_load(args.assert_val_ids)
    anchor_classes = list(payload.get("classes") or [])
    if anchor_classes != ALL_CLASSES:
        raise ValueError(
            f"validation anchor classes mismatch: expected={ALL_CLASSES} actual={anchor_classes}"
        )
    if payload.get("split") != args.split:
        raise ValueError(
            f"validation anchor split mismatch: expected={args.split} actual={payload.get('split')}"
        )
    if int(payload.get("seed", -1)) != int(args.seed):
        raise ValueError(
            f"validation anchor seed mismatch: expected={args.seed} actual={payload.get('seed')}"
        )

    anchor_ids = [safe_text(sample_id) for sample_id in payload.get("ids") or []]
    val_ids = [safe_text(samples[idx].get("id")) for idx in val_idx]
    if len(anchor_ids) != len(set(anchor_ids)):
        raise ValueError("validation anchor contains duplicate ids")
    if len(val_ids) != len(set(val_ids)):
        raise ValueError("computed validation split contains duplicate ids")
    if set(anchor_ids) != set(val_ids):
        missing = sorted(set(anchor_ids) - set(val_ids))[:5]
        extra = sorted(set(val_ids) - set(anchor_ids))[:5]
        raise ValueError(
            "validation ids do not match anchor: "
            f"anchor={len(anchor_ids)} computed={len(val_ids)} missing={missing} extra={extra}"
        )

    train_ids = {safe_text(samples[idx].get("id")) for idx in train_idx}
    overlap = train_ids & set(anchor_ids)
    if overlap:
        raise ValueError(f"training ids overlap anchor validation ids: {sorted(overlap)[:5]}")

    anchor_y = payload.get("y_true")
    if anchor_y is not None:
        anchor_labels = {sample_id: int(label) for sample_id, label in zip(anchor_ids, anchor_y)}
        mismatched = [
            sample_id
            for sample_id, idx in zip(val_ids, val_idx)
            if anchor_labels.get(sample_id) != int(y[idx])
        ]
        if mismatched:
            raise ValueError(f"validation labels do not match anchor for ids: {mismatched[:5]}")
    print(
        f"validation anchor OK: ids={len(val_ids)} train_disjoint={len(train_ids)} "
        f"split={args.split} seed={args.seed}"
    )


def make_batches(indices, batch_size, rng=None, lengths=None, bucket_multiplier=1):
    indices = indices[:]
    if rng is not None:
        rng.shuffle(indices)
    if lengths is None or bucket_multiplier <= 1:
        for start in range(0, len(indices), batch_size):
            yield indices[start:start + batch_size]
        return

    bucket_size = max(batch_size, batch_size * bucket_multiplier)
    for bucket_start in range(0, len(indices), bucket_size):
        bucket = indices[bucket_start:bucket_start + bucket_size]
        bucket.sort(key=lambda idx: lengths[idx], reverse=True)
        for start in range(0, len(bucket), batch_size):
            yield bucket[start:start + batch_size]


def cache_path(args, source_path, sample_count, kind, cache_scope="train"):
    source_path = Path(source_path)
    try:
        stamp = source_path.stat().st_mtime_ns
    except FileNotFoundError:
        stamp = 0
    base_model = safe_slug(args.base_model)
    serializer = safe_slug(args.serializer)
    replay = ""
    if getattr(args, "replay_mode", "none") != "none":
        replay = (
            f"_replay-{safe_slug(args.replay_mode)}-n{args.max_replay_samples}"
            f"-w{safe_slug(args.replay_sample_weight)}-scope-{safe_slug(cache_scope)}"
            f"-seed{args.seed}"
        )
        if getattr(args, "split", "") == "session_oof":
            replay += f"-oof{args.fold_id}of{args.n_folds}"
    return (
        Path(args.cache_dir)
        / f"{kind}_{base_model}_{serializer}{replay}_{source_path.stem}_n{sample_count}_m{stamp}_len{args.max_length}.pt"
    )


def build_serialized_texts(
    samples, args, source_path, cache_scope="train", tokenizer=None
):
    path = cache_path(args, source_path, len(samples), "texts", cache_scope)
    if not args.no_text_cache and path.exists() and not args.rebuild_cache:
        payload = torch_load(path)
        if payload.get("serializer_name") == args.serializer and len(payload.get("texts", [])) == len(samples):
            print(f"loaded serialized text cache: {path}")
            return payload["texts"], path

    start = time.perf_counter()
    texts = [
        serialize_transformer_sample(
            sample, args.serializer, tokenizer=tokenizer
        )
        for sample in samples
    ]
    elapsed = time.perf_counter() - start
    print(f"serialized texts={len(texts)} serializer={args.serializer} elapsed={elapsed:.2f}s")
    if not args.no_text_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "serializer_name": args.serializer,
                "texts": texts,
                "sample_count": len(samples),
                "created_utc": datetime.now(timezone.utc).isoformat(),
            },
            path,
        )
        print(f"saved serialized text cache: {path}")
    return texts, path if not args.no_text_cache else ""


def tokenize_texts(tokenizer, texts, args, source_path, cache_scope="train"):
    path = cache_path(args, source_path, len(texts), "tokens", cache_scope)
    if not args.no_token_cache and path.exists() and not args.rebuild_cache:
        payload = torch_load(path)
        meta = payload.get("meta", {})
        if (
            meta.get("base_model") == args.base_model
            and meta.get("serializer_name") == args.serializer
            and int(meta.get("max_length", -1)) == args.max_length
            and len(payload.get("features", [])) == len(texts)
        ):
            print(f"loaded token cache: {path}")
            return payload["features"], payload["lengths"], path

    start = time.perf_counter()
    features = []
    chunk_size = max(1, args.tokenize_batch_size)
    total_chunks = math.ceil(len(texts) / chunk_size) if texts else 0
    for chunk_no, chunk_start in enumerate(range(0, len(texts), chunk_size), 1):
        chunk_texts = texts[chunk_start:chunk_start + chunk_size]
        encoded = tokenizer(chunk_texts, padding=False, truncation=True, max_length=args.max_length)
        keys = list(encoded.keys())
        features.extend(
            {key: encoded[key][idx] for key in keys}
            for idx in range(len(chunk_texts))
        )
        if total_chunks > 1 and (chunk_no == 1 or chunk_no == total_chunks or chunk_no % 10 == 0):
            print(f"  tokenized chunk {chunk_no}/{total_chunks} samples={len(features)}")
    lengths = [len(feature["input_ids"]) for feature in features]
    elapsed = time.perf_counter() - start
    print(f"tokenized samples={len(features)} max_length={args.max_length} elapsed={elapsed:.2f}s")
    if not args.no_token_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "features": features,
                "lengths": lengths,
                "meta": {
                    "base_model": args.base_model,
                    "serializer_name": args.serializer,
                    "max_length": args.max_length,
                    "tokenize_batch_size": args.tokenize_batch_size,
                    "sample_count": len(texts),
                    "created_utc": datetime.now(timezone.utc).isoformat(),
                },
            },
            path,
        )
        print(f"saved token cache: {path}")
    return features, lengths, path if not args.no_token_cache else ""


def make_encoded_batch(tokenizer, encoded_features, batch_idx, args, device):
    features = [encoded_features[i] for i in batch_idx]
    encoded = tokenizer.pad(
        features,
        padding=True,
        pad_to_multiple_of=args.pad_to_multiple_of if args.pad_to_multiple_of > 1 else None,
        return_tensors="pt",
    )
    return {key: value.to(device, non_blocking=True) for key, value in encoded.items()}


def evaluate(model, tokenizer, encoded_features, lengths, y, indices, args, device):
    model.eval()
    logits_parts = []
    ordered_indices = []
    with torch.inference_mode():
        for batch_idx in make_batches(
            indices,
            args.eval_batch_size,
            lengths=lengths,
            bucket_multiplier=args.eval_bucket_multiplier,
        ):
            encoded = make_encoded_batch(tokenizer, encoded_features, batch_idx, args, device)
            with torch.amp.autocast(device_type="cuda", enabled=device.type == "cuda", dtype=torch.bfloat16 if args.bf16 else torch.float16):
                logits = model(**encoded).logits.float()
            logits_parts.append(logits.detach().cpu())
            ordered_indices.extend(batch_idx)
    logits = torch.cat(logits_parts, dim=0)
    y_true = [y[i] for i in ordered_indices]
    pred = torch.argmax(logits, dim=1).tolist()
    metrics = f1_metrics(y_true, pred)
    return logits, y_true, metrics, ordered_indices


def build_teacher_targets(samples, args):
    """Align an OOF teacher payload (ids + log-prob logits) to `samples` by id.
    Returns (logprobs, mask) CPU tensors or None when --distill-logits is unset.
    Replay pseudo-samples and unmatched ids get mask 0 (pure hard-label loss),
    so OOF teachers stay leak-free by construction.

    The mask is a per-row alpha SCALE, not just 0/1: the loss uses
    alpha_row = --distill-alpha * mask, so with --distill-alpha-weak set,
    teacher-matched original rows whose true label is Weak4 carry
    mask = alpha_weak/alpha (team condalpha semantics: matched Weak4-true rows
    get alpha_weak, other matched rows alpha, replay/unmatched stay 0)."""
    if not getattr(args, "distill_logits", None):
        return None
    payload = torch.load(args.distill_logits, map_location="cpu", weights_only=False)
    teacher_rows = payload["logits"].float()
    by_id = {safe_text(sample_id): row for sample_id, row in zip(payload["ids"], teacher_rows)}
    logprobs = torch.zeros(len(samples), teacher_rows.shape[1])
    mask = torch.zeros(len(samples))
    for i, sample in enumerate(samples):
        row = by_id.get(safe_text(sample.get("id")))
        if row is not None:
            logprobs[i] = row
            mask[i] = 1.0
    matched = int(mask.sum())
    weak_alpha = getattr(args, "distill_alpha_weak", None)
    weak_count = 0
    if weak_alpha is not None:
        labels_by_id = load_labels(Path(args.data_dir) / "train_labels.csv")
        weak_labels = set(ALL_CLASSES[:4])
        scale = float(weak_alpha) / float(args.distill_alpha)
        for i, sample in enumerate(samples):
            if mask[i] > 0 and labels_by_id.get(safe_text(sample.get("id"))) in weak_labels:
                mask[i] = scale
                weak_count += 1
    print(
        f"distill: matched {matched}/{len(samples)} rows from {args.distill_logits} "
        f"(alpha={args.distill_alpha} T={args.distill_temp}"
        + (f" alpha_weak={weak_alpha} weak_rows={weak_count}" if weak_alpha is not None else "")
        + ")"
    )
    return logprobs, mask


def parse_consensus_backbone_weights(value, expected_count=None):
    """Parse the raw c=0..N backbone-gradient scales used by the sieve."""
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            raise ValueError("consensus backbone weights are empty")
        try:
            values = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError(
                f"invalid consensus backbone weights: {value!r}"
            ) from exc
    else:
        values = [float(item) for item in value]
    if expected_count is not None and len(values) != expected_count:
        raise ValueError(
            "consensus backbone weights length mismatch: "
            f"expected={expected_count} actual={len(values)}"
        )
    if any(not math.isfinite(item) or item < 0.0 or item > 1.0 for item in values):
        raise ValueError("consensus backbone weights must be finite values in [0, 1]")
    if any(left > right for left, right in zip(values, values[1:])):
        raise ValueError("consensus backbone weights must be nondecreasing")
    return values


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_consensus_reliability(samples, y, train_idx, args):
    """Align and normalize an OOF-consensus reliability artifact by row id.

    Original rows must have exact artifact coverage and matching labels. Replay
    rows are intentionally outside the OOF artifact and retain scale 1.0. When
    class normalization is enabled, raw scales are divided by their mean within
    each true class on the current training split. This preserves the baseline
    class-level hard-loss mass while moving its backbone gradient away from
    rows on which all OOF teachers failed.
    """
    artifact_path = safe_text(getattr(args, "consensus_reliability", ""))
    if not artifact_path:
        return None
    path = Path(artifact_path)
    if not path.is_file():
        raise ValueError(f"consensus reliability artifact is missing: {path}")
    payload = torch_load(path)
    if not isinstance(payload, dict):
        raise ValueError("consensus reliability artifact must be a dict payload")
    if int(payload.get("schema_version", -1)) != 1:
        raise ValueError(
            "unsupported consensus reliability schema_version: "
            f"{payload.get('schema_version')!r}"
        )
    if payload.get("kind") != "oof_correct_consensus_reliability":
        raise ValueError(f"unexpected consensus reliability kind: {payload.get('kind')!r}")
    usage_scope = payload.get("usage_scope")
    if usage_scope != "full_data_refit_only":
        raise ValueError(
            "unsupported consensus reliability usage_scope: "
            f"{usage_scope!r}"
        )
    if list(payload.get("classes") or []) != ALL_CLASSES:
        raise ValueError("consensus reliability class order does not match ALL_CLASSES")

    artifact_ids = [safe_text(sample_id) for sample_id in payload.get("ids") or []]
    if not artifact_ids:
        raise ValueError("consensus reliability artifact has no ids")
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError("consensus reliability artifact contains duplicate ids")
    artifact_labels = torch.as_tensor(payload.get("y_true"), dtype=torch.long).view(-1)
    correct_counts = torch.as_tensor(payload.get("correct_counts"), dtype=torch.long).view(-1)
    if len(artifact_ids) != len(artifact_labels) or len(artifact_ids) != len(correct_counts):
        raise ValueError(
            "consensus reliability row length mismatch: "
            f"ids={len(artifact_ids)} y_true={len(artifact_labels)} "
            f"correct_counts={len(correct_counts)}"
        )
    model_count = int(payload.get("model_count", -1))
    if model_count < 1:
        raise ValueError(f"invalid consensus model_count: {model_count}")
    if bool(((correct_counts < 0) | (correct_counts > model_count)).any()):
        raise ValueError(f"correct_counts must lie in [0, {model_count}]")
    configured_weights = safe_text(getattr(args, "consensus_backbone_weights", ""))
    weight_source = configured_weights or payload.get("backbone_weights")
    if weight_source is None:
        raise ValueError(
            "consensus artifact has no backbone_weights and no CLI override was supplied"
        )
    raw_weight_values = parse_consensus_backbone_weights(
        weight_source, expected_count=model_count + 1
    )
    raw_weight_table = torch.tensor(raw_weight_values, dtype=torch.float32)

    if len(samples) != len(y):
        raise ValueError(f"samples/y length mismatch: {len(samples)} != {len(y)}")
    original_rows = [idx for idx, sample in enumerate(samples) if not sample.get("_is_replay", False)]
    replay_rows = [idx for idx, sample in enumerate(samples) if sample.get("_is_replay", False)]
    original_ids = [safe_text(samples[idx].get("id")) for idx in original_rows]
    if len(original_ids) != len(set(original_ids)):
        raise ValueError("current original samples contain duplicate ids")
    artifact_id_set = set(artifact_ids)
    original_id_set = set(original_ids)
    if artifact_id_set != original_id_set:
        missing = sorted(original_id_set - artifact_id_set)[:5]
        extra = sorted(artifact_id_set - original_id_set)[:5]
        raise ValueError(
            "consensus reliability id coverage mismatch: "
            f"artifact={len(artifact_ids)} original={len(original_ids)} "
            f"missing={missing} extra={extra}"
        )

    artifact_pos = {sample_id: idx for idx, sample_id in enumerate(artifact_ids)}
    aligned_counts = torch.full((len(samples),), -1, dtype=torch.long)
    raw_scales = torch.ones(len(samples), dtype=torch.float32)
    label_mismatches = []
    for sample_idx in original_rows:
        sample_id = safe_text(samples[sample_idx].get("id"))
        source_idx = artifact_pos[sample_id]
        source_label = int(artifact_labels[source_idx])
        if source_label != int(y[sample_idx]):
            label_mismatches.append((sample_id, source_label, int(y[sample_idx])))
            continue
        count = int(correct_counts[source_idx])
        aligned_counts[sample_idx] = count
        raw_scales[sample_idx] = raw_weight_table[count]
    if label_mismatches:
        raise ValueError(
            "consensus reliability labels do not match current training labels: "
            f"{label_mismatches[:5]}"
        )

    train_set = set(int(idx) for idx in train_idx)
    if any(idx < 0 or idx >= len(samples) for idx in train_set):
        raise ValueError("train_idx contains an out-of-range row")
    heldout_original = [idx for idx in original_rows if idx not in train_set]
    if heldout_original:
        raise ValueError(
            "full-data OOF consensus reliability cannot be used with held-out "
            "validation rows: its source OOF models may have trained on those "
            "labels. Use it only for --final-model --final-only, or build a "
            "nested reliability artifact from the outer training split. "
            f"heldout_original={len(heldout_original)}"
        )
    class_normalize = bool(getattr(args, "consensus_class_normalize", True))
    class_rows = {}
    for class_id, label in enumerate(ALL_CLASSES):
        class_rows[label] = (
            [idx for idx in original_rows if idx in train_set and int(y[idx]) == class_id],
            [idx for idx in replay_rows if idx in train_set and int(y[idx]) == class_id],
            [idx for idx in original_rows if int(y[idx]) == class_id],
        )

    def normalize_scales(raw, branch):
        effective = raw.clone()
        stats = {}
        for label, (
            class_original_train,
            class_replay_train,
            class_all_original,
        ) in class_rows.items():
            if not class_original_train and not class_replay_train:
                continue
            normalization_factor = 1.0
            raw_mean = None
            if class_original_train:
                raw_mean = float(raw[class_original_train].mean())
                if class_normalize:
                    if raw_mean <= 0.0:
                        raise ValueError(
                            f"consensus raw {branch} weights have zero mean "
                            f"for training class {label}"
                        )
                    normalization_factor = 1.0 / raw_mean
                    effective[class_all_original] *= normalization_factor
            stats[label] = {
                "original_train_rows": len(class_original_train),
                "replay_train_rows": len(class_replay_train),
                "raw_mean": raw_mean,
                "normalization_factor": normalization_factor,
                "effective_original_mean": (
                    float(effective[class_original_train].mean())
                    if class_original_train
                    else None
                ),
            }
        # Replay pseudo-targets describe older actions and cannot inherit the
        # current row's OOF correctness. Keep their baseline gradient (the KD
        # branch additionally masks them out entirely via the teacher mask).
        if replay_rows:
            effective[replay_rows] = 1.0
        return effective, stats

    effective_scales, class_stats = normalize_scales(raw_scales, "backbone")

    kd_config = safe_text(getattr(args, "consensus_kd_weights", ""))
    kd_raw_scales = None
    kd_effective_scales = None
    kd_class_stats = None
    kd_weight_values = None
    if kd_config:
        kd_weight_values = parse_consensus_backbone_weights(
            kd_config, expected_count=model_count + 1
        )
        kd_weight_table = torch.tensor(kd_weight_values, dtype=torch.float32)
        kd_raw_scales = torch.ones(len(samples), dtype=torch.float32)
        for sample_idx in original_rows:
            kd_raw_scales[sample_idx] = kd_weight_table[int(aligned_counts[sample_idx])]
        kd_effective_scales, kd_class_stats = normalize_scales(kd_raw_scales, "kd")

    train_original = [idx for idx in original_rows if idx in train_set]
    train_replay = [idx for idx in replay_rows if idx in train_set]
    count_histogram = Counter(int(aligned_counts[idx]) for idx in train_original)
    meta = {
        "enabled": True,
        "artifact_path": str(path),
        "artifact_sha256": _sha256_file(path),
        "artifact_schema_version": 1,
        "usage_scope": usage_scope,
        "model_count": model_count,
        "backbone_weights": raw_weight_values,
        "weights_source": "cli" if configured_weights else "artifact",
        "class_normalize": class_normalize,
        "train_original_rows": len(train_original),
        "train_replay_rows": len(train_replay),
        "correct_count_histogram": {
            str(count): int(count_histogram.get(count, 0))
            for count in range(model_count + 1)
        },
        "raw_train_mean": (
            float(raw_scales[train_original].mean()) if train_original else None
        ),
        "effective_train_mean": (
            float(effective_scales[train_original].mean()) if train_original else None
        ),
        "class_stats": class_stats,
        "kd_weights": kd_weight_values,
        "kd_raw_train_mean": (
            float(kd_raw_scales[train_original].mean())
            if kd_raw_scales is not None and train_original
            else None
        ),
        "kd_effective_train_mean": (
            float(kd_effective_scales[train_original].mean())
            if kd_effective_scales is not None and train_original
            else None
        ),
        "kd_class_stats": kd_class_stats,
        "sources": payload.get("sources") or [],
    }
    args.consensus_reliability_meta = meta
    print(
        "consensus sieve: "
        f"artifact={path} models={model_count} original_train={len(train_original)} "
        f"replay_train={len(train_replay)} weights={raw_weight_values} "
        f"kd_weights={kd_weight_values} "
        f"class_normalize={class_normalize} histogram={meta['correct_count_histogram']}"
    )
    return {
        "gradient_scales": effective_scales,
        "raw_scales": raw_scales,
        "kd_gradient_scales": kd_effective_scales,
        "kd_raw_scales": kd_raw_scales,
        "correct_counts": aligned_counts,
        "meta": meta,
    }


def find_terminal_classifier_head(model):
    """Find the final Linear producing the canonical 14 action logits."""
    candidates = []
    for name, module in model.named_modules():
        if isinstance(module, torch.nn.Linear) and int(module.out_features) == len(ALL_CLASSES):
            if name == "score" or name.endswith(".score"):
                rank = 0
            elif name == "classifier" or name.endswith(".classifier"):
                rank = 1
            elif name.endswith(".classifier.out_proj") or name.endswith(".out_proj"):
                rank = 2
            else:
                rank = 3
            candidates.append((rank, name, module))
    if not candidates:
        raise ValueError(
            "consensus sieve could not find a terminal Linear classifier with "
            f"out_features={len(ALL_CLASSES)}"
        )
    candidates.sort(key=lambda item: (item[0], item[1]))
    best_rank = candidates[0][0]
    best = [item for item in candidates if item[0] == best_rank]
    if len(best) != 1:
        raise ValueError(
            "consensus sieve found ambiguous terminal classifier heads: "
            f"{[name for _, name, _ in best]}"
        )
    return best[0][2], best[0][1]


def _pool_recomputed_head_logits(head_logits, reference_logits, encoded):
    if tuple(head_logits.shape) == tuple(reference_logits.shape):
        return head_logits
    if (
        head_logits.ndim == 3
        and reference_logits.ndim == 2
        and head_logits.shape[0] == reference_logits.shape[0]
        and head_logits.shape[2] == reference_logits.shape[1]
    ):
        attention_mask = encoded.get("attention_mask")
        if attention_mask is None or tuple(attention_mask.shape) != tuple(head_logits.shape[:2]):
            raise ValueError(
                "consensus sieve needs a matching attention_mask to pool token-level logits"
            )
        positions = torch.arange(
            attention_mask.shape[1], device=attention_mask.device
        ).view(1, -1)
        positions = positions.expand_as(attention_mask)
        sequence_end = positions.masked_fill(~attention_mask.bool(), -1).max(dim=1).values
        if bool((sequence_end < 0).any()):
            raise ValueError("consensus sieve encountered an all-padding sequence")
        rows = torch.arange(head_logits.shape[0], device=head_logits.device)
        return head_logits[rows, sequence_end]
    raise ValueError(
        "consensus sieve cannot map terminal head output to model logits: "
        f"head={tuple(head_logits.shape)} model={tuple(reference_logits.shape)}"
    )


def forward_with_consensus_sieve(
    model, encoded, backbone_scales, classifier_head=None, kd_backbone_scales=None
):
    """Return (ordinary logits, hard-label logits with gated backbone gradient).

    Numerically, the recomputed hard logits equal the ordinary model logits.
    Autograd sees `stopgrad(h) + w * (h - stopgrad(h))` at the final classifier
    input, so classifier parameters receive the full hard-label gradient while
    the gradient entering the backbone is scaled per row. The ordinary logits
    remain available for an untouched KD branch.

    With ``kd_backbone_scales`` the same gate is applied a second time for the
    KD branch (KD head gradient stays full, KD backbone gradient is scaled per
    row) and a third element is returned: (ordinary, hard, kd).
    """
    if classifier_head is None:
        classifier_head, _ = find_terminal_classifier_head(model)
    captured_inputs = []

    def capture_head_input(_module, inputs):
        if not inputs or not torch.is_tensor(inputs[0]):
            raise ValueError("terminal classifier did not receive a positional tensor input")
        captured_inputs.append(inputs[0])

    handle = classifier_head.register_forward_pre_hook(capture_head_input)
    try:
        outputs = model(**encoded)
    finally:
        handle.remove()
    if len(captured_inputs) != 1:
        raise ValueError(
            "terminal classifier must run exactly once in the sequence-classifier forward; "
            f"observed={len(captured_inputs)}"
        )
    hidden = captured_inputs[0]

    def gated_logits(row_scales, branch):
        scales = torch.as_tensor(row_scales, dtype=hidden.dtype, device=hidden.device)
        if scales.ndim != 1 or scales.shape[0] != hidden.shape[0]:
            raise ValueError(
                f"consensus {branch} scale batch mismatch: "
                f"scales={tuple(scales.shape)} hidden={tuple(hidden.shape)}"
            )
        scale_shape = [hidden.shape[0]] + [1] * (hidden.ndim - 1)
        scales = scales.view(scale_shape)
        gated_hidden = hidden.detach() + scales * (hidden - hidden.detach())
        return _pool_recomputed_head_logits(
            classifier_head(gated_hidden), outputs.logits, encoded
        )

    hard_logits = gated_logits(backbone_scales, "backbone")
    if kd_backbone_scales is None:
        return outputs.logits, hard_logits
    kd_logits = gated_logits(kd_backbone_scales, "kd-backbone")
    return outputs.logits, hard_logits, kd_logits


def load_sequence_classifier(args, tokenizer, label_kwargs):
    lora_r = int(getattr(args, "lora_r", 0) or 0)
    resume_from = getattr(args, "resume_from", "") or ""
    resume_path = Path(resume_from) if resume_from else None
    resume_is_adapter = bool(resume_path and (resume_path / "adapter_config.json").exists())
    load_source = args.base_model if resume_is_adapter else (resume_from or args.base_model)
    load_dtype = torch.float16 if lora_r > 0 else (torch.bfloat16 if args.bf16 else torch.float32)
    model_class = getattr(args, "model_class", "auto")

    if model_class == "gemma4custom":
        if args.dropout is not None:
            raise ValueError("--dropout is not wired for --model-class gemma4custom")
        from gemma4_seqcls import build_gemma4_seqcls

        model = build_gemma4_seqcls(load_source, **label_kwargs, torch_dtype=load_dtype)
    elif model_class == "qwen35text":
        from transformers import Qwen3_5TextForSequenceClassification

        model = Qwen3_5TextForSequenceClassification.from_pretrained(
            load_source,
            **label_kwargs,
            torch_dtype=load_dtype,
        )
    elif model_class == "gemma3text":
        from transformers import Gemma3TextForSequenceClassification

        model = Gemma3TextForSequenceClassification.from_pretrained(
            load_source,
            **label_kwargs,
            torch_dtype=load_dtype,
        )
    else:
        dtype_kwargs = {"torch_dtype": load_dtype}
        if args.dropout is not None:
            # decoder configs (Llama/Qwen family) default all dropout to 0.0, which
            # makes R-Drop's two passes identical -- override before weight load
            config = AutoConfig.from_pretrained(load_source, **label_kwargs)
            touched = []
            for attr in (
                "attention_dropout",
                "hidden_dropout",
                "hidden_dropout_prob",
                "attention_probs_dropout_prob",
                "classifier_dropout",
                "resid_pdrop",
                "embd_pdrop",
            ):
                if hasattr(config, attr):
                    setattr(config, attr, args.dropout)
                    touched.append(attr)
            print(f"dropout override={args.dropout} on: {', '.join(touched) if touched else 'NO MATCHING CONFIG ATTRS'}")
            model = AutoModelForSequenceClassification.from_pretrained(load_source, config=config, **dtype_kwargs)
        else:
            model = AutoModelForSequenceClassification.from_pretrained(load_source, **label_kwargs, **dtype_kwargs)

    ensure_model_pad_token(model, tokenizer.pad_token_id)
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()

    if lora_r > 0:
        from peft import LoraConfig, PeftModel, get_peft_model

        if resume_is_adapter:
            print(f"loading trainable LoRA adapter from {resume_from}")
            model = PeftModel.from_pretrained(model, resume_from, is_trainable=True)
        else:
            model = get_peft_model(
                model,
                LoraConfig(
                    r=lora_r,
                    lora_alpha=2 * lora_r,
                    lora_dropout=0.05,
                    target_modules=[
                        "q_proj",
                        "k_proj",
                        "v_proj",
                        "o_proj",
                        "gate_proj",
                        "up_proj",
                        "down_proj",
                    ],
                    modules_to_save=["score"],
                    task_type="SEQ_CLS",
                ),
            )
        if args.gradient_checkpointing and hasattr(model, "enable_input_require_grads"):
            model.enable_input_require_grads()
        for param in model.parameters():
            if param.requires_grad and param.dtype == torch.float16:
                param.data = param.data.float()
        if hasattr(model, "print_trainable_parameters"):
            model.print_trainable_parameters()

    return model


def train_model(
    tokenizer,
    encoded_features,
    lengths,
    y,
    sample_weights,
    train_idx,
    args,
    device,
    teacher=None,
    consensus=None,
):
    label_kwargs = dict(
        num_labels=len(ALL_CLASSES),
        id2label={i: label for i, label in enumerate(ALL_CLASSES)},
        label2id={label: i for i, label in enumerate(ALL_CLASSES)},
    )
    model = load_sequence_classifier(args, tokenizer, label_kwargs).to(device)
    consensus_classifier_head = None
    if consensus is not None:
        consensus_classifier_head, consensus_head_name = find_terminal_classifier_head(model)
        consensus["meta"]["classifier_head"] = consensus_head_name
        args.consensus_reliability_meta = consensus["meta"]
        print(f"consensus sieve classifier head: {consensus_head_name}")

    weights = class_weights([y[i] for i in train_idx], device, args.class_weight_power)
    weak4_ids = torch.tensor(WEAK4_CLASS_IDS, dtype=torch.long, device=device)
    weak4_weights = weights[weak4_ids]
    if args.train_label_filter == "weak4":
        weak4_weights = weak4_weights / torch.clamp(weak4_weights.mean(), min=1e-8)
    explorer4_ids = torch.tensor(EXPLORER4_CLASS_IDS, dtype=torch.long, device=device)
    explorer4_local_targets = torch.full((len(ALL_CLASSES),), -1, dtype=torch.long, device=device)
    explorer4_local_targets[explorer4_ids] = torch.arange(len(EXPLORER4_CLASS_IDS), device=device)
    explorer4_weights = None
    if args.explorer4_loss_balance:
        explorer4_weights = weights[explorer4_ids]
        explorer4_weights = explorer4_weights / torch.clamp(explorer4_weights.mean(), min=1e-8)
    optimizer_params = specialist_optimizer_groups(
        model, args.weight_decay, args.train_label_filter
    )
    if args.optim == "adamw8bit":
        import bitsandbytes as bnb

        optimizer = bnb.optim.AdamW8bit(
            optimizer_params, lr=args.lr, weight_decay=args.weight_decay
        )
    else:
        optimizer = torch.optim.AdamW(
            optimizer_params, lr=args.lr, weight_decay=args.weight_decay
        )
    accum = max(1, args.grad_accum_steps)
    batches_per_epoch = math.ceil(len(train_idx) / args.batch_size)
    total_steps = math.ceil(batches_per_epoch / accum) * args.epochs
    warmup_steps = int(total_steps * args.warmup_ratio)
    scheduler = get_linear_schedule_with_warmup(optimizer, warmup_steps, total_steps)
    # bf16 needs no loss scaling; a disabled scaler passes scale/unscale_/step through
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda" and not args.bf16)
    rng = random.Random(args.seed)

    def optimizer_step():
        scaler.unscale_(optimizer)
        grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
        if not scaler.is_enabled() and not torch.isfinite(grad_norm):
            raise FloatingPointError(f"non-finite grad norm: {float(grad_norm.detach().cpu())}")
        scaler.step(optimizer)
        scaler.update()
        scheduler.step()
        optimizer.zero_grad(set_to_none=True)

    def per_row_loss(logits, labels, batch_idx, hard_logits=None, kd_logits=None):
        supervised_logits = logits if hard_logits is None else hard_logits
        if args.train_label_filter == "weak4":
            if bool(((labels < 0) | (labels >= len(WEAK4_CLASS_IDS))).any()):
                raise ValueError(f"weak4 specialist batch has labels outside 0-3: {labels.tolist()}")
            loss_weights = weak4_weights
            loss_class_ids = weak4_ids
        else:
            loss_weights = weights
            loss_class_ids = None
        loss_values = classification_loss_values(
            supervised_logits,
            labels,
            loss_weights,
            args.label_smoothing,
            args.loss,
            args.focal_gamma,
            class_ids=loss_class_ids,
        )
        if args.explorer4_loss_weight > 0 and args.train_label_filter == "none":
            explorer4_targets = explorer4_local_targets[labels]
            explorer4_mask = explorer4_targets >= 0
            if bool(explorer4_mask.any()):
                explorer4_loss_values = torch.zeros_like(loss_values)
                explorer4_loss_values[explorer4_mask] = F.cross_entropy(
                    supervised_logits.float()[explorer4_mask][:, explorer4_ids],
                    explorer4_targets[explorer4_mask],
                    weight=explorer4_weights,
                    reduction="none",
                )
                loss_values = loss_values + args.explorer4_loss_weight * explorer4_loss_values
        if teacher is not None:
            temp = args.distill_temp
            teacher_p = F.softmax(teacher[0][batch_idx].to(device) / temp, dim=-1)
            # KD student logits: the kd-gated recompute when the KD-branch
            # sieve is active (numerically identical, backbone gradient scaled)
            kd_student_logits = logits if kd_logits is None else kd_logits
            student_lp = F.log_softmax(kd_student_logits.float() / temp, dim=-1)
            kd = F.kl_div(student_lp, teacher_p, reduction="none").sum(-1) * (temp * temp)
            # per-row alpha: replay/unmatched rows (mask 0) keep the pure hard-label loss
            alpha = args.distill_alpha * teacher[1][batch_idx].to(device)
            loss_values = (1.0 - alpha) * loss_values + alpha * kd
        return loss_values

    start_epoch = 1
    if args.resume_from:
        state_path = Path(args.resume_from) / "checkpoint_state.json"
        last_done = 0
        if state_path.exists():
            last_done = int(json.loads(state_path.read_text(encoding="utf-8")).get("last_completed_epoch", 0))
        start_epoch = last_done + 1
        steps_per_opt_epoch = math.ceil(batches_per_epoch / accum)
        for _ in range(steps_per_opt_epoch * last_done):
            scheduler.step()
        for _ in range(last_done):
            list(make_batches(train_idx, args.batch_size, rng, lengths, args.bucket_multiplier))
        print(
            f"resume: {args.resume_from} last_completed_epoch={last_done} "
            f"start_epoch={start_epoch} lr={scheduler.get_last_lr()[0]:.3e}"
        )

    optimizer.zero_grad(set_to_none=True)
    for epoch in range(start_epoch, args.epochs + 1):
        model.train()
        total_loss = 0.0
        total_kl = 0.0
        seen = 0
        step = 0
        for step, batch_idx in enumerate(
            make_batches(train_idx, args.batch_size, rng, lengths, args.bucket_multiplier),
            1,
        ):
            labels = torch.tensor([y[i] for i in batch_idx], dtype=torch.long, device=device)
            weights_for_samples = torch.tensor([sample_weights[i] for i in batch_idx], dtype=torch.float32, device=device)
            encoded = make_encoded_batch(tokenizer, encoded_features, batch_idx, args, device)
            with torch.amp.autocast(device_type="cuda", enabled=device.type == "cuda", dtype=torch.bfloat16 if args.bf16 else torch.float16):
                if consensus is None:
                    logits = model(**encoded).logits
                    hard_logits = None
                    kd_logits = None
                elif consensus["kd_gradient_scales"] is None:
                    batch_scales = consensus["gradient_scales"][batch_idx].to(device)
                    logits, hard_logits = forward_with_consensus_sieve(
                        model,
                        encoded,
                        batch_scales,
                        classifier_head=consensus_classifier_head,
                    )
                    kd_logits = None
                else:
                    batch_scales = consensus["gradient_scales"][batch_idx].to(device)
                    kd_batch_scales = consensus["kd_gradient_scales"][batch_idx].to(device)
                    logits, hard_logits, kd_logits = forward_with_consensus_sieve(
                        model,
                        encoded,
                        batch_scales,
                        classifier_head=consensus_classifier_head,
                        kd_backbone_scales=kd_batch_scales,
                    )
                loss_values = per_row_loss(
                    logits, labels, batch_idx, hard_logits=hard_logits, kd_logits=kd_logits
                )
                if args.rdrop_alpha > 0:
                    # R-Drop (arXiv:2106.14448): second dropout-perturbed pass +
                    # symmetric KL; needs --dropout > 0 or both passes are identical
                    logits2 = model(**encoded).logits
                    if args.train_label_filter == "weak4":
                        rdrop_logits1 = logits.float()[:, weak4_ids]
                        rdrop_logits2 = logits2.float()[:, weak4_ids]
                    else:
                        rdrop_logits1 = logits.float()
                        rdrop_logits2 = logits2.float()
                    lp1 = F.log_softmax(rdrop_logits1, dim=-1)
                    lp2 = F.log_softmax(rdrop_logits2, dim=-1)
                    rdrop_kl = 0.5 * (
                        F.kl_div(lp1, lp2.exp(), reduction="none").sum(-1)
                        + F.kl_div(lp2, lp1.exp(), reduction="none").sum(-1)
                    )
                    loss_values = 0.5 * (loss_values + per_row_loss(logits2, labels, batch_idx)) + args.rdrop_alpha * rdrop_kl
                    total_kl += float(rdrop_kl.detach().mean().cpu()) * len(batch_idx)
                loss = (loss_values * weights_for_samples).sum() / torch.clamp(weights_for_samples.sum(), min=1.0)
            if not torch.isfinite(loss):
                raise FloatingPointError(f"non-finite loss at epoch={epoch} step={step}")
            scaler.scale(loss / accum).backward()
            if step % accum == 0:
                optimizer_step()
            total_loss += float(loss.detach().cpu()) * len(batch_idx)
            seen += len(batch_idx)
            if args.log_every and step % args.log_every == 0:
                kl_note = f" rdrop_kl={total_kl / max(1, seen):.5f}" if args.rdrop_alpha > 0 else ""
                print(f"    step={step:04d} loss={total_loss / max(1, seen):.5f}{kl_note}")
        if step % accum != 0:
            optimizer_step()
        if device.type == "cuda":
            torch.cuda.synchronize()
        kl_note = f" rdrop_kl={total_kl / max(1, seen):.5f}" if args.rdrop_alpha > 0 else ""
        print(f"  epoch={epoch:02d} train_loss={total_loss / max(1, seen):.5f}{kl_note}")
        if args.epoch_checkpoint_dir:
            try:
                save_epoch_checkpoint(
                    model,
                    tokenizer,
                    args.epoch_checkpoint_dir,
                    epoch,
                    snapshot_epoch=bool(args.snapshot_epoch_checkpoints),
                )
            except Exception as exc:
                # checkpoint is insurance only — a Drive-mount hiccup must not kill the run
                print(f"  epoch checkpoint FAILED (continuing): {exc}")
    return model


def save_epoch_checkpoint(model, tokenizer, ckpt_dir, epoch, snapshot_epoch=False):
    """Crash insurance for preemptible runtimes: overwrite ckpt_dir with an
    fp16 copy of the last completed epoch (point it at a Drive path)."""
    import copy

    start = time.perf_counter()
    ckpt_dir = Path(ckpt_dir)
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    if hasattr(model, "peft_config"):
        model.save_pretrained(ckpt_dir, safe_serialization=True)
        tokenizer.save_pretrained(ckpt_dir)
    else:
        snapshot = copy.deepcopy(model).half().cpu()
        snapshot.save_pretrained(ckpt_dir, safe_serialization=True)
        del snapshot
        if epoch == 1:
            tokenizer.save_pretrained(ckpt_dir)
    (ckpt_dir / "checkpoint_state.json").write_text(
        json.dumps({"last_completed_epoch": epoch}), encoding="utf-8")
    if snapshot_epoch:
        snapshot_dir = ckpt_dir.with_name(f"{ckpt_dir.name}_ep{epoch}")
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        shutil.copytree(ckpt_dir, snapshot_dir)
        print(f"  epoch checkpoint snapshot -> {snapshot_dir}")
    print(f"  epoch checkpoint -> {ckpt_dir} (epoch={epoch}, {time.perf_counter() - start:.0f}s)")


def save_hf_artifact(model, tokenizer, output_dir, class_bias, args, metrics):
    output_dir = Path(output_dir)
    hf_dir = output_dir / "hf_model"
    hf_dir.mkdir(parents=True, exist_ok=True)
    if args.save_fp16:
        model = model.half()
    model.save_pretrained(hf_dir, safe_serialization=True)
    tokenizer.save_pretrained(hf_dir)
    if int(getattr(args, "lora_r", 0) or 0) > 0:
        try:
            from importlib.metadata import PackageNotFoundError, version

            def package_version(name):
                try:
                    return version(name)
                except PackageNotFoundError:
                    return "missing"

            try:
                repo_dir = Path(__file__).resolve().parent
                git_sha = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo_dir,
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
                git_status = subprocess.check_output(
                    ["git", "status", "--porcelain"],
                    cwd=repo_dir,
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).splitlines()
                worktree_digest = hashlib.sha256(
                    subprocess.check_output(
                        ["git", "diff", "--binary", "HEAD"],
                        cwd=repo_dir,
                        stderr=subprocess.DEVNULL,
                    )
                )
                untracked = []
                for line in git_status:
                    if not line.startswith("?? "):
                        continue
                    candidate = repo_dir / line[3:]
                    paths = sorted(candidate.rglob("*")) if candidate.is_dir() else [candidate]
                    untracked.extend(path for path in paths if path.is_file())
                for path in sorted(set(untracked)):
                    rel = str(path.relative_to(repo_dir)).encode("utf-8")
                    worktree_digest.update(len(rel).to_bytes(4, "big"))
                    worktree_digest.update(rel)
                    worktree_digest.update(path.read_bytes())
                worktree_sha = worktree_digest.hexdigest()
            except (OSError, subprocess.CalledProcessError):
                git_sha = "unknown"
                git_status = ["unknown"]
                worktree_sha = "unknown"
            cloud_manifest_path = repo_dir / "cloud_manifest.json"
            cloud_manifest = None
            if cloud_manifest_path.is_file():
                cloud_manifest = json.loads(cloud_manifest_path.read_text(encoding="utf-8"))
            provenance = {
                "git_sha": git_sha,
                "git_dirty": bool(git_status),
                "git_status": git_status,
                "working_tree_diff_sha256": worktree_sha,
                "cloud_manifest": cloud_manifest,
                "python": sys.version.split()[0],
                "packages": {
                    "peft": package_version("peft"),
                    "transformers": package_version("transformers"),
                    "torch": package_version("torch"),
                    "safetensors": package_version("safetensors"),
                },
                "train_command": " ".join(shlex.quote(part) for part in sys.argv),
                "resume_from": str(args.resume_from),
                "serializer_name": args.serializer,
                "train_label_filter": args.train_label_filter,
            }
            (hf_dir / "weak4_training_provenance.json").write_text(
                json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            raise RuntimeError(f"failed to record LoRA training provenance: {exc}") from exc
    meta = {
        "classes": ALL_CLASSES,
        "class_bias": [float(x) for x in class_bias.tolist()],
        "max_length": args.max_length,
        "batch_size": args.eval_batch_size,
        "validation_macro_f1": metrics["macro_f1"],
        "validation_split": args.split,
        "fold_id": args.fold_id if args.split == "session_oof" else None,
        "n_folds": args.n_folds if args.split == "session_oof" else None,
        "serializer_name": args.serializer,
        "replay_mode": args.replay_mode,
        "replay_sample_weight": args.replay_sample_weight,
        "base_model": args.base_model,
        "model_class": getattr(args, "model_class", "auto"),
        "lora_r": int(getattr(args, "lora_r", 0) or 0),
        "train_label_filter": args.train_label_filter,
        "seed": int(args.seed),
        "epochs": int(args.epochs),
        "train_batch_size": int(args.batch_size),
        "grad_accum_steps": int(args.grad_accum_steps),
        "gradient_checkpointing": bool(args.gradient_checkpointing),
        "learning_rate": float(args.lr),
        "weight_decay": float(args.weight_decay),
        "label_smoothing": float(args.label_smoothing),
        "loss": args.loss,
        "focal_gamma": float(args.focal_gamma),
        "class_weight_power": float(args.class_weight_power),
        "optim": args.optim,
        "bf16": bool(args.bf16),
        "explorer4_loss_weight": float(args.explorer4_loss_weight),
        "explorer4_loss_balance": bool(args.explorer4_loss_balance),
        "consensus_reliability": getattr(args, "consensus_reliability_meta", None),
        "trained_with_cuda": torch.cuda.is_available(),
        "final_refit": bool(args.final_model),
        "saved_fp16": bool(args.save_fp16),
    }
    if args.class_bias_artifact:
        meta["class_bias_source"] = str(args.class_bias_artifact)
    if args.rule_boosts_path:
        with Path(args.rule_boosts_path).open(encoding="utf-8") as f:
            rule_payload = json.load(f)
        meta["rule_boosts"] = rule_payload.get("rules", [])
        meta["rule_boosts_source"] = str(args.rule_boosts_path)
    with (output_dir / "hf_meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def dir_size_mb(path):
    path = Path(path)
    if not path.exists():
        return 0.0
    total = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return total / (1024 * 1024)


def summarize_weak_classes(metrics, count=5):
    return ";".join(f"{name}:{score:.4f}" for name, score in sorted(metrics["per_class_f1"].items(), key=lambda kv: kv[1])[:count])


def explorer4_metrics(y_true, y_pred, full_metrics=None):
    if full_metrics is None:
        full_metrics = f1_metrics(y_true, y_pred)
    per_class = {
        label: float(full_metrics["per_class_f1"].get(label, 0.0))
        for label in EXPLORER4_CLASSES
    }
    confusion = [[0 for _ in EXPLORER4_CLASSES] for _ in EXPLORER4_CLASSES]
    true_explorer4 = 0
    true_explorer4_pred_non = 0
    true_non_explorer4 = 0
    true_non_pred_explorer4 = 0
    pair_confusions = Counter()
    for true_id, pred_id in zip(y_true, y_pred):
        true_is_e4 = true_id in EXPLORER4_CLASS_ID_SET
        pred_is_e4 = pred_id in EXPLORER4_CLASS_ID_SET
        if true_is_e4:
            true_explorer4 += 1
            if pred_is_e4:
                confusion[EXPLORER4_CLASS_IDS.index(true_id)][EXPLORER4_CLASS_IDS.index(pred_id)] += 1
                if true_id != pred_id:
                    pair_confusions[(ALL_CLASSES[true_id], ALL_CLASSES[pred_id])] += 1
            else:
                true_explorer4_pred_non += 1
        else:
            true_non_explorer4 += 1
            if pred_is_e4:
                true_non_pred_explorer4 += 1
    explorer4_sum = sum(per_class.values())
    return {
        "explorer4_classes": EXPLORER4_CLASSES,
        "explorer4_macro_f1": explorer4_sum / len(EXPLORER4_CLASSES),
        "explorer4_sum_f1": explorer4_sum,
        "explorer4_per_class": per_class,
        "explorer4_confusion_4x4": confusion,
        "true_explorer4_pred_non_explorer_rate": true_explorer4_pred_non / max(1, true_explorer4),
        "true_non_explorer_pred_explorer4_rate": true_non_pred_explorer4 / max(1, true_non_explorer4),
        "explorer4_pair_confusions": [
            {"count": count, "true": true_label, "pred": pred_label}
            for (true_label, pred_label), count in pair_confusions.most_common(12)
        ],
    }


def explorer4_metrics_from_logits(logits, y_true, bias, full_metrics=None):
    pred = predict_with_bias(logits, bias)
    return explorer4_metrics(y_true, pred, full_metrics=full_metrics)


def weak4_conditional_metrics(logits, y_true):
    weak_rows = [idx for idx, label in enumerate(y_true) if label in WEAK4_CLASS_ID_SET]
    if not weak_rows:
        raise ValueError("validation set has no true Weak4 rows")
    row_idx = torch.tensor(weak_rows, dtype=torch.long)
    weak_ids = torch.tensor(WEAK4_CLASS_IDS, dtype=torch.long)
    pred_local = torch.argmax(logits.float()[row_idx][:, weak_ids], dim=1).tolist()
    true_local = [int(y_true[idx]) for idx in weak_rows]
    confusion = [[0 for _ in WEAK4_CLASSES] for _ in WEAK4_CLASSES]
    for true_id, pred_id in zip(true_local, pred_local):
        confusion[true_id][pred_id] += 1
    per_class = {}
    for class_id, label in enumerate(WEAK4_CLASSES):
        tp = confusion[class_id][class_id]
        fp = sum(confusion[row][class_id] for row in range(len(WEAK4_CLASSES))) - tp
        fn = sum(confusion[class_id]) - tp
        denom = 2 * tp + fp + fn
        per_class[label] = (2 * tp / denom) if denom else 0.0
    top_confusions = []
    for true_id, true_label in enumerate(WEAK4_CLASSES):
        for pred_id, pred_label in enumerate(WEAK4_CLASSES):
            if true_id != pred_id and confusion[true_id][pred_id]:
                top_confusions.append(
                    (confusion[true_id][pred_id], true_label, pred_label)
                )
    top_confusions.sort(reverse=True)
    return {
        "n_true_weak4": len(weak_rows),
        "macro_f1": sum(per_class.values()) / len(per_class),
        "per_class_f1": per_class,
        "confusion_4x4": confusion,
        "prediction_distribution": dict(Counter(WEAK4_CLASSES[pred] for pred in pred_local)),
        "top_confusions": top_confusions,
    }


def load_tokenizer_for_args(args):
    try:
        tokenizer = AutoTokenizer.from_pretrained(args.base_model, use_fast=not args.slow_tokenizer)
    except ImportError:
        if args.slow_tokenizer:
            raise
        print(f"fast tokenizer unavailable for {args.base_model}; falling back to slow tokenizer")
        tokenizer = AutoTokenizer.from_pretrained(args.base_model, use_fast=False)
    if tokenizer.pad_token is None:
        # decoder checkpoints (Qwen etc.) ship without a pad token; tokenizer.pad() needs one
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def ensure_model_pad_token(model, pad_token_id):
    """Set pad_token_id on top-level and nested text configs when present."""
    configs = [getattr(model, "config", None), getattr(getattr(model, "config", None), "text_config", None)]
    for config in configs:
        if config is None:
            continue
        if not hasattr(config, "pad_token_id") or getattr(config, "pad_token_id") is None:
            setattr(config, "pad_token_id", pad_token_id)


def load_class_bias_artifact(path):
    if not path:
        return None, None
    with Path(path).open(encoding="utf-8") as f:
        payload = json.load(f)
    raw_bias = payload.get("class_bias")
    if raw_bias is None:
        raise ValueError(f"{path} does not contain class_bias")
    if isinstance(raw_bias, dict):
        bias_values = [float(raw_bias.get(label, 0.0)) for label in ALL_CLASSES]
    else:
        bias_values = [float(value) for value in raw_bias]
    if len(bias_values) != len(ALL_CLASSES):
        raise ValueError(f"{path} class_bias has {len(bias_values)} values, expected {len(ALL_CLASSES)}")
    metrics = payload.get("metrics") or {}
    if "macro_f1" not in metrics:
        metrics = {"macro_f1": float(payload.get("validation_macro_f1", 0.0))}
    return torch.tensor(bias_values, dtype=torch.float32), metrics


def append_log(
    experiment_id,
    args,
    raw_metrics,
    metrics,
    decision,
    runtime,
    val_logits_path,
    artifact_size_mb,
    old_bias_metrics=None,
):
    weak = sorted(metrics["per_class_f1"].items(), key=lambda kv: kv[1])[:5]
    strong = sorted(metrics["per_class_f1"].items(), key=lambda kv: kv[1], reverse=True)[:5]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    quick_note = f", quick_val_size={args.quick_val_size}" if args.quick_val_size else ""
    fold_note = f", fold={args.fold_id}/{args.n_folds}" if args.split == "session_oof" else ""
    lines = [
        f"## {experiment_id}",
        "",
        f"- Date/time: {now}",
        "- Hypothesis: A cached multilingual transformer pipeline should make fixed-session screening faster without changing the model family.",
        f"- Code/config changes: `{args.base_model}`, serializer={args.serializer}, replay={args.replay_mode}, max_length={args.max_length}, epochs={args.epochs}, lr={args.lr}, batch={args.batch_size}, bucket_multiplier={args.bucket_multiplier}, explorer4_loss={args.explorer4_loss_weight}, explorer4_balance={args.explorer4_loss_balance}.",
        f"- Validation setup: {args.split}{fold_note}{quick_note}",
        f"- Raw Macro-F1: {raw_metrics['macro_f1']:.6f}",
        f"- Old bias-tuned Macro-F1: {old_bias_metrics['macro_f1']:.6f}" if old_bias_metrics else "- Old bias-tuned Macro-F1: not run",
        f"- Overall Macro-F1: {metrics['macro_f1']:.6f}",
        "- Per-class observations:",
        f"  - Weakest: {', '.join(f'{k}={v:.3f}' for k, v in weak)}",
        f"  - Strongest: {', '.join(f'{k}={v:.3f}' for k, v in strong)}",
        f"- Top confusions: {metrics['top_confusions'][:8]}",
        f"- Prediction distribution: {metrics['prediction_distribution']}",
        f"- Runtime or package-size concerns: runtime={runtime['total_sec']:.1f}s, tokenize={runtime['tokenize_sec']:.1f}s, train={runtime['train_sec']:.1f}s, eval={runtime['eval_sec']:.1f}s, artifact_size_mb={artifact_size_mb:.1f}.",
        f"- Validation logits: {val_logits_path or 'not saved'}",
        f"- Decision: {decision}",
        "- Next suggested experiment: quick-screen serializer/replay variants, then promote only broad fixed-session improvements to OOF.",
        "",
    ]
    with open("research_log.md", "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def save_val_logits(
    experiment_id,
    logits,
    y_true,
    ordered_indices,
    samples,
    bias,
    raw_metrics,
    metrics,
    args,
    old_bias=None,
    old_bias_metrics=None,
    weak4_conditional=None,
):
    if not args.save_val_logits:
        return ""
    path = Path(args.logits_dir) / f"{experiment_id}_val_logits.pt"
    path.parent.mkdir(parents=True, exist_ok=True)
    zero_bias = torch.zeros(len(ALL_CLASSES), dtype=torch.float32)
    torch.save(
        {
            "logits": logits.float().cpu(),
            "y_true": y_true,
            "indices": ordered_indices,
            "ids": [samples[i].get("id", "") for i in ordered_indices],
            "classes": ALL_CLASSES,
            "class_bias": [float(x) for x in bias.tolist()],
            "old_class_bias": [float(x) for x in old_bias.tolist()] if old_bias is not None else None,
            "raw_metrics": raw_metrics,
            "old_bias_metrics": old_bias_metrics,
            "metrics": metrics,
            "raw_explorer4_metrics": explorer4_metrics_from_logits(logits, y_true, zero_bias, full_metrics=raw_metrics),
            "old_bias_explorer4_metrics": (
                explorer4_metrics_from_logits(logits, y_true, old_bias, full_metrics=old_bias_metrics)
                if old_bias is not None and old_bias_metrics is not None
                else None
            ),
            "explorer4_metrics": explorer4_metrics_from_logits(logits, y_true, bias, full_metrics=metrics),
            "base_model": args.base_model,
            "serializer_name": args.serializer,
            "max_length": args.max_length,
            "split": args.split,
            "fold_id": args.fold_id if args.split == "session_oof" else None,
            "n_folds": args.n_folds if args.split == "session_oof" else None,
            "seed": args.seed,
            "train_label_filter": args.train_label_filter,
            "weak4_conditional_metrics": weak4_conditional,
        },
        path,
    )
    return str(path)


def experiment_id_for(args):
    parts = [
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        "gpu_transformer",
        args.split,
        safe_slug(args.serializer),
        f"len{args.max_length}",
    ]
    if args.split == "session_oof":
        parts.append(f"fold{args.fold_id}-of{args.n_folds}")
    if args.quick_val_size:
        parts.append(f"qv{args.quick_val_size}")
    if args.replay_mode != "none":
        parts.append(f"replay-{safe_slug(args.replay_mode)}")
    suffix = args.experiment_suffix or args.notes
    if suffix:
        parts.append(safe_slug(suffix)[:48])
    return "_".join(parts)


def run(args):
    run_start = time.perf_counter()
    if args.final_model and args.split == "session_oof":
        raise ValueError("Use --split session, not session_oof, for final refit.")
    if args.save_val_model and args.output_dir == "model":
        raise ValueError("--save-val-model needs an explicit --output-dir; refusing to overwrite the packaged model/")
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    validate_specialist_warmstart(args)
    if (
        (args.train_label_filter != "none" or args.assert_val_ids)
        and args.resume_from
        and (Path(args.resume_from) / "checkpoint_state.json").exists()
    ):
        raise ValueError(
            "specialist warm-start --resume-from must be a completed full-weight checkpoint; "
            "checkpoint_state.json indicates an epoch-resume directory"
        )
    device = torch.device(args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
    if device.type == "cuda":
        torch.cuda.set_device(0)
        torch.backends.cuda.matmul.allow_tf32 = True
        print(f"device=cuda name={torch.cuda.get_device_name(0)}")
    else:
        print("device=cpu")
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(args.seed)

    data_dir = Path(args.data_dir)
    train_path = data_dir / "train.jsonl"
    samples = load_jsonl(train_path)
    labels_by_id = load_labels(data_dir / "train_labels.csv")
    y = [CLASS_TO_ID[labels_by_id[sample["id"]]] for sample in samples]
    base_samples = samples
    base_y = y
    if args.final_only:
        if not args.final_model:
            raise ValueError("--final-only requires --final-model")
        if args.split == "session_oof":
            raise ValueError("Use --split session, not session_oof, for --final-only")
        tokenizer = load_tokenizer_for_args(args)
        if args.replay_mode == "none":
            final_samples = base_samples
            final_y = base_y
            final_idx = list(range(len(base_samples)))
            final_sample_weights = [1.0] * len(base_samples)
            final_replay_size = 0
        else:
            final_samples, final_y, final_idx, final_sample_weights, final_replay_size = add_replay_examples(
                base_samples,
                base_y,
                list(range(len(base_samples))),
                args,
            )
        final_idx = filter_train_indices(final_idx, final_y, args.train_label_filter)
        print(f"split=final_refit train={len(final_idx)} replay_size={final_replay_size}")
        token_start = time.perf_counter()
        final_texts, text_cache_path = build_serialized_texts(
            final_samples,
            args,
            train_path,
            cache_scope="final",
            tokenizer=tokenizer,
        )
        final_encoded_features, final_lengths, token_cache_path = tokenize_texts(
            tokenizer,
            final_texts,
            args,
            train_path,
            cache_scope="final",
        )
        token_sec = time.perf_counter() - token_start
        avg_len = sum(final_lengths) / max(1, len(final_lengths))
        print(f"token lengths avg={avg_len:.1f} max={max(final_lengths) if final_lengths else 0}")
        if args.cache_only:
            print("cache-only requested; skipping final refit")
            return

        train_start = time.perf_counter()
        final_model = train_model(
            tokenizer,
            final_encoded_features,
            final_lengths,
            final_y,
            final_sample_weights,
            final_idx,
            args,
            device,
            teacher=build_teacher_targets(final_samples, args),
            consensus=build_consensus_reliability(
                final_samples, final_y, final_idx, args
            ),
        )
        train_sec = time.perf_counter() - train_start
        artifact_bias, source_metrics = load_class_bias_artifact(args.class_bias_artifact)
        if artifact_bias is None:
            artifact_bias = torch.zeros(len(ALL_CLASSES), dtype=torch.float32)
            source_metrics = {"macro_f1": 0.0}
        save_hf_artifact(final_model, tokenizer, args.output_dir, artifact_bias, args, source_metrics)
        artifact_size = dir_size_mb(args.output_dir)
        runtime = {
            "tokenize_sec": token_sec,
            "train_sec": train_sec,
            "eval_sec": 0.0,
            "total_sec": time.perf_counter() - run_start,
        }
        experiment_id = experiment_id_for(args)
        metric_value = source_metrics.get("macro_f1", "")
        weak = ""
        if source_metrics.get("per_class_f1"):
            weak = summarize_weak_classes(source_metrics)
        append_results_csv(
            Path("experiments/results.csv"),
            {
                "experiment_id": experiment_id,
                "model_family": (
                    "weak4_lora_specialist_final_refit"
                    if args.train_label_filter == "weak4"
                    else "torch_gpu_transformer_final_refit"
                ),
                "base_model": args.base_model,
                "features": "serialized prompt/action/workspace text",
                "serializer_name": args.serializer,
                "split_type": "final_refit",
                "seed": args.seed,
                "max_length": args.max_length,
                "epochs": args.epochs,
                "learning_rate": args.lr,
                "batch_size": args.batch_size,
                "class_weight_power": args.class_weight_power,
                "label_smoothing": args.label_smoothing,
                "replay_mode": args.replay_mode,
                "replay_size": final_replay_size,
                "macro_f1": f"{metric_value:.6f}" if isinstance(metric_value, (float, int)) else "",
                "weakest_classes": weak,
                "artifact_path": args.output_dir,
                "runtime_sec": f"{runtime['total_sec']:.3f}",
                "artifact_size_mb": f"{artifact_size:.3f}",
                "train_command": " ".join(shlex.quote(part) for part in sys.argv),
                "notes": args.notes or "final refit",
                "decision": (
                    "final Weak4 LoRA refit complete; sparse is forbidden; build with the screen tuner report"
                    if args.train_label_filter == "weak4"
                    else "final transformer refit complete; requires sparse artifact and package smoke test"
                ),
            },
        )
        metrics_path = Path("experiments/artifacts") / f"{experiment_id}_metrics.json"
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        with metrics_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "experiment_id": experiment_id,
                    "metrics_source": args.class_bias_artifact,
                    "source_metrics": source_metrics,
                    "class_bias": dict(zip(ALL_CLASSES, [float(x) for x in artifact_bias.tolist()])),
                    "device": str(device),
                    "runtime": runtime,
                    "serializer_name": args.serializer,
                    "text_cache_path": str(text_cache_path),
                    "token_cache_path": str(token_cache_path),
                    "replay_size": final_replay_size,
                    "artifact_size_mb": artifact_size,
                    "rule_boosts_path": args.rule_boosts_path,
                    "explorer4_loss_weight": args.explorer4_loss_weight,
                    "explorer4_loss_balance": args.explorer4_loss_balance,
                    "consensus_reliability": getattr(
                        args, "consensus_reliability_meta", None
                    ),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        lines = [
            f"## {experiment_id}",
            "",
            f"- Date/time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "- Validation setup: final refit on all labeled rows; no validation rows used.",
            f"- Code/config changes: `{args.base_model}`, serializer={args.serializer}, replay={args.replay_mode}, max_length={args.max_length}, epochs={args.epochs}, lr={args.lr}, batch={args.batch_size}, explorer4_loss={args.explorer4_loss_weight}, explorer4_balance={args.explorer4_loss_balance}.",
            f"- OOF class-bias source: {args.class_bias_artifact or 'none'}",
            f"- Rule boosts source: {args.rule_boosts_path or 'none'}",
            f"- Runtime or package-size concerns: runtime={runtime['total_sec']:.1f}s, tokenize={runtime['tokenize_sec']:.1f}s, train={runtime['train_sec']:.1f}s, artifact_size_mb={artifact_size:.1f}.",
            (
                "- Decision: final Weak4 LoRA refit complete; use the fixed screen tuner config and no sparse stack."
                if args.train_label_filter == "weak4"
                else "- Decision: final transformer refit complete; next train final sparse SVC artifact and smoke-test offline submission package."
            ),
            "",
        ]
        if not args.no_research_log:
            with Path("research_log.md").open("a", encoding="utf-8") as f:
                f.write("\n".join(lines))
        print(f"saved final HF artifact: {args.output_dir} artifact_size_mb={artifact_size:.1f}")
        return

    train_idx, val_idx = split_for_args(samples, y, args)
    assert_validation_anchor(samples, y, train_idx, val_idx, args)
    full_val_count = len(val_idx)
    val_idx = select_balanced_subset(val_idx, y, args.quick_val_size, args.seed + 17)
    samples, y, train_idx, sample_weights, replay_size = add_replay_examples(samples, y, train_idx, args)
    train_idx = filter_train_indices(train_idx, y, args.train_label_filter)
    fold_text = f" fold={args.fold_id}/{args.n_folds}" if args.split == "session_oof" else ""
    print(f"split={args.split}{fold_text} train={len(train_idx)} val={len(val_idx)} full_val={full_val_count}")

    tokenizer = load_tokenizer_for_args(args)
    token_start = time.perf_counter()
    train_cache_scope = "train"
    if args.split == "session_oof":
        train_cache_scope = f"oof-fold{args.fold_id}-of{args.n_folds}"
    texts, text_cache_path = build_serialized_texts(
        samples,
        args,
        train_path,
        cache_scope=train_cache_scope,
        tokenizer=tokenizer,
    )
    encoded_features, lengths, token_cache_path = tokenize_texts(tokenizer, texts, args, train_path, cache_scope=train_cache_scope)
    token_sec = time.perf_counter() - token_start
    avg_len = sum(lengths) / max(1, len(lengths))
    print(f"token lengths avg={avg_len:.1f} max={max(lengths) if lengths else 0}")
    if args.cache_only:
        print("cache-only requested; skipping training")
        return

    train_start = time.perf_counter()
    model = train_model(
        tokenizer, encoded_features, lengths, y, sample_weights, train_idx, args, device,
        teacher=build_teacher_targets(samples, args),
        consensus=build_consensus_reliability(samples, y, train_idx, args),
    )
    train_sec = time.perf_counter() - train_start

    eval_start = time.perf_counter()
    logits, y_val, raw_metrics, ordered_val_idx = evaluate(model, tokenizer, encoded_features, lengths, y, val_idx, args, device)
    eval_sec = time.perf_counter() - eval_start
    print(f"  raw_macro_f1={raw_metrics['macro_f1']:.6f}")

    weak4_conditional = None
    if args.train_label_filter == "weak4":
        weak4_conditional = weak4_conditional_metrics(logits, y_val)
        print(
            "  weak4_conditional "
            f"rows={weak4_conditional['n_true_weak4']} "
            f"macro_f1={weak4_conditional['macro_f1']:.6f}"
        )
        print("  weak4 conditional confusion (rows=true, cols=pred):")
        print(f"    {'':18s} " + " ".join(f"{label[:8]:>8s}" for label in WEAK4_CLASSES))
        for label, row in zip(WEAK4_CLASSES, weak4_conditional["confusion_4x4"]):
            print(f"    {label:18s} " + " ".join(f"{value:8d}" for value in row))

    bias = torch.zeros(len(ALL_CLASSES), dtype=torch.float32)
    old_bias = None
    old_bias_metrics = None
    metrics = raw_metrics
    raw_explorer4_metrics = explorer4_metrics_from_logits(logits, y_val, bias, full_metrics=raw_metrics)
    old_bias_explorer4_metrics = None
    if args.tune_bias:
        print("  tuning old class bias")
        old_bias, _ = tune_class_bias(logits, y_val, rounds=2)
        old_pred = predict_with_bias(logits, old_bias)
        old_bias_metrics = f1_metrics(y_val, old_pred)
        old_bias_explorer4_metrics = explorer4_metrics(y_val, old_pred, full_metrics=old_bias_metrics)
        print(f"  old_tuned_macro_f1={old_bias_metrics['macro_f1']:.6f}")
        print("  tuning 2-stage class bias")
        bias, _ = tune_class_bias_two_stage(
            logits,
            y_val,
            initial_bias=old_bias,
            initial_best=old_bias_metrics["macro_f1"],
            fine_rounds=2,
        )
        pred = predict_with_bias(logits, bias)
        metrics = f1_metrics(y_val, pred)
        print(f"  tuned_2stage_macro_f1={metrics['macro_f1']:.6f}")
    final_explorer4_metrics = explorer4_metrics_from_logits(logits, y_val, bias, full_metrics=metrics)
    print(
        "  explorer4 "
        f"raw={raw_explorer4_metrics['explorer4_macro_f1']:.6f} "
        f"final={final_explorer4_metrics['explorer4_macro_f1']:.6f} "
        f"sum={final_explorer4_metrics['explorer4_sum_f1']:.6f}"
    )
    report_raw_metrics = weak4_conditional if weak4_conditional is not None else raw_metrics
    report_metrics = weak4_conditional if weak4_conditional is not None else metrics

    experiment_id = experiment_id_for(args)
    val_logits_path = save_val_logits(
        experiment_id,
        logits,
        y_val,
        ordered_val_idx,
        samples,
        bias,
        raw_metrics,
        metrics,
        args,
        old_bias=old_bias,
        old_bias_metrics=old_bias_metrics,
        weak4_conditional=weak4_conditional,
    )
    runtime = {
        "tokenize_sec": token_sec,
        "train_sec": train_sec,
        "eval_sec": eval_sec,
        "total_sec": time.perf_counter() - run_start,
    }
    if args.quick_val_size:
        decision = "screening only; require full fixed-session validation"
    elif args.split == "session_oof":
        decision = "oof fold complete; aggregate before decision"
    else:
        decision = "keep as GPU candidate" if report_metrics["macro_f1"] >= args.keep_threshold else "discard or revisit"

    artifact_size = 0.0
    if args.final_model:
        print("training final transformer on all training rows")
        if args.replay_mode == "none":
            final_encoded_features = encoded_features
            final_lengths = lengths
            final_y = y
            final_sample_weights = sample_weights
            final_idx = list(range(len(samples)))
        else:
            final_samples, final_y, final_idx, final_sample_weights, final_replay_size = add_replay_examples(
                base_samples,
                base_y,
                list(range(len(base_samples))),
                args,
            )
            final_texts, _ = build_serialized_texts(
                final_samples,
                args,
                train_path,
                cache_scope="final",
                tokenizer=tokenizer,
            )
            final_encoded_features, final_lengths, _ = tokenize_texts(tokenizer, final_texts, args, train_path, cache_scope="final")
            print(f"final replay_size={final_replay_size}")
        final_idx = filter_train_indices(final_idx, final_y, args.train_label_filter)
        final_model = train_model(
            tokenizer,
            final_encoded_features,
            final_lengths,
            final_y,
            final_sample_weights,
            final_idx,
            args,
            device,
            teacher=build_teacher_targets(samples if args.replay_mode == "none" else final_samples, args),
            consensus=build_consensus_reliability(
                samples if args.replay_mode == "none" else final_samples,
                final_y,
                final_idx,
                args,
            ),
        )
        save_hf_artifact(final_model, tokenizer, args.output_dir, bias, args, report_metrics)
        artifact_size = dir_size_mb(args.output_dir)
        print(f"saved HF artifact: {args.output_dir}")
    elif args.save_val_model:
        save_hf_artifact(model, tokenizer, args.output_dir, bias, args, report_metrics)
        artifact_size = dir_size_mb(args.output_dir)
        print(f"saved val-split HF artifact: {args.output_dir}")
    elif Path(args.output_dir).exists():
        artifact_size = dir_size_mb(args.output_dir)

    split_type = args.split
    if args.split == "session_oof":
        split_type = f"session_oof_fold{args.fold_id}_of{args.n_folds}"
    if args.quick_val_size:
        split_type = f"{split_type}_quick{args.quick_val_size}"

    append_results_csv(
        Path("experiments/results.csv"),
        {
            "experiment_id": experiment_id,
            "model_family": "weak4_lora_specialist" if args.train_label_filter == "weak4" else "torch_gpu_transformer",
            "base_model": args.base_model,
            "features": "serialized prompt/action/workspace text",
            "serializer_name": args.serializer,
            "split_type": split_type,
            "seed": args.seed,
            "fold_id": f"{args.fold_id}/{args.n_folds}" if args.split == "session_oof" else "",
            "max_length": args.max_length,
            "epochs": args.epochs,
            "learning_rate": args.lr,
            "batch_size": args.batch_size,
            "class_weight_power": args.class_weight_power,
            "label_smoothing": args.label_smoothing,
            "replay_mode": args.replay_mode,
            "replay_size": replay_size,
            "macro_f1_raw": f"{report_raw_metrics['macro_f1']:.6f}",
            "macro_f1_bias_tuned": f"{old_bias_metrics['macro_f1']:.6f}" if old_bias_metrics else "",
            "macro_f1_bias_tuned_2stage": f"{metrics['macro_f1']:.6f}" if args.tune_bias else "",
            "macro_f1": f"{report_metrics['macro_f1']:.6f}",
            "weakest_classes": summarize_weak_classes(report_metrics),
            "top_confusions": json.dumps(report_metrics["top_confusions"][:8], ensure_ascii=False),
            "prediction_distribution": json.dumps(report_metrics["prediction_distribution"], ensure_ascii=False, sort_keys=True),
            "artifact_path": args.output_dir if args.final_model else "",
            "val_logits_path": val_logits_path,
            "test_logits_path": "",
            "inference_time_sec": "",
            "runtime_sec": f"{runtime['total_sec']:.3f}",
            "artifact_size_mb": f"{artifact_size:.3f}",
            "train_command": " ".join(shlex.quote(part) for part in sys.argv),
            "notes": args.notes or args.base_model,
            "decision": decision,
        },
    )
    if not args.no_research_log:
        append_log(
            experiment_id,
            args,
            report_raw_metrics,
            report_metrics,
            decision,
            runtime,
            val_logits_path,
            artifact_size,
            old_bias_metrics if weak4_conditional is None else None,
        )

    metrics_path = Path("experiments/artifacts") / f"{experiment_id}_metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "experiment_id": experiment_id,
                "raw_metrics": raw_metrics,
                "old_bias_metrics": old_bias_metrics,
                "metrics": metrics,
                "raw_explorer4_metrics": raw_explorer4_metrics,
                "old_bias_explorer4_metrics": old_bias_explorer4_metrics,
                "explorer4_metrics": final_explorer4_metrics,
                "weak4_conditional_metrics": weak4_conditional,
                "class_bias": dict(zip(ALL_CLASSES, [float(x) for x in bias.tolist()])),
                "old_class_bias": dict(zip(ALL_CLASSES, [float(x) for x in old_bias.tolist()])) if old_bias is not None else None,
                "device": str(device),
                "runtime": runtime,
                "serializer_name": args.serializer,
                "text_cache_path": str(text_cache_path),
                "token_cache_path": str(token_cache_path),
                "val_logits_path": val_logits_path,
                "validation_rows": len(val_idx),
                "full_validation_rows": full_val_count,
                "fold_id": args.fold_id if args.split == "session_oof" else None,
                "n_folds": args.n_folds if args.split == "session_oof" else None,
                "replay_size": replay_size,
                "replay_sample_weight": args.replay_sample_weight,
                "train_label_filter": args.train_label_filter,
                "explorer4_loss_weight": args.explorer4_loss_weight,
                "explorer4_loss_balance": args.explorer4_loss_balance,
                "consensus_reliability": getattr(
                    args, "consensus_reliability_meta", None
                ),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("  weakest classes:")
    for label, score in sorted(report_metrics["per_class_f1"].items(), key=lambda kv: kv[1])[:8]:
        print(f"    {label:18s} {score:.4f}")
    print("  top confusions:")
    for count, true_label, pred_label in report_metrics["top_confusions"][:10]:
        print(f"    {true_label:18s} -> {pred_label:18s} {count}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--base-model", default="distilbert-base-multilingual-cased")
    parser.add_argument("--serializer", choices=["current_v1", "chat_v1_contract", "weak_nav_v1", "weak_nav_paths_v1", "current_v2", "current_v5", "current_v6", "current_v6e", "current_v7", "current_v7r", "current_v7rl", "current_v7rm", "current_v7rd", "current_v7rb", "current_v7rc", "current_v7rw", "current_v7rg", "current_v7rcgw", "current_v8", "current_v8t", "current_v9o", "current_v9f", "current_v9h", "current_v10", "current_v11s", "state_v2", "recent_pairs_v1", "compact_events_v1", "hybrid_v1"], default="current_v1")
    parser.add_argument("--split", choices=["random", "session", "session_oof"], default="session")
    parser.add_argument("--n-folds", type=int, default=3)
    parser.add_argument("--fold-id", type=int, default=0)
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="cuda")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--eval-batch-size", type=int, default=64)
    parser.add_argument("--max-length", type=int, default=192)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.06)
    parser.add_argument("--label-smoothing", type=float, default=0.02)
    parser.add_argument("--loss", choices=["ce", "focal"], default="ce")
    parser.add_argument("--focal-gamma", type=float, default=2.0)
    parser.add_argument("--distill-logits", default=None,
                        help="OOF teacher payload (.pt with ids + log-prob logits); rows matched by id, replay rows get no KD term")
    parser.add_argument("--distill-alpha", type=float, default=0.5)
    parser.add_argument("--distill-alpha-weak", type=float, default=None,
                        help="KD alpha override for teacher-matched original rows whose true label is "
                             "Weak4 (list_directory/read_file/grep_search/glob_pattern); other matched "
                             "rows keep --distill-alpha (team condalpha option)")
    parser.add_argument("--distill-temp", type=float, default=2.0)
    parser.add_argument(
        "--consensus-reliability",
        default="",
        help=(
            "OOF correctness-consensus artifact; keeps full hard-label gradient on "
            "the classifier head while gating only the backbone gradient"
        ),
    )
    parser.add_argument(
        "--consensus-backbone-weights",
        default="",
        help=(
            "optional comma-separated c=0..N backbone scales; default uses the "
            "weights embedded in --consensus-reliability"
        ),
    )
    parser.add_argument(
        "--no-consensus-class-normalize",
        dest="consensus_class_normalize",
        action="store_false",
        help=(
            "do not normalize raw consensus scales to mean 1 inside each true "
            "class on the training split"
        ),
    )
    parser.set_defaults(consensus_class_normalize=True)
    parser.add_argument(
        "--consensus-kd-weights",
        default=None,
        help=(
            "apply the consensus sieve to the KD branch too: per-c-bin KD "
            "backbone-gradient scales (e.g. '0,0.25,0.75,1'), class-normalized "
            "like the hard-branch weights; KD head gradient stays full. "
            "Requires --consensus-reliability and --distill-logits. Unset "
            "keeps the KD branch untouched (bit-identical to prior behavior)"
        ),
    )
    parser.add_argument("--train-label-filter", choices=["none", "weak4"], default="none",
                        help="restrict optimizer rows to canonical Weak4 labels and train with conditional 4-way loss")
    parser.add_argument("--assert-val-ids", default="",
                        help="validation-logits payload whose classes/split/seed/id set must match before training")
    parser.add_argument("--explorer4-loss-weight", type=float, default=0.0,
                        help="extra plain CE on true list/read/grep/glob rows, restricted to those four logits")
    parser.add_argument("--explorer4-loss-balance", action="store_true",
                        help="normalize existing main class weights inside the Explorer4 auxiliary CE")
    parser.add_argument("--optim", choices=["adamw", "adamw8bit"], default="adamw",
                        help="adamw8bit (bitsandbytes) fits 0.6B training in 8GB VRAM")
    parser.add_argument("--bf16", action="store_true",
                        help="load weights and autocast in bfloat16, GradScaler off (no fp32 master "
                             "copy — required for 9B-class full FT on a single 80-96GB GPU; "
                             "default fp32+fp16-autocast path is unchanged without this flag)")
    parser.add_argument("--rdrop-alpha", type=float, default=0.0,
                        help="R-Drop: weight of the symmetric KL between two dropout-perturbed forward passes (0 disables; ~2x train time when on)")
    parser.add_argument("--dropout", type=float, default=None,
                        help="override model dropout probs (attention_dropout etc.) at load; decoder configs default to 0.0, required for --rdrop-alpha to bite")
    parser.add_argument("--class-weight-power", type=float, default=0.5)
    parser.add_argument("--grad-clip", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--log-every", type=int, default=250)
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--tune-bias", action="store_true")
    parser.add_argument("--final-model", action="store_true")
    parser.add_argument("--final-only", action="store_true")
    parser.add_argument("--save-val-model", action="store_true",
                        help="save the val-split-trained model (no refit) as a submittable HF artifact")
    parser.add_argument("--epoch-checkpoint-dir", default="",
                        help="overwrite this dir with an fp16 snapshot after every epoch (crash insurance; use a Drive path on Colab)")
    parser.add_argument("--snapshot-epoch-checkpoints", action="store_true",
                        help="after each epoch checkpoint, also copy it to <epoch-checkpoint-dir>_epN")
    parser.add_argument("--resume-from", default="",
                        help="resume from an epoch-checkpoint dir; scheduler/RNG skip past completed epochs, optimizer state restarts")
    parser.add_argument("--grad-accum-steps", type=int, default=1,
                        help="optimizer step every N batches (effective batch = batch-size x N); matches teammate recipe batch4 x accum4")
    parser.add_argument("--lora-r", type=int, default=0,
                        help="LoRA rank for teacher training (0=full fine-tune)")
    parser.add_argument("--model-class", choices=["auto", "qwen35text", "gemma3text", "gemma4custom"], default="auto",
                        help="text-only/custom sequence-classification class override")
    parser.add_argument("--save-fp16", action="store_true")
    parser.add_argument("--output-dir", default="model")
    parser.add_argument("--rule-boosts-path", default="")
    parser.add_argument("--class-bias-artifact", default="")
    parser.add_argument("--keep-threshold", type=float, default=0.60)
    parser.add_argument("--notes", default="")
    parser.add_argument("--experiment-suffix", default="")
    parser.add_argument("--no-research-log", action="store_true")
    parser.add_argument("--cache-dir", default="experiments/cache")
    parser.add_argument("--no-text-cache", action="store_true")
    parser.add_argument("--no-token-cache", action="store_true")
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--tokenize-batch-size", type=int, default=4096)
    parser.add_argument("--slow-tokenizer", action="store_true")
    parser.add_argument("--quick-val-size", type=int, default=0)
    parser.add_argument("--replay-mode", choices=["none", "last1", "last2"], default="none")
    parser.add_argument("--max-replay-samples", type=int, default=20000)
    parser.add_argument("--replay-sample-weight", type=float, default=0.5)
    parser.add_argument("--bucket-multiplier", type=int, default=8)
    parser.add_argument("--eval-bucket-multiplier", type=int, default=50)
    parser.add_argument("--pad-to-multiple-of", type=int, default=8)
    parser.add_argument("--save-val-logits", dest="save_val_logits", action="store_true", default=True)
    parser.add_argument("--no-save-val-logits", dest="save_val_logits", action="store_false")
    parser.add_argument("--logits-dir", default="experiments/logits")
    args = parser.parse_args()
    if args.rdrop_alpha > 0 and not args.dropout:
        parser.error(
            "--rdrop-alpha > 0 requires --dropout > 0: decoder configs default all dropout "
            "to 0.0, so both R-Drop passes would be identical (KL=0) at 2x train cost"
        )
    if args.explorer4_loss_weight < 0:
        parser.error("--explorer4-loss-weight must be >= 0")
    if args.distill_alpha_weak is not None:
        if not args.distill_logits:
            parser.error("--distill-alpha-weak requires --distill-logits")
        if args.distill_alpha <= 0:
            parser.error("--distill-alpha-weak requires --distill-alpha > 0 (mask carries alpha_weak/alpha)")
    if args.consensus_backbone_weights and not args.consensus_reliability:
        parser.error("--consensus-backbone-weights requires --consensus-reliability")
    if not args.consensus_class_normalize and not args.consensus_reliability:
        parser.error("--no-consensus-class-normalize requires --consensus-reliability")
    if args.consensus_backbone_weights:
        try:
            parse_consensus_backbone_weights(args.consensus_backbone_weights)
        except ValueError as exc:
            parser.error(str(exc))
    if args.consensus_kd_weights:
        if not args.consensus_reliability:
            parser.error("--consensus-kd-weights requires --consensus-reliability")
        if not args.distill_logits:
            parser.error("--consensus-kd-weights requires --distill-logits")
        try:
            parse_consensus_backbone_weights(args.consensus_kd_weights)
        except ValueError as exc:
            parser.error(str(exc))
    if args.consensus_reliability and args.rdrop_alpha > 0:
        parser.error(
            "--consensus-reliability does not support --rdrop-alpha; the sieve "
            "is intentionally isolated to the hard-label branch"
        )
    if args.consensus_reliability and args.train_label_filter != "none":
        parser.error("--consensus-reliability does not support --train-label-filter")
    if args.train_label_filter != "none" and args.replay_mode != "none":
        parser.error("--train-label-filter requires --replay-mode none")
    if args.train_label_filter != "none" and args.distill_logits:
        parser.error("--train-label-filter does not support --distill-logits")
    if args.train_label_filter == "weak4":
        if args.lora_r != 16:
            parser.error("--train-label-filter weak4 requires --lora-r 16")
        if not args.save_fp16:
            parser.error("--train-label-filter weak4 requires --save-fp16")
        if args.tune_bias or args.class_bias_artifact or args.rule_boosts_path:
            parser.error("Weak4 specialist training forbids bias/rule post-processing inputs")
        if args.explorer4_loss_weight > 0 or args.explorer4_loss_balance:
            parser.error("Weak4 specialist training cannot be combined with Explorer4 auxiliary loss")
        if args.final_only:
            if args.assert_val_ids:
                parser.error("--final-only has no validation split; omit --assert-val-ids")
        else:
            if not args.assert_val_ids:
                parser.error("Weak4 screen training requires --assert-val-ids")
            if args.quick_val_size:
                parser.error("Weak4 screen validation must keep all 14,001 rows")
            if not args.save_val_model:
                parser.error("Weak4 screen training requires --save-val-model")
            if args.final_model:
                parser.error("run the Weak4 final refit separately with --final-model --final-only")
    return args


if __name__ == "__main__":
    run(parse_args())
