import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import torch

from script import (
    ALL_CLASSES,
    disable_decoder_cache,
    load_hf_model,
    load_int8_state_dict,
    load_jsonl,
    safe_text,
    serialize_transformer_sample,
)


def load_labels(labels_path):
    if not labels_path or not labels_path.exists():
        return {}
    with labels_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or reader.fieldnames[:2] != ["id", "action"]:
            raise ValueError(f"labels file must start with id,action: {labels_path}")
        return {row["id"]: row["action"] for row in reader}


def resolve_input_jsonl(args):
    if args.input_jsonl:
        return Path(args.input_jsonl)
    return Path(args.data_dir) / f"{args.split}.jsonl"


def load_source_ids(args):
    input_jsonl = resolve_input_jsonl(args)
    samples = load_jsonl(input_jsonl)
    if args.limit:
        samples = samples[: args.limit]
    return input_jsonl, samples, [safe_text(sample.get("id", "")) for sample in samples]


def dtype_for_save(name):
    if name == "fp16":
        return torch.float16
    if name == "fp32":
        return torch.float32
    raise ValueError(f"unsupported dtype: {name}")


def tokenized_features(tokenizer, texts, max_length, chunk_size):
    features = []
    total = (len(texts) + chunk_size - 1) // chunk_size if texts else 0
    for chunk_no, start in enumerate(range(0, len(texts), chunk_size), 1):
        chunk = texts[start:start + chunk_size]
        encoded = tokenizer(chunk, padding=False, truncation=True, max_length=max_length)
        keys = list(encoded.keys())
        features.extend({key: encoded[key][i] for key in keys} for i in range(len(chunk)))
        if total > 1 and (chunk_no == 1 or chunk_no == total or chunk_no % 10 == 0):
            print(f"tokenized chunk {chunk_no}/{total} rows={len(features)}", flush=True)
    lengths = [len(feature["input_ids"]) for feature in features]
    return features, lengths


def load_export_tokenizer(hf_dir):
    from transformers import AutoTokenizer, PreTrainedTokenizerFast

    try:
        return AutoTokenizer.from_pretrained(hf_dir, local_files_only=True)
    except ValueError as exc:
        if "TokenizersBackend" not in str(exc):
            raise
    config_path = Path(hf_dir) / "tokenizer_config.json"
    tokenizer_path = Path(hf_dir) / "tokenizer.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    special_tokens = {
        key: config[key]
        for key in ("bos_token", "eos_token", "unk_token", "pad_token")
        if config.get(key)
    }
    tokenizer = PreTrainedTokenizerFast(tokenizer_file=str(tokenizer_path), **special_tokens)
    if config.get("model_max_length"):
        tokenizer.model_max_length = int(config["model_max_length"])
    print("loaded tokenizer.json via PreTrainedTokenizerFast fallback", flush=True)
    return tokenizer


def load_export_model(hf_dir, device):
    try:
        return load_hf_model(str(hf_dir), device)
    except ValueError as exc:
        if "qwen3_5_text" not in str(exc):
            raise

    from transformers.models.qwen3_5.modeling_qwen3_5 import (
        Qwen3_5TextConfig,
        Qwen3_5TextForSequenceClassification,
    )

    dtype = torch.float16 if device.type == "cuda" else torch.float32
    config = Qwen3_5TextConfig.from_pretrained(hf_dir, local_files_only=True)
    config.dtype = "float16" if dtype == torch.float16 else "float32"
    if dtype == torch.float16:
        config.torch_dtype = torch.float16
    model = Qwen3_5TextForSequenceClassification(config)
    model = model.half() if dtype == torch.float16 else model.float()

    int8_path = Path(hf_dir) / "model.int8.safetensors"
    if int8_path.exists():
        state = load_int8_state_dict(str(int8_path), dtype=dtype)
        missing, unexpected = model.load_state_dict(state, strict=False)
        missing = [key for key in missing if not key.endswith("position_ids")]
        if missing or unexpected:
            raise RuntimeError(f"qwen3_5_text int8 mismatch: missing={missing} unexpected={unexpected}")
        print("Loaded qwen3_5_text int8 checkpoint via qwen3_5 direct class", flush=True)
    else:
        model = Qwen3_5TextForSequenceClassification.from_pretrained(
            hf_dir, local_files_only=True, torch_dtype=dtype
        )
    disable_decoder_cache(model)
    return model.to(device)


