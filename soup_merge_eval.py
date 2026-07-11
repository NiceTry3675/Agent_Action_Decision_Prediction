"""Weight-space soup tooling: merge same-architecture fp16 checkpoints and
evaluate candidates on the fixed session split (seed42, 14,001 rows).

Merge is a streaming per-tensor weighted average (fp32 accumulate -> fp16),
so peak RAM stays near one tensor regardless of checkpoint count. Eval
serializes the fixed-val rows once and reuses the encodings across every
model dir passed in, reporting raw macro-F1, Weak4/Explorer4 detail, and
optionally the 2-stage bias-tuned score.

Caveat recorded in soup_meta.json: full-refit ingredients trained on the val
rows, so their fixed-val scores are optimistic and comparable only against
other refits (connectivity/relative reads). Screen checkpoints
(e.g. kd_hcx_m8_screen, raw 0.783852 anchor) are the clean-surface check.

Usage:
  soup_merge_eval.py merge --dirs A,B[,C..] [--weights w1,w2,..] --out OUT
  soup_merge_eval.py eval --dirs A,B,OUT [--two-stage] [--batch-size 48]
  soup_merge_eval.py interp --dirs A,B --lambdas 0.25,0.5,0.75 --out-root DIR
      (merge+eval each interpolation point plus both endpoints)
"""

import argparse
import gc
import json
import shutil
import time
from pathlib import Path

import torch

from script import ALL_CLASSES, load_jsonl, safe_text, serialize_transformer_sample
from train import (
    CLASS_TO_ID,
    f1_metrics,
    load_labels,
    predict_with_bias,
    split_indices,
    tune_class_bias_two_stage,
)

WEAK4 = ALL_CLASSES[:4]
EXPLORER4 = ["list_directory", "read_file", "grep_search", "glob_pattern"]
COPY_FILES = (
    "config.json",
    "hf_meta.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "vocab.json",
    "merges.txt",
)


def resolve_model_dir(path):
    """Accept either a dir holding model.safetensors or its parent with hf_model/."""
    path = Path(path)
    if (path / "model.safetensors").is_file():
        return path
    if (path / "hf_model" / "model.safetensors").is_file():
        return path / "hf_model"
    raise FileNotFoundError(f"no model.safetensors under {path} (or {path}/hf_model)")


