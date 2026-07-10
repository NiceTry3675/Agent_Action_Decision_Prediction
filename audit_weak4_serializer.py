import argparse
import hashlib
import json
from pathlib import Path

import torch

from script import (
    ALL_CLASSES,
    select_weak4_routes,
    serialize_transformer_sample,
    serialize_transformer_sample_current_parts,
    weak_nav_line,
    weak_nav_path_values,
)
from tune_weak4_router import length_summary, torch_load


EXPECTED_TRAIN_ROWS = 70000
CURRENT_V1_70K_SHA256 = "902206d8e369a30a92f941efa405fa912ef53d40661aba35627e62ecbf9f7c25"


def token_lengths(tokenizer, texts):
    encoded = tokenizer(
        texts,
        padding=False,
        truncation=False,
        return_length=True,
    )
    values = encoded.get("length")
    if values is None:
        values = [len(row) for row in encoded["input_ids"]]
    return [int(value) for value in values]


def update_framed_hash(digest, text):
    encoded = text.encode("utf-8")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def expected_specialist_parts(sample, serializer_name):
    parts = serialize_transformer_sample_current_parts(sample)
    inserted = [weak_nav_line(sample)]
    if serializer_name == "weak_nav_paths_v1":
        paths = weak_nav_path_values(sample)
        inserted.append(f"last_paths: {' | '.join(paths) if paths else 'none'}")
    expected = parts[:1] + inserted + parts[1:]
    recovered = expected[:1] + expected[1 + len(inserted):]
    return parts, expected, recovered


def audit(args):
    from transformers import AutoTokenizer

    payload = torch_load(args.main_logits)
    if list(payload.get("classes") or []) != ALL_CLASSES:
        raise ValueError("main logits payload has a non-canonical class order")
    ids = [str(value) for value in payload["ids"]]
    logits = torch.as_tensor(payload["logits"]).float().cpu()
    if logits.shape != (len(ids), len(ALL_CLASSES)):
        raise ValueError(f"main logits shape mismatch: {tuple(logits.shape)}")
    routed = select_weak4_routes(logits, list(range(4)), args.route_fraction)
    routed_ids = {ids[idx] for idx in routed.tolist()}

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_dir, local_files_only=True)
    digest = hashlib.sha256()
    current_lengths = []
    specialist_lengths = []
    routed_lengths = []
    rows = 0
    batch_samples = []

    def flush():
        if not batch_samples:
            return
        current_texts = []
        specialist_texts = []
        batch_ids = []
        for sample in batch_samples:
            current_parts, expected_parts, recovered_parts = expected_specialist_parts(
                sample, args.serializer_name
            )
            current_text = "\n".join(current_parts)
            specialist_text = serialize_transformer_sample(sample, args.serializer_name)
            if specialist_text != "\n".join(expected_parts):
                raise AssertionError(f"specialist parts insertion mismatch for id={sample.get('id')}")
            if "\n".join(recovered_parts) != current_text:
                raise AssertionError(f"nav removal did not recover current_v1 for id={sample.get('id')}")
            update_framed_hash(digest, current_text)
            current_texts.append(current_text)
            specialist_texts.append(specialist_text)
            batch_ids.append(str(sample.get("id", "")))
        current_lengths.extend(token_lengths(tokenizer, current_texts))
        batch_specialist_lengths = token_lengths(tokenizer, specialist_texts)
        specialist_lengths.extend(batch_specialist_lengths)
        routed_lengths.extend(
            length for sample_id, length in zip(batch_ids, batch_specialist_lengths)
            if sample_id in routed_ids
        )
        batch_samples.clear()

    with Path(args.train_jsonl).open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                batch_samples.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{args.train_jsonl}:{line_no}: invalid JSON") from exc
            rows += 1
            if len(batch_samples) >= args.batch_size:
                flush()
    flush()

    current_hash = digest.hexdigest()
    if rows != EXPECTED_TRAIN_ROWS:
        raise AssertionError(f"expected {EXPECTED_TRAIN_ROWS} train rows, got {rows}")
    if current_hash != CURRENT_V1_70K_SHA256:
        raise AssertionError(
            "current_v1 byte regression: "
            f"expected={CURRENT_V1_70K_SHA256} actual={current_hash}"
        )
    expected_routed_found = sum(sample_id in routed_ids for sample_id in ids)
    if len(routed_lengths) != expected_routed_found:
        raise AssertionError(
            f"routed id join mismatch: expected={expected_routed_found} found={len(routed_lengths)}"
        )

    report = {
        "serializer_name": args.serializer_name,
        "train_rows": rows,
        "current_v1_sha256": current_hash,
        "current_v1_byte_regression_passed": True,
        "main_logits": str(args.main_logits),
        "route_fraction": args.route_fraction,
        "routed_validation_rows": len(routed),
        "routed_rows_found_in_train": len(routed_lengths),
        "max_length": args.max_length,
        "token_lengths": {
            "current_v1_all_70k": length_summary(current_lengths, args.max_length),
            f"{args.serializer_name}_all_70k": length_summary(specialist_lengths, args.max_length),
            f"{args.serializer_name}_routed_validation_subset": length_summary(
                routed_lengths, args.max_length
            ),
            "mean_added_tokens": (
                sum(specialist_lengths) - sum(current_lengths)
            ) / max(1, len(current_lengths)),
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"serializer audit OK: rows={rows} sha256={current_hash} "
        f"mean_added_tokens={report['token_lengths']['mean_added_tokens']:.2f}"
    )
    print(f"saved {output}")
    return report


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-jsonl", default="open/data/train.jsonl")
    parser.add_argument("--main-logits", required=True)
    parser.add_argument(
        "--tokenizer-dir",
        default="experiments/incoming/models/kd_m8_refit/hf_model",
    )
    parser.add_argument(
        "--serializer-name",
        choices=["weak_nav_v1", "weak_nav_paths_v1"],
        default="weak_nav_v1",
    )
    parser.add_argument("--route-fraction", type=float, default=0.30)
    parser.add_argument("--max-length", type=int, default=384)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument(
        "--output",
        default="experiments/artifacts/weak_nav_v1_serializer_audit.json",
    )
    args = parser.parse_args()
    if not 0.0 <= args.route_fraction <= 1.0:
        parser.error("--route-fraction must be in [0, 1]")
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    return args


if __name__ == "__main__":
    audit(parse_args())
