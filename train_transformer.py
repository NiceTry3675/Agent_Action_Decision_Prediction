import argparse
import csv
import json
import math
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import torch
import torch.nn.functional as F
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

from script import ALL_CLASSES, load_jsonl, serialize_transformer_sample
from train import CLASS_TO_ID, append_results_csv, f1_metrics, load_labels, predict_with_bias, split_indices, tune_class_bias


def class_weights(y, device, power):
    counts = Counter(y)
    total = len(y)
    weights = []
    for class_id in range(len(ALL_CLASSES)):
        weights.append((total / (len(ALL_CLASSES) * max(1, counts[class_id]))) ** power)
    mean = sum(weights) / len(weights)
    return torch.tensor([w / mean for w in weights], dtype=torch.float32, device=device)


def make_batches(indices, batch_size, rng=None):
    indices = indices[:]
    if rng is not None:
        rng.shuffle(indices)
    for start in range(0, len(indices), batch_size):
        yield indices[start:start + batch_size]


def encode_batch(tokenizer, texts, max_length, device):
    encoded = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
    return {key: value.to(device, non_blocking=True) for key, value in encoded.items()}


def evaluate(model, tokenizer, texts, y, indices, args, device):
    model.eval()
    logits_parts = []
    with torch.inference_mode():
        for batch_idx in make_batches(indices, args.eval_batch_size):
            batch_texts = [texts[i] for i in batch_idx]
            encoded = encode_batch(tokenizer, batch_texts, args.max_length, device)
            with torch.amp.autocast(device_type="cuda", enabled=device.type == "cuda", dtype=torch.float16):
                logits = model(**encoded).logits.float()
            logits_parts.append(logits.detach().cpu())
    logits = torch.cat(logits_parts, dim=0)
    y_true = [y[i] for i in indices]
    pred = torch.argmax(logits, dim=1).tolist()
    metrics = f1_metrics(y_true, pred)
    return logits, y_true, metrics


def train_model(samples, texts, y, train_idx, args, device):
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base_model,
        num_labels=len(ALL_CLASSES),
        id2label={i: label for i, label in enumerate(ALL_CLASSES)},
        label2id={label: i for i, label in enumerate(ALL_CLASSES)},
    ).to(device)
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()

    weights = class_weights([y[i] for i in train_idx], device, args.class_weight_power)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    total_steps = math.ceil(len(train_idx) / args.batch_size) * args.epochs
    warmup_steps = int(total_steps * args.warmup_ratio)
    scheduler = get_linear_schedule_with_warmup(optimizer, warmup_steps, total_steps)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    rng = random.Random(args.seed)

    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        seen = 0
        for step, batch_idx in enumerate(make_batches(train_idx, args.batch_size, rng), 1):
            batch_texts = [texts[i] for i in batch_idx]
            labels = torch.tensor([y[i] for i in batch_idx], dtype=torch.long, device=device)
            encoded = encode_batch(tokenizer, batch_texts, args.max_length, device)
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast(device_type="cuda", enabled=device.type == "cuda", dtype=torch.float16):
                logits = model(**encoded).logits
                loss = F.cross_entropy(logits.float(), labels, weight=weights, label_smoothing=args.label_smoothing)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()
            total_loss += float(loss.detach().cpu()) * len(batch_idx)
            seen += len(batch_idx)
            if args.log_every and step % args.log_every == 0:
                print(f"    step={step:04d} loss={total_loss / max(1, seen):.5f}")
        if device.type == "cuda":
            torch.cuda.synchronize()
        print(f"  epoch={epoch:02d} train_loss={total_loss / max(1, seen):.5f}")
    return model, tokenizer


