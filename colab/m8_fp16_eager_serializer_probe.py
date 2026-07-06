"""M8 eager fp16 DeltaNet timing by serializer/max_length.

This intentionally avoids torch.compile. Colab/Public CPU differences make
Inductor compile wall-clock hard to transfer, so this probe measures the plain
eager fp16 fallback path and token-length levers only.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from colab.m8_fp16_deltanet_probe import make_fp16_rule, rebind_rule  # noqa: E402
from colab.m8_qwen35_compile_probe import make_batches, run_batches  # noqa: E402
from colab.m8_qwen35_maxpack import mirror_to_drive, write_json  # noqa: E402
from colab.m8_qwen35_t4_replica_probe import (  # noqa: E402
    QWEN3,
    QWEN35,
    capture_model_load,
    model_timing,
    read_jsonl,
    run,
    set_pad_token,
    stack_info,
    tokenize_features,
    utc_now,
)


EXPERIMENT_ID = "m8_fp16_eager_serializer"
VENV_PY = "/content/venv311/bin/python"
DATA_PATH = Path("open/data/train.jsonl")
RESULTS_PATH = Path("experiments/results.csv")
INFER_ANCHOR_SEC = 478.0
BATCH_SIZE = 64


def art_path():
    return Path("experiments/artifacts") / f"{EXPERIMENT_ID}.json"


def configure(experiment_id):
    global EXPERIMENT_ID
    EXPERIMENT_ID = experiment_id


def ensure_venv():
    if Path(VENV_PY).exists():
        return
    run(["add-apt-repository", "-y", "ppa:deadsnakes/ppa"], check=False, timeout=300)
    run(
        ["apt-get", "install", "-y", "-q", "python3.11", "python3.11-venv", "python3.11-dev"],
        check=False,
        timeout=600,
    )
    made = run(["python3.11", "-m", "venv", "/content/venv311"], check=False, timeout=300)
    if made["returncode"] != 0 or not Path(VENV_PY).exists():
        run([sys.executable, "-m", "venv", "/content/venv311"], check=False, timeout=300)
    run([VENV_PY, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=False, timeout=600)
    run(
        [VENV_PY, "-m", "pip", "install", "-q", "torch==2.7.1", "--index-url", "https://download.pytorch.org/whl/cu128"],
        check=False,
        timeout=1800,
    )
    run(
        [VENV_PY, "-m", "pip", "install", "-q", "transformers>=5.13,<5.14", "safetensors==0.8.0"],
        check=False,
        timeout=900,
    )


def disable_decoder_cache(model):
    for cfg in (getattr(model, "config", None), getattr(getattr(model, "model", None), "config", None)):
        if cfg is not None and hasattr(cfg, "use_cache"):
            cfg.use_cache = False


def trained_dirs():
    drive = Path("/content/drive/MyDrive")
    exchange = os.environ.get("AADP_EXCHANGE_DIR", "AADP_exchange")
    return [
        drive / exchange / "models/m8_qwen35_refit/hf_model",
        drive / "AADP_exchange/models/m8_qwen35_refit/hf_model",
    ]


def load_model():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    for model_dir in trained_dirs():
        if model_dir.exists():
            tokenizer = AutoTokenizer.from_pretrained(model_dir)
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token
            model = AutoModelForSequenceClassification.from_pretrained(model_dir, torch_dtype=torch.float16)
            set_pad_token(model, tokenizer)
            disable_decoder_cache(model)
            model.to("cuda").eval().half()
            return model, tokenizer, torch.device("cuda"), f"trained:{model_dir}"

    tokenizer = AutoTokenizer.from_pretrained(QWEN35)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    torch.manual_seed(1234)
    model, warnings = capture_model_load(QWEN35, torch.float16)
    set_pad_token(model, tokenizer)
    disable_decoder_cache(model)
    model.to("cuda").eval().half()
    return model, tokenizer, torch.device("cuda"), {"hub_random_head": warnings}


def selected_texts(count, serializer):
    from script import serialize_transformer_sample

    samples = read_jsonl(DATA_PATH)
    order = list(range(len(samples)))
    random.Random(42).shuffle(order)
    chosen = [samples[i] for i in order[:count]]
    texts = [serialize_transformer_sample(sample, serializer) for sample in chosen]
    ids = [sample.get("id") for sample in chosen]
    return texts, ids


def append_result_row(payload):
    row = {
        "experiment_id": EXPERIMENT_ID,
        "model_family": "m8_t4_eager_fp16_timing",
        "base_model": QWEN35,
        "features": "fp16 DeltaNet eager timing by serializer/max_length",
        "serializer_name": payload.get("best_serializer", ""),
        "split_type": "timing_probe",
        "seed": "42",
        "max_length": str(payload.get("best_max_length", "")),
        "batch_size": str(BATCH_SIZE),
        "artifact_path": str(art_path()),
        "inference_time_sec": str(payload.get("best_projected_infer_sec", "")),
        "runtime_sec": f"{payload.get('runtime_sec', 0.0):.3f}",
        "train_command": "colab/m8_fp16_eager_serializer_probe.py",
        "notes": payload.get("notes", ""),
        "decision": payload.get("verdict", ""),
    }
    try:
        from train import append_results_csv

        append_results_csv(RESULTS_PATH, row)
    except Exception as exc:  # noqa: BLE001
        payload["append_result_error"] = repr(exc)


def checkpoint(payload):
    write_json(art_path(), payload)
    mirror_to_drive(art_path())


def parse_configs(configs):
    parsed = []
    for item in configs.split(","):
        item = item.strip()
        if not item:
            continue
        serializer, max_len = item.split(":", 1)
        parsed.append((serializer, int(max_len)))
    return parsed


def main():
    if sys.executable != VENV_PY:
        ensure_venv()
        if Path(VENV_PY).exists():
            os.execv(VENV_PY, [VENV_PY, "-u", str(Path(__file__).resolve()), *sys.argv[1:]])

    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-id", default=EXPERIMENT_ID)
    parser.add_argument("--rows", type=int, default=4096)
    parser.add_argument("--warmup-rows", type=int, default=128)
    parser.add_argument("--configs", default="current_v6:352,current_v6:384,current_v6:400,current_v1:400")
    args = parser.parse_args()
    configure(args.experiment_id)

    import gc

    import torch

    started = time.perf_counter()
    payload = {
        "what": "eager fp16 DeltaNet timing by serializer/max_length (no torch.compile)",
        "experiment_id": EXPERIMENT_ID,
        "created_utc": utc_now(),
        "stack": stack_info(),
        "rows": args.rows,
        "batch_size": BATCH_SIZE,
        "configs": {},
        "infer_anchor_sec": INFER_ANCHOR_SEC,
    }
    step = "start"
    try:
        step = "denominator"
        denom = model_timing(QWEN3, 416, BATCH_SIZE, "sorted", args.rows)
        payload["qwen3_denominator"] = {k: denom[k] for k in ("tokenize_plus_infer_sec", "infer_sec")}
        denom_sec = denom["tokenize_plus_infer_sec"]
        gc.collect()
        torch.cuda.empty_cache()
        checkpoint(payload)

        step = "load_model"
        model, tokenizer, device, weights = load_model()
        payload["weights"] = str(weights)
        rule = make_fp16_rule(state_in_fp32=False)
        rebound = rebind_rule(model, rule)
        payload["fp16_rebound_layers"] = rebound

        # One small warmup avoids charging lazy CUDA/module setup to whichever
        # serializer happens to run first. This is not a compile warmup.
        if args.warmup_rows > 0:
            texts, _ = selected_texts(args.warmup_rows, "current_v6")
            features, lengths, _ = tokenize_features(tokenizer, texts, 384)
            batches = make_batches(features, lengths, BATCH_SIZE, None)
            run_batches(model, tokenizer, features, batches, BATCH_SIZE, None, device, len(texts))
            torch.cuda.synchronize()
            payload["warmup_rows"] = args.warmup_rows
            payload["kernel_calls_after_warmup"] = rule.calls["n"]
            checkpoint(payload)

        best = None
        for serializer, max_length in parse_configs(args.configs):
            step = f"{serializer}:{max_length}"
            calls_before = rule.calls["n"]
            texts, ids = selected_texts(args.rows, serializer)
            features, lengths, tokenize_sec = tokenize_features(tokenizer, texts, max_length)
            batches = make_batches(features, lengths, BATCH_SIZE, None)
            logits, infer_sec, batch_times = run_batches(
                model, tokenizer, features, batches, BATCH_SIZE, None, device, len(texts)
            )
            total = tokenize_sec + infer_sec
            ratio = total / denom_sec
            projected = ratio * INFER_ANCHOR_SEC
            entry = {
                "serializer": serializer,
                "max_length": max_length,
                "tokenize_sec": tokenize_sec,
                "infer_sec": infer_sec,
                "tokenize_plus_infer_sec": total,
                "ratio_vs_qwen3": ratio,
                "projected_infer_sec": projected,
                "ten_min_full_model": projected <= 600,
                "max_full_rows_fraction_for_600s": min(1.0, 600.0 / projected) if projected else None,
                "batch_count": len(batch_times),
                "batch_times_first8": batch_times[:8],
                "batch_times_slowest5": sorted(batch_times)[-5:],
                "kernel_calls": rule.calls["n"] - calls_before,
                "lengths": {
                    "mean": sum(lengths) / max(1, len(lengths)),
                    "max": max(lengths) if lengths else 0,
                    "over_352": sum(1 for length in lengths if length > 352),
                    "over_384": sum(1 for length in lengths if length > 384),
                    "over_400": sum(1 for length in lengths if length > 400),
                },
                "prediction_distribution": {
                    str(k): int(v) for k, v in zip(*torch.unique(logits.argmax(1), return_counts=True))
                },
                "sample_ids_first5": ids[:5],
            }
            payload["configs"][f"{serializer}:{max_length}"] = entry
            if best is None or projected < best["projected_infer_sec"]:
                best = entry
            checkpoint(payload)

        payload["best_serializer"] = best["serializer"] if best else None
        payload["best_max_length"] = best["max_length"] if best else None
        payload["best_projected_infer_sec"] = best["projected_infer_sec"] if best else None
        payload["verdict"] = (
            "GREEN_all_rows_under_10m"
            if best and best["projected_infer_sec"] <= 600
            else "YELLOW_needs_cascade_or_more_rows_cut"
            if best
            else "RED_no_result"
        )
        payload["notes"] = "projection uses same-VM Qwen3 denominator and M7 infer anchor; no compile path"
        payload["runtime_sec"] = time.perf_counter() - started
        checkpoint(payload)
        append_result_row(payload)
    except Exception:  # noqa: BLE001
        payload["verdict"] = "RED_error"
        payload["failed_step"] = step
        payload["traceback"] = traceback.format_exc()[-4000:]
        payload["runtime_sec"] = time.perf_counter() - started
        checkpoint(payload)
        append_result_row(payload)
        raise


if __name__ == "__main__":
    main()
