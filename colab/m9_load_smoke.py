import argparse
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from script import ALL_CLASSES, load_jsonl, serialize_transformer_sample


def main():
    parser = argparse.ArgumentParser(description="M9 Qwen3.5 load + one-batch forward smoke.")
    parser.add_argument("--base-model", default="Qwen/Qwen3.5-9B-Base")
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--serializer", default="current_v1")
    parser.add_argument("--max-length", type=int, default=400)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--bf16", action="store_true")
    parser.add_argument("--gradient-checkpointing", action="store_true")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.cuda.reset_peak_memory_stats()
        print("device", torch.cuda.get_device_name(0), flush=True)
        print("vram_total_gb", round(torch.cuda.get_device_properties(0).total_memory / 1e9, 2), flush=True)

    dtype = torch.bfloat16 if args.bf16 else torch.float16
    label_kwargs = {
        "num_labels": len(ALL_CLASSES),
        "id2label": {i: label for i, label in enumerate(ALL_CLASSES)},
        "label2id": {label: i for i, label in enumerate(ALL_CLASSES)},
    }

    t0 = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    print("tokenizer_loaded_sec", round(time.perf_counter() - t0, 1), flush=True)

    t1 = time.perf_counter()
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base_model,
        torch_dtype=dtype,
        **label_kwargs,
    )
    if model.config.pad_token_id is None:
        model.config.pad_token_id = tokenizer.pad_token_id
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()
    model.to(device)
    model.eval()
    print("model_loaded_sec", round(time.perf_counter() - t1, 1), flush=True)
    print("model_class", model.__class__.__name__, flush=True)
    print("config_class", model.config.__class__.__name__, flush=True)

    samples = load_jsonl(f"{args.data_dir}/train.jsonl")[: args.batch_size]
    texts = [serialize_transformer_sample(sample, args.serializer) for sample in samples]
    encoded = tokenizer(texts, truncation=True, max_length=args.max_length, padding=True, return_tensors="pt")
    encoded = {key: value.to(device) for key, value in encoded.items()}
    print("batch_shape", {key: tuple(value.shape) for key, value in encoded.items()}, flush=True)

    t2 = time.perf_counter()
    with torch.inference_mode():
        with torch.amp.autocast(device_type="cuda", enabled=device.type == "cuda", dtype=dtype):
            logits = model(**encoded).logits.float()
    if device.type == "cuda":
        torch.cuda.synchronize()
        print("vram_peak_gb", round(torch.cuda.max_memory_allocated() / 1e9, 2), flush=True)
    print("forward_sec", round(time.perf_counter() - t2, 3), flush=True)
    print("logits_shape", tuple(logits.shape), flush=True)
    print("argmax", logits.argmax(dim=-1).tolist(), flush=True)
    print("ok", flush=True)


if __name__ == "__main__":
    main()
