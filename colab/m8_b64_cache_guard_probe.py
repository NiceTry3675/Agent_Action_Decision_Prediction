"""M8 C-lane probe: exact b64 compile-cache guard + fp16 DeltaNet timing.

The previous `m8_ep3_cc.zip` timeout was contaminated: `hf_meta` advertised
batch 64 / buckets {256,400}, but the embedded Inductor cache only contained a
batch 128 shape. This probe bakes the intended shapes and audits the generated
guards before we consider another package.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tarfile
import time
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from colab.m8_fp16_deltanet_probe import make_fp16_rule, rebind_rule  # noqa: E402
from colab.m8_qwen35_compile_probe import make_batches, pad_batch, run_batches  # noqa: E402
from colab.m8_qwen35_maxpack import mirror_to_drive, write_json  # noqa: E402
from colab.m8_qwen35_t4_replica_probe import (  # noqa: E402
    capture_model_load,
    model_timing,
    run,
    selected_samples,
    set_pad_token,
    stack_info,
    tokenize_features,
    utc_now,
)


EXPERIMENT_ID = "m8_b64_cache_guard"
QWEN35 = "igorktech/Qwen3.5-0.8B-Base-LM"
QWEN3 = "Qwen/Qwen3-0.6B"
NUM_LABELS = 14
INFER_ANCHOR_SEC = 478.0
VENV_PY = "/content/venv311/bin/python"
INDUCTOR_CACHE = "/content/m8_b64_guard_inductor_cache"
TRITON_CACHE = "/content/m8_b64_guard_triton_cache"
RESULTS_PATH = Path("experiments/results.csv")
BUCKETS = [256, 400]
BATCH_SIZE = 64
MAX_LENGTH = 400


def art_path(suffix=""):
    name = EXPERIMENT_ID + (f"_{suffix}" if suffix else "")
    return Path("experiments/artifacts") / f"{name}.json"


def configure(experiment_id):
    global EXPERIMENT_ID
    EXPERIMENT_ID = experiment_id


def vrun(args, check=True, timeout=None):
    return run([VENV_PY, *args], check=check, timeout=timeout)


def ensure_venv():
    if Path(VENV_PY).exists():
        return True
    run(["add-apt-repository", "-y", "ppa:deadsnakes/ppa"], check=False, timeout=300)
    run(
        ["apt-get", "install", "-y", "-q", "python3.11", "python3.11-venv", "python3.11-dev"],
        check=False,
        timeout=600,
    )
    made = run(["python3.11", "-m", "venv", "/content/venv311"], check=False, timeout=300)
    if made["returncode"] != 0 or not Path(VENV_PY).exists():
        run([sys.executable, "-m", "venv", "/content/venv311"], check=False, timeout=300)
    vrun(["-m", "pip", "install", "-q", "--upgrade", "pip"], check=False, timeout=600)
    vrun(
        ["-m", "pip", "install", "-q", "torch==2.7.1", "--index-url", "https://download.pytorch.org/whl/cu128"],
        check=False,
        timeout=1800,
    )
    vrun(
        ["-m", "pip", "install", "-q", "transformers>=5.13,<5.14", "safetensors==0.8.0"],
        check=False,
        timeout=900,
    )
    return Path(VENV_PY).exists()


def append_result_row(payload):
    row = {
        "experiment_id": EXPERIMENT_ID,
        "model_family": "m8_t4_compile_cache_probe",
        "base_model": QWEN35,
        "features": "current_v1, fp16 DeltaNet fallback, torch.compile b64 buckets 256/400",
        "serializer_name": "current_v1",
        "split_type": "timing_probe",
        "seed": "42",
        "max_length": str(MAX_LENGTH),
        "batch_size": str(BATCH_SIZE),
        "artifact_path": str(art_path()),
        "inference_time_sec": str(payload.get("projected_total_sec", "")),
        "runtime_sec": f"{payload.get('runtime_sec', 0.0):.3f}",
        "train_command": "colab/m8_b64_cache_guard_probe.py controller",
        "notes": payload.get("notes", ""),
        "decision": payload.get("verdict", ""),
    }
    try:
        from train import append_results_csv

        append_results_csv(RESULTS_PATH, row)
    except Exception as exc:  # noqa: BLE001
        payload["append_result_error"] = repr(exc)


def checkpoint(payload, suffix="", *extra_paths):
    path = art_path(suffix)
    write_json(path, payload)
    mirror_to_drive(path, *extra_paths)


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
            model = AutoModelForSequenceClassification.from_pretrained(model_dir, torch_dtype=torch.float16)
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token
            set_pad_token(model, tokenizer)
            disable_decoder_cache(model)
            model.to("cuda").eval().half()
            return model, tokenizer, torch.device("cuda"), f"trained:{model_dir}"

    tokenizer = AutoTokenizer.from_pretrained(QWEN35)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    torch.manual_seed(1234)
    model, load_warnings = capture_model_load(QWEN35, torch.float16)
    set_pad_token(model, tokenizer)
    disable_decoder_cache(model)
    model.to("cuda").eval().half()
    return model, tokenizer, torch.device("cuda"), {"hub_random_head": load_warnings}


def timed_run(model, tokenizer, device, rows, buckets=None):
    _, texts, _ = selected_samples(rows)
    features, lengths, tokenize_sec = tokenize_features(tokenizer, texts, MAX_LENGTH)
    batches = make_batches(features, lengths, BATCH_SIZE, buckets)
    logits, infer_sec, batch_times = run_batches(
        model, tokenizer, features, batches, BATCH_SIZE, buckets, device, len(texts)
    )
    return logits, tokenize_sec + infer_sec, {
        "tokenize_sec": tokenize_sec,
        "infer_sec": infer_sec,
        "tokenize_plus_infer_sec": tokenize_sec + infer_sec,
        "batch_count": len(batch_times),
        "batch_times_first8": batch_times[:8],
        "batch_times_slowest5": sorted(batch_times)[-5:],
        "bucket_counts": {str(b): sum(1 for batch in batches if batch["bucket"] == b) for b in BUCKETS},
        "lengths": {
            "mean": sum(lengths) / max(1, len(lengths)),
            "max": max(lengths) if lengths else 0,
            "over_256": sum(1 for length in lengths if length > 256),
        },
    }


def warm_exact_buckets(model, tokenizer, features, device):
    import torch

    times = {}
    with torch.no_grad():
        for bucket in BUCKETS:
            batch = {"idx": [0], "bucket": bucket}
            enc, _ = pad_batch(tokenizer, features, batch, BATCH_SIZE, BUCKETS, device)
            t0 = time.perf_counter()
            with torch.amp.autocast(device_type="cuda", dtype=torch.float16):
                model(**enc)
            torch.cuda.synchronize()
            times[str(bucket)] = time.perf_counter() - t0
    return times


def audit_cache_dirs():
    pattern = re.compile(r"assert_size_stride\(arg0_1,\s*\((\d+),\s*(\d+)\)")
    any_arg_pattern = re.compile(r"assert_size_stride\((arg\d+_1),\s*\(([^)]*)\)")
    hits = []
    symbolic_hits = []
    literal_hits = []
    for root in (Path(INDUCTOR_CACHE), Path(TRITON_CACHE)):
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            for match in pattern.finditer(text):
                hits.append({"shape": [int(match.group(1)), int(match.group(2))], "path": str(path)})
            for match in any_arg_pattern.finditer(text):
                shape = match.group(2).replace(" ", "")
                if shape.startswith(f"{BATCH_SIZE},s"):
                    symbolic_hits.append({"arg": match.group(1), "shape": shape, "path": str(path)})
            for bucket in BUCKETS:
                # Inductor can store a cudagraph with a symbolic input guard
                # `(64, s0)` while the generated benchmark/sample still carries
                # the concrete bucket shape. Keep this as explicit evidence so
                # we do not misclassify a valid dynamic cache as missing.
                if f"({BATCH_SIZE}, {bucket})" in text or f"({BATCH_SIZE},{bucket})" in text:
                    literal_hits.append({"shape": [BATCH_SIZE, bucket], "path": str(path)})
    counts = {}
    for hit in hits:
        key = tuple(hit["shape"])
        counts[str(key)] = counts.get(str(key), 0) + 1
    expected = [[BATCH_SIZE, bucket] for bucket in BUCKETS]
    literal_shapes = {tuple(hit["shape"]) for hit in literal_hits}
    static_shapes = {tuple(hit["shape"]) for hit in hits}
    return {
        "expected_input_shapes": expected,
        "guard_counts": counts,
        "hits_first20": hits[:20],
        "symbolic_input_hits_first20": symbolic_hits[:20],
        "literal_bucket_hits_first20": literal_hits[:20],
        "has_all_expected": all(
            tuple(shape) in static_shapes or tuple(shape) in literal_shapes for shape in expected
        ),
        "has_static_or_symbolic_expected": all(
            tuple(shape) in static_shapes
            or (tuple(shape) in literal_shapes and symbolic_hits)
            for shape in expected
        ),
        "has_batch128_shape": any(shape[0] == 128 for shape in (hit["shape"] for hit in hits)),
    }


def save_caches(tag):
    info = {}
    try:
        import torch

        result = torch.compiler.save_cache_artifacts()
        if result is not None:
            out = Path("experiments/artifacts") / f"{tag}_megacache.bin"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(result[0])
            info["megacache_path"] = str(out)
            info["megacache_bytes"] = len(result[0])
    except Exception as exc:  # noqa: BLE001
        info["megacache_error"] = repr(exc)
    try:
        out = Path("experiments/artifacts") / f"{tag}_cachedirs.tar.gz"
        with tarfile.open(out, "w:gz") as tf:
            for d in (INDUCTOR_CACHE, TRITON_CACHE):
                if Path(d).exists():
                    tf.add(d, arcname=Path(d).name)
        info["cachedirs_path"] = str(out)
        info["cachedirs_tar_bytes"] = out.stat().st_size
    except Exception as exc:  # noqa: BLE001
        info["cachedirs_error"] = repr(exc)
    info["cache_audit"] = audit_cache_dirs()
    return info


def apply_fp16(model):
    rule = make_fp16_rule(state_in_fp32=False)
    rebound = rebind_rule(model, rule)
    return rule, rebound


def measure_stage(args):
    if sys.executable != VENV_PY:
        ensure_venv()
        os.execv(VENV_PY, [VENV_PY, "-u", str(Path(__file__).resolve()), *sys.argv[1:]])
    os.environ["TORCHINDUCTOR_CACHE_DIR"] = INDUCTOR_CACHE
    os.environ["TRITON_CACHE_DIR"] = TRITON_CACHE
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    import gc
    import shutil

    import torch

    torch._dynamo.config.cache_size_limit = 64
    if args.clear_cache:
        shutil.rmtree(INDUCTOR_CACHE, ignore_errors=True)
        shutil.rmtree(TRITON_CACHE, ignore_errors=True)

    started = time.perf_counter()
    payload = {
        "stage": "measure",
        "experiment_id": EXPERIMENT_ID,
        "created_utc": utc_now(),
        "stack": stack_info(),
        "batch_size": BATCH_SIZE,
        "buckets": BUCKETS,
        "max_length": MAX_LENGTH,
    }
    step = "start"
    try:
        step = "denominator"
        denom = model_timing(QWEN3, 416, 64, "sorted", args.timing_rows)
        payload["qwen3_denominator"] = {k: denom[k] for k in ("tokenize_plus_infer_sec", "infer_sec")}
        denom_sec = denom["tokenize_plus_infer_sec"]
        gc.collect()
        torch.cuda.empty_cache()
        checkpoint(payload, "measure")

        step = "load_stock"
        model, tokenizer, device, weights_note = load_model()
        payload["weights"] = str(weights_note)
        stock_ref, _, stock_ref_meta = timed_run(model, tokenizer, device, args.correctness_rows, None)
        payload["stock_correctness_batch_meta"] = stock_ref_meta

        step = "fp16_correctness"
        rule, rebound = apply_fp16(model)
        fp16_ref, _, _ = timed_run(model, tokenizer, device, args.correctness_rows, None)
        diff = (fp16_ref.float() - stock_ref.float()).abs()
        agree = (fp16_ref.argmax(1) == stock_ref.argmax(1)).float().mean().item()
        payload["fp16_correctness"] = {
            "rebound_layers": rebound,
            "kernel_calls": rule.calls["n"],
            "argmax_agreement": agree,
            "max_abs_diff": float(diff.max()),
            "mean_abs_diff": float(diff.mean()),
        }
        if rule.calls["n"] == 0 or agree < args.min_agreement:
            payload["verdict"] = "RED_fp16_correctness"
            payload["runtime_sec"] = time.perf_counter() - started
            checkpoint(payload, "measure")
            return 41
        checkpoint(payload, "measure")

        step = "compile"
        compiled = torch.compile(model, mode="reduce-overhead")
        _, texts, _ = selected_samples(args.timing_rows)
        features, lengths, tokenize_sec = tokenize_features(tokenizer, texts, MAX_LENGTH)
        batches = make_batches(features, lengths, BATCH_SIZE, BUCKETS)
        t0 = time.perf_counter()
        payload["warm_shape_times"] = warm_exact_buckets(compiled, tokenizer, features, device)
        payload["compile_wall_sec"] = time.perf_counter() - t0
        payload["cache_artifacts_after_compile"] = save_caches(EXPERIMENT_ID)
        cache_paths = [
            Path(info)
            for info in (
                payload["cache_artifacts_after_compile"].get("megacache_path"),
                payload["cache_artifacts_after_compile"].get("cachedirs_path"),
            )
            if info
        ]
        checkpoint(payload, "measure", *cache_paths)

        step = "timing"
        logits, infer_sec, batch_times = run_batches(
            compiled, tokenizer, features, batches, BATCH_SIZE, BUCKETS, device, len(texts)
        )
        total = tokenize_sec + infer_sec
        payload["compiled_timing"] = {
            "tokenize_sec": tokenize_sec,
            "infer_sec": infer_sec,
            "tokenize_plus_infer_sec": total,
            "batch_count": len(batch_times),
            "batch_times_first8": batch_times[:8],
            "batch_times_slowest5": sorted(batch_times)[-5:],
            "bucket_counts": {str(b): sum(1 for batch in batches if batch["bucket"] == b) for b in BUCKETS},
            "lengths": {
                "mean": sum(lengths) / max(1, len(lengths)),
                "max": max(lengths) if lengths else 0,
                "over_256": sum(1 for length in lengths if length > 256),
            },
            "prediction_distribution": {
                str(k): int(v) for k, v in zip(*torch.unique(logits.argmax(1), return_counts=True))
            },
        }
        payload["ratio_vs_qwen3"] = total / denom_sec
        payload["projected_infer_sec"] = payload["ratio_vs_qwen3"] * INFER_ANCHOR_SEC
        payload["runtime_sec"] = time.perf_counter() - started
        payload["verdict"] = (
            "GREEN_measure"
            if payload["cache_artifacts_after_compile"]["cache_audit"]["has_all_expected"]
            else "RED_cache_shape"
        )
        checkpoint(payload, "measure", *cache_paths)
        return 0 if payload["verdict"] == "GREEN_measure" else 42
    except Exception:  # noqa: BLE001
        payload["verdict"] = "RED_measure_error"
        payload["failed_step"] = step
        payload["traceback"] = traceback.format_exc()[-4000:]
        payload["runtime_sec"] = time.perf_counter() - started
        checkpoint(payload, "measure")
        return 43


def warm_stage(args):
    if sys.executable != VENV_PY:
        ensure_venv()
        os.execv(VENV_PY, [VENV_PY, "-u", str(Path(__file__).resolve()), *sys.argv[1:]])
    os.environ["TORCHINDUCTOR_CACHE_DIR"] = INDUCTOR_CACHE
    os.environ["TRITON_CACHE_DIR"] = TRITON_CACHE
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    import torch

    torch._dynamo.config.cache_size_limit = 64
    started = time.perf_counter()
    payload = {
        "stage": "warm",
        "experiment_id": EXPERIMENT_ID,
        "created_utc": utc_now(),
        "stack": stack_info(),
        "batch_size": BATCH_SIZE,
        "buckets": BUCKETS,
        "max_length": MAX_LENGTH,
    }
    step = "start"
    try:
        step = "load_cache_artifact"
        mega = Path("experiments/artifacts") / f"{EXPERIMENT_ID}_megacache.bin"
        if mega.exists():
            try:
                torch.compiler.load_cache_artifacts(mega.read_bytes())
                payload["megacache_loaded"] = True
            except Exception as exc:  # noqa: BLE001
                payload["megacache_loaded"] = repr(exc)
        step = "load_model"
        model, tokenizer, device, weights_note = load_model()
        payload["weights"] = str(weights_note)
        rule, rebound = apply_fp16(model)
        payload["fp16_rebound_layers"] = rebound
        compiled = torch.compile(model, mode="reduce-overhead")

        step = "warm_shapes"
        _, texts, _ = selected_samples(args.timing_rows)
        features, lengths, tokenize_sec = tokenize_features(tokenizer, texts, MAX_LENGTH)
        batches = make_batches(features, lengths, BATCH_SIZE, BUCKETS)
        t0 = time.perf_counter()
        payload["warm_shape_times"] = warm_exact_buckets(compiled, tokenizer, features, device)
        payload["warm_compile_wall_sec"] = time.perf_counter() - t0
        payload["startup_total_sec"] = time.perf_counter() - started
        payload["cache_audit_after_warm"] = audit_cache_dirs()

        step = "timing"
        _, infer_sec, batch_times = run_batches(
            compiled, tokenizer, features, batches, BATCH_SIZE, BUCKETS, device, len(texts)
        )
        total = tokenize_sec + infer_sec
        payload["compiled_timing"] = {
            "tokenize_sec": tokenize_sec,
            "infer_sec": infer_sec,
            "tokenize_plus_infer_sec": total,
            "batch_count": len(batch_times),
            "batch_times_first8": batch_times[:8],
            "batch_times_slowest5": sorted(batch_times)[-5:],
            "bucket_counts": {str(b): sum(1 for batch in batches if batch["bucket"] == b) for b in BUCKETS},
            "lengths": {
                "mean": sum(lengths) / max(1, len(lengths)),
                "max": max(lengths) if lengths else 0,
                "over_256": sum(1 for length in lengths if length > 256),
            },
        }
        payload["kernel_calls"] = rule.calls["n"]
        payload["runtime_sec"] = time.perf_counter() - started
        payload["verdict"] = (
            "GREEN_warm"
            if payload["cache_audit_after_warm"]["has_all_expected"] and rule.calls["n"] > 0
            else "RED_warm_cache_or_patch"
        )
        checkpoint(payload, "warm")
        return 0 if payload["verdict"] == "GREEN_warm" else 44
    except Exception:  # noqa: BLE001
        payload["verdict"] = "RED_warm_error"
        payload["failed_step"] = step
        payload["traceback"] = traceback.format_exc()[-4000:]
        payload["runtime_sec"] = time.perf_counter() - started
        checkpoint(payload, "warm")
        return 43


def controller_stage(args):
    started = time.perf_counter()
    os.environ["TORCHINDUCTOR_CACHE_DIR"] = INDUCTOR_CACHE
    os.environ["TRITON_CACHE_DIR"] = TRITON_CACHE
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    ensure_venv()
    logs = []

    common = [
        "--experiment-id",
        EXPERIMENT_ID,
        "--correctness-rows",
        str(args.correctness_rows),
        "--timing-rows",
        str(args.timing_rows),
        "--min-agreement",
        str(args.min_agreement),
    ]
    measure = vrun(
        ["colab/m8_b64_cache_guard_probe.py", "measure", "--clear-cache", *common],
        check=False,
        timeout=args.stage_timeout,
    )
    logs.append(measure)
    warm = vrun(
        ["colab/m8_b64_cache_guard_probe.py", "warm", *common],
        check=False,
        timeout=args.stage_timeout,
    )
    logs.append(warm)

    measure_art = json.loads(art_path("measure").read_text(encoding="utf-8")) if art_path("measure").exists() else {}
    warm_art = json.loads(art_path("warm").read_text(encoding="utf-8")) if art_path("warm").exists() else {}
    projected_total = None
    if isinstance(measure_art.get("projected_infer_sec"), (int, float)) and isinstance(
        warm_art.get("warm_compile_wall_sec"), (int, float)
    ):
        projected_total = measure_art["projected_infer_sec"] + warm_art["warm_compile_wall_sec"]
    payload = {
        "what": "exact b64/{256,400} compile cache guard + fp16 DeltaNet timing",
        "experiment_id": EXPERIMENT_ID,
        "created_utc": utc_now(),
        "measure_verdict": measure_art.get("verdict"),
        "warm_verdict": warm_art.get("verdict"),
        "projected_infer_sec": measure_art.get("projected_infer_sec"),
        "warm_compile_wall_sec": warm_art.get("warm_compile_wall_sec"),
        "warm_startup_total_sec": warm_art.get("startup_total_sec"),
        "projected_total_sec": projected_total,
        "measure_cache_audit": (measure_art.get("cache_artifacts_after_compile") or {}).get("cache_audit"),
        "warm_cache_audit": warm_art.get("cache_audit_after_warm"),
        "compiled_timing": measure_art.get("compiled_timing"),
        "warm_compiled_timing": warm_art.get("compiled_timing"),
        "stage_rcs": [{k: item.get(k) for k in ("returncode", "duration_sec")} for item in logs],
        "notes": "valid only if cache audits show (64,256) and (64,400), not batch128 contamination",
        "runtime_sec": time.perf_counter() - started,
    }
    measure_ok = str(payload.get("measure_verdict")) == "GREEN_measure"
    warm_ok = str(payload.get("warm_verdict")) == "GREEN_warm"
    if not measure_ok:
        payload["verdict"] = "RED_measure"
    elif not warm_ok:
        payload["verdict"] = "RED_warm"
    elif projected_total is not None and projected_total <= 600:
        payload["verdict"] = "GREEN_package_candidate"
    elif projected_total is not None:
        payload["verdict"] = "YELLOW_cache_valid_timing_borderline"
    else:
        payload["verdict"] = "RED_no_projection"
    checkpoint(payload)
    append_result_row(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000], flush=True)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("controller", "measure", "warm"):
        p = sub.add_parser(name)
        p.add_argument("--experiment-id", default=EXPERIMENT_ID)
        p.add_argument("--correctness-rows", type=int, default=256)
        p.add_argument("--timing-rows", type=int, default=4096)
        p.add_argument("--stage-timeout", type=int, default=9000)
        p.add_argument("--min-agreement", type=float, default=0.995)
        if name == "measure":
            p.add_argument("--clear-cache", action="store_true")
    args = parser.parse_args()
    configure(args.experiment_id)
    if args.command == "controller":
        controller_stage(args)
    elif args.command == "measure":
        raise SystemExit(measure_stage(args))
    elif args.command == "warm":
        raise SystemExit(warm_stage(args))


if __name__ == "__main__":
    main()
