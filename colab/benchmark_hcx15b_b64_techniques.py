"""Fair batch-64 T4 timing and serving-technique probes for HCX-1.5B.

The default path clean-extracts the candidate and current champion and measures
the same deterministic 30k rows without editing either package's ``hf_meta``.
The candidate-only attention-mask A/B and optional torch.compile worker are
deliberately outside the two full-run timings.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
import random
import signal
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from colab.benchmark_hcx15b_t4 import (  # noqa: E402
    make_proxy_data,
    selected_rows,
    validate_submission,
)


REPLICA_PYTHON = Path("/content/venv311/bin/python")
REPLICA_ENV_FLAG = "AADP_HCX_B64_REPLICA_ACTIVE"
FIXED_BATCH_SIZE = 64
DEFAULT_OUTPUT = (
    "/content/drive/MyDrive/AADP_exchange_d/artifacts/"
    "20260713_hcx15b_b64_techniques.json"
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path, payload):
    """Checkpoint safely enough for Drive FUSE and leave readable JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        finally:
            path.write_text(text, encoding="utf-8")
    print(f"CHECKPOINT {path}", flush=True)


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
                "import sys,torch,transformers,safetensors,sklearn,joblib,sentencepiece;"
                "assert sys.version_info[:2]==(3,11);"
                "assert torch.__version__.split('+')[0]=='2.7.1';"
                "assert transformers.__version__=='4.46.3';"
                "assert safetensors.__version__=='0.8.0';"
                "assert sklearn.__version__=='1.8.0';"
                "assert joblib.__version__=='1.5.3'"
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
    """Re-exec this file under the evaluation server's exact core stack."""
    if os.environ.get(REPLICA_ENV_FLAG) == "1":
        if not replica_ready():
            raise RuntimeError("replica child has the wrong dependency stack")
        return
    if not REPLICA_PYTHON.is_file():
        if shutil.which("python3.11") is None:
            run_setup(["add-apt-repository", "-y", "ppa:deadsnakes/ppa"], 300)
            run_setup(["apt-get", "update", "-qq"], 600)
            run_setup(
                [
                    "apt-get", "install", "-y", "-q", "python3.11",
                    "python3.11-venv", "python3.11-dev",
                ],
                900,
            )
        run_setup(["python3.11", "-m", "venv", str(REPLICA_PYTHON.parent.parent)], 300)
    if not replica_ready():
        run_setup([str(REPLICA_PYTHON), "-m", "pip", "install", "-q", "--upgrade", "pip"], 600)
        run_setup(
            [
                str(REPLICA_PYTHON), "-m", "pip", "install", "-q",
                "torch==2.7.1", "--index-url", "https://download.pytorch.org/whl/cu128",
            ],
            1800,
        )
        run_setup(
            [
                str(REPLICA_PYTHON), "-m", "pip", "install", "-q",
                "transformers==4.46.3", "safetensors==0.8.0",
                "scikit-learn==1.8.0", "joblib==1.5.3", "sentencepiece",
            ],
            1200,
        )
    if not replica_ready():
        raise RuntimeError("failed to prepare Python 3.11 / torch 2.7.1 replica")
    env = dict(os.environ, **{REPLICA_ENV_FLAG: "1", "PYTHONNOUSERSITE": "1"})
    command = [str(REPLICA_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
    print("REEXEC", " ".join(command), flush=True)
    raise SystemExit(subprocess.run(command, cwd=ROOT, env=env).returncode)


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def import_pack_script(path, name):
    spec = importlib.util.spec_from_file_location(f"{name}_pack_{os.getpid()}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean_extract(zip_path, destination):
    zip_path = Path(zip_path)
    started = time.perf_counter()
    with zipfile.ZipFile(zip_path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise RuntimeError(f"CRC failure in {zip_path}: {bad_member}")
        roots = sorted({Path(name).parts[0] for name in archive.namelist() if Path(name).parts})
        if roots != ["model", "requirements.txt", "script.py"]:
            raise RuntimeError(f"unexpected archive roots in {zip_path}: {roots}")
        archive.extractall(destination)
    meta_path = destination / "model/hf_meta.json"
    meta_bytes = meta_path.read_bytes()
    meta = json.loads(meta_bytes)
    if int(meta.get("batch_size", -1)) != FIXED_BATCH_SIZE:
        raise RuntimeError(
            f"{zip_path} original hf_meta batch_size={meta.get('batch_size')}, expected 64"
        )
    return {
        "zip": str(zip_path),
        "zip_bytes": zip_path.stat().st_size,
        "extract_seconds": time.perf_counter() - started,
        "archive_roots": roots,
        "hf_meta_batch_size": int(meta["batch_size"]),
        "hf_meta_sha256_before": sha256_bytes(meta_bytes),
    }, meta, meta_bytes


def stack_info(torch, transformers):
    return {
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "cuda_runtime": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "gpu_capability": list(torch.cuda.get_device_capability(0)) if torch.cuda.is_available() else None,
    }


def sdp_flag_state(torch):
    cuda = torch.backends.cuda
    result = {}
    for name in (
        "flash_sdp_enabled", "mem_efficient_sdp_enabled", "math_sdp_enabled",
        "cudnn_sdp_enabled",
    ):
        fn = getattr(cuda, name, None)
        if fn is not None:
            try:
                result[name] = bool(fn())
            except Exception as exc:  # pragma: no cover - version-specific
                result[name] = f"ERROR: {exc!r}"
    return result


def attention_info(model, torch):
    config = getattr(model, "config", None)
    class_counts = {}
    for module in model.modules():
        cls = type(module)
        if "Attention" in cls.__name__:
            key = f"{cls.__module__}.{cls.__name__}"
            class_counts[key] = class_counts.get(key, 0) + 1
    return {
        "config_attn_implementation": getattr(config, "_attn_implementation", None),
        "config_attn_implementation_internal": getattr(
            config, "_attn_implementation_internal", None
        ),
        "attention_module_classes": class_counts,
        "sdpa_flags": sdp_flag_state(torch),
    }


def encoded_features(tokenizer, texts, max_length):
    started = time.perf_counter()
    encoded = tokenizer(texts, padding=False, truncation=True, max_length=max_length)
    keys = list(encoded.keys())
    features = [{key: encoded[key][i] for key in keys} for i in range(len(texts))]
    lengths = [len(feature["input_ids"]) for feature in features]
    order = sorted(range(len(features)), key=lengths.__getitem__)
    seconds = time.perf_counter() - started
    return features, lengths, order, {
        "seconds": seconds,
        "rows": len(features),
        "keys": keys,
        "tokens": int(sum(lengths)),
        "mean_tokens": float(sum(lengths) / len(lengths)),
        "min_tokens": int(min(lengths)),
        "max_tokens": int(max(lengths)),
    }


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))])


