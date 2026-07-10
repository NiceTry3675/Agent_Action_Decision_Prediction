import argparse
import csv
import gc
import json
import os
import tempfile
from pathlib import Path

import torch

from script import ALL_CLASSES, load_jsonl, run_hf_inference


def write_benchmark_data(source_path, data_dir, rows):
    source = load_jsonl(source_path)
    if not source:
        raise ValueError(f"no source samples in {source_path}")
    samples = []
    for idx in range(rows):
        sample = dict(source[idx % len(source)])
        sample["id"] = f"weak4_bench_{idx:06d}"
        samples.append(sample)
    with (data_dir / "test.jsonl").open("w", encoding="utf-8") as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    with (data_dir / "sample_submission.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "action"])
        writer.writeheader()
        writer.writerows({"id": sample["id"], "action": ALL_CLASSES[0]} for sample in samples)


def benchmark(args):
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    device = torch.device(args.device)
    pack_dir = Path(args.pack_dir).resolve()
    meta = json.loads((pack_dir / "hf_meta.json").read_text(encoding="utf-8"))
    specialist = meta.get("weak4_specialist") or {}
    if not specialist.get("enabled", False):
        raise ValueError("pack has no enabled weak4_specialist")

    reports = []
    with tempfile.TemporaryDirectory(prefix="weak4_benchmark_") as td:
        root = Path(td)
        data_dir = root / "data"
        data_dir.mkdir()
        write_benchmark_data(args.source_jsonl, data_dir, args.rows)
        for route_fraction in args.route_fractions:
            run_dir = root / f"pack_{route_fraction:.2f}"
            run_dir.mkdir()
            os.symlink(pack_dir / "hf_model", run_dir / "hf_model", target_is_directory=True)
            os.symlink(pack_dir / specialist["lora_dir"], run_dir / specialist["lora_dir"], target_is_directory=True)
            run_meta = dict(meta)
            run_specialist = dict(specialist)
            run_specialist["route_fraction"] = route_fraction
            run_meta["weak4_specialist"] = run_specialist
            (run_dir / "hf_meta.json").write_text(
                json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if device.type == "cuda":
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
            stats = run_hf_inference(
                str(run_dir),
                str(data_dir),
                str(root / f"submission_{route_fraction:.2f}.csv"),
                device,
            )
            if device.type == "cuda":
                torch.cuda.synchronize()
                stats["peak_cuda_memory_mb"] = torch.cuda.max_memory_allocated() / (1024 * 1024)
            reports.append(stats)
            print(
                f"benchmark cap={route_fraction:.2f} wall={stats['wall_seconds']:.2f}s "
                f"merge={stats['lora_merge_seconds']:.2f}s routed={stats['routed_rows']}"
            )
            gc.collect()
            if device.type == "cuda":
                torch.cuda.empty_cache()

    report = {
        "pack_dir": str(pack_dir),
        "source_jsonl": str(args.source_jsonl),
        "device": str(device),
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "rows": args.rows,
        "runs": reports,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved {output}")
    return report


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-dir", required=True)
    parser.add_argument("--source-jsonl", default="open/data/train.jsonl")
    parser.add_argument("--rows", type=int, default=30000)
    parser.add_argument("--route-fractions", nargs="+", type=float, default=[0.30, 0.35])
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument(
        "--output",
        default="experiments/artifacts/weak4_t4_full_volume_benchmark.json",
    )
    args = parser.parse_args()
    if args.rows <= 0:
        parser.error("--rows must be positive")
    if any(not 0.0 <= value <= 1.0 for value in args.route_fractions):
        parser.error("all --route-fractions values must be in [0, 1]")
    return args


if __name__ == "__main__":
    benchmark(parse_args())
