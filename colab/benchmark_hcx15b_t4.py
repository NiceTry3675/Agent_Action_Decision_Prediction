"""Clean-ZIP T4 runtime gate for the HCX-1.5B mixed INT4 package.

The probe first loads the submitted package and benchmarks a small batch-size
grid, requiring prediction parity between successful sizes. It then runs the
package's own ``script.py`` in a clean extracted directory on a deterministic
30k train-input proxy, so the final wall time includes codec restore,
tokenization, inference, post-processing, and CSV writing.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import random
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

REPLICA_PYTHON = Path("/content/venv311/bin/python")
REPLICA_ENV_FLAG = "AADP_T4_REPLICA_ACTIVE"


def run_setup(command, timeout):
    print("SETUP", " ".join(map(str, command)), flush=True)
    started = time.perf_counter()
    subprocess.run(list(map(str, command)), check=True, timeout=timeout)
    print(f"SETUP_DONE seconds={time.perf_counter() - started:.1f}", flush=True)


def replica_ready():
    if not REPLICA_PYTHON.is_file():
        return False
    probe = subprocess.run(
        [
            str(REPLICA_PYTHON),
            "-c",
            (
                "import sys, torch, transformers, safetensors, sklearn, joblib, sentencepiece; "
                "assert sys.version_info[:2] == (3, 11); "
                "assert torch.__version__.split('+')[0] == '2.7.1'; "
                "assert transformers.__version__ == '4.46.3'; "
                "assert safetensors.__version__ == '0.8.0'; "
                "assert sklearn.__version__ == '1.8.0'; "
                "assert joblib.__version__ == '1.5.3'"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if probe.returncode:
        print("REPLICA_PROBE_FAILED", probe.stdout[-4000:], flush=True)
    return probe.returncode == 0


def ensure_replica_and_reexec():
    """Run the timing probe under the eval-server Python/torch stack."""
    if os.environ.get(REPLICA_ENV_FLAG) == "1":
        if not replica_ready():
            raise RuntimeError("replica child started with an invalid dependency stack")
        return

    if not REPLICA_PYTHON.is_file():
        if shutil.which("python3.11") is None:
            run_setup(
                ["add-apt-repository", "-y", "ppa:deadsnakes/ppa"],
                timeout=300,
            )
            run_setup(["apt-get", "update", "-qq"], timeout=600)
            run_setup(
                [
                    "apt-get",
                    "install",
                    "-y",
                    "-q",
                    "python3.11",
                    "python3.11-venv",
                    "python3.11-dev",
                ],
                timeout=900,
            )
        run_setup(["python3.11", "-m", "venv", "/content/venv311"], timeout=300)

    if not replica_ready():
        run_setup(
            [str(REPLICA_PYTHON), "-m", "pip", "install", "-q", "--upgrade", "pip"],
            timeout=600,
        )
        run_setup(
            [
                str(REPLICA_PYTHON),
                "-m",
                "pip",
                "install",
                "-q",
                "torch==2.7.1",
                "--index-url",
                "https://download.pytorch.org/whl/cu128",
            ],
            timeout=1800,
        )
        run_setup(
            [
                str(REPLICA_PYTHON),
                "-m",
                "pip",
                "install",
                "-q",
                "transformers==4.46.3",
                "safetensors==0.8.0",
                "scikit-learn==1.8.0",
                "joblib==1.5.3",
                "sentencepiece",
            ],
            timeout=1200,
        )
    if not replica_ready():
        raise RuntimeError("failed to prepare the Python 3.11 / torch 2.7.1 replica stack")

    env = dict(os.environ, **{REPLICA_ENV_FLAG: "1", "PYTHONNOUSERSITE": "1"})
    command = [str(REPLICA_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
    print("REEXEC", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT, env=env)
    raise SystemExit(completed.returncode)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def save_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def import_pack_script(path):
    spec = importlib.util.spec_from_file_location("hcx15b_pack_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selected_rows(path, count, seed):
    rows = read_jsonl(path)
    order = list(range(len(rows)))
    random.Random(seed).shuffle(order)
    chosen = [rows[index] for index in order[: min(count, len(rows))]]
    if len(chosen) < count:
        raise ValueError(f"requested {count} rows but {path} has only {len(rows)}")
    return chosen


def make_proxy_data(data_dir, rows):
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "test.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (data_dir / "sample_submission.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "action"])
        writer.writerows((str(row.get("id", "")), "read_file") for row in rows)


def validate_submission(path, rows, classes):
    with Path(path).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        actual = list(reader)
    if reader.fieldnames != ["id", "action"]:
        raise RuntimeError(f"bad submission columns: {reader.fieldnames}")
    expected_ids = [str(row.get("id", "")) for row in rows]
    actual_ids = [row["id"] for row in actual]
    if actual_ids != expected_ids:
        raise RuntimeError("submission id order mismatch")
    bad = sorted({row["action"] for row in actual} - set(classes))
    if bad:
        raise RuntimeError(f"invalid actions: {bad}")
    return len(actual)


def run_grid(pack, model_dir, rows, batch_sizes, max_length, device):
    import torch
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_dir / "hf_model", local_files_only=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    meta = json.loads((model_dir / "hf_meta.json").read_text(encoding="utf-8"))
    serializer = meta.get("serializer_name", "current_v1")
    texts = [pack.serialize_transformer_sample(row, serializer, tokenizer=tokenizer) for row in rows]

    load_start = time.perf_counter()
    model = pack.load_hf_model(str(model_dir / "hf_model"), device)
    if device.type == "cuda":
        model.half()
    model.eval()
    if device.type == "cuda":
        torch.cuda.synchronize()
    load_seconds = time.perf_counter() - load_start
    print(f"GRID model_load_s={load_seconds:.3f}", flush=True)

    results = []
    reference_logits = None
    for batch_size in batch_sizes:
        if device.type == "cuda":
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        try:
            logits = pack.model_logits_sorted(
                model, tokenizer, texts, max_length, batch_size, device
            )
            if device.type == "cuda":
                torch.cuda.synchronize()
            seconds = time.perf_counter() - started
            peak = int(torch.cuda.max_memory_allocated()) if device.type == "cuda" else 0
            item = {
                "batch_size": batch_size,
                "ok": True,
                "seconds": seconds,
                "rows_per_second": len(rows) / seconds,
                "peak_cuda_bytes": peak,
            }
            if reference_logits is None:
                reference_logits = logits
                item["argmax_agreement_vs_reference"] = 1.0
                item["max_abs_logit_diff_vs_reference"] = 0.0
            else:
                item["argmax_agreement_vs_reference"] = float(
                    (logits.argmax(1) == reference_logits.argmax(1)).float().mean()
                )
                item["max_abs_logit_diff_vs_reference"] = float(
                    (logits - reference_logits).abs().max()
                )
            results.append(item)
            print("GRID", json.dumps(item), flush=True)
            del logits
        except torch.cuda.OutOfMemoryError as exc:
            item = {"batch_size": batch_size, "ok": False, "error": f"OOM: {exc}"}
            results.append(item)
            print("GRID", json.dumps(item), flush=True)
            torch.cuda.empty_cache()

    valid = [
        item for item in results
        if item.get("ok") and item.get("argmax_agreement_vs_reference") == 1.0
    ]
    if not valid:
        raise RuntimeError("no batch size passed OOM and prediction-parity gates")
    chosen = max(valid, key=lambda item: item["rows_per_second"])["batch_size"]
    del model
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return {"model_load_seconds": load_seconds, "results": results, "chosen_batch_size": chosen}


def run_clean_package(extract_dir, rows, batch_size, timeout):
    meta_path = extract_dir / "model/hf_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["batch_size"] = int(batch_size)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    make_proxy_data(extract_dir / "data", rows)
    env = dict(
        os.environ,
        TRANSFORMERS_OFFLINE="1",
        HF_DATASETS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="true",
    )
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "script.py"],
        cwd=extract_dir,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    wall = time.perf_counter() - started
    max_rss_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    print(proc.stdout[-12000:], flush=True)
    if proc.returncode:
        raise RuntimeError(f"clean package failed rc={proc.returncode}")
    output_path = extract_dir / "output/submission.csv"
    output_rows = validate_submission(output_path, rows, meta["classes"])
    return {
        "batch_size": batch_size,
        "wall_seconds": wall,
        "rows": output_rows,
        "seconds_per_row": wall / output_rows,
        "max_rss_kb": int(max_rss_kb),
        "stdout_tail": proc.stdout[-4000:],
    }


def main():
    ensure_replica_and_reexec()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True)
    parser.add_argument("--reference-zip")
    parser.add_argument("--reference-batch-size", type=int, default=64)
    parser.add_argument("--server-reference-seconds", type=float, default=358.0)
    parser.add_argument("--data", default="open/data/train.jsonl")
    parser.add_argument("--samples", type=int, default=30000)
    parser.add_argument("--grid-samples", type=int, default=2048)
    parser.add_argument("--batch-sizes", default="16,32,48,64")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--output",
        default="experiments/artifacts/20260713_hcx15b_t4_runtime.json",
    )
    args = parser.parse_args()

    import torch

    report = {
        "schema_version": 1,
        "started_at": now_iso(),
        "zip": args.zip,
        "data": args.data,
        "samples": args.samples,
        "grid_samples": args.grid_samples,
        "seed": args.seed,
        "stack": {
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        },
    }
    save_report(args.output, report)
    try:
        if not torch.cuda.is_available() or "T4" not in torch.cuda.get_device_name(0):
            raise RuntimeError(f"T4 required, got {report['stack']['gpu']!r}")
        source_rows = selected_rows(args.data, args.samples, args.seed)
        batch_sizes = [int(value) for value in args.batch_sizes.split(",") if value]
        with tempfile.TemporaryDirectory(prefix="hcx15b_t4_") as td:
            extract_dir = Path(td)
            extract_start = time.perf_counter()
            with zipfile.ZipFile(args.zip) as archive:
                bad = archive.testzip()
                if bad:
                    raise RuntimeError(f"zip CRC failure: {bad}")
                archive.extractall(extract_dir)
            report["extract_seconds"] = time.perf_counter() - extract_start
            report["zip_bytes"] = Path(args.zip).stat().st_size
            pack = import_pack_script(extract_dir / "script.py")
            report["grid"] = run_grid(
                pack,
                extract_dir / "model",
                source_rows[: args.grid_samples],
                batch_sizes,
                384,
                torch.device("cuda"),
            )
            save_report(args.output, report)
            report["clean_run"] = run_clean_package(
                extract_dir,
                source_rows,
                report["grid"]["chosen_batch_size"],
                args.timeout,
            )
            if args.reference_zip:
                reference_dir = extract_dir / "reference_extract"
                reference_dir.mkdir()
                reference_start = time.perf_counter()
                with zipfile.ZipFile(args.reference_zip) as archive:
                    bad = archive.testzip()
                    if bad:
                        raise RuntimeError(f"reference zip CRC failure: {bad}")
                    archive.extractall(reference_dir)
                report["reference"] = {
                    "zip": args.reference_zip,
                    "zip_bytes": Path(args.reference_zip).stat().st_size,
                    "extract_seconds": time.perf_counter() - reference_start,
                    "clean_run": run_clean_package(
                        reference_dir,
                        source_rows,
                        args.reference_batch_size,
                        args.timeout,
                    ),
                }
                candidate_wall = report["clean_run"]["wall_seconds"]
                reference_wall = report["reference"]["clean_run"]["wall_seconds"]
                report["same_t4_wall_ratio"] = candidate_wall / reference_wall
                report["projected_server_seconds"] = (
                    args.server_reference_seconds * report["same_t4_wall_ratio"]
                )
        wall = report["clean_run"]["wall_seconds"]
        verdict_seconds = report.get("projected_server_seconds", wall)
        report["verdict_basis"] = (
            "projected_server_seconds"
            if "projected_server_seconds" in report
            else "candidate_wall_seconds"
        )
        report["verdict_seconds"] = verdict_seconds
        report["verdict"] = (
            "GREEN" if verdict_seconds <= 510
            else "YELLOW" if verdict_seconds <= 600
            else "RED"
        )
        report["completed_at"] = now_iso()
        save_report(args.output, report)
        print("T4_RESULT", json.dumps({
            "verdict": report["verdict"],
            "wall_seconds": wall,
            "batch_size": report["grid"]["chosen_batch_size"],
            "same_t4_wall_ratio": report.get("same_t4_wall_ratio"),
            "projected_server_seconds": report.get("projected_server_seconds"),
            "output": args.output,
        }), flush=True)
    except Exception as exc:
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
        report["completed_at"] = now_iso()
        report["verdict"] = "ERROR"
        save_report(args.output, report)
        raise


if __name__ == "__main__":
    main()