def maybe_rebind_fp16_deltanet(model):
    try:
        from colab.m8_fp16_deltanet_probe import make_fp16_rule, rebind_rule

        rule = make_fp16_rule(state_in_fp32=False)
        rebound = rebind_rule(model, rule)
        print(f"fp16 DeltaNet rebound_layers={rebound}", flush=True)
        return rebound
    except Exception as exc:
        print(f"fp16 DeltaNet rebind skipped: {exc!r}", flush=True)
        return 0


def infer_logits(args, samples):
    model_dir = Path(args.model_dir)
    hf_dir = model_dir / "hf_model"
    meta_path = model_dir / "hf_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"missing hf_meta.json: {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    serializer = args.serializer or meta.get("serializer_name", "current_v1")
    max_length = args.max_length or int(meta.get("max_length", 192))
    batch_size = args.batch_size or int(meta.get("batch_size", 16))

    device = torch.device(args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
    if device.type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        print(f"device=cuda name={torch.cuda.get_device_name(0)}", flush=True)
    else:
        print("device=cpu", flush=True)

    tokenizer = load_export_tokenizer(hf_dir)
    model = load_export_model(hf_dir, device)
    if device.type == "cuda":
        model.half()
    else:
        model.float()
    model.eval()
    if args.fp16_deltanet:
        export_meta_rebound = maybe_rebind_fp16_deltanet(model)
    else:
        export_meta_rebound = 0

    texts = [serialize_transformer_sample(sample, serializer) for sample in samples]
    features, lengths = tokenized_features(tokenizer, texts, max_length, args.tokenize_batch_size)
    order = sorted(range(len(features)), key=lambda i: lengths[i])
    logits = torch.empty((len(features), len(ALL_CLASSES)), dtype=torch.float32)
    total_batches = (len(order) + batch_size - 1) // batch_size if order else 0
    with torch.inference_mode():
        for batch_no, start in enumerate(range(0, len(order), batch_size), 1):
            chunk = order[start:start + batch_size]
            batch = tokenizer.pad(
                [features[i] for i in chunk],
                padding=True,
                pad_to_multiple_of=args.pad_to_multiple_of if args.pad_to_multiple_of > 1 else None,
                return_tensors="pt",
            )
            batch = {key: value.to(device, non_blocking=True) for key, value in batch.items()}
            with torch.amp.autocast(device_type="cuda", enabled=device.type == "cuda", dtype=torch.float16):
                batch_logits = model(**batch).logits.float().cpu()
            logits[chunk] = batch_logits
            if batch_no == 1 or batch_no == total_batches or batch_no % args.progress_every == 0:
                print(f"infer batch {batch_no}/{total_batches} rows={min(start + batch_size, len(order))}", flush=True)

    export_meta = {
        "source": "hf_model_export",
        "model_dir": str(model_dir),
        "base_model": meta.get("base_model"),
        "serializer_name": serializer,
        "max_length": max_length,
        "batch_size": batch_size,
        "fp16_deltanet_rebound_layers": export_meta_rebound,
    }
    return logits, export_meta


def load_payload_logits(args, expected_ids):
    payload = torch.load(args.input_payload, map_location="cpu", weights_only=False)
    logits = payload["logits"].float().cpu()
    ids = [safe_text(x) for x in payload["ids"]]
    if args.limit:
        ids = ids[: args.limit]
        logits = logits[: args.limit]
    if len(ids) != logits.shape[0]:
        raise ValueError("input payload ids/logits row count mismatch")
    if expected_ids is not None:
        missing = sorted(set(expected_ids) - set(ids))
        extra = sorted(set(ids) - set(expected_ids))
        if missing or extra:
            raise ValueError(
                f"payload ids do not match source ids: missing={missing[:5]} extra={extra[:5]}"
            )
    export_meta = {
        "source": "payload_repackage",
        "input_payload": args.input_payload,
        "input_payload_keys": sorted(payload.keys()),
    }
    return logits, ids, export_meta


def attach_labels(payload, ids, labels_path):
    labels = load_labels(labels_path)
    if not labels:
        return
    class_to_id = {label: idx for idx, label in enumerate(ALL_CLASSES)}
    missing = [sample_id for sample_id in ids if sample_id not in labels]
    if missing:
        raise ValueError(f"labels missing for ids: {missing[:5]}")
    label_names = [labels[sample_id] for sample_id in ids]
    payload["labels"] = label_names
    payload["y_true"] = torch.tensor([class_to_id[label] for label in label_names], dtype=torch.long)


def reorder(ids, logits, payload, sort_by_id):
    if not sort_by_id:
        return ids, logits, payload
    order = sorted(range(len(ids)), key=lambda i: ids[i])
    ids = [ids[i] for i in order]
    logits = logits[order]
    for key in ("labels", "y_true"):
        if key in payload:
            value = payload[key]
            if torch.is_tensor(value):
                payload[key] = value[order]
            else:
                payload[key] = [value[i] for i in order]
    return ids, logits, payload


def save_npz(path, payload):
    import numpy as np

    arrays = {
        "ids": np.asarray(payload["ids"]),
        "logits": payload["logits"].cpu().numpy(),
        "classes": np.asarray(payload["classes"]),
    }
    if "y_true" in payload:
        arrays["y_true"] = payload["y_true"].cpu().numpy()
    if "labels" in payload:
        arrays["labels"] = np.asarray(payload["labels"])
    arrays["metadata_json"] = np.asarray(json.dumps(payload["metadata"], ensure_ascii=False, sort_keys=True))
    np.savez_compressed(path, **arrays)


def save_output(path, payload, fmt):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "pt":
        torch.save(payload, path)
    elif fmt == "npz":
        save_npz(path, payload)
    else:
        raise ValueError(f"unsupported output format: {fmt}")
    print(f"saved {path} rows={len(payload['ids'])} dtype={payload['logits'].dtype}", flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Export or repackage teacher logits for KD.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--model-dir", help="HF artifact dir containing hf_model/ and hf_meta.json")
    source.add_argument("--input-payload", help="Existing .pt payload with ids/logits to repackage")
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--split", choices=["train", "test"], default="train")
    parser.add_argument("--input-jsonl", default="")
    parser.add_argument("--labels-csv", default="", help="Defaults to <data-dir>/train_labels.csv for train split")
    parser.add_argument("--output", required=True)
    parser.add_argument("--also-npz", default="", help="Optional second output path in .npz format")
    parser.add_argument("--dtype", choices=["fp16", "fp32"], default="fp16")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--serializer", default="")
    parser.add_argument("--max-length", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=0)
    parser.add_argument("--tokenize-batch-size", type=int, default=4096)
    parser.add_argument("--pad-to-multiple-of", type=int, default=8)
    parser.add_argument("--progress-every", type=int, default=20)
    parser.add_argument("--fp16-deltanet", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Debug export on the first N source rows")
    parser.add_argument("--preserve-input-order", dest="sort_by_id", action="store_false")
    parser.set_defaults(sort_by_id=True)
    return parser.parse_args()


def main():
    args = parse_args()
    input_jsonl, samples, source_ids = load_source_ids(args)
    if args.model_dir:
        logits, export_meta = infer_logits(args, samples)
        ids = source_ids
    else:
        logits, ids, export_meta = load_payload_logits(args, source_ids)

    output_path = Path(args.output)
    labels_path = Path(args.labels_csv) if args.labels_csv else Path(args.data_dir) / "train_labels.csv"
    payload = {
        "ids": ids,
        "logits": logits.to(dtype_for_save(args.dtype)).cpu(),
        "classes": list(ALL_CLASSES),
        "metadata": {
            **export_meta,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "data_path": str(input_jsonl),
            "row_count": len(ids),
            "dtype": args.dtype,
            "id_order": "lexicographic_id" if args.sort_by_id else "input_order",
        },
    }
    if args.split == "train":
        attach_labels(payload, ids, labels_path)
    ids, payload["logits"], payload = reorder(ids, payload["logits"], payload, args.sort_by_id)
    payload["ids"] = ids

    fmt = "npz" if output_path.suffix == ".npz" else "pt"
    save_output(output_path, payload, fmt)
    if args.also_npz:
        save_output(Path(args.also_npz), payload, "npz")


if __name__ == "__main__":
    main()
