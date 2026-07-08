#!/usr/bin/env python3
"""Compare tokenizer behavior on Korean and Korean/Latin mixed text.

This is an offline diagnostic. It uses the same serializer path as training
and writes a compact JSON artifact for later experiment decisions.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from script import serialize_transformer_sample  # noqa: E402


HANGUL_RE = re.compile(r"[\uac00-\ud7a3\u3131-\u318e\u1100-\u11ff]")
LATIN_RE = re.compile(r"[A-Za-z]")
CHUNK_RE = re.compile(r"[^\s\|,;:()\[\]{}<>\"'`]+")


TOKENIZER_SPECS = {
    "qwen3_06b": {
        "path": "experiments/incoming/models/m7_qwen3_refit/hf_model",
        "family": "Qwen/Qwen3-0.6B",
    },
    "qwen35_08b": {
        "path": "experiments/incoming/models/m8_qwen35_refit/hf_model",
        "family": "igorktech/Qwen3.5-0.8B-Base-LM",
    },
    "hcx05b": {
        "path": "experiments/incoming/models/hcx05b_refit/hf_model",
        "family": "naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B",
    },
    "xlm_roberta_base": {
        "path": "xlm-roberta-base",
        "family": "xlm-roberta-base",
    },
    "mbert_cased": {
        "path": "bert-base-multilingual-cased",
        "family": "bert-base-multilingual-cased",
    },
}


def load_tokenizer(path: str):
    from transformers import AutoTokenizer, PreTrainedTokenizerFast

    try:
        tok = AutoTokenizer.from_pretrained(path, local_files_only=True, use_fast=True)
    except ValueError as exc:
        cfg_path = Path(path) / "tokenizer_config.json"
        tok_path = Path(path) / "tokenizer.json"
        if "TokenizersBackend" not in str(exc) or not cfg_path.exists() or not tok_path.exists():
            raise
        cfg = json.loads(cfg_path.read_text())
        tok = PreTrainedTokenizerFast(
            tokenizer_file=str(tok_path),
            eos_token=cfg.get("eos_token"),
            pad_token=cfg.get("pad_token") or cfg.get("eos_token"),
            bos_token=cfg.get("bos_token"),
            unk_token=cfg.get("unk_token"),
        )
    if tok.pad_token is None and tok.eos_token is not None:
        tok.pad_token = tok.eos_token
    # Avoid noisy "longer than max length" warnings during diagnostic tokenization.
    tok.model_max_length = int(1e9)
    return tok


def load_labels(path: Path) -> dict[str, str]:
    with path.open(newline="") as f:
        return {row["id"]: row["action"] for row in csv.DictReader(f)}


def read_samples(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [json.loads(line) for line in f]


def clean_chunk(chunk: str) -> str:
    return chunk.strip(".,!?…~“”‘’「」『』")


def quantile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def weighted_quantile(values_and_weights: list[tuple[float, int]], q: float) -> float | None:
    total = sum(weight for _, weight in values_and_weights)
    if total <= 0:
        return None
    threshold = q * total
    running = 0
    for value, weight in sorted(values_and_weights, key=lambda item: item[0]):
        running += weight
        if running >= threshold:
            return value
    return values_and_weights[-1][0]


def basic_stats(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    vals = sorted(values)
    return {
        "n": len(vals),
        "mean": round(statistics.fmean(vals), 4),
        "p50": quantile(vals, 0.50),
        "p90": quantile(vals, 0.90),
        "p95": quantile(vals, 0.95),
        "p99": quantile(vals, 0.99),
        "max": vals[-1],
        "over_384": sum(v > 384 for v in vals),
        "over_400": sum(v > 400 for v in vals),
        "over_416": sum(v > 416 for v in vals),
        "rate_over_384": round(sum(v > 384 for v in vals) / len(vals), 6),
        "rate_over_400": round(sum(v > 400 for v in vals) / len(vals), 6),
        "rate_over_416": round(sum(v > 416 for v in vals) / len(vals), 6),
    }


def ratio_stats(num: list[int], den: list[int], mask: list[bool] | None = None) -> dict[str, Any]:
    ratios = []
    deltas = []
    for idx, (a, b) in enumerate(zip(num, den, strict=True)):
        if mask is not None and not mask[idx]:
            continue
        if b <= 0:
            continue
        ratios.append(a / b)
        deltas.append(a - b)
    if not ratios:
        return {"n": 0}
    sorted_ratios = sorted(ratios)
    sorted_deltas = sorted(deltas)
    return {
        "n": len(ratios),
        "mean_ratio": round(statistics.fmean(ratios), 6),
        "p50_ratio": round(quantile(sorted_ratios, 0.50), 6),
        "p90_ratio": round(quantile(sorted_ratios, 0.90), 6),
        "mean_delta_tokens": round(statistics.fmean(deltas), 4),
        "p50_delta_tokens": quantile(sorted_deltas, 0.50),
        "p90_delta_tokens": quantile(sorted_deltas, 0.90),
    }


def batch_lengths(tokenizer, texts: list[str], *, add_special_tokens: bool, batch_size: int) -> list[int]:
    lengths: list[int] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        encoded = tokenizer(
            batch,
            add_special_tokens=add_special_tokens,
            padding=False,
            truncation=False,
            return_attention_mask=False,
        )
        lengths.extend(len(ids) for ids in encoded["input_ids"])
    return lengths


def class_breakdown(lengths: list[int], labels: list[str], mask: list[bool]) -> dict[str, Any]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for value, label, keep in zip(lengths, labels, mask, strict=True):
        if keep:
            grouped[label].append(value)
    return {label: basic_stats(vals) for label, vals in sorted(grouped.items())}


def summarize_chunks(
    name: str,
    counter: Counter[str],
    tokenizers: dict[str, Any],
    *,
    reference_name: str,
    batch_size: int,
) -> dict[str, Any]:
    chunks = list(counter)
    result: dict[str, Any] = {
        "unique_chunks": len(chunks),
        "occurrences": int(sum(counter.values())),
        "top_by_frequency": counter.most_common(20),
    }
    if not chunks:
        return result

    lengths_by_tok = {
        tok_name: batch_lengths(tok, chunks, add_special_tokens=False, batch_size=batch_size)
        for tok_name, tok in tokenizers.items()
    }
    char_lengths = [len(chunk) for chunk in chunks]
    for tok_name, lengths in lengths_by_tok.items():
        weighted_lengths = [(length, counter[chunk]) for chunk, length in zip(chunks, lengths, strict=True)]
        total_occ = sum(counter.values())
        total_chars = sum(len(chunk) * counter[chunk] for chunk in chunks)
        total_tokens = sum(length * counter[chunk] for chunk, length in zip(chunks, lengths, strict=True))
        result[tok_name] = {
            "mean_tokens_weighted": round(total_tokens / total_occ, 6),
            "p50_tokens_weighted": weighted_quantile(weighted_lengths, 0.50),
            "p90_tokens_weighted": weighted_quantile(weighted_lengths, 0.90),
            "p99_tokens_weighted": weighted_quantile(weighted_lengths, 0.99),
            "max_tokens_unique": max(lengths),
            "tokens_per_char_weighted": round(total_tokens / total_chars, 6),
            "single_token_occurrence_rate": round(
                sum(counter[chunk] for chunk, length in zip(chunks, lengths, strict=True) if length == 1) / total_occ,
                6,
            ),
            "mean_tokens_per_unique_chunk": round(statistics.fmean(lengths), 6),
        }

    if reference_name in lengths_by_tok:
        ref_lengths = lengths_by_tok[reference_name]
        for tok_name, lengths in lengths_by_tok.items():
            if tok_name == reference_name:
                continue
            values = []
            for chunk, length, ref_length in zip(chunks, lengths, ref_lengths, strict=True):
                if ref_length > 0:
                    values.append((length / ref_length, counter[chunk]))
            result[f"{tok_name}_vs_{reference_name}"] = {
                "mean_ratio_weighted": round(
                    sum(ratio * weight for ratio, weight in values) / sum(weight for _, weight in values), 6
                ),
                "p50_ratio_weighted": weighted_quantile(values, 0.50),
                "p90_ratio_weighted": weighted_quantile(values, 0.90),
            }

    qwen_name = "qwen3_06b"
    if qwen_name in lengths_by_tok and reference_name in lengths_by_tok:
        qwen_lengths = lengths_by_tok[qwen_name]
        ref_lengths = lengths_by_tok[reference_name]
        rows = []
        for idx, chunk in enumerate(chunks):
            ref = ref_lengths[idx]
            ratio = qwen_lengths[idx] / ref if ref else float("inf")
            rows.append((ratio, qwen_lengths[idx], counter[chunk], chunk, ref, char_lengths[idx]))
        rows.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        result["qwen3_worst_ratio_examples"] = [
            {
                "chunk": chunk,
                "count": count,
                "chars": chars,
                "qwen3_tokens": qlen,
                f"{reference_name}_tokens": rlen,
                "ratio": round(ratio, 4),
            }
            for ratio, qlen, count, chunk, rlen, chars in rows[:30]
        ]

        rows.sort(key=lambda item: (item[1], item[2]), reverse=True)
        result["qwen3_longest_examples"] = [
            {
                "chunk": chunk,
                "count": count,
                "chars": chars,
                "qwen3_tokens": qlen,
                f"{reference_name}_tokens": rlen,
                "ratio": round(ratio, 4),
            }
            for ratio, qlen, count, chunk, rlen, chars in rows[:30]
        ]

    result["name"] = name
    return result


def sample_token_splits(tokenizers: dict[str, Any], chunks: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for chunk in chunks:
        out[chunk] = {}
        for name, tok in tokenizers.items():
            ids = tok(chunk, add_special_tokens=False, return_attention_mask=False)["input_ids"]
            if ids and isinstance(ids[0], list):
                ids = ids[0]
            out[chunk][name] = {
                "n": len(ids),
                "tokens": tok.convert_ids_to_tokens(ids),
            }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="open/data/train.jsonl")
    parser.add_argument("--labels", default="open/data/train_labels.csv")
    parser.add_argument("--serializer", default="current_v1")
    parser.add_argument("--output", default="experiments/artifacts/20260708_qwen_korean_tokenizer_probe.json")
    parser.add_argument("--batch-size", type=int, default=2048)
    args = parser.parse_args()

    data_path = REPO_ROOT / args.data
    labels_path = REPO_ROOT / args.labels
    output_path = REPO_ROOT / args.output

    labels_by_id = load_labels(labels_path)
    samples = read_samples(data_path)
    texts = [serialize_transformer_sample(sample, args.serializer) for sample in samples]
    labels = [labels_by_id.get(sample["id"], "") for sample in samples]
    lang_prefs = [
        str((sample.get("session_meta") or {}).get("language_pref") or "")
        for sample in samples
    ]

    row_has_hangul = [bool(HANGUL_RE.search(text)) for text in texts]
    row_has_latin = [bool(LATIN_RE.search(text)) for text in texts]
    row_has_mixed_chunk: list[bool] = []
    mixed_counter: Counter[str] = Counter()
    hangul_counter: Counter[str] = Counter()
    hangul_no_latin_counter: Counter[str] = Counter()

    for text in texts:
        mixed = False
        for raw_chunk in CHUNK_RE.findall(text):
            chunk = clean_chunk(raw_chunk)
            if not chunk:
                continue
            has_hangul = bool(HANGUL_RE.search(chunk))
            has_latin = bool(LATIN_RE.search(chunk))
            if has_hangul and has_latin:
                mixed_counter[chunk] += 1
                mixed = True
            if has_hangul:
                hangul_counter[chunk] += 1
                if not has_latin:
                    hangul_no_latin_counter[chunk] += 1
        row_has_mixed_chunk.append(mixed)

    tokenizers = {name: load_tokenizer(spec["path"]) for name, spec in TOKENIZER_SPECS.items()}
    lengths_by_tok = {
        name: batch_lengths(tok, texts, add_special_tokens=True, batch_size=args.batch_size)
        for name, tok in tokenizers.items()
    }

    masks = {
        "all": [True] * len(texts),
        "hangul_rows": row_has_hangul,
        "non_hangul_rows": [not value for value in row_has_hangul],
        "mixed_hangul_latin_chunk_rows": row_has_mixed_chunk,
        "hangul_without_mixed_chunk_rows": [
            has_h and not has_m for has_h, has_m in zip(row_has_hangul, row_has_mixed_chunk, strict=True)
        ],
        "language_pref_ko": [lang == "ko" for lang in lang_prefs],
        "language_pref_en": [lang == "en" for lang in lang_prefs],
    }

    row_summary: dict[str, Any] = {
        "row_counts": {name: int(sum(mask)) for name, mask in masks.items()},
        "tokenizers": {
            name: {
                "family": TOKENIZER_SPECS[name]["family"],
                "path": TOKENIZER_SPECS[name]["path"],
                "loaded_class": type(tokenizers[name]).__name__,
                "vocab_size": getattr(tokenizers[name], "vocab_size", None),
                "stats_by_row_group": {
                    group_name: basic_stats([value for value, keep in zip(lengths_by_tok[name], mask, strict=True) if keep])
                    for group_name, mask in masks.items()
                },
                "per_class_hangul_rows": class_breakdown(lengths_by_tok[name], labels, row_has_hangul),
                "per_class_mixed_rows": class_breakdown(lengths_by_tok[name], labels, row_has_mixed_chunk),
            }
            for name in tokenizers
        },
        "paired_ratios_vs_hcx05b": {
            name: {
                group_name: ratio_stats(lengths_by_tok[name], lengths_by_tok["hcx05b"], mask)
                for group_name, mask in masks.items()
            }
            for name in tokenizers
            if name != "hcx05b"
        },
    }

    chunks_summary = {
        "mixed_hangul_latin_chunks": summarize_chunks(
            "mixed_hangul_latin_chunks",
            mixed_counter,
            tokenizers,
            reference_name="hcx05b",
            batch_size=args.batch_size,
        ),
        "all_hangul_chunks": summarize_chunks(
            "all_hangul_chunks",
            hangul_counter,
            tokenizers,
            reference_name="hcx05b",
            batch_size=args.batch_size,
        ),
        "hangul_no_latin_chunks": summarize_chunks(
            "hangul_no_latin_chunks",
            hangul_no_latin_counter,
            tokenizers,
            reference_name="hcx05b",
            batch_size=args.batch_size,
        ),
    }

    split_examples = [
        "Dockerfile에서",
        "README.md에",
        "CI에서",
        "grep으로",
        "requirements.txt에",
        "한국어English",
        "API응답",
        "테스트ABC",
        "한글테스트",
        "파일",
    ]
    payload = {
        "artifact": output_path.name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "data_path": args.data,
        "labels_path": args.labels,
        "serializer": args.serializer,
        "row_count": len(samples),
        "definition": {
            "hangul": r"[\uac00-\ud7a3\u3131-\u318e\u1100-\u11ff]",
            "latin": r"[A-Za-z]",
            "mixed_chunk": "a non-whitespace chunk containing at least one Hangul codepoint and one Latin letter",
            "row_lengths": "tokenizer(text, add_special_tokens=True), no truncation",
            "chunk_lengths": "tokenizer(chunk, add_special_tokens=False), no truncation",
        },
        "row_summary": row_summary,
        "chunk_summary": chunks_summary,
        "token_split_examples": sample_token_splits(tokenizers, split_examples),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "output": str(output_path),
        "rows": len(samples),
        "row_counts": row_summary["row_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