def padded_batch(tokenizer, features, indices, device, drop_attention_mask=False):
    batch = tokenizer.pad(
        [features[index] for index in indices], padding=True, return_tensors="pt"
    )
    if drop_attention_mask:
        batch.pop("attention_mask", None)
    return {key: value.to(device) for key, value in batch.items()}


def run_sorted_b64(
    model,
    tokenizer,
    features,
    lengths,
    device,
    *,
    drop_attention_mask=False,
    checkpoint_every=0,
    progress=None,
):
    """Run a fixed b64 length-sorted loop and time forward kernels with events."""
    import torch

    order = sorted(range(len(features)), key=lengths.__getitem__)
    outputs = [None] * len(features)
    event_ms = []
    padded_tokens = 0
    checkpoint_io_seconds = 0.0
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    wall_start = time.perf_counter()
    with torch.inference_mode():
        for batch_number, start in enumerate(range(0, len(order), FIXED_BATCH_SIZE), 1):
            indices = order[start : start + FIXED_BATCH_SIZE]
            batch = padded_batch(
                tokenizer, features, indices, device, drop_attention_mask=drop_attention_mask
            )
            padded_tokens += int(batch["input_ids"].numel())
            begin = torch.cuda.Event(enable_timing=True)
            end = torch.cuda.Event(enable_timing=True)
            begin.record()
            logits_gpu = model(**batch).logits
            end.record()
            logits = logits_gpu.float().cpu()
            end.synchronize()
            event_ms.append(float(begin.elapsed_time(end)))
            for row, index in enumerate(indices):
                outputs[index] = logits[row]
            if progress is not None and checkpoint_every and batch_number % checkpoint_every == 0:
                checkpoint_started = time.perf_counter()
                progress(
                    {
                        "completed_batches": batch_number,
                        "total_batches": (len(order) + FIXED_BATCH_SIZE - 1) // FIXED_BATCH_SIZE,
                        "completed_rows": min(start + FIXED_BATCH_SIZE, len(order)),
                        "elapsed_seconds": time.perf_counter() - wall_start,
                    }
                )
                checkpoint_io_seconds += time.perf_counter() - checkpoint_started
            del batch, logits_gpu, logits
    result_logits = torch.stack(outputs, dim=0)
    torch.cuda.synchronize()
    raw_wall = time.perf_counter() - wall_start
    wall = raw_wall - checkpoint_io_seconds
    forward = sum(event_ms) / 1000.0
    timing = {
        "batch_size": FIXED_BATCH_SIZE,
        "drop_attention_mask": bool(drop_attention_mask),
        "rows": len(features),
        "batches": len(event_ms),
        "sorted_b64_loop_wall_seconds": wall,
        "instrumented_raw_loop_wall_seconds": raw_wall,
        "progress_checkpoint_io_seconds_excluded": checkpoint_io_seconds,
        "cuda_event_forward_sum_seconds": forward,
        "non_forward_wall_seconds": wall - forward,
        "rows_per_wall_second": len(features) / wall,
        "padded_tokens": padded_tokens,
        "padding_fraction": 1.0 - (sum(lengths) / padded_tokens),
        "forward_batch_ms_first8": event_ms[:8],
        "forward_batch_ms_p50": percentile(event_ms, 0.50),
        "forward_batch_ms_p95": percentile(event_ms, 0.95),
        "forward_batch_ms_max": max(event_ms),
        "peak_cuda_allocated_bytes": int(torch.cuda.max_memory_allocated()),
        "peak_cuda_reserved_bytes": int(torch.cuda.max_memory_reserved()),
    }
    return result_logits, timing


