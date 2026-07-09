"""Storage-only int4 codec for HF safetensors checkpoints (aggressive package-size relief).

Extends the team's int8-rowwise-v1 codec (quantize_checkpoint.py): float weights
(ndim >= 2) are flattened to 2D, split into groups of 128 along the column axis,
and quantized to symmetric int4 ([-7, 7]) with one fp16 scale per (row, group).
Two 4-bit values pack into one uint8 byte. 1D floats and non-float tensors pass
through unchanged. The loader reconstructs an fp16/fp32 state_dict, so GPU
inference stays plain fp16 -- compression codec only, not an inference mode.

Usage:
    python quantize_int4.py quantize --input model/hf_model/model.safetensors --output OUT.safetensors
    python quantize_int4.py verify --model-dir model --quantized OUT.safetensors \
        [--data-dir open/data] [--samples 512] [--device cuda]
"""

import argparse
import json
import os

import torch
from safetensors.torch import load_file, save_file

FORMAT_VERSION = "int4-group128-v1"
GROUP_SIZE = 128
SCALE_SUFFIX = ".__scale__"


def quantize_state_dict(state):
    """fp state_dict -> (packed tensors, meta). Group-128 symmetric int4 for ndim>=2 floats."""
    packed, quantized, dtypes, shapes = {}, [], {}, {}
    for name, tensor in state.items():
        dtypes[name] = str(tensor.dtype).replace("torch.", "")
        if tensor.is_floating_point() and tensor.ndim >= 2:
            shapes[name] = list(tensor.shape)
            w = tensor.float().reshape(tensor.shape[0], -1)
            rows, cols = w.shape
            pad = (-cols) % GROUP_SIZE
            if pad:
                w = torch.nn.functional.pad(w, (0, pad))
            g = w.reshape(rows, -1, GROUP_SIZE)                      # (rows, n_groups, 128)
            scale = g.abs().amax(dim=2) / 7.0                        # (rows, n_groups)
            scale = torch.where(scale == 0, torch.ones_like(scale), scale)
            q = torch.clamp((g / scale.unsqueeze(2)).round(), -7, 7).to(torch.int8)
            nib = (q + 8).to(torch.uint8).reshape(rows, -1)          # [1, 15]
            packed[name] = (nib[:, 0::2] | (nib[:, 1::2] << 4)).contiguous()  # 2 values / byte
            packed[name + SCALE_SUFFIX] = scale.to(torch.float16)
            quantized.append(name)
        elif tensor.is_floating_point():
            packed[name] = tensor.to(torch.float16)
        else:
            packed[name] = tensor
    meta = {
        "format": FORMAT_VERSION,
        "group_size": GROUP_SIZE,
        "quantized": quantized,
        "dtypes": dtypes,
        "shapes": shapes,
    }
    return packed, meta


