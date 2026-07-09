"""Export teacher logits from a LoRA epoch checkpoint (adapter-only) by merging onto the base.
Same output schema as export_teacher_logits.py."""
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from peft import PeftModel

from script import ALL_CLASSES, load_jsonl, serialize_transformer_sample
from train import CLASS_TO_ID, load_labels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ckpt", required=True, help="adapter checkpoint dir (epoch snapshot)")
    parser.add_argument("--base", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--serializer", default="current_v1")
    parser.add_argument("--max-length", type=int, default=384)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--out", required=True)
    parser.add_argument("--source-note", default="")
    args = parser.parse_args()

    device = torch.device("cuda")
    data_dir = Path(args.data_dir)
    samples = load_jsonl(data_dir / "train.jsonl")
    labels_by_id = load_labels(data_dir / "train_labels.csv")
    order = sorted(range(len(samples)), key=lambda i: str(samples[i]["id"]))
    ids = [str(samples[i]["id"]) for i in order]
    labels = [labels_by_id[samples[i]["id"]] for i in order]
    y_true = [CLASS_TO_ID[label] for label in labels]

    tokenizer = AutoTokenizer.from_pretrained(args.ckpt)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base, num_labels=len(ALL_CLASSES),
        id2label={i: c for i, c in enumerate(ALL_CLASSES)},
        label2id={c: i for i, c in enumerate(ALL_CLASSES)},
        torch_dtype=torch.float16)
    model = PeftModel.from_pretrained(model, args.ckpt)
    model = model.merge_and_unload()
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    if model.config.pad_token_id is None:
        model.config.pad_token_id = tokenizer.pad_token_id
    model.to(device).eval()

    texts = [serialize_transformer_sample(samples[i], args.serializer) for i in order]
    features = []
    for start in range(0, len(texts), 1024):
        encoded = tokenizer(texts[start:start + 1024], padding=False, truncation=True, max_length=args.max_length)
        keys = list(encoded.keys())
        features.extend({k: encoded[k][j] for k in keys} for j in range(len(encoded[keys[0]])))
    lengths = [len(f["input_ids"]) for f in features]

    bucket = sorted(range(len(features)), key=lambda i: lengths[i])
    logits_out = torch.zeros(len(features), len(ALL_CLASSES), dtype=torch.float32)
    start_time = time.perf_counter()
    with torch.inference_mode():
        for bstart in range(0, len(bucket), args.batch_size):
            batch_idx = bucket[bstart:bstart + args.batch_size]
            encoded = tokenizer.pad([features[i] for i in batch_idx], padding=True, return_tensors="pt")
            encoded = {k: v.to(device) for k, v in encoded.items()}
            with torch.amp.autocast(device_type="cuda", enabled=True, dtype=torch.float16):
                logits = model(**encoded).logits.float()
            logits_out[torch.tensor(batch_idx)] = logits.detach().cpu()
            done = bstart + len(batch_idx)
            if done % (args.batch_size * 100) < args.batch_size or done == len(bucket):
                print(f"  logits {done}/{len(bucket)} elapsed={time.perf_counter() - start_time:.0f}s", flush=True)

    pred = torch.argmax(logits_out, dim=1)
    acc = float((pred == torch.tensor(y_true)).float().mean())
    print(f"teacher train-set argmax acc={acc:.4f}")

    payload = {
        "ids": ids,
        "logits": logits_out.to(torch.float16),
        "classes": list(ALL_CLASSES),
        "labels": labels,
        "y_true": y_true,
        "metadata": {
            "source_model": f"{args.base} + {args.ckpt}",
            "serializer": args.serializer,
            "max_length": args.max_length,
            "note": args.source_note,
            "train_argmax_acc": acc,
            "created_utc": datetime.now(timezone.utc).isoformat(),
        },
    }
    torch.save(payload, args.out)
    print(f"saved teacher logits: {args.out} shape={tuple(logits_out.shape)}")


if __name__ == "__main__":
    main()