def compare_logits(left, right):
    import torch

    diff = (left.float() - right.float()).abs()
    agreement = (left.argmax(1) == right.argmax(1)).float().mean()
    return {
        "rows": int(left.shape[0]),
        "argmax_agreement": float(agreement),
        "argmax_parity": bool(torch.equal(left.argmax(1), right.argmax(1))),
        "max_abs_logit_diff": float(diff.max()),
        "mean_abs_logit_diff": float(diff.mean()),
    }


def profile_one_or_two_batches(model, tokenizer, features, lengths, device, drop_mask, count):
    """Small diagnostic only; it is never included in a reported timing stage."""
    import torch

    if count <= 0:
        return {"status": "SKIPPED"}
    try:
        from torch.profiler import ProfilerActivity, profile

        order = sorted(range(len(features)), key=lengths.__getitem__)
        indices = order[: min(len(order), FIXED_BATCH_SIZE * count)]
        chunks = [indices[start : start + FIXED_BATCH_SIZE] for start in range(0, len(indices), FIXED_BATCH_SIZE)]
        with profile(
            activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
            record_shapes=True,
            profile_memory=False,
        ) as prof:
            with torch.inference_mode():
                for chunk in chunks:
                    batch = padded_batch(
                        tokenizer, features, chunk, device, drop_attention_mask=drop_mask
                    )
                    model(**batch).logits
            torch.cuda.synchronize()
        rows = []
        for event in prof.key_averages():
            device_us = float(
                getattr(event, "self_device_time_total", getattr(event, "self_cuda_time_total", 0.0))
            )
            rows.append(
                {
                    "op": event.key,
                    "count": int(event.count),
                    "self_cpu_ms": float(event.self_cpu_time_total) / 1000.0,
                    "self_cuda_ms": device_us / 1000.0,
                }
            )
        rows.sort(key=lambda item: item["self_cuda_ms"], reverse=True)
        op_names = [item["op"] for item in rows]
        backend_ops = [
            name for name in op_names
            if "scaled_dot_product" in name or "flash_attention" in name or "efficient_attention" in name
        ]
        return {
            "status": "OK",
            "drop_attention_mask": drop_mask,
            "profiled_batches": len(chunks),
            "backend_ops": backend_ops,
            "top_cuda_ops": rows[:20],
        }
    except Exception as exc:  # pragma: no cover - profiler varies by runtime
        return {"status": "ERROR", "error": repr(exc), "traceback": traceback.format_exc()[-3000:]}


