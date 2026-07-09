"""Pseudo-holdout ranking proxy for refit lottery tickets.

Training (replay last1) consumed only the LAST user->action pair per session; every
earlier pair was never a training target. We evaluate packed models on the
SECOND-TO-LAST pair (one per session) to rank refit seeds without spending
submission slots. Absolute numbers are contaminated (same sessions were seen),
but the contamination is identical across packs, so the ranking is comparable.

Usage: python eval_pseudo_holdout.py --pack <dir with hf_meta.json + hf_model/> \
       --data-dir open/data [--limit 20000]
"""
import argparse
import json
from pathlib import Path

import torch

from script import ALL_CLASSES, load_jsonl, safe_text, serialize_transformer_sample
from train import CLASS_TO_ID, f1_metrics


def second_last_pairs(samples, limit, pair_pos=-2):
    out = []
    for sample in samples:
        history = sample.get("history") or []
        candidates = []
        for idx, event in enumerate(history[:-1]):
            if event.get("role") != "user":
                continue
            next_event = history[idx + 1]
            if next_event.get("role") == "assistant_action":
                label = safe_text(next_event.get("name"))
                if label in CLASS_TO_ID:
                    candidates.append((idx, event, label))
        if len(candidates) < abs(pair_pos):
            continue
        idx, user_event, label = candidates[pair_pos]  # -1 was trained on (last1); -2 was not
        out.append((
            CLASS_TO_ID[label],
            {
                "id": f"{safe_text(sample.get('id'))}::pseudo_{idx}",
                "session_meta": sample.get("session_meta") or {},
                "history": history[:idx],
                "current_prompt": safe_text(user_event.get("content")),
            },
        ))
        if limit and len(out) >= limit:
            break
    return out


def load_pack_model(pack_dir, device):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    hf_dir = Path(pack_dir) / "hf_model"
    tokenizer = AutoTokenizer.from_pretrained(hf_dir)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    int8_path = hf_dir / "model.int8.safetensors"
    if (hf_dir / "model.safetensors").exists():
        model = AutoModelForSequenceClassification.from_pretrained(hf_dir, torch_dtype=torch.float16)
    elif int8_path.exists():
        # int8-rowwise-v1 codec: dequantize to fp16 then load via state dict
        from safetensors.torch import load_file
        from transformers import AutoConfig
        raw = load_file(str(int8_path))
        state = {}
        for key in list(raw.keys()):
            if key.endswith("::int8"):
                base = key[:-6]
                scale = raw[base + "::scale"].float()
                state[base] = (raw[key].float() * scale.unsqueeze(-1)).to(torch.float16)
            elif "::" not in key:
                state[key] = raw[key]
        config = AutoConfig.from_pretrained(hf_dir)
        model = AutoModelForSequenceClassification.from_config(config)
        model.load_state_dict(state, strict=False)
        model = model.half()
    else:
        raise FileNotFoundError(f"no model weights under {hf_dir}")
    if model.config.pad_token_id is None:
        model.config.pad_token_id = tokenizer.pad_token_id
    return model.to(device).eval(), tokenizer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--serializer", default="current_v1")
    parser.add_argument("--max-length", type=int, default=384)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--limit", type=int, default=20000)
    parser.add_argument("--pair-pos", type=int, default=-2,
                        help="-2: untrained second-to-last pair (holdout); -1: trained last pair (memorization check)")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    samples = load_jsonl(Path(args.data_dir) / "train.jsonl")
    pairs = second_last_pairs(samples, args.limit, args.pair_pos)
    print(f"pseudo-holdout pairs: {len(pairs)}")
    y_true = [label for label, _ in pairs]
    texts = [serialize_transformer_sample(s, args.serializer) for _, s in pairs]

    model, tokenizer = load_pack_model(args.pack, device)
    meta_path = Path(args.pack) / "hf_meta.json"
    bias = torch.zeros(len(ALL_CLASSES))
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        bias = torch.tensor([float(x) for x in meta.get("class_bias", [0.0] * len(ALL_CLASSES))])

    features = []
    for start in range(0, len(texts), 1024):
        enc = tokenizer(texts[start:start + 1024], padding=False, truncation=True, max_length=args.max_length)
        keys = list(enc.keys())
        features.extend({k: enc[k][j] for k in keys} for j in range(len(enc[keys[0]])))
    lengths = [len(f["input_ids"]) for f in features]
    order = sorted(range(len(features)), key=lambda i: lengths[i])

    preds = [0] * len(features)
    with torch.inference_mode():
        for bstart in range(0, len(order), args.batch_size):
            batch_idx = order[bstart:bstart + args.batch_size]
            enc = tokenizer.pad([features[i] for i in batch_idx], padding=True, return_tensors="pt")
            enc = {k: v.to(device) for k, v in enc.items()}
            with torch.amp.autocast(device_type="cuda", enabled=device.type == "cuda", dtype=torch.float16):
                logits = model(**enc).logits.float().cpu()
            logits = logits + bias
            for row, i in zip(torch.argmax(logits, dim=1).tolist(), batch_idx):
                preds[i] = row
            if bstart % (args.batch_size * 100) == 0:
                print(f"  {bstart}/{len(order)}", flush=True)

    metrics = f1_metrics(y_true, preds)
    print(f"PSEUDO_HOLDOUT macro_f1={metrics['macro_f1']:.6f} pack={args.pack}")
    for name, f1 in sorted(metrics["per_class_f1"].items(), key=lambda x: x[1]):
        print(f"  {name}: {f1:.4f}")


if __name__ == "__main__":
    main()
