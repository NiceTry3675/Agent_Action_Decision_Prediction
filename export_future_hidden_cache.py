#!/usr/bin/env python3
"""T2 Stage F1 (GPU half): frozen encoding of next-user future texts.

Encodes the *privileged* future texts (actual next-user plus matched
counterfactual donors, from ``audit_future_donors.py``'s payload) through the
same frozen leak-free screen checkpoint that produced the current-input
hidden cache, as **separate sequences** — the current input is never
re-tokenized or truncated by future text.  Only rows in the checkpoint's own
held-out fixed validation split are covered: the parent memorized every
outer-train row, so only the validation surface supports an honest oracle.

Output cache holds one pooled fp16 hidden state per unique future text plus
per-row index maps (actual / donors), keyed against the current-hidden cache.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shlex
import sys
from pathlib import Path

import torch

from export_privileged_mode_cache import (
    export_hidden,
    resolve_checkpoint,
    sha256_file,
    tokenize_all,
)
from train_transformer import make_batches

FUTURE_CACHE_FORMAT = "future-nextuser-hidden-cache-v1"
PAYLOAD_FORMAT = "future-nextuser-donor-payload-v1"
FUTURE_PREFIX = "future_user: "


def load_payload(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("format") != PAYLOAD_FORMAT:
        raise ValueError(f"unsupported payload format: {payload.get('format')!r}")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--payload",
        default="experiments/privileged_targets/20260711_future_nextuser_donors.json.gz",
    )
    parser.add_argument("--cache", required=True, help="current-hidden cache .pt")
    parser.add_argument(
        "--checkpoint", default="experiments/incoming/models/kd_hcx_m8_screen"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--future-max-length", type=int, default=128)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--eval-bucket-multiplier", type=int, default=50)
    parser.add_argument("--pad-to-multiple-of", type=int, default=8)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--limit-rows", type=int, default=0, help="smoke only")
    args = parser.parse_args()

    payload = load_payload(args.payload)
    cache = torch.load(args.cache, map_location="cpu", weights_only=False)
    cache_ids = [str(value) for value in cache["ids"]]
    if cache_ids != [str(value) for value in payload["ids"]]:
        raise ValueError("payload/cache id order mismatch (fail-closed, no join)")

    k = int(payload["k"])
    texts = payload["next_user"]
    statuses = payload["status"]
    donor_ids = payload["donor_ids"]
    id_to_index = {sample_id: index for index, sample_id in enumerate(cache_ids)}

    val_indices = [int(v) for v in cache["val_indices"].tolist()]
    if args.limit_rows:
        val_indices = val_indices[: args.limit_rows]

    unique_index = {}
    unique_texts = []

    def intern(text):
        slot = unique_index.get(text)
        if slot is None:
            slot = len(unique_texts)
            unique_index[text] = slot
            unique_texts.append(text)
        return slot

    actual_uidx = torch.full((len(val_indices),), -1, dtype=torch.int64)
    donor_uidx = torch.full((len(val_indices), k), -1, dtype=torch.int64)
    covered = 0
    for position, row_index in enumerate(val_indices):
        if statuses[row_index] != "recovered":
            continue
        donors = donor_ids[row_index]
        if not donors or len(donors) != k:
            continue
        actual_uidx[position] = intern(texts[row_index])
        for donor_position, donor_id in enumerate(donors):
            donor_row = id_to_index[donor_id]
            if statuses[donor_row] != "recovered":
                raise ValueError(f"donor without recovered text: {donor_id!r}")
            donor_uidx[position, donor_position] = intern(texts[donor_row])
        covered += 1

    serialized = [FUTURE_PREFIX + text for text in unique_texts]
    root, hf_dir, meta, weights = resolve_checkpoint(args.checkpoint)
    if sha256_file(weights) != cache.get("checkpoint_sha256"):
        raise ValueError("checkpoint does not match the current-hidden cache")

    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(hf_dir, local_files_only=True)
    if tokenizer.padding_side != "right":
        raise ValueError(f"tokenizer must right-pad, got {tokenizer.padding_side!r}")
    features, lengths = tokenize_all(tokenizer, serialized, args.future_max_length)
    truncated = sum(length >= args.future_max_length for length in lengths)
    if truncated:
        raise ValueError(
            f"{truncated} future texts hit max length {args.future_max_length}; raise it"
        )

    device = torch.device(args.device)
    model = AutoModelForSequenceClassification.from_pretrained(
        hf_dir, local_files_only=True, torch_dtype=torch.float16
    ).to(device)
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
    flattened = [index for batch in batch_groups for index in batch]
    if sorted(flattened) != list(range(len(features))):
        raise AssertionError("future batches do not cover every unique text once")

    hidden, _future_logits = export_hidden(
        model,
        tokenizer,
        features,
        batch_groups,
        args.pad_to_multiple_of,
        device,
    )

    output = {
        "format": FUTURE_CACHE_FORMAT,
        "future_prefix": FUTURE_PREFIX,
        "future_max_length": args.future_max_length,
        "k": k,
        "val_indices": torch.tensor(val_indices, dtype=torch.int64),
        "row_ids": [cache_ids[index] for index in val_indices],
        "actual_uidx": actual_uidx,
        "donor_uidx": donor_uidx,
        "unique_hidden": hidden,
        "unique_text_sha256": [
            hashlib.sha256(text.encode("utf-8")).hexdigest() for text in unique_texts
        ],
        "unique_token_lengths": torch.tensor(lengths, dtype=torch.int16),
        "covered_rows": covered,
        "pooling": cache.get("pooling"),
        "checkpoint_sha256": cache.get("checkpoint_sha256"),
        "current_cache_sha256": sha256_file(args.cache),
        "payload_sha256": sha256_file(args.payload),
        "command": shlex.join(sys.argv),
        "code_sha256": {
            "export_future_hidden_cache.py": sha256_file(Path(__file__)),
            "audit_future_donors.py": sha256_file(
                Path(__file__).with_name("audit_future_donors.py")
            ),
        },
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(output, output_path)
    print(
        f"future cache -> {output_path} rows={len(val_indices)} covered={covered} "
        f"unique_texts={len(unique_texts)} hidden={tuple(hidden.shape)}"
    )


if __name__ == "__main__":
    main()
