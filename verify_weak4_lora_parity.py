import argparse
import gc
import json
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import torch

from script import (
    load_hf_model,
    load_jsonl,
    merge_lora_inplace,
    model_logits_sorted,
    serialize_transformer_sample,
)
from tune_weak4_router import torch_load


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return "missing"


def resolve_adapter_dir(path):
    path = Path(path)
    if (path / "adapter_config.json").is_file():
        return path
    if (path / "hf_model" / "adapter_config.json").is_file():
        return path / "hf_model"
    raise ValueError(f"adapter_config.json not found in {path} or {path / 'hf_model'}")


def select_samples(train_jsonl, anchor_payload, row_count):
    anchor_ids = sorted(str(value) for value in anchor_payload["ids"])
    selected_ids = set(anchor_ids[:row_count])
    by_id = {
        str(sample.get("id", "")): sample
        for sample in load_jsonl(train_jsonl)
        if str(sample.get("id", "")) in selected_ids
    }
    missing = sorted(selected_ids - set(by_id))
    if missing:
        raise ValueError(f"anchor ids missing from train JSONL: {missing[:5]}")
    ids = anchor_ids[:row_count]
    return ids, [by_id[sample_id] for sample_id in ids]


def release_cuda_memory(device):
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()


def run(args):
    if not torch.cuda.is_available():
        raise RuntimeError("LoRA parity requires CUDA so both paths use the same fp16 base")
    device = torch.device("cuda")
    base_dir = Path(args.base_dir)
    adapter_dir = resolve_adapter_dir(args.adapter_dir)
    if package_version("peft") != "0.19.1":
        raise RuntimeError(f"LoRA parity requires peft==0.19.1, got {package_version('peft')}")
    adapter_config = json.loads((adapter_dir / "adapter_config.json").read_text(encoding="utf-8"))
    configured_base = str(adapter_config.get("base_model_name_or_path", "")).replace("\\", "/")
    configured_tail = "/".join(Path(configured_base).parts[-2:]) if configured_base else ""
    supplied_tail = "/".join(base_dir.resolve().parts[-2:])
    if configured_tail and configured_tail != supplied_tail:
        raise ValueError(
            f"adapter/base checkpoint mismatch: config={configured_tail!r} supplied={supplied_tail!r}"
        )
    anchor = torch_load(args.anchor_logits)
    ids, samples = select_samples(args.train_jsonl, anchor, args.rows)
    texts = [serialize_transformer_sample(sample, args.serializer_name) for sample in samples]

    from peft import PeftModel
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(base_dir, local_files_only=True)
    peft_base = load_hf_model(str(base_dir), device)
    peft_model = PeftModel.from_pretrained(peft_base, str(adapter_dir), is_trainable=False)
    peft_merged = peft_model.merge_and_unload()
    peft_merged.eval()
    peft_logits = model_logits_sorted(
        peft_merged,
        tokenizer,
        texts,
        args.max_length,
        args.batch_size,
        device,
    )
    del peft_merged, peft_model, peft_base
    release_cuda_memory(device)

    manual_model = load_hf_model(str(base_dir), device)
    merge_lora_inplace(manual_model, str(adapter_dir))
    manual_model.eval()
    manual_logits = model_logits_sorted(
        manual_model,
        tokenizer,
        texts,
        args.max_length,
        args.batch_size,
        device,
    )
    del manual_model
    release_cuda_memory(device)

    diff = (peft_logits - manual_logits).abs()
    peft_pred = torch.argmax(peft_logits, dim=1)
    manual_pred = torch.argmax(manual_logits, dim=1)
    peft_weak = torch.argmax(peft_logits[:, :4], dim=1)
    manual_weak = torch.argmax(manual_logits[:, :4], dim=1)
    report = {
        "rows": len(ids),
        "base_dir": str(base_dir),
        "adapter_dir": str(adapter_dir),
        "serializer_name": args.serializer_name,
        "max_abs_diff": float(diff.max()),
        "mean_abs_diff": float(diff.mean()),
        "argmax_match": int((peft_pred == manual_pred).sum()),
        "argmax_match_fraction": float((peft_pred == manual_pred).float().mean()),
        "weak4_conditional_argmax_match": int((peft_weak == manual_weak).sum()),
        "weak4_conditional_argmax_match_fraction": float(
            (peft_weak == manual_weak).float().mean()
        ),
        "thresholds": {
            "max_abs_diff": args.max_abs_tolerance,
            "mean_abs_diff": args.mean_abs_tolerance,
            "exact_argmax_required": True,
        },
        "environment": {
            "python": platform.python_version(),
            "peft": package_version("peft"),
            "transformers": package_version("transformers"),
            "torch": package_version("torch"),
            "safetensors": package_version("safetensors"),
            "gpu": torch.cuda.get_device_name(0),
        },
    }
    passed = (
        report["max_abs_diff"] <= args.max_abs_tolerance
        and report["mean_abs_diff"] <= args.mean_abs_tolerance
        and report["argmax_match"] == len(ids)
        and report["weak4_conditional_argmax_match"] == len(ids)
    )
    report["passed"] = passed
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"LoRA parity: max_abs={report['max_abs_diff']:.8g} "
        f"mean_abs={report['mean_abs_diff']:.8g} "
        f"argmax={report['argmax_match']}/{len(ids)} "
        f"weak4={report['weak4_conditional_argmax_match']}/{len(ids)}"
    )
    if not passed:
        raise AssertionError("manual LoRA merge parity failed; int8 verification must not proceed")
    print(f"saved {output}")
    return report


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", required=True, help="fp16 full-weight hf_model directory")
    parser.add_argument("--adapter-dir", required=True)
    parser.add_argument("--anchor-logits", required=True)
    parser.add_argument("--train-jsonl", default="open/data/train.jsonl")
    parser.add_argument(
        "--serializer-name",
        choices=["current_v1", "weak_nav_v1", "weak_nav_paths_v1"],
        required=True,
    )
    parser.add_argument("--rows", type=int, default=512)
    parser.add_argument("--max-length", type=int, default=384)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--max-abs-tolerance", type=float, default=1e-5)
    parser.add_argument("--mean-abs-tolerance", type=float, default=1e-6)
    parser.add_argument(
        "--output",
        default="experiments/artifacts/weak4_lora_manual_merge_parity.json",
    )
    args = parser.parse_args()
    if args.rows <= 0 or args.batch_size <= 0 or args.max_length <= 0:
        parser.error("--rows, --batch-size, and --max-length must be positive")
    return args


if __name__ == "__main__":
    run(parse_args())