def postprocess_and_validate(pack, meta, logits, samples, data_dir, output_path, device):
    import torch

    started = time.perf_counter()
    unsupported = [name for name in ("encoders", "cascade", "weak4_specialist") if meta.get(name)]
    if unsupported:
        raise RuntimeError(f"staged benchmark does not support package features: {unsupported}")
    if pack.load_sparse_ensemble(str(output_path.parents[1] / "model"), meta["classes"]) is not None:
        raise RuntimeError("staged benchmark does not support a sparse ensemble")
    bias = torch.tensor(meta.get("class_bias", [0.0] * len(meta["classes"])), device=device)
    bias = bias + pack.batch_prior_calibration_bias(
        logits, meta.get("prior_calibration"), meta["classes"], device
    )
    final = torch.empty_like(logits, dtype=torch.float32, device="cpu")
    rules = meta.get("rule_boosts") or []
    for start in range(0, len(samples), FIXED_BATCH_SIZE):
        chunk = logits[start : start + FIXED_BATCH_SIZE].to(device) + bias
        chunk = pack.apply_rule_boosts_to_logits(
            chunk, samples[start : start + FIXED_BATCH_SIZE], rules, meta["classes"]
        )
        final[start : start + len(chunk)] = chunk.float().cpu()
    predictions = [meta["classes"][index] for index in final.argmax(1).tolist()]
    ids = [str(sample.get("id", "")) for sample in samples]
    fields, rows = pack.load_sample_submission(data_dir / "sample_submission.csv", ids)
    pred_map = dict(zip(ids, predictions))
    for row in rows:
        row["action"] = pred_map[row["id"]]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pack.save_submission(output_path, fields, rows)
    count = validate_submission(output_path, samples, meta["classes"])
    return {
        "seconds": time.perf_counter() - started,
        "rows": count,
        "output_bytes": output_path.stat().st_size,
        "prediction_sha256": hashlib.sha256("\n".join(predictions).encode()).hexdigest(),
    }


def checkpoint_model(report, output, name, stage, value=None):
    entry = report["models"][name]
    entry["active_stage"] = stage
    if value is not None:
        entry.setdefault("stages", {})[stage] = value
    report["updated_at"] = utc_now()
    atomic_write_json(output, report)


