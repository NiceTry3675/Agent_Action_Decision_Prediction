#!/usr/bin/env python3
"""Export pooled hidden states of one session-OOF fold's held-out rows.

Loads a fold model's raw HF checkpoint (the last-epoch save in its `_ckpt`
dir), encodes exactly the rows listed in that fold's saved OOF logits file,
and stores the final non-padding hidden state per row.  The recomputed
logits must agree with the saved fold logits (argmax gate) — this proves the
checkpoint is the same model that produced the deployed consensus, so the
hidden space is a legitimate reliability-refinement space (every stored row
is held out from this fold model's training).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
from pathlib import Path

import torch

from export_privileged_mode_cache import export_hidden, sha256_file, tokenize_all
from script import ALL_CLASSES, load_jsonl, serialize_transformer_sample
from train_transformer import make_batches

FOLD_HIDDEN_FORMAT = "oof-fold-hidden-cache-v1"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, help="raw HF dir (fold ckpt)")
    parser.add_argument("--fold-logits", required=True)
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--serializer", default="current_v1")
    parser.add_argument("--max-length", type=int, default=400)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--eval-bucket-multiplier", type=int, default=50)
    parser.add_argument("--pad-to-multiple-of", type=int, default=8)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--min-argmax-agreement", type=float, default=0.99)
    parser.add_argument("--expected-epoch", type=int, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    fold = torch.load(args.fold_logits, map_location="cpu", weights_only=False)
    if list(fold.get("classes") or []) != ALL_CLASSES:
        raise ValueError("fold logits class order mismatch")
    fold_ids = [str(value) for value in fold["ids"]]
    fold_logits = fold["logits"].float()

    checkpoint = Path(args.checkpoint)
    state_path = checkpoint / "checkpoint_state.json"
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        done = int(state.get("last_completed_epoch", -1))
        if done != args.expected_epoch:
            raise ValueError(
                f"checkpoint epoch mismatch: last_completed_epoch={done} "
                f"expected={args.expected_epoch}"
            )

    samples = load_jsonl(Path(args.data_dir) / "train.jsonl")
    by_id = {str(sample.get("id")): sample for sample in samples}
    missing = [sample_id for sample_id in fold_ids if sample_id not in by_id]
    if missing:
        raise ValueError(f"{len(missing)} fold ids missing from train.jsonl")
    fold_samples = [by_id[sample_id] for sample_id in fold_ids]
    texts = [
        serialize_transformer_sample(sample, args.serializer)
        for sample in fold_samples
    ]

    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(checkpoint, local_files_only=True)
    if tokenizer.padding_side != "right":
        raise ValueError(f"tokenizer must right-pad, got {tokenizer.padding_side!r}")
    features, lengths = tokenize_all(tokenizer, texts, args.max_length)

    device = torch.device(args.device)
    model = AutoModelForSequenceClassification.from_pretrained(
        checkpoint, local_files_only=True, torch_dtype=torch.float16
    ).to(device)
    if model.config.pad_token_id is None:
        model.config.pad_token_id = tokenizer.pad_token_id
    model.config.use_cache = False
    model.eval()

    batch_groups = list(
        make_batches(
            list(range(len(features))),
            args.batch_size,
            lengths=lengths,
            bucket_multiplier=args.eval_bucket_multiplier,
        )
    )
    hidden, logits = export_hidden(
        model, tokenizer, features, batch_groups, args.pad_to_multiple_of, device
    )

    agreement = float(
        (logits.argmax(dim=-1) == fold_logits.argmax(dim=-1)).float().mean()
    )
    max_diff = float((logits - fold_logits).abs().max())
    print(f"fold argmax agreement={agreement:.6f} max_logit_diff={max_diff:.4f}")
    if agreement < args.min_argmax_agreement:
        raise ValueError(
            f"recomputed logits disagree with saved fold logits: "
            f"agreement={agreement:.6f} < {args.min_argmax_agreement}"
        )

    payload = {
        "format": FOLD_HIDDEN_FORMAT,
        "ids": fold_ids,
        "hidden": hidden,
        "argmax_agreement": agreement,
        "max_logit_diff": max_diff,
        "serializer_name": args.serializer,
        "max_length": args.max_length,
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": sha256_file(checkpoint / "model.safetensors"),
        "fold_logits_sha256": sha256_file(args.fold_logits),
        "fold_id": fold.get("fold_id"),
        "n_folds": fold.get("n_folds"),
        "command": shlex.join(sys.argv),
        "code_sha256": {
            "export_oof_fold_hidden.py": sha256_file(Path(__file__)),
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, output)
    print(f"fold hidden -> {output} rows={len(fold_ids)} hidden={tuple(hidden.shape)}")


if __name__ == "__main__":
    main()
