import argparse
import json
import math
import random
import re
import shlex
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
    weights = []
    for class_id in range(len(ALL_CLASSES)):
        weights.append((total / (len(ALL_CLASSES) * max(1, counts[class_id]))) ** power)
    mean = sum(weights) / len(weights)
    return torch.tensor([w / mean for w in weights], dtype=torch.float32, device=device)


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
                candidates.append((idx, event, label))

    replay_samples = []
    for idx, user_event, label in candidates[-pair_limit:]:
        replay_samples.append(
            (
                CLASS_TO_ID[label],
                {
                    "id": f"{safe_text(sample.get('id'))}::replay_{idx}_{label}",
                    "session_meta": sample.get("session_meta") or {},
                    "history": history[:idx],
                    "current_prompt": safe_text(user_event.get("content")),
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
        )
    return (
        Path(args.cache_dir)
        / f"{kind}_{base_model}_{serializer}{replay}_{source_path.stem}_n{sample_count}_m{stamp}_len{args.max_length}.pt"
    )


def build_serialized_texts(samples, args, source_path, cache_scope="train"):
    path = cache_path(args, source_path, len(samples), "texts", cache_scope)
    if not args.no_text_cache and path.exists() and not args.rebuild_cache:
        payload = torch_load(path)
        if payload.get("serializer_name") == args.serializer and len(payload.get("texts", [])) == len(samples):
            print(f"loaded serialized text cache: {path}")
            return payload["texts"], path

    start = time.perf_counter()
    texts = [serialize_transformer_sample(sample, args.serializer) for sample in samples]
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
    so OOF teachers stay leak-free by construction."""
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
    print(
        f"distill: matched {int(mask.sum())}/{len(samples)} rows from {args.distill_logits} "
        f"(alpha={args.distill_alpha} T={args.distill_temp})"
    )
    return logprobs, mask


def train_model(tokenizer, encoded_features, lengths, y, sample_weights, train_idx, args, device, teacher=None):
    label_kwargs = dict(
        num_labels=len(ALL_CLASSES),
        id2label={i: label for i, label in enumerate(ALL_CLASSES)},
        label2id={label: i for i, label in enumerate(ALL_CLASSES)},
    )
    # bf16: no fp32 master copy — halves weight+grad memory (9B: 90GB -> ~54GB
    # with adamw8bit), the only way 9B-class full FT fits a single 80-96GB GPU
    dtype_kwargs = {"torch_dtype": torch.bfloat16 if args.bf16 else torch.float32}
    if args.dropout is not None:
        # decoder configs (Llama/Qwen family) default all dropout to 0.0, which
        # makes R-Drop's two passes identical — override before weight load
        config = AutoConfig.from_pretrained(args.base_model, **label_kwargs)
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
        model = AutoModelForSequenceClassification.from_pretrained(args.base_model, config=config, **dtype_kwargs).to(device)
    else:
        model = AutoModelForSequenceClassification.from_pretrained(args.base_model, **label_kwargs, **dtype_kwargs).to(device)
    if not hasattr(model.config, "pad_token_id") or model.config.pad_token_id is None:
        # decoder classifiers (Qwen2ForSequenceClassification) refuse batch>1 without it;
        # persisted into config.json by save_pretrained for inference/quantize
        ensure_model_pad_token(model, tokenizer.pad_token_id)
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()

    weights = class_weights([y[i] for i in train_idx], device, args.class_weight_power)
    if args.optim == "adamw8bit":
        import bitsandbytes as bnb

        optimizer = bnb.optim.AdamW8bit(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    else:
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
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
        torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
        scaler.step(optimizer)
        scaler.update()
        scheduler.step()
        optimizer.zero_grad(set_to_none=True)

    def per_row_loss(logits, labels, batch_idx):
        loss_values = F.cross_entropy(
            logits.float(),
            labels,
            weight=weights,
            label_smoothing=args.label_smoothing,
            reduction="none",
        )
        if args.loss == "focal":
            # focal factor from the unsmoothed target probability; class
            # weights and label smoothing stay inside the CE term so
            # ce -> focal changes exactly one thing
            pt = F.log_softmax(logits.float(), dim=-1).gather(1, labels.view(-1, 1)).squeeze(1).exp()
            loss_values = (1.0 - pt) ** args.focal_gamma * loss_values
        if teacher is not None:
            temp = args.distill_temp
            teacher_p = F.softmax(teacher[0][batch_idx].to(device) / temp, dim=-1)
            student_lp = F.log_softmax(logits.float() / temp, dim=-1)
            kd = F.kl_div(student_lp, teacher_p, reduction="none").sum(-1) * (temp * temp)
            # per-row alpha: replay/unmatched rows (mask 0) keep the pure hard-label loss
            alpha = args.distill_alpha * teacher[1][batch_idx].to(device)
            loss_values = (1.0 - alpha) * loss_values + alpha * kd
        return loss_values

    optimizer.zero_grad(set_to_none=True)
    for epoch in range(1, args.epochs + 1):
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
                logits = model(**encoded).logits
                loss_values = per_row_loss(logits, labels, batch_idx)
                if args.rdrop_alpha > 0:
                    # R-Drop (arXiv:2106.14448): second dropout-perturbed pass +
                    # symmetric KL; needs --dropout > 0 or both passes are identical
                    logits2 = model(**encoded).logits
                    lp1 = F.log_softmax(logits.float(), dim=-1)
                    lp2 = F.log_softmax(logits2.float(), dim=-1)
                    rdrop_kl = 0.5 * (
                        F.kl_div(lp1, lp2.exp(), reduction="none").sum(-1)
                        + F.kl_div(lp2, lp1.exp(), reduction="none").sum(-1)
                    )
                    loss_values = 0.5 * (loss_values + per_row_loss(logits2, labels, batch_idx)) + args.rdrop_alpha * rdrop_kl
                    total_kl += float(rdrop_kl.detach().mean().cpu()) * len(batch_idx)
                loss = (loss_values * weights_for_samples).sum() / torch.clamp(weights_for_samples.sum(), min=1.0)
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
                save_epoch_checkpoint(model, tokenizer, args.epoch_checkpoint_dir, epoch)
            except Exception as exc:
                # checkpoint is insurance only — a Drive-mount hiccup must not kill the run
                print(f"  epoch checkpoint FAILED (continuing): {exc}")
    return model


def save_epoch_checkpoint(model, tokenizer, ckpt_dir, epoch):
    """Crash insurance for preemptible runtimes: overwrite ckpt_dir with an
    fp16 copy of the last completed epoch (point it at a Drive path)."""
    import copy

    start = time.perf_counter()
    ckpt_dir = Path(ckpt_dir)
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    snapshot = copy.deepcopy(model).half().cpu()
    snapshot.save_pretrained(ckpt_dir, safe_serialization=True)
    del snapshot
    if epoch == 1:
        tokenizer.save_pretrained(ckpt_dir)
    (ckpt_dir / "checkpoint_state.json").write_text(
        json.dumps({"last_completed_epoch": epoch}), encoding="utf-8")
    print(f"  epoch checkpoint -> {ckpt_dir} (epoch={epoch}, {time.perf_counter() - start:.0f}s)")


def save_hf_artifact(model, tokenizer, output_dir, class_bias, args, metrics):
    output_dir = Path(output_dir)
    hf_dir = output_dir / "hf_model"
    hf_dir.mkdir(parents=True, exist_ok=True)
    if args.save_fp16:
        model = model.half()
    model.save_pretrained(hf_dir, safe_serialization=True)
    tokenizer.save_pretrained(hf_dir)
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
        f"- Code/config changes: `{args.base_model}`, serializer={args.serializer}, replay={args.replay_mode}, max_length={args.max_length}, epochs={args.epochs}, lr={args.lr}, batch={args.batch_size}, bucket_multiplier={args.bucket_multiplier}.",
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
):
    if not args.save_val_logits:
        return ""
    path = Path(args.logits_dir) / f"{experiment_id}_val_logits.pt"
    path.parent.mkdir(parents=True, exist_ok=True)
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
            "base_model": args.base_model,
            "serializer_name": args.serializer,
            "max_length": args.max_length,
            "split": args.split,
            "fold_id": args.fold_id if args.split == "session_oof" else None,
            "n_folds": args.n_folds if args.split == "session_oof" else None,
            "seed": args.seed,
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
        print(f"split=final_refit train={len(final_idx)} replay_size={final_replay_size}")
        token_start = time.perf_counter()
        final_texts, text_cache_path = build_serialized_texts(final_samples, args, train_path, cache_scope="final")
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
                "model_family": "torch_gpu_transformer_final_refit",
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
                "decision": "final transformer refit complete; requires sparse artifact and package smoke test",
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
            f"- Code/config changes: `{args.base_model}`, serializer={args.serializer}, replay={args.replay_mode}, max_length={args.max_length}, epochs={args.epochs}, lr={args.lr}, batch={args.batch_size}.",
            f"- OOF class-bias source: {args.class_bias_artifact or 'none'}",
            f"- Rule boosts source: {args.rule_boosts_path or 'none'}",
            f"- Runtime or package-size concerns: runtime={runtime['total_sec']:.1f}s, tokenize={runtime['tokenize_sec']:.1f}s, train={runtime['train_sec']:.1f}s, artifact_size_mb={artifact_size:.1f}.",
            f"- Decision: final transformer refit complete; next train final sparse SVC artifact and smoke-test offline submission package.",
            "",
        ]
        if not args.no_research_log:
            with Path("research_log.md").open("a", encoding="utf-8") as f:
                f.write("\n".join(lines))
        print(f"saved final HF artifact: {args.output_dir} artifact_size_mb={artifact_size:.1f}")
        return

    train_idx, val_idx = split_for_args(samples, y, args)
    full_val_count = len(val_idx)
    val_idx = select_balanced_subset(val_idx, y, args.quick_val_size, args.seed + 17)
    samples, y, train_idx, sample_weights, replay_size = add_replay_examples(samples, y, train_idx, args)
    fold_text = f" fold={args.fold_id}/{args.n_folds}" if args.split == "session_oof" else ""
    print(f"split={args.split}{fold_text} train={len(train_idx)} val={len(val_idx)} full_val={full_val_count}")

    tokenizer = load_tokenizer_for_args(args)
    token_start = time.perf_counter()
    train_cache_scope = "train"
    if args.split == "session_oof":
        train_cache_scope = f"oof-fold{args.fold_id}-of{args.n_folds}"
    texts, text_cache_path = build_serialized_texts(samples, args, train_path, cache_scope=train_cache_scope)
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
    )
    train_sec = time.perf_counter() - train_start

    eval_start = time.perf_counter()
    logits, y_val, raw_metrics, ordered_val_idx = evaluate(model, tokenizer, encoded_features, lengths, y, val_idx, args, device)
    eval_sec = time.perf_counter() - eval_start
    print(f"  raw_macro_f1={raw_metrics['macro_f1']:.6f}")

    bias = torch.zeros(len(ALL_CLASSES), dtype=torch.float32)
    old_bias = None
    old_bias_metrics = None
    metrics = raw_metrics
    if args.tune_bias:
        print("  tuning old class bias")
        old_bias, _ = tune_class_bias(logits, y_val, rounds=2)
        old_pred = predict_with_bias(logits, old_bias)
        old_bias_metrics = f1_metrics(y_val, old_pred)
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
        decision = "keep as GPU candidate" if metrics["macro_f1"] >= args.keep_threshold else "discard or revisit"

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
            final_texts, _ = build_serialized_texts(final_samples, args, train_path, cache_scope="final")
            final_encoded_features, final_lengths, _ = tokenize_texts(tokenizer, final_texts, args, train_path, cache_scope="final")
            print(f"final replay_size={final_replay_size}")
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
        )
        save_hf_artifact(final_model, tokenizer, args.output_dir, bias, args, metrics)
        artifact_size = dir_size_mb(args.output_dir)
        print(f"saved HF artifact: {args.output_dir}")
    elif args.save_val_model:
        save_hf_artifact(model, tokenizer, args.output_dir, bias, args, metrics)
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
            "model_family": "torch_gpu_transformer",
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
            "macro_f1_raw": f"{raw_metrics['macro_f1']:.6f}",
            "macro_f1_bias_tuned": f"{old_bias_metrics['macro_f1']:.6f}" if old_bias_metrics else "",
            "macro_f1_bias_tuned_2stage": f"{metrics['macro_f1']:.6f}" if args.tune_bias else "",
            "macro_f1": f"{metrics['macro_f1']:.6f}",
            "weakest_classes": summarize_weak_classes(metrics),
            "top_confusions": json.dumps(metrics["top_confusions"][:8], ensure_ascii=False),
            "prediction_distribution": json.dumps(metrics["prediction_distribution"], ensure_ascii=False, sort_keys=True),
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
        append_log(experiment_id, args, raw_metrics, metrics, decision, runtime, val_logits_path, artifact_size, old_bias_metrics)

    metrics_path = Path("experiments/artifacts") / f"{experiment_id}_metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "experiment_id": experiment_id,
                "raw_metrics": raw_metrics,
                "old_bias_metrics": old_bias_metrics,
                "metrics": metrics,
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
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("  weakest classes:")
    for label, score in sorted(metrics["per_class_f1"].items(), key=lambda kv: kv[1])[:8]:
        print(f"    {label:18s} {score:.4f}")
    print("  top confusions:")
    for count, true_label, pred_label in metrics["top_confusions"][:10]:
        print(f"    {true_label:18s} -> {pred_label:18s} {count}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--base-model", default="distilbert-base-multilingual-cased")
    parser.add_argument("--serializer", choices=["current_v1", "current_v2", "current_v5", "current_v6", "current_v6e", "current_v7", "current_v7r", "current_v7rl", "current_v7rm", "current_v7rd", "current_v7rb", "current_v7rc", "current_v7rw", "current_v7rg", "current_v7rcgw", "current_v8", "current_v8t", "current_v9o", "current_v9f", "current_v9h", "state_v2", "recent_pairs_v1", "compact_events_v1", "hybrid_v1"], default="current_v1")
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
    parser.add_argument("--distill-temp", type=float, default=2.0)
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
    parser.add_argument("--grad-accum-steps", type=int, default=1,
                        help="optimizer step every N batches (effective batch = batch-size x N); matches teammate recipe batch4 x accum4")
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
    return args


if __name__ == "__main__":
    run(parse_args())
