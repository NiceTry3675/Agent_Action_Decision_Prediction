#!/usr/bin/env python3
"""Export the exact frozen HCX classifier inputs used by a mode-head probe.

The exporter deliberately accepts only a leak-free, fp16 fixed-validation
checkpoint.  It records the final non-padding hidden state, the checkpoint's
parent 14-way logits, and the canonical session split.  A later CPU/GPU probe
can therefore train many tiny residual heads without running the backbone
again.
"""

import argparse
import hashlib
import json
import platform
import shlex
import sys
import time
from pathlib import Path

import torch
import transformers
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from privileged_event_modes import (
    event_mode_signature,
    reconstruct_full_events,
    turn_bin,
)
from script import ALL_CLASSES, load_jsonl, serialize_transformer_sample
from train import CLASS_TO_ID, load_labels, session_id, split_indices
from train import f1_metrics
from train_transformer import make_batches


CACHE_FORMAT = "selective-privileged-mode-cache-v1"


def llama_sequence_lengths_446(input_ids, pad_token_id):
    """Exact pooling indices from transformers 4.46.3 Llama sequence classification."""

    if input_ids.ndim != 2:
        raise ValueError(f"input_ids must be rank 2, got {tuple(input_ids.shape)}")
    return (
        input_ids.eq(int(pad_token_id)).to(torch.int32).argmax(-1) - 1
    ) % input_ids.shape[-1]


