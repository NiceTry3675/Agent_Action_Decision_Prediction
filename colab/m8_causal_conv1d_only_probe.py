"""M8 Qwen3.5 causal-conv1d-only timing probe.

FLA fast path is closed on the server-matched T4 stack, but Qwen3.5 can still
use causal-conv1d for the short depthwise conv inside each DeltaNet layer while
keeping the torch DeltaNet fallback. This runner measures that isolated lever:

1. uninstall FLA/causal-conv1d, measure fp16-DeltaNet eager baseline;
2. install only the causal-conv1d GitHub wheel, no FLA, measure the same rows;
3. compare logits on a correctness slice and report same-VM speedup.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from colab.m8_fp16_deltanet_probe import make_fp16_rule, rebind_rule  # noqa: E402
from colab.m8_fp16_eager_serializer_probe import load_model, selected_texts  # noqa: E402
from colab.m8_qwen35_compile_probe import make_batches, run_batches  # noqa: E402
from colab.m8_qwen35_maxpack import mirror_to_drive, write_json  # noqa: E402
from colab.m8_qwen35_t4_replica_probe import (  # noqa: E402
    QWEN35,
    qwen35_flags,
    run,
    stack_info,
    tokenize_features,
    utc_now,
)


EXPERIMENT_ID = "m8_causal_conv1d_only"
VENV_PY = "/content/venv311/bin/python"
ART_DIR = Path("experiments/artifacts")
RESULTS_PATH = Path("experiments/results.csv")
BATCH_SIZE = 64
REFERENCE_PROJECTION_SEC = 698.7692211217885  # current_v6:352 no-causal C-lane anchor.


def artifact_path(experiment_id=None, suffix=""):
    experiment_id = experiment_id or EXPERIMENT_ID
    stem = experiment_id + (f"_{suffix}" if suffix else "")
    return ART_DIR / f"{stem}.json"


def reference_path(experiment_id=None):
    experiment_id = experiment_id or EXPERIMENT_ID
    return ART_DIR / f"{experiment_id}_no_causal_reference.pt"


def configure(experiment_id):
    global EXPERIMENT_ID
    EXPERIMENT_ID = experiment_id


def py_run(args, check=True, timeout=None):
    return run([sys.executable, *args], check=check, timeout=timeout)


def ensure_venv():
    if sys.executable == VENV_PY or not str(ROOT).startswith("/content/"):
        return
    if not Path(VENV_PY).exists():
        run(["add-apt-repository", "-y", "ppa:deadsnakes/ppa"], check=False, timeout=300)
        run(
            ["apt-get", "install", "-y", "-q", "python3.11", "python3.11-venv", "python3.11-dev"],
            check=False,
            timeout=600,
        )
        run(["python3.11", "-m", "venv", "/content/venv311"], check=False, timeout=300)
        if not Path(VENV_PY).exists():
            run([sys.executable, "-m", "venv", "/content/venv311"], check=False, timeout=300)
        run([VENV_PY, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=False, timeout=600)
    os.execv(VENV_PY, [VENV_PY, "-u", str(Path(__file__).resolve()), *sys.argv[1:]])


def install_base_stack():
    logs = []
    logs.append(
        py_run(
            ["-m", "pip", "install", "torch==2.7.1", "--index-url", "https://download.pytorch.org/whl/cu128"],
            check=False,
            timeout=1800,
        )
    )
    logs.append(py_run(["-m", "pip", "uninstall", "-y", "torchvision", "torchaudio", "torchtext"],
                       check=False, timeout=300))
    logs.append(py_run(["-m", "pip", "install", "transformers>=5.13,<5.14", "safetensors==0.8.0"],
                       check=False, timeout=900))
    logs.append(uninstall_optional())
    return logs


def uninstall_optional():
    return py_run(
        [
            "-m",
            "pip",
            "uninstall",
            "-y",
            "causal-conv1d",
            "causal_conv1d",
            "fla-core",
            "flash-linear-attention",
            "flash_linear_attention",
        ],
        check=False,
        timeout=300,
    )


def install_causal_conv1d():
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    wheel = (
        "https://github.com/Dao-AILab/causal-conv1d/releases/download/v1.6.2.post1/"
        f"causal_conv1d-1.6.2.post1+cu12torch2.7cxx11abiTRUE-{py_tag}-{py_tag}-linux_x86_64.whl"
    )
    logs = [uninstall_optional()]
    logs.append(py_run(["-m", "pip", "install", wheel], check=False, timeout=600))
    logs.append(py_run(["-c", "import causal_conv1d; print(causal_conv1d.__version__)"],
                       check=False, timeout=120))
    return logs


def checkpoint(path, payload, mirror=True):
    write_json(path, payload)
    if mirror:
        mirror_to_drive(path)
    print(f"checkpoint: {path}", flush=True)


def conv_status(model):
    layers = []
    for name, module in model.named_modules():
        if not hasattr(module, "causal_conv1d_fn") or not hasattr(module, "conv1d"):
            continue
        fn = getattr(module, "causal_conv1d_fn")
        layers.append(
            {
                "name": name,
                "causal_conv1d_fn_is_none": fn is None,
                "causal_conv1d_fn_module": getattr(fn, "__module__", None) if fn is not None else None,
                "conv_channels": int(getattr(module.conv1d, "in_channels", 0)),
                "kernel_size": list(getattr(module.conv1d, "kernel_size", ())),
            }
        )
    return {
        "linear_layers_seen": len(layers),
        "causal_conv1d_active_layers": sum(not item["causal_conv1d_fn_is_none"] for item in layers),
        "first_layers": layers[:4],
    }


def compare_logits(logits, ref_logits):
    import torch

    ref_logits = ref_logits.float()
    logits = logits.float()
    diff = (logits - ref_logits).abs()
    return {
        "argmax_agreement": float((logits.argmax(1) == ref_logits.argmax(1)).float().mean().item()),
        "max_abs_diff": float(diff.max().item()),
        "mean_abs_diff": float(diff.mean().item()),
        "p95_abs_diff": float(torch.quantile(diff.flatten(), 0.95).item()),
    }


def run_variant(args):
    import gc

    import torch

    configure(args.experiment_id)
    started = time.perf_counter()
    out_path = artifact_path(args.experiment_id, args.variant)
    payload = {
        "experiment_id": f"{args.experiment_id}_{args.variant}",
        "parent_experiment_id": args.experiment_id,
        "what": "Qwen3.5 causal-conv1d-only fp16 DeltaNet eager timing variant",
        "created_utc": utc_now(),
        "variant": args.variant,
        "rows": args.rows,
        "correctness_rows": args.correctness_rows,
        "warmup_rows": args.warmup_rows,
        "serializer": args.serializer,
        "max_length": args.max_length,
        "batch_size": BATCH_SIZE,
        "stack_before_load": stack_info(),
    }
    step = "start"
    try:
        step = "load_model"
        model, tokenizer, device, weights = load_model()
        payload["weights"] = str(weights)
        payload["stack_after_load"] = stack_info()
        payload["qwen35_flags"] = qwen35_flags()
        payload["conv_status"] = conv_status(model)

        step = "rebind_fp16_deltanet"
        rule = make_fp16_rule(state_in_fp32=False)
        payload["fp16_rebound_layers"] = rebind_rule(model, rule)

        if args.warmup_rows > 0:
            step = "warmup"
            texts, _ = selected_texts(args.warmup_rows, args.serializer)
            features, lengths, _ = tokenize_features(tokenizer, texts, args.max_length)
            batches = make_batches(features, lengths, BATCH_SIZE, None)
            run_batches(model, tokenizer, features, batches, BATCH_SIZE, None, device, len(texts))
            torch.cuda.synchronize()
            payload["kernel_calls_after_warmup"] = rule.calls["n"]
            checkpoint(out_path, payload)

        step = "timing"
        calls_before = rule.calls["n"]
        texts, ids = selected_texts(args.rows, args.serializer)
        features, lengths, tokenize_sec = tokenize_features(tokenizer, texts, args.max_length)
        batches = make_batches(features, lengths, BATCH_SIZE, None)
        logits, infer_sec, batch_times = run_batches(
            model,
            tokenizer,
            features,
            batches,
            BATCH_SIZE,
            None,
            device,
            len(texts),
        )
        payload["timing"] = {
            "tokenize_sec": tokenize_sec,
            "infer_sec": infer_sec,
            "tokenize_plus_infer_sec": tokenize_sec + infer_sec,
            "batch_count": len(batch_times),
            "batch_times_first8": batch_times[:8],
            "batch_times_slowest5": sorted(batch_times)[-5:],
            "kernel_calls": rule.calls["n"] - calls_before,
            "lengths": {
                "mean": sum(lengths) / max(1, len(lengths)),
                "max": max(lengths) if lengths else 0,
                "over_max_length": sum(1 for length in lengths if length > args.max_length),
            },
            "sample_ids_first5": ids[:5],
        }

        step = "reference"
        if args.write_reference:
            reference_path(args.experiment_id).parent.mkdir(parents=True, exist_ok=True)
            torch.save(
                {
                    "variant": args.variant,
                    "logits": logits[: args.correctness_rows].cpu(),
                    "ids": ids[: args.correctness_rows],
                    "timing": payload["timing"],
                    "conv_status": payload["conv_status"],
                    "stack": payload["stack_after_load"],
                },
                reference_path(args.experiment_id),
            )
            payload["reference_path"] = str(reference_path(args.experiment_id))
        elif args.reference_path:
            ref = torch.load(args.reference_path, map_location="cpu", weights_only=False)
            payload["correctness_vs_reference"] = compare_logits(
                logits[: args.correctness_rows].cpu(),
                ref["logits"][: args.correctness_rows],
            )
            payload["reference_variant"] = ref.get("variant")
            payload["reference_ids_match"] = ids[: args.correctness_rows] == ref.get("ids", [])[: args.correctness_rows]

        payload["verdict"] = "GREEN_variant_measured"
        payload["runtime_sec"] = time.perf_counter() - started
        checkpoint(out_path, payload)

        del model, tokenizer, logits
        gc.collect()
        torch.cuda.empty_cache()
        return 0
    except Exception:
        payload["verdict"] = "RED_error"
        payload["failed_step"] = step
        payload["traceback"] = traceback.format_exc()[-5000:]
        payload["runtime_sec"] = time.perf_counter() - started
        checkpoint(out_path, payload)
        return 43


def append_result_row(summary):
    row = {
        "experiment_id": summary.get("experiment_id", EXPERIMENT_ID),
        "model_family": "m8_causal_conv1d_only_timing",
        "base_model": QWEN35,
        "features": "fp16 DeltaNet eager baseline vs causal-conv1d-only",
        "serializer_name": summary.get("serializer", ""),
        "split_type": "timing_probe",
        "seed": "42",
        "fold_id": "",
        "max_length": str(summary.get("max_length", "")),
        "epochs": "",
        "learning_rate": "",
        "batch_size": str(BATCH_SIZE),
        "class_weight_power": "",
        "label_smoothing": "",
        "replay_mode": "",
        "replay_size": "",
        "macro_f1_raw": "",
        "macro_f1_bias_tuned": "",
        "macro_f1_bias_tuned_2stage": "",
        "macro_f1": "",
        "weakest_classes": "",
        "top_confusions": "",
        "prediction_distribution": "",
        "artifact_path": str(artifact_path(summary.get("experiment_id", EXPERIMENT_ID))),
        "val_logits_path": "",
        "test_logits_path": "",
        "inference_time_sec": str(summary.get("causal_adjusted_projection_sec", "")),
        "runtime_sec": f"{summary.get('runtime_sec', 0.0):.3f}",
        "artifact_size_mb": "",
        "train_command": "colab/m8_causal_conv1d_only_probe.py controller",
        "notes": summary.get("notes", ""),
        "decision": summary.get("verdict", ""),
    }
    try:
        from train import append_results_csv

        append_results_csv(RESULTS_PATH, row)
        return
    except Exception:
        pass
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    exists = RESULTS_PATH.exists() and RESULTS_PATH.stat().st_size > 0
    with RESULTS_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def load_variant_artifact(experiment_id, variant):
    path = artifact_path(experiment_id, variant)
    if not path.exists():
        return {"verdict": "RED_missing_artifact", "path": str(path)}
    return json.loads(path.read_text(encoding="utf-8"))


def controller(args):
    configure(args.experiment_id)
    ensure_venv()
    started = time.perf_counter()
    ART_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "experiment_id": args.experiment_id,
        "what": "Qwen3.5 causal-conv1d-only A/B timing controller",
        "created_utc": utc_now(),
        "rows": args.rows,
        "correctness_rows": args.correctness_rows,
        "warmup_rows": args.warmup_rows,
        "serializer": args.serializer,
        "max_length": args.max_length,
        "batch_size": BATCH_SIZE,
        "logs_tail": [],
        "variants": {},
        "reference_projection_sec": REFERENCE_PROJECTION_SEC,
    }
    try:
        gpu = run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], check=False)
        summary["gpu"] = gpu.get("output_tail", "").strip()
        if args.require_t4 and "T4" not in summary["gpu"]:
            summary["verdict"] = "WRONG_GPU"
            summary["runtime_sec"] = time.perf_counter() - started
            checkpoint(artifact_path(args.experiment_id), summary)
            append_result_row(summary)
            return

        if not args.skip_install:
            summary["logs_tail"].extend(install_base_stack())
        else:
            summary["logs_tail"].append(uninstall_optional())

        baseline_cmd = [
            "colab/m8_causal_conv1d_only_probe.py",
            "variant",
            "--experiment-id",
            args.experiment_id,
            "--variant",
            "no_causal",
            "--rows",
            str(args.rows),
            "--correctness-rows",
            str(args.correctness_rows),
            "--warmup-rows",
            str(args.warmup_rows),
            "--serializer",
            args.serializer,
            "--max-length",
            str(args.max_length),
            "--write-reference",
        ]
        rc = py_run(baseline_cmd, check=False, timeout=args.variant_timeout)
        summary["logs_tail"].append(rc)
        summary["variants"]["no_causal"] = load_variant_artifact(args.experiment_id, "no_causal")
        checkpoint(artifact_path(args.experiment_id), summary)
        if rc["returncode"] != 0 or not reference_path(args.experiment_id).exists():
            summary["verdict"] = "RED_no_causal_failed"
            summary["runtime_sec"] = time.perf_counter() - started
            checkpoint(artifact_path(args.experiment_id), summary)
            append_result_row(summary)
            return

        summary["logs_tail"].extend(install_causal_conv1d())
        causal_cmd = [
            "colab/m8_causal_conv1d_only_probe.py",
            "variant",
            "--experiment-id",
            args.experiment_id,
            "--variant",
            "causal_only",
            "--rows",
            str(args.rows),
            "--correctness-rows",
            str(args.correctness_rows),
            "--warmup-rows",
            str(args.warmup_rows),
            "--serializer",
            args.serializer,
            "--max-length",
            str(args.max_length),
            "--reference-path",
            str(reference_path(args.experiment_id)),
        ]
        rc = py_run(causal_cmd, check=False, timeout=args.variant_timeout)
        summary["logs_tail"].append(rc)
        summary["variants"]["causal_only"] = load_variant_artifact(args.experiment_id, "causal_only")

        base_t = summary["variants"]["no_causal"].get("timing", {})
        causal_t = summary["variants"]["causal_only"].get("timing", {})
        base_total = base_t.get("tokenize_plus_infer_sec")
        causal_total = causal_t.get("tokenize_plus_infer_sec")
        base_infer = base_t.get("infer_sec")
        causal_infer = causal_t.get("infer_sec")
        if base_total and causal_total:
            ratio = causal_total / base_total
            summary["speedup"] = {
                "tokenize_plus_infer_ratio": ratio,
                "tokenize_plus_infer_speedup_pct": (1.0 - ratio) * 100.0,
                "infer_ratio": causal_infer / base_infer if base_infer and causal_infer else None,
                "infer_speedup_pct": (1.0 - causal_infer / base_infer) * 100.0
                if base_infer and causal_infer
                else None,
            }
            summary["causal_adjusted_projection_sec"] = REFERENCE_PROJECTION_SEC * ratio

        correctness = summary["variants"]["causal_only"].get("correctness_vs_reference", {})
        active = summary["variants"]["causal_only"].get("conv_status", {}).get("causal_conv1d_active_layers", 0)
        if rc["returncode"] != 0:
            summary["verdict"] = "RED_causal_variant_failed"
        elif active <= 0:
            summary["verdict"] = "RED_causal_not_active"
        elif correctness.get("argmax_agreement", 0.0) < args.min_agreement:
            summary["verdict"] = "RED_correctness"
        elif summary.get("causal_adjusted_projection_sec", 9999.0) <= 600.0:
            summary["verdict"] = "GREEN_causal_closes_10m"
        else:
            summary["verdict"] = "YELLOW_small_speedup_only"
        summary["notes"] = "causal-conv1d isolated from FLA; projection rescales prior current_v6:352 C-lane anchor"
        summary["runtime_sec"] = time.perf_counter() - started
        checkpoint(artifact_path(args.experiment_id), summary)
        append_result_row(summary)
    except Exception:
        summary["verdict"] = "RED_error"
        summary["traceback"] = traceback.format_exc()[-5000:]
        summary["runtime_sec"] = time.perf_counter() - started
        checkpoint(artifact_path(args.experiment_id), summary)
        append_result_row(summary)
        raise


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_controller = sub.add_parser("controller")
    p_controller.add_argument("--experiment-id", default=EXPERIMENT_ID)
    p_controller.add_argument("--rows", type=int, default=4096)
    p_controller.add_argument("--correctness-rows", type=int, default=256)
    p_controller.add_argument("--warmup-rows", type=int, default=128)
    p_controller.add_argument("--serializer", default="current_v6")
    p_controller.add_argument("--max-length", type=int, default=352)
    p_controller.add_argument("--variant-timeout", type=int, default=1200)
    p_controller.add_argument("--min-agreement", type=float, default=0.995)
    p_controller.add_argument("--require-t4", action="store_true")
    p_controller.add_argument("--skip-install", action="store_true")

    p_variant = sub.add_parser("variant")
    p_variant.add_argument("--experiment-id", required=True)
    p_variant.add_argument("--variant", required=True)
    p_variant.add_argument("--rows", type=int, required=True)
    p_variant.add_argument("--correctness-rows", type=int, required=True)
    p_variant.add_argument("--warmup-rows", type=int, required=True)
    p_variant.add_argument("--serializer", required=True)
    p_variant.add_argument("--max-length", type=int, required=True)
    p_variant.add_argument("--write-reference", action="store_true")
    p_variant.add_argument("--reference-path", default="")

    args = parser.parse_args()
    if args.command == "controller":
        controller(args)
    else:
        raise SystemExit(run_variant(args))


if __name__ == "__main__":
    main()