def merge_checkpoints(dirs, weights, out_dir):
    from safetensors import safe_open
    from safetensors.torch import save_file

    dirs = [resolve_model_dir(d) for d in dirs]
    if len(dirs) < 2:
        raise ValueError("merge needs at least 2 model dirs")
    if weights is None:
        weights = [1.0 / len(dirs)] * len(dirs)
    if len(weights) != len(dirs):
        raise ValueError(f"{len(weights)} weights for {len(dirs)} dirs")
    total = sum(weights)
    if abs(total - 1.0) > 1e-6:
        weights = [w / total for w in weights]

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    handles = [safe_open(str(d / "model.safetensors"), framework="pt") for d in dirs]
    key_sets = [set(h.keys()) for h in handles]
    if any(ks != key_sets[0] for ks in key_sets[1:]):
        diff = set.union(*key_sets) - set.intersection(*key_sets)
        raise ValueError(f"tensor key mismatch across checkpoints: {sorted(diff)[:8]}")

    merged = {}
    for key in sorted(key_sets[0]):
        acc = None
        for handle, weight in zip(handles, weights):
            tensor = handle.get_tensor(key)
            if not tensor.is_floating_point():
                if acc is None:
                    acc = tensor
                continue
            term = tensor.float() * weight
            acc = term if acc is None or not torch.is_floating_point(acc) else acc + term
        merged[key] = acc.to(torch.float16) if acc.is_floating_point() else acc
    save_file(merged, str(out_dir / "model.safetensors"))
    n_tensors = len(merged)
    del merged
    gc.collect()

    for name in COPY_FILES:
        src = dirs[0] / name
        if not src.is_file():
            src = dirs[0].parent / name  # hf_meta.json lives one level up by convention
        if src.is_file():
            shutil.copy2(src, out_dir / name)

    meta = {
        "kind": "weight-space soup",
        "inputs": [str(d) for d in dirs],
        "weights": weights,
        "tensors": n_tensors,
        "note": "fp32-accumulated weighted average of fp16 checkpoints; "
        "tokenizer/config copied from the first input. Refit ingredients "
        "saw the fixed-val rows: local scores are relative-only.",
    }
    (out_dir / "soup_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"merged {len(dirs)} checkpoints -> {out_dir} ({n_tensors} tensors, weights={weights})")
    return out_dir


def build_fixed_val(data_dir, seed=42):
    samples = load_jsonl(Path(data_dir) / "train.jsonl")
    labels_by_id = load_labels(Path(data_dir) / "train_labels.csv")
    y = [CLASS_TO_ID[labels_by_id[safe_text(s["id"])]] for s in samples]
    _, val_idx = split_indices(samples, y, "session", seed)
    val_samples = [samples[i] for i in val_idx]
    val_y = [y[i] for i in val_idx]
    print(f"fixed session split seed={seed}: val rows={len(val_idx)}")
    return val_samples, val_y


def encode_val(val_samples, tokenizer, serializer, max_length):
    start = time.perf_counter()
    texts = [serialize_transformer_sample(s, serializer, tokenizer=tokenizer) for s in val_samples]
    encoded = tokenizer(texts, padding=False, truncation=True, max_length=max_length)
    feats = [
        {"input_ids": encoded["input_ids"][i], "attention_mask": encoded["attention_mask"][i]}
        for i in range(len(texts))
    ]
    order = sorted(range(len(feats)), key=lambda i: len(feats[i]["input_ids"]))
    print(f"serialized+tokenized {len(texts)} rows in {time.perf_counter() - start:.1f}s")
    return feats, order


def model_logits(model_dir, tokenizer, feats, order, batch_size, device):
    from transformers import AutoModelForSequenceClassification

    model_dir = resolve_model_dir(model_dir)
    dtype = torch.float16 if device.type == "cuda" else torch.float32
    model = AutoModelForSequenceClassification.from_pretrained(model_dir, torch_dtype=dtype)
    if model.config.pad_token_id is None:
        model.config.pad_token_id = tokenizer.pad_token_id
    if hasattr(model.config, "use_cache"):
        model.config.use_cache = False
    model.to(device)
    model.eval()

    logits = torch.empty(len(feats), len(ALL_CLASSES), dtype=torch.float32)
    start = time.perf_counter()
    with torch.no_grad():
        for begin in range(0, len(order), batch_size):
            chunk = order[begin : begin + batch_size]
            batch = tokenizer.pad([feats[i] for i in chunk], padding=True, return_tensors="pt")
            batch = {k: v.to(device) for k, v in batch.items()}
            out = model(**batch).logits.float().cpu()
            logits[torch.tensor(chunk)] = out
    elapsed = time.perf_counter() - start
    print(f"  forward {len(feats)} rows in {elapsed:.1f}s ({model_dir})")
    del model
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return logits


def report(name, logits, val_y, two_stage):
    pred = torch.argmax(logits, dim=1).tolist()
    metrics = f1_metrics(val_y, pred)
    per_class = metrics["per_class_f1"]
    weak4_mean = sum(per_class[c] for c in WEAK4) / len(WEAK4)
    explorer4_sum = sum(per_class[c] for c in EXPLORER4)
    row = {
        "model": name,
        "raw_macro_f1": round(metrics["macro_f1"], 6),
        "weak4_mean": round(weak4_mean, 6),
        "explorer4_sum": round(explorer4_sum, 6),
        "per_class_f1": {k: round(v, 4) for k, v in per_class.items()},
    }
    if two_stage:
        bias, best = tune_class_bias_two_stage(logits, val_y)
        row["two_stage_macro_f1"] = round(best, 6)
        row["two_stage_bias"] = [round(float(b), 2) for b in bias]
    print(
        f"== {name}: raw={row['raw_macro_f1']:.6f} weak4={row['weak4_mean']:.6f} "
        f"explorer4={row['explorer4_sum']:.6f}"
        + (f" 2stage={row['two_stage_macro_f1']:.6f}" if two_stage else "")
    )
    return row


def run_eval(args, extra_dirs=None):
    from transformers import AutoTokenizer

    dirs = [d for d in args.dirs.split(",") if d] + list(extra_dirs or [])
    device = torch.device("cuda" if torch.cuda.is_available() and args.device != "cpu" else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(resolve_model_dir(dirs[0]))
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    val_samples, val_y = build_fixed_val(args.data_dir, args.seed)
    if args.limit:
        val_samples, val_y = val_samples[: args.limit], val_y[: args.limit]
    feats, order = encode_val(val_samples, tokenizer, args.serializer, args.max_length)

    rows = []
    for model_dir in dirs:
        logits = model_logits(model_dir, tokenizer, feats, order, args.batch_size, device)
        rows.append(report(model_dir, logits, val_y, args.two_stage))
        if args.save_logits_dir:
            out = Path(args.save_logits_dir)
            out.mkdir(parents=True, exist_ok=True)
            stem = Path(model_dir).name.strip("/").replace("/", "_")
            torch.save({"logits": logits, "model": str(model_dir)}, out / f"{stem}_fixedval_logits.pt")
    if args.report_json:
        Path(args.report_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_json).write_text(json.dumps(rows, indent=2))
        print(f"wrote {args.report_json}")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_eval_args(p):
        p.add_argument("--data-dir", default="open/data")
        p.add_argument("--serializer", default="current_v1")
        p.add_argument("--max-length", type=int, default=384)
        p.add_argument("--batch-size", type=int, default=48)
        p.add_argument("--seed", type=int, default=42)
        p.add_argument("--limit", type=int, default=0, help="eval only the first N val rows (smoke)")
        p.add_argument("--device", choices=["auto", "cpu"], default="auto")
        p.add_argument("--two-stage", action="store_true")
        p.add_argument("--save-logits-dir", default="")
        p.add_argument("--report-json", default="")

    p_merge = sub.add_parser("merge")
    p_merge.add_argument("--dirs", required=True, help="comma-separated model dirs")
    p_merge.add_argument("--weights", default="", help="comma-separated, defaults to uniform")
    p_merge.add_argument("--out", required=True)

    p_eval = sub.add_parser("eval")
    p_eval.add_argument("--dirs", required=True, help="comma-separated model dirs")
    add_eval_args(p_eval)

    p_interp = sub.add_parser("interp")
    p_interp.add_argument("--dirs", required=True, help="exactly two model dirs A,B")
    p_interp.add_argument("--lambdas", default="0.5", help="weights on B, comma-separated")
    p_interp.add_argument("--out-root", required=True)
    add_eval_args(p_interp)

    args = parser.parse_args()

    if args.cmd == "merge":
        weights = [float(w) for w in args.weights.split(",")] if args.weights else None
        merge_checkpoints(args.dirs.split(","), weights, args.out)
        return

    if args.cmd == "interp":
        dir_a, dir_b = args.dirs.split(",")
        merged_dirs = []
        for lam in [float(x) for x in args.lambdas.split(",")]:
            out = Path(args.out_root) / f"interp_lam{lam:g}"
            merge_checkpoints([dir_a, dir_b], [1.0 - lam, lam], out)
            merged_dirs.append(str(out))
        args.dirs = f"{dir_a},{dir_b}"
        run_eval(args, extra_dirs=merged_dirs)
        return

    run_eval(args)


if __name__ == "__main__":
    main()