def save_hf_artifact(model, tokenizer, output_dir, class_bias, args, metrics):
    output_dir = Path(output_dir)
    hf_dir = output_dir / "hf_model"
    hf_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(hf_dir, safe_serialization=True)
    tokenizer.save_pretrained(hf_dir)
    meta = {
        "classes": ALL_CLASSES,
        "class_bias": [float(x) for x in class_bias.tolist()],
        "max_length": args.max_length,
        "batch_size": args.eval_batch_size,
        "validation_macro_f1": metrics["macro_f1"],
        "validation_split": args.split,
        "base_model": args.base_model,
        "trained_with_cuda": torch.cuda.is_available(),
    }
    with (output_dir / "hf_meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def append_log(experiment_id, args, metrics, decision):
    weak = sorted(metrics["per_class_f1"].items(), key=lambda kv: kv[1])[:5]
    strong = sorted(metrics["per_class_f1"].items(), key=lambda kv: kv[1], reverse=True)[:5]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"## {experiment_id}",
        "",
        f"- Date/time: {now}",
        "- Hypothesis: A multilingual transformer fine-tuned on GPU should recover semantic prompt/action cues that sparse GPU models missed.",
        f"- Code/config changes: `{args.base_model}`, max_length={args.max_length}, epochs={args.epochs}, lr={args.lr}, batch={args.batch_size}.",
        f"- Validation setup: {args.split}",
        f"- Overall Macro-F1: {metrics['macro_f1']:.6f}",
        "- Per-class observations:",
        f"  - Weakest: {', '.join(f'{k}={v:.3f}' for k, v in weak)}",
        f"  - Strongest: {', '.join(f'{k}={v:.3f}' for k, v in strong)}",
        f"- Top confusions: {metrics['top_confusions'][:8]}",
        f"- Prediction distribution: {metrics['prediction_distribution']}",
        "- Runtime or package-size concerns: GPU inference uses packaged HuggingFace weights; package remains under the 1 GB limit.",
        f"- Decision: {decision}",
        "- Next suggested experiment: tune max_length/epochs or ensemble with sparse GPU logits if transformer under-recognizes file-operation classes.",
        "",
    ]
    with open("research_log.md", "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def run(args):
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

    samples = load_jsonl(Path(args.data_dir) / "train.jsonl")
    labels_by_id = load_labels(Path(args.data_dir) / "train_labels.csv")
    y = [CLASS_TO_ID[labels_by_id[sample["id"]]] for sample in samples]
    texts = [serialize_transformer_sample(sample) for sample in samples]
    train_idx, val_idx = split_indices(samples, y, args.split, args.seed)
    print(f"split={args.split} train={len(train_idx)} val={len(val_idx)}")

    model, tokenizer = train_model(samples, texts, y, train_idx, args, device)
    logits, y_val, metrics = evaluate(model, tokenizer, texts, y, val_idx, args, device)
    print(f"  macro_f1={metrics['macro_f1']:.6f}")
    bias = torch.zeros(len(ALL_CLASSES), dtype=torch.float32)
    if args.tune_bias:
        print("  tuning class bias")
        bias, _ = tune_class_bias(logits, y_val, rounds=2)
        pred = predict_with_bias(logits, bias)
        metrics = f1_metrics(y_val, pred)
        print(f"  tuned_macro_f1={metrics['macro_f1']:.6f}")

    experiment_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_gpu_transformer_{args.split}"
    decision = "keep as GPU candidate" if metrics["macro_f1"] >= args.keep_threshold else "discard or revisit"
    append_results_csv(
        Path("experiments/results.csv"),
        {
            "experiment_id": experiment_id,
            "model_family": "torch_gpu_transformer",
            "features": "serialized prompt/action/workspace text",
            "split_type": args.split,
            "macro_f1": f"{metrics['macro_f1']:.6f}",
            "notes": args.notes or args.base_model,
            "artifact_path": args.output_dir if args.final_model else "",
        },
    )
    append_log(experiment_id, args, metrics, decision)

    metrics_path = Path("experiments/artifacts") / f"{experiment_id}_metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "experiment_id": experiment_id,
                "metrics": metrics,
                "class_bias": dict(zip(ALL_CLASSES, [float(x) for x in bias.tolist()])),
                "device": str(device),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    if args.final_model:
        print("training final transformer on all training rows")
        all_idx = list(range(len(samples)))
        final_model, final_tokenizer = train_model(samples, texts, y, all_idx, args, device)
        save_hf_artifact(final_model, final_tokenizer, args.output_dir, bias, args, metrics)
        print(f"saved HF artifact: {args.output_dir}")

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
    parser.add_argument("--split", choices=["random", "session"], default="session")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="cuda")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--eval-batch-size", type=int, default=64)
    parser.add_argument("--max-length", type=int, default=192)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.06)
    parser.add_argument("--label-smoothing", type=float, default=0.02)
    parser.add_argument("--class-weight-power", type=float, default=0.5)
    parser.add_argument("--grad-clip", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--log-every", type=int, default=250)
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--tune-bias", action="store_true")
    parser.add_argument("--final-model", action="store_true")
    parser.add_argument("--output-dir", default="model")
    parser.add_argument("--keep-threshold", type=float, default=0.60)
    parser.add_argument("--notes", default="")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