def sha256_file(path, chunk_size=8 * 1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def resolve_checkpoint(path):
    path = Path(path).resolve()
    if (path / "hf_model").is_dir():
        root = path
        hf_dir = path / "hf_model"
    elif path.is_dir() and (path / "config.json").is_file():
        hf_dir = path
        root = path.parent
    else:
        raise FileNotFoundError(f"checkpoint must be a run dir or hf_model dir: {path}")

    meta_path = root / "hf_meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"fixed-screen checkpoint is missing hf_meta.json: {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if bool(meta.get("final_refit", False)):
        raise ValueError("refusing a final-refit checkpoint: frozen validation would leak")
    if meta.get("validation_split") != "session":
        raise ValueError(
            f"fixed-screen checkpoint must use session validation, got {meta.get('validation_split')!r}"
        )
    if not bool(meta.get("saved_fp16", False)):
        raise ValueError("frozen probe requires the saved fp16 screen checkpoint")
    if list(meta.get("classes") or []) != ALL_CLASSES:
        raise ValueError("checkpoint class order does not match ALL_CLASSES")
    weights = hf_dir / "model.safetensors"
    if not weights.is_file():
        raise FileNotFoundError(f"fp16 model.safetensors is required: {weights}")
    return root, hf_dir, meta, weights


def load_anchor(path):
    payload = torch.load(path, map_location="cpu", weights_only=False)
    required = {"ids", "y_true", "logits", "classes", "split", "seed"}
    missing = required - set(payload)
    if missing:
        raise ValueError(f"validation anchor missing keys: {sorted(missing)}")
    if list(payload["classes"]) != ALL_CLASSES:
        raise ValueError("validation anchor class order does not match ALL_CLASSES")
    if payload["split"] != "session" or int(payload["seed"]) != 42:
        raise ValueError(
            f"expected canonical session/seed42 anchor, got {payload['split']}/{payload['seed']}"
        )
    row_count = len(payload["ids"])
    if len(payload["y_true"]) != row_count:
        raise ValueError("validation anchor ids/y_true row counts do not match")
    if tuple(payload["logits"].shape) != (row_count, len(ALL_CLASSES)):
        raise ValueError("validation anchor logits must have shape [N,14]")
    return payload


def build_dataset(data_dir):
    data_dir = Path(data_dir)
    samples = load_jsonl(data_dir / "train.jsonl")
    label_map = load_labels(data_dir / "train_labels.csv")
    ids = [str(sample.get("id", "")) for sample in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("train.jsonl contains duplicate ids")
    missing = [sample_id for sample_id in ids if sample_id not in label_map]
    if missing:
        raise ValueError(f"labels missing for {len(missing)} samples; first={missing[0]}")
    y = [CLASS_TO_ID[label_map[sample_id]] for sample_id in ids]
    train_idx, val_idx = split_indices(samples, y, "session", 42)
    return samples, ids, y, train_idx, val_idx


def validate_anchor_split(anchor, ids, y, val_idx):
    expected = {ids[i]: y[i] for i in val_idx}
    anchor_ids = [str(value) for value in anchor["ids"]]
    if len(anchor_ids) != len(set(anchor_ids)):
        raise ValueError("validation anchor contains duplicate ids")
    if set(anchor_ids) != set(expected):
        missing = sorted(set(expected) - set(anchor_ids))
        extra = sorted(set(anchor_ids) - set(expected))
        raise ValueError(
            f"anchor validation ids do not match session/seed42 split: "
            f"missing={missing[:3]} extra={extra[:3]}"
        )
    bad = [
        sample_id
        for sample_id, target in zip(anchor_ids, anchor["y_true"])
        if expected[sample_id] != int(target)
    ]
    if bad:
        raise ValueError(f"validation anchor labels disagree for {len(bad)} ids; first={bad[0]}")


def tokenize_all(tokenizer, texts, max_length):
    start = time.perf_counter()
    encoded = tokenizer(
        texts,
        padding=False,
        truncation=True,
        max_length=max_length,
    )
    features = [
        {key: encoded[key][i] for key in encoded}
        for i in range(len(texts))
    ]
    lengths = [len(feature["input_ids"]) for feature in features]
    print(
        f"tokenized {len(texts)} rows in {time.perf_counter() - start:.1f}s "
        f"mean={sum(lengths) / max(1, len(lengths)):.1f} max={max(lengths, default=0)}"
    )
    return features, lengths


def export_hidden(
    model,
    tokenizer,
    features,
    batch_groups,
    pad_to_multiple_of,
    device,
):
    hidden_dim = int(model.config.hidden_size)
    n_rows = len(features)
    hidden = torch.empty((n_rows, hidden_dim), dtype=torch.float16)
    logits = torch.empty((n_rows, len(ALL_CLASSES)), dtype=torch.float32)
    captured = []

    def capture_score_input(_module, inputs):
        if len(inputs) != 1:
            raise RuntimeError(f"unexpected score inputs: {len(inputs)}")
        captured.append(inputs[0].detach())

    hook = model.score.register_forward_pre_hook(capture_score_input)
    start = time.perf_counter()
    rows_done = 0
    try:
        with torch.inference_mode():
            for batch_no, batch_idx in enumerate(batch_groups, 1):
                batch_features = [features[i] for i in batch_idx]
                encoded = tokenizer.pad(
                    batch_features,
                    padding=True,
                    pad_to_multiple_of=(
                        pad_to_multiple_of if pad_to_multiple_of > 1 else None
                    ),
                    return_tensors="pt",
                )
                encoded = {key: value.to(device, non_blocking=True) for key, value in encoded.items()}
                captured.clear()
                with torch.amp.autocast(
                    device_type="cuda",
                    enabled=device.type == "cuda",
                    dtype=torch.float16,
                ):
                    output = model(**encoded)
                if len(captured) != 1:
                    raise RuntimeError(f"score hook fired {len(captured)} times for one batch")
                token_hidden = captured[0]
                input_ids = encoded["input_ids"]
                # Match transformers 4.46.3 LlamaForSequenceClassification
                # exactly.  The later rightmost-nonpad implementation is
                # equivalent on this corpus, but it is not the checkpoint's
                # original execution contract.
                last_non_pad = llama_sequence_lengths_446(
                    input_ids, model.config.pad_token_id
                )
                pooled = token_hidden[
                    torch.arange(len(batch_idx), device=device), last_non_pad
                ]
                hidden[batch_idx] = pooled.detach().to(device="cpu", dtype=torch.float16)
                logits[batch_idx] = output.logits.detach().to(device="cpu", dtype=torch.float32)
                rows_done += len(batch_idx)
                if batch_no % 100 == 0 or rows_done == n_rows:
                    print(
                        f"  cache batches={batch_no} rows={rows_done}/{n_rows} "
                        f"elapsed={time.perf_counter() - start:.0f}s"
                    )
    finally:
        hook.remove()
    return hidden, logits


def compare_anchor(
    anchor,
    ids,
    parent_logits,
    max_abs_tolerance,
    min_argmax_agreement,
    max_macro_error,
):
    by_id = {sample_id: i for i, sample_id in enumerate(ids)}
    idx = torch.tensor([by_id[str(sample_id)] for sample_id in anchor["ids"]], dtype=torch.long)
    actual = parent_logits[idx]
    expected = anchor["logits"].float()
    if actual.shape != expected.shape:
        raise ValueError(f"anchor logits shape mismatch: {actual.shape} != {expected.shape}")
    diff = (actual - expected).abs()
    agreement = float((actual.argmax(1) == expected.argmax(1)).float().mean())
    argmax_mismatches = int((actual.argmax(1) != expected.argmax(1)).sum())
    max_abs = float(diff.max())
    mean_abs = float(diff.mean())
    y_true = [int(value) for value in anchor["y_true"]]
    actual_macro = f1_metrics(y_true, actual.argmax(1).tolist())["macro_f1"]
    expected_macro = f1_metrics(y_true, expected.argmax(1).tolist())["macro_f1"]
    print(
        f"anchor comparison argmax={agreement:.6%} mismatches={argmax_mismatches} "
        f"max_abs={max_abs:.7f} mean_abs={mean_abs:.7f} "
        f"macro={actual_macro:.9f} anchor_macro={expected_macro:.9f}"
    )
    if agreement < min_argmax_agreement:
        raise ValueError(
            f"screen checkpoint argmax agreement {agreement:.9f} is below "
            f"{min_argmax_agreement:.9f}"
        )
    if max_abs > max_abs_tolerance:
        raise ValueError(
            f"screen checkpoint logit drift {max_abs:.7f} exceeds {max_abs_tolerance:.7f}"
        )
    macro_error = abs(actual_macro - expected_macro)
    if macro_error > max_macro_error:
        raise ValueError(
            f"screen checkpoint macro drift {macro_error:.9f} exceeds {max_macro_error:.9f}"
        )
    return {
        "argmax_agreement": agreement,
        "argmax_mismatches": argmax_mismatches,
        "max_abs": max_abs,
        "mean_abs": mean_abs,
        "macro_f1": actual_macro,
        "anchor_macro_f1": expected_macro,
        "macro_abs_error": macro_error,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, help="fixed-screen run dir or hf_model dir")
    parser.add_argument("--val-anchor", required=True, help="canonical 14,001-row val logits payload")
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="must match the screen eval batch size recorded in hf_meta.json",
    )
    parser.add_argument("--eval-bucket-multiplier", type=int, default=50)
    parser.add_argument("--pad-to-multiple-of", type=int, default=8)
    parser.add_argument(
        "--model-load-dtype",
        choices=["float16", "float32"],
        default="float16",
        help="parameter dtype after loading the saved fp16 artifact",
    )
    parser.add_argument(
        "--validation-only",
        action="store_true",
        help="run only the exact held-out batches and anchor check; do not save a cache",
    )
    parser.add_argument("--max-anchor-logit-error", type=float, default=0.02)
    parser.add_argument("--min-anchor-argmax-agreement", type=float, default=0.9995)
    parser.add_argument("--max-anchor-macro-error", type=float, default=0.0001)
    args = parser.parse_args()

    if transformers.__version__ != "4.46.3":
        raise RuntimeError(
            "exact screen-cache export requires transformers==4.46.3; "
            f"active version is {transformers.__version__}"
        )

    root, hf_dir, meta, weights = resolve_checkpoint(args.checkpoint)
    anchor = load_anchor(args.val_anchor)
    samples, ids, y, train_idx, val_idx = build_dataset(args.data_dir)
    validate_anchor_split(anchor, ids, y, val_idx)
    labels = [ALL_CLASSES[label_id] for label_id in y]
    reconstruction = reconstruct_full_events(samples, labels)
    if reconstruction.metadata["conflicting_rows"]:
        raise ValueError("full-event reconstruction contains conflicts")
    if reconstruction.metadata["name_agreement_rate"] != 1.0:
        raise ValueError("full-event reconstruction name agreement is not 100%")
    candidate_mode_labels = [
        event_mode_signature(event) if event is not None else None
        for event in reconstruction.events
    ]

    serializer = meta.get("serializer_name", "current_v1")
    max_length = int(meta.get("max_length", 384))
    if serializer != anchor.get("serializer_name") or max_length != int(anchor.get("max_length")):
        raise ValueError(
            f"checkpoint/anchor serializer mismatch: {serializer}/{max_length} vs "
            f"{anchor.get('serializer_name')}/{anchor.get('max_length')}"
        )
    texts = [serialize_transformer_sample(sample, serializer) for sample in samples]
    tokenizer = AutoTokenizer.from_pretrained(hf_dir, local_files_only=True)
    if tokenizer.padding_side != "right":
        raise ValueError(f"screen tokenizer must right-pad, got {tokenizer.padding_side!r}")
    features, lengths = tokenize_all(tokenizer, texts, max_length)
    del texts

    screen_batch_size = int(meta.get("batch_size", 64))
    batch_size = screen_batch_size if args.batch_size is None else args.batch_size
    if batch_size != screen_batch_size:
        raise ValueError(
            f"cache batch size must match screen eval batch size: "
            f"{batch_size} != {screen_batch_size}"
        )
    train_batches = list(
        make_batches(
            train_idx,
            batch_size,
            lengths=lengths,
            bucket_multiplier=args.eval_bucket_multiplier,
        )
    )
    # Keep the held-out rows in exactly the same batch composition/order as
    # train_transformer.evaluate(), so the fp16 anchor comparison is meaningful.
    val_batches = list(
        make_batches(
            val_idx,
            batch_size,
            lengths=lengths,
            bucket_multiplier=args.eval_bucket_multiplier,
        )
    )
    batch_groups = val_batches if args.validation_only else train_batches + val_batches
    flattened = [index for batch in batch_groups for index in batch]
    expected_indices = set(val_idx) if args.validation_only else set(range(len(ids)))
    if len(flattened) != len(expected_indices) or set(flattened) != expected_indices:
        raise AssertionError("cache batches do not cover every original row exactly once")

    device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    dtype = torch.float16 if args.model_load_dtype == "float16" else torch.float32
    model = AutoModelForSequenceClassification.from_pretrained(
        hf_dir,
        local_files_only=True,
        torch_dtype=dtype,
    ).to(device)
    model.config.use_cache = False
    model.eval()
    hidden, parent_logits = export_hidden(
        model,
        tokenizer,
        features,
        batch_groups,
        args.pad_to_multiple_of,
        device,
    )
    anchor_check = compare_anchor(
        anchor,
        ids,
        parent_logits,
        args.max_anchor_logit_error,
        args.min_anchor_argmax_agreement,
        args.max_anchor_macro_error,
    )
    if args.validation_only:
        print("validation-only anchor diagnostic passed; cache not written")
        return

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": CACHE_FORMAT,
        "ids": ids,
        "y_true": torch.tensor(y, dtype=torch.int64),
        "hidden": hidden,
        "parent_logits": parent_logits,
        "token_lengths": torch.tensor(lengths, dtype=torch.int16),
        "event_recovery_statuses": list(reconstruction.statuses),
        "event_recovered": torch.tensor(
            [status == "recovered" for status in reconstruction.statuses],
            dtype=torch.bool,
        ),
        "candidate_mode_labels": candidate_mode_labels,
        "session_ids": [session_id(sample_id) for sample_id in ids],
        "turn_bins": [turn_bin(sample) for sample in samples],
        "train_indices": torch.tensor(train_idx, dtype=torch.int64),
        "val_indices": torch.tensor(val_idx, dtype=torch.int64),
        "classes": ALL_CLASSES,
        "split": "session",
        "seed": 42,
        "serializer_name": serializer,
        "max_length": max_length,
        "checkpoint_root": str(root),
        "checkpoint_sha256": sha256_file(weights),
        "checkpoint_meta_sha256": sha256_file(root / "hf_meta.json"),
        "checkpoint_meta": meta,
        "anchor_sha256": sha256_file(args.val_anchor),
        "train_jsonl_sha256": sha256_file(Path(args.data_dir) / "train.jsonl"),
        "train_labels_sha256": sha256_file(Path(args.data_dir) / "train_labels.csv"),
        "class_order_sha256": hashlib.sha256(
            json.dumps(ALL_CLASSES, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "pooling": "transformers_4.46.3_first_pad_minus_one_modulo_score_input",
        "batch_contract": {
            "batch_size": batch_size,
            "eval_bucket_multiplier": args.eval_bucket_multiplier,
            "pad_to_multiple_of": args.pad_to_multiple_of,
            "train_then_validation_batches": True,
        },
        "runtime": {
            "command": shlex.join(sys.argv),
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "cuda": torch.version.cuda,
            "device": str(device),
            "model_load_dtype": args.model_load_dtype,
        },
        "code_sha256": {
            "export_privileged_mode_cache.py": sha256_file(Path(__file__)),
            "script.py": sha256_file(Path(__file__).with_name("script.py")),
            "train_transformer.py": sha256_file(
                Path(__file__).with_name("train_transformer.py")
            ),
            "privileged_event_modes.py": sha256_file(
                Path(__file__).with_name("privileged_event_modes.py")
            ),
        },
        "rows": "original_only_no_replay",
        "reconstruction": reconstruction.metadata,
        "val_anchor": str(Path(args.val_anchor).resolve()),
        "anchor_check": anchor_check,
    }
    torch.save(payload, output)
    print(
        f"saved {output} ({output.stat().st_size / 1024**2:.1f} MiB) "
        f"hidden={tuple(hidden.shape)}"
    )


if __name__ == "__main__":
    main()