def load_int4_state_dict(path, dtype=torch.float16):
    """Quantized safetensors file -> reconstructed fp state_dict (the script.py loader)."""
    packed = load_file(path)
    with open(path + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["format"] == FORMAT_VERSION, f"unknown codec format {meta['format']}"
    quantized = set(meta["quantized"])
    state = {}
    for name, tensor in packed.items():
        if name.endswith(SCALE_SUFFIX):
            continue
        if name in quantized:
            shape = meta["shapes"][name]
            rows = shape[0]
            lo = (tensor & 0x0F).to(torch.int8) - 8
            hi = (tensor >> 4).to(torch.int8) - 8
            q = torch.stack((lo, hi), dim=2).reshape(rows, -1)       # interleave back
            scale = packed[name + SCALE_SUFFIX].float()
            w = (q.float().reshape(rows, -1, meta["group_size"]) * scale.unsqueeze(2)).reshape(rows, -1)
            cols = 1
            for d in shape[1:]:
                cols *= d
            state[name] = w[:, :cols].reshape(shape).to(dtype)
        elif tensor.is_floating_point():
            state[name] = tensor.to(dtype)
        else:
            state[name] = tensor
    return state


def cmd_quantize(args):
    state = load_file(args.input)
    packed, meta = quantize_state_dict(state)
    save_file(packed, args.output)
    with open(args.output + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    in_mb = os.path.getsize(args.input) / 1e6
    out_mb = os.path.getsize(args.output) / 1e6
    print(f"quantized {len(meta['quantized'])}/{len(state)} tensors")
    print(f"size: {in_mb:.1f} MB -> {out_mb:.1f} MB ({out_mb / in_mb:.2%})")


def cmd_roundtrip(args):
    """Synthetic sanity check: random tensors -> quantize -> dequantize -> error report."""
    torch.manual_seed(0)
    state = {
        "emb.weight": torch.randn(1000, 256) * 0.02,
        "layer.weight": torch.randn(512, 300),  # cols not a multiple of 128 (pad path)
        "norm.weight": torch.randn(512),        # 1D passthrough
        "ids": torch.arange(10),                # non-float passthrough
    }
    packed, meta = quantize_state_dict(state)
    tmp = args.output
    save_file(packed, tmp)
    with open(tmp + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    restored = load_int4_state_dict(tmp, dtype=torch.float32)
    for name, tensor in state.items():
        r = restored[name]
        assert r.shape == tensor.shape, f"{name}: shape {r.shape} != {tensor.shape}"
        if tensor.is_floating_point():
            err = (r.float() - tensor.float()).abs()
            rel = err.sum().item() / tensor.float().abs().sum().item()
            print(f"{name}: max_abs={err.max().item():.6f} mean_rel={rel:.4%}")
        else:
            assert torch.equal(r, tensor), f"{name}: non-float mismatch"
            print(f"{name}: exact")
    os.remove(tmp)
    os.remove(tmp + ".meta.json")
    print("roundtrip OK")


def cmd_verify(args):
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from script import load_jsonl, serialize_transformer_sample
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = torch.device(args.device)
    dtype = torch.float16 if device.type == "cuda" else torch.float32
    hf_dir = os.path.join(args.model_dir, "hf_model")
    with open(os.path.join(args.model_dir, "hf_meta.json"), encoding="utf-8") as f:
        hf_meta = json.load(f)

    original = load_file(os.path.join(hf_dir, "model.safetensors"))
    restored = load_int4_state_dict(args.quantized, dtype=torch.float32)
    max_w_err = rel_num = rel_den = 0.0
    for name, tensor in original.items():
        err = (restored[name].float() - tensor.float()).abs()
        max_w_err = max(max_w_err, err.max().item())
        rel_num += err.sum().item()
        rel_den += tensor.float().abs().sum().item()
    print(f"weight error: max_abs={max_w_err:.6f} mean_rel={rel_num / rel_den:.6%}")

    tokenizer = AutoTokenizer.from_pretrained(hf_dir, local_files_only=True)
    model_a = AutoModelForSequenceClassification.from_pretrained(hf_dir, local_files_only=True)
    model_b = AutoModelForSequenceClassification.from_pretrained(hf_dir, local_files_only=True)
    missing, unexpected = model_b.load_state_dict(
        {k: v for k, v in restored.items()}, strict=False)
    if missing or unexpected:
        print(f"load_state_dict: missing={list(missing)} unexpected={list(unexpected)}")
    for model in (model_a, model_b):
        model.to(device)
        model.to(dtype)
        model.eval()

    samples = load_jsonl(os.path.join(args.data_dir, "train.jsonl"))[: args.samples]
    serializer_name = hf_meta.get("serializer_name", "current_v1")
    texts = [serialize_transformer_sample(s, serializer_name) for s in samples]
    max_length = int(hf_meta.get("max_length", 192))

    agree = 0
    max_logit_err = sum_logit_err = n_logits = 0.0
    with torch.inference_mode():
        for start in range(0, len(texts), args.batch_size):
            encoded = tokenizer(texts[start:start + args.batch_size], padding=True,
                                truncation=True, max_length=max_length, return_tensors="pt")
            encoded = {k: v.to(device) for k, v in encoded.items()}
            la = model_a(**encoded).logits.float()
            lb = model_b(**encoded).logits.float()
            err = (la - lb).abs()
            max_logit_err = max(max_logit_err, err.max().item())
            sum_logit_err += err.sum().item()
            n_logits += err.numel()
            agree += (la.argmax(dim=1) == lb.argmax(dim=1)).sum().item()
    print(f"logit error: max_abs={max_logit_err:.4f} mean_abs={sum_logit_err / n_logits:.5f}")
    print(f"argmax agreement: {agree}/{len(texts)} ({agree / len(texts):.4%})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_q = sub.add_parser("quantize", help="safetensors fp checkpoint -> int4 codec file")
    p_q.add_argument("--input", required=True)
    p_q.add_argument("--output", required=True)
    p_r = sub.add_parser("roundtrip", help="synthetic random-tensor roundtrip sanity check")
    p_r.add_argument("--output", default="_int4_roundtrip_tmp.safetensors")
    p_v = sub.add_parser("verify", help="compare original vs dequantized logits on real samples")
    p_v.add_argument("--model-dir", default="model")
    p_v.add_argument("--quantized", required=True)
    p_v.add_argument("--data-dir", default="open/data")
    p_v.add_argument("--samples", type=int, default=512)
    p_v.add_argument("--batch-size", type=int, default=32)
    p_v.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()
    if args.command == "quantize":
        cmd_quantize(args)
    elif args.command == "roundtrip":
        cmd_roundtrip(args)
    else:
        cmd_verify(args)


if __name__ == "__main__":
    main()