def benchmark_package(name, extract_dir, meta, meta_bytes, samples, report, args, keep_context=False):
    import torch
    from transformers import AutoTokenizer

    entry = report["models"][name]
    pack_started = time.perf_counter()
    import_start = time.perf_counter()
    pack = import_pack_script(extract_dir / "script.py", name)
    entry["script_import_seconds"] = time.perf_counter() - import_start
    hf_dir = extract_dir / "model/hf_model"

    checkpoint_model(report, args.output, name, "tokenizer_load")
    started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(hf_dir, local_files_only=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    entry["stages"]["tokenizer_load"] = {"seconds": time.perf_counter() - started}

    checkpoint_model(report, args.output, name, "serialization")
    started = time.perf_counter()
    serializer = meta.get("serializer_name", "current_v1")
    texts = [pack.serialize_transformer_sample(row, serializer, tokenizer=tokenizer) for row in samples]
    entry["stages"]["serialization"] = {
        "seconds": time.perf_counter() - started,
        "rows": len(texts),
        "characters": sum(map(len, texts)),
    }

    checkpoint_model(report, args.output, name, "model_restore_load")
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    model = pack.load_hf_model(str(hf_dir), torch.device("cuda"))
    model.half().eval()
    torch.cuda.synchronize()
    entry["stages"]["model_restore_load"] = {
        "seconds": time.perf_counter() - started,
        "peak_cuda_allocated_bytes": int(torch.cuda.max_memory_allocated()),
        "peak_cuda_reserved_bytes": int(torch.cuda.max_memory_reserved()),
    }
    entry["attention"] = attention_info(model, torch)

    checkpoint_model(report, args.output, name, "tokenize")
    features, lengths, _, tokenize_timing = encoded_features(
        tokenizer, texts, int(meta.get("max_length", 384))
    )
    entry["stages"]["tokenize"] = tokenize_timing

    checkpoint_model(report, args.output, name, "sorted_b64_inference")

    def progress(value):
        entry["progress"] = value
        report["updated_at"] = utc_now()
        atomic_write_json(args.output, report)

    logits, inference_timing = run_sorted_b64(
        model,
        tokenizer,
        features,
        lengths,
        torch.device("cuda"),
        checkpoint_every=args.checkpoint_every_batches,
        progress=progress,
    )
    entry.pop("progress", None)
    entry["stages"]["sorted_b64_inference"] = inference_timing

    checkpoint_model(report, args.output, name, "postprocess_csv_validation")
    post = postprocess_and_validate(
        pack,
        meta,
        logits,
        samples,
        extract_dir / "data",
        extract_dir / "output/submission.csv",
        torch.device("cuda"),
    )
    entry["stages"]["postprocess_csv_validation"] = post
    after = (extract_dir / "model/hf_meta.json").read_bytes()
    entry["hf_meta_sha256_after"] = sha256_bytes(after)
    entry["hf_meta_unchanged"] = after == meta_bytes
    if not entry["hf_meta_unchanged"]:
        raise RuntimeError(f"{name} hf_meta changed during benchmark")
    entry["total_staged_wall_seconds"] = time.perf_counter() - pack_started
    entry["active_stage"] = "complete"
    checkpoint_model(report, args.output, name, "complete")

    context = None
    if keep_context:
        context = {
            "pack": pack,
            "model": model,
            "tokenizer": tokenizer,
            "features": features,
            "lengths": lengths,
        }
    else:
        del model, tokenizer, features, lengths, texts, logits
        gc.collect()
        torch.cuda.empty_cache()
    return context


def candidate_mask_ab(context, rows, profile_batches):
    import torch

    count = min(rows, len(context["features"]))
    features = context["features"][:count]
    lengths = context["lengths"][:count]
    tokenizer = context["tokenizer"]
    model = context["model"]
    config = getattr(model, "config", None)
    attn_implementation = getattr(config, "_attn_implementation", None)
    pad_token_id = tokenizer.pad_token_id
    config_pad_token_id = getattr(config, "pad_token_id", None)
    use_cache = getattr(config, "use_cache", None)
    valid_pad_id_occurrences = sum(
        token_id == pad_token_id
        for feature in features
        for token_id in feature["input_ids"]
    )
    preconditions = {
        "padding_side": tokenizer.padding_side,
        "pad_token_id": pad_token_id,
        "config_pad_token_id": config_pad_token_id,
        "valid_pad_id_occurrences": int(valid_pad_id_occurrences),
        "model_eval": not model.training,
        "use_cache": use_cache,
        "attn_implementation": attn_implementation,
    }
    violations = []
    if tokenizer.padding_side != "right":
        violations.append(f"padding_side={tokenizer.padding_side!r}, expected 'right'")
    if pad_token_id is None:
        violations.append("tokenizer has no pad_token_id")
    if config_pad_token_id != pad_token_id:
        violations.append(
            f"model config pad_token_id={config_pad_token_id!r} does not match "
            f"tokenizer pad_token_id={pad_token_id!r}"
        )
    if valid_pad_id_occurrences:
        violations.append(f"pad token appears {valid_pad_id_occurrences} times in valid inputs")
    if model.training:
        violations.append("model is not in eval mode")
    if use_cache is not False:
        violations.append(f"model config use_cache={use_cache!r}, expected False")
    if attn_implementation != "sdpa":
        violations.append(f"attention implementation is {attn_implementation!r}, expected 'sdpa'")
    preconditions["eligible"] = not violations
    preconditions["violations"] = violations
    if violations:
        raise RuntimeError("unsafe attention-mask drop probe: " + "; ".join(violations))
    common = dict(
        model=model,
        tokenizer=tokenizer,
        features=features,
        lengths=lengths,
        device=torch.device("cuda"),
    )

    # Give each path one representative, unmeasured batch so backend setup does
    # not systematically make the second variant look better or worse.
    order = sorted(range(len(features)), key=lengths.__getitem__)
    warm_start = max(0, (len(order) - FIXED_BATCH_SIZE) // 2)
    warm_indices = order[warm_start : warm_start + FIXED_BATCH_SIZE]
    warmup_seconds = {}
    with torch.inference_mode():
        for label, drop_mask in (("keep", False), ("drop", True)):
            started = time.perf_counter()
            warm_batch = padded_batch(
                tokenizer, features, warm_indices, torch.device("cuda"),
                drop_attention_mask=drop_mask,
            )
            model(**warm_batch).logits
            torch.cuda.synchronize()
            warmup_seconds[label] = time.perf_counter() - started
            del warm_batch
    keep_logits, keep = run_sorted_b64(**common, drop_attention_mask=False)
    drop_logits, drop = run_sorted_b64(**common, drop_attention_mask=True)
    per_variant_profiles = max(1, profile_batches // 2) if profile_batches else 0
    profiles = {
        "keep": profile_one_or_two_batches(
            **common, drop_mask=False, count=per_variant_profiles
        ),
        "drop": profile_one_or_two_batches(
            **common, drop_mask=True, count=per_variant_profiles
        ),
    }
    comparison = compare_logits(keep_logits, drop_logits)
    result = {
        "rows": count,
        "batch_size": FIXED_BATCH_SIZE,
        "same_model_and_encoded_features": True,
        "preconditions": preconditions,
        "unmeasured_warmup_seconds": warmup_seconds,
        "measurement_order": ["keep_attention_mask", "drop_attention_mask"],
        "keep_attention_mask": keep,
        "drop_attention_mask": drop,
        "drop_over_keep_wall_ratio": (
            drop["sorted_b64_loop_wall_seconds"] / keep["sorted_b64_loop_wall_seconds"]
        ),
        "comparison": comparison,
        "eligible_for_full_run": bool(preconditions["eligible"] and comparison["argmax_parity"]),
        "profiles": profiles,
    }
    del keep_logits, drop_logits
    return result


def directory_stats(path):
    path = Path(path)
    files = [item for item in path.rglob("*") if item.is_file()] if path.exists() else []
    return {"path": str(path), "files": len(files), "bytes": sum(item.stat().st_size for item in files)}


def compile_worker(args):
    """Fresh-process optional probe; never contaminates the full eager runs."""
    cache_root = Path(args.compile_cache_dir) if args.compile_cache_dir else Path(
        tempfile.mkdtemp(prefix="aadp_hcx_compile_", dir="/content" if Path("/content").is_dir() else None)
    )
    cache_root.mkdir(parents=True, exist_ok=True)
    inductor = cache_root / "inductor"
    triton = cache_root / "triton"
    os.environ["TORCHINDUCTOR_CACHE_DIR"] = str(inductor)
    os.environ["TRITON_CACHE_DIR"] = str(triton)
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    import torch
    import transformers
    from transformers import AutoTokenizer

    payload = {
        "status": "RUNNING",
        "started_at": utc_now(),
        "rows": args.compile_rows,
        "batch_size": FIXED_BATCH_SIZE,
        "compile_kwargs": {"dynamic": True, "mode": "reduce-overhead"},
        "stack": stack_info(torch, transformers),
        "cache": {"initial": directory_stats(cache_root)},
    }
    atomic_write_json(args.worker_output, payload)
    try:
        samples = selected_rows(args.data, args.compile_rows, args.seed)
        with tempfile.TemporaryDirectory(prefix="hcx_compile_worker_") as td:
            extract_dir = Path(td)
            package, meta, meta_bytes = clean_extract(args.zip, extract_dir)
            payload["package"] = package
            pack = import_pack_script(extract_dir / "script.py", "compile_candidate")
            tokenizer = AutoTokenizer.from_pretrained(extract_dir / "model/hf_model", local_files_only=True)
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token
            texts = [
                pack.serialize_transformer_sample(
                    row, meta.get("serializer_name", "current_v1"), tokenizer=tokenizer
                )
                for row in samples
            ]
            model = pack.load_hf_model(str(extract_dir / "model/hf_model"), torch.device("cuda"))
            model.half().eval()
            features, lengths, _, token_timing = encoded_features(
                tokenizer, texts, int(meta.get("max_length", 384))
            )
            payload["tokenize"] = token_timing
            eager_logits, eager_timing = run_sorted_b64(
                model, tokenizer, features, lengths, torch.device("cuda")
            )
            payload["eager"] = eager_timing
            payload["cache"]["before_compile"] = directory_stats(cache_root)
            atomic_write_json(args.worker_output, payload)

            torch._dynamo.config.cache_size_limit = 64
            started = time.perf_counter()
            compiled = torch.compile(model, dynamic=True, mode="reduce-overhead")
            payload["compile_wrap_seconds"] = time.perf_counter() - started
            cold_logits, cold_timing = run_sorted_b64(
                compiled, tokenizer, features, lengths, torch.device("cuda")
            )
            payload["cold_pass"] = cold_timing
            payload["cold_parity_vs_eager"] = compare_logits(eager_logits, cold_logits)
            payload["cache"]["after_cold"] = directory_stats(cache_root)
            atomic_write_json(args.worker_output, payload)

            warm_logits, warm_timing = run_sorted_b64(
                compiled, tokenizer, features, lengths, torch.device("cuda")
            )
            payload["second_pass_warm"] = warm_timing
            payload["warm_parity_vs_eager"] = compare_logits(eager_logits, warm_logits)
            payload["cache"]["after_warm"] = directory_stats(cache_root)
            payload["hf_meta_unchanged"] = (
                (extract_dir / "model/hf_meta.json").read_bytes() == meta_bytes
            )
            payload["status"] = "COMPLETE"
            payload["completed_at"] = utc_now()
            atomic_write_json(args.worker_output, payload)
        return 0
    except Exception as exc:
        payload["status"] = "ERROR"
        payload["error"] = repr(exc)
        payload["traceback"] = traceback.format_exc()[-6000:]
        payload["completed_at"] = utc_now()
        atomic_write_json(args.worker_output, payload)
        return 42


def launch_compile_worker(args):
    sidecar = Path(args.output).with_name(Path(args.output).stem + ".compile_worker.json")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker", "compile",
        "--zip", args.zip,
        "--reference-zip", args.reference_zip,
        "--data", args.data,
        "--seed", str(args.seed),
        "--compile-rows", str(args.compile_rows),
        "--worker-output", str(sidecar),
    ]
    if args.compile_cache_dir:
        command.extend(["--compile-cache-dir", args.compile_cache_dir])
    started = time.perf_counter()
    proc = subprocess.Popen(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        stdout, _ = proc.communicate(timeout=args.compile_timeout)
        partial = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        return {
            "controller_status": "COMPLETE" if proc.returncode == 0 else "WORKER_ERROR",
            "returncode": proc.returncode,
            "wall_seconds": time.perf_counter() - started,
            "timeout_seconds": args.compile_timeout,
            "worker_output": str(sidecar),
            "stdout_tail": stdout[-8000:],
            "result": partial,
        }
    except subprocess.TimeoutExpired:
        # torch.compile may own Inductor/Triton children. Kill the whole fresh
        # process group so a timed-out probe cannot keep consuming the T4.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, _ = proc.communicate()
        partial = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        cache_path = partial.get("cache", {}).get("initial", {}).get("path")
        return {
            "controller_status": "TIMEOUT",
            "wall_seconds": time.perf_counter() - started,
            "timeout_seconds": args.compile_timeout,
            "worker_output": str(sidecar),
            "stdout_tail": stdout[-8000:],
            "partial_result": partial,
            "cache_at_timeout": directory_stats(cache_path) if cache_path else None,
        }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, help="HCX-1.5B candidate ZIP")
    parser.add_argument("--reference-zip", required=True, help="current champion ZIP")
    parser.add_argument("--data", default="open/data/train.jsonl")
    parser.add_argument("--samples", type=int, default=30000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--mask-ab-rows", type=int, default=4096)
    parser.add_argument("--profile-batches", type=int, default=2)
    parser.add_argument("--checkpoint-every-batches", type=int, default=32)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--compile-probe", action="store_true")
    parser.add_argument("--compile-rows", type=int, default=1024)
    parser.add_argument("--compile-timeout", type=int, default=1800)
    parser.add_argument("--compile-cache-dir")
    parser.add_argument("--worker", choices=["compile"], help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.seed != 42:
        parser.error("this comparison is fixed to seed 42")
    if args.samples <= 0:
        parser.error("--samples must be positive")
    if not 512 <= args.compile_rows <= 2048:
        parser.error("--compile-rows must be between 512 and 2048")
    if args.worker and not args.worker_output:
        parser.error("--worker-output is required for a worker")
    return args


def controller(args):
    import torch
    import transformers

    if not torch.cuda.is_available() or "T4" not in torch.cuda.get_device_name(0):
        raise RuntimeError(f"Tesla T4 required, got {torch.cuda.get_device_name(0) if torch.cuda.is_available() else None!r}")
    cuda_started = time.perf_counter()
    torch.cuda.init()
    torch.cuda.synchronize()
    cuda_init_seconds = time.perf_counter() - cuda_started
    report = {
        "schema_version": 1,
        "what": "HCX-1.5B vs champion fixed-b64 staged timing and serving probes",
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "status": "RUNNING",
        "seed": args.seed,
        "samples": args.samples,
        "fixed_batch_size": FIXED_BATCH_SIZE,
        "stack": stack_info(torch, transformers),
        "cuda_init_seconds": cuda_init_seconds,
        "models": {},
    }
    atomic_write_json(args.output, report)
    try:
        samples = selected_rows(args.data, args.samples, args.seed)
        with tempfile.TemporaryDirectory(prefix="hcx_b64_fair_") as td:
            root = Path(td)
            candidate_dir = root / "candidate"
            champion_dir = root / "champion"
            candidate_dir.mkdir()
            champion_dir.mkdir()
            for name, zip_path, destination in (
                ("candidate", args.zip, candidate_dir),
                ("champion", args.reference_zip, champion_dir),
            ):
                package, meta, meta_bytes = clean_extract(zip_path, destination)
                make_proxy_data(destination / "data", samples)
                report["models"][name] = {
                    "package": package,
                    "stages": {},
                    "active_stage": "extracted",
                }
                report["updated_at"] = utc_now()
                atomic_write_json(args.output, report)
                if name == "candidate":
                    candidate_meta, candidate_meta_bytes = meta, meta_bytes
                else:
                    champion_meta, champion_meta_bytes = meta, meta_bytes

            candidate_context = benchmark_package(
                "candidate", candidate_dir, candidate_meta, candidate_meta_bytes,
                samples, report, args, keep_context=True,
            )
            report["candidate_attention_mask_ab"] = {"status": "RUNNING"}
            atomic_write_json(args.output, report)
            report["candidate_attention_mask_ab"] = candidate_mask_ab(
                candidate_context, args.mask_ab_rows, args.profile_batches
            )
            atomic_write_json(args.output, report)
            del candidate_context
            gc.collect()
            torch.cuda.empty_cache()

            benchmark_package(
                "champion", champion_dir, champion_meta, champion_meta_bytes,
                samples, report, args, keep_context=False,
            )

        candidate_timing = report["models"]["candidate"]["stages"]["sorted_b64_inference"]
        champion_timing = report["models"]["champion"]["stages"]["sorted_b64_inference"]
        report["comparison"] = {
            "candidate_over_champion_sorted_loop_wall_ratio": (
                candidate_timing["sorted_b64_loop_wall_seconds"]
                / champion_timing["sorted_b64_loop_wall_seconds"]
            ),
            "candidate_over_champion_cuda_forward_ratio": (
                candidate_timing["cuda_event_forward_sum_seconds"]
                / champion_timing["cuda_event_forward_sum_seconds"]
            ),
            "both_original_hf_meta_batch64": True,
            "full_run_order": ["candidate", "champion"],
            "note": "mask A/B and compile probe are excluded from both full-run timings",
        }
        if args.compile_probe:
            report["compile_probe"] = {"controller_status": "LAUNCHING"}
            atomic_write_json(args.output, report)
            report["compile_probe"] = launch_compile_worker(args)
        else:
            report["compile_probe"] = {"controller_status": "SKIPPED"}
        report["status"] = "COMPLETE"
        report["completed_at"] = utc_now()
        report["updated_at"] = utc_now()
        atomic_write_json(args.output, report)
        print(
            "B64_RESULT",
            json.dumps(
                {
                    "candidate_wall": candidate_timing["sorted_b64_loop_wall_seconds"],
                    "champion_wall": champion_timing["sorted_b64_loop_wall_seconds"],
                    "ratio": report["comparison"]["candidate_over_champion_sorted_loop_wall_ratio"],
                    "mask_drop_parity": report["candidate_attention_mask_ab"]["comparison"]["argmax_parity"],
                    "output": args.output,
                }
            ),
            flush=True,
        )
    except Exception as exc:
        report["status"] = "ERROR"
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()[-8000:]
        report["completed_at"] = utc_now()
        atomic_write_json(args.output, report)
        raise


def main(argv=None):
    args = parse_args(argv)
    ensure_replica_and_reexec()
    if args.worker == "compile":
        raise SystemExit(compile_worker(args))
    controller(args)


if __name__ == "__main__":
    main()
