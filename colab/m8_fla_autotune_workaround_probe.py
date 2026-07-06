"""M8 Qwen3.5 FLA Triton autotune/config workaround probe.

The r4 fast-path repro proved that causal-conv1d + fla-core imports can work
on T4/torch 2.7.1, but the first Qwen3.5 forward died inside Triton's
autotuner while compiling the Gated DeltaNet KKT/solve kernel. This runner
keeps that evidence isolated and tests two engineering workarounds:

1. fla-core 0.5.1 config-cache mode: provide default kernel configs so FLA
   skips Triton's multi-config autotune path.
2. Runtime Autotuner narrowing: monkeypatch imported Triton Autotuner objects
   so each kernel compiles one selected config instead of benchmarking all
   candidates.

The first goal is survival of a one-batch correctness smoke. Full timing is
only useful after the fast path can run without PassManager failures.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from colab.m8_qwen35_t4_replica_probe import (  # noqa: E402
    capture_model_load,
    fastpath_active_from_warnings,
    infer_logits,
    load_head_payload,
    model_timing,
    qwen35_flags,
    selected_samples,
    set_pad_token,
    stack_info,
    tokenize_features,
)
from colab.m8_qwen35_maxpack import mirror_to_drive, write_json  # noqa: E402


EXPERIMENT_ID = "m8_fla_autotune_workaround"
QWEN35 = "igorktech/Qwen3.5-0.8B-Base-LM"
QWEN3 = "Qwen/Qwen3-0.6B"
VENV_PY = "/content/venv311/bin/python"
ART_DIR = Path("experiments/artifacts")
RESULTS_PATH = Path("experiments/results.csv")
FLA_CONFIG_DIR = Path("/content/aadp_fla_configs")
TRITON_CACHE_DIR = "/content/aadp_fla_triton_cache"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def artifact_path(experiment_id=None, suffix=""):
    experiment_id = experiment_id or EXPERIMENT_ID
    stem = experiment_id + (f"_{suffix}" if suffix else "")
    return ART_DIR / f"{stem}.json"


def fallback_pt_path(experiment_id=None):
    experiment_id = experiment_id or EXPERIMENT_ID
    return ART_DIR / f"{experiment_id}_fallback.pt"


def run(cmd, check=True, timeout=None, env=None):
    start = time.perf_counter()
    print("+", " ".join(cmd), flush=True)
    proc = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        env=env,
    )
    duration = time.perf_counter() - start
    tail = proc.stdout[-12000:]
    if tail:
        print(tail, flush=True)
    item = {
        "cmd": cmd,
        "returncode": proc.returncode,
        "duration_sec": round(duration, 3),
        "output_tail": tail[-5000:],
    }
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed rc={proc.returncode}: {' '.join(cmd)}")
    return item


def py_run(args, check=True, timeout=None, env=None):
    return run([sys.executable, *args], check=check, timeout=timeout, env=env)


def append_result_row(payload):
    row = {
        "experiment_id": payload.get("experiment_id", EXPERIMENT_ID),
        "model_family": "m8_fla_autotune_workaround",
        "base_model": QWEN35,
        "features": "Qwen3.5 fast-path FLA Triton config/autotune workaround",
        "serializer_name": "current_v1",
        "split_type": "timing_probe",
        "seed": "42",
        "max_length": str(payload.get("max_length", "")),
        "batch_size": str(payload.get("batch_size", "")),
        "artifact_path": str(artifact_path(payload.get("experiment_id", EXPERIMENT_ID))),
        "inference_time_sec": str(payload.get("best_projected_infer_sec", "")),
        "runtime_sec": f"{payload.get('runtime_sec', 0.0):.3f}",
        "train_command": "colab/m8_fla_autotune_workaround_probe.py controller",
        "notes": payload.get("notes", ""),
        "decision": payload.get("verdict", ""),
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


def checkpoint(path, payload, mirror=True):
    write_json(path, payload)
    if mirror:
        mirror_to_drive(path)
    print(f"checkpoint: {path}", flush=True)


def ensure_venv():
    if sys.executable == VENV_PY or not str(ROOT).startswith("/content/"):
        return
    if not Path(VENV_PY).exists():
        run(["add-apt-repository", "-y", "ppa:deadsnakes/ppa"], check=False, timeout=300)
        run(["apt-get", "install", "-y", "-q", "python3.11", "python3.11-venv", "python3.11-dev"],
            check=False, timeout=600)
        run(["python3.11", "-m", "venv", "/content/venv311"], check=False, timeout=300)
        if not Path(VENV_PY).exists():
            run([sys.executable, "-m", "venv", "/content/venv311"], check=False, timeout=300)
        run([VENV_PY, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=False, timeout=600)
    os.execv(VENV_PY, [VENV_PY, "-u", str(Path(__file__).resolve()), *sys.argv[1:]])


def install_base_stack():
    logs = []
    logs.append(py_run(["-m", "pip", "install", "torch==2.7.1", "--index-url",
                        "https://download.pytorch.org/whl/cu128"], timeout=1800))
    logs.append(py_run(["-m", "pip", "uninstall", "-y", "torchvision", "torchaudio", "torchtext"],
                       check=False, timeout=300))
    logs.append(py_run(["-m", "pip", "install", "transformers>=5.13,<5.14",
                        "safetensors==0.8.0", "einops"], timeout=900))
    logs.append(py_run(["-m", "pip", "uninstall", "-y", "causal-conv1d", "causal_conv1d",
                        "fla-core", "flash-linear-attention", "flash_linear_attention"],
                       check=False, timeout=300))
    return logs


def install_optional_stack(fla_candidate):
    logs = []
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    wheel = (
        "https://github.com/Dao-AILab/causal-conv1d/releases/download/v1.6.2.post1/"
        f"causal_conv1d-1.6.2.post1+cu12torch2.7cxx11abiTRUE-{py_tag}-{py_tag}-linux_x86_64.whl"
    )
    logs.append(py_run(["-m", "pip", "install", wheel], timeout=600))
    if fla_candidate in ("", "latest"):
        spec = "fla-core"
    else:
        spec = f"fla-core=={fla_candidate}"
    logs.append(py_run(["-m", "pip", "install", "--force-reinstall", "--no-deps", spec], timeout=900))
    logs.append(py_run(["-c", "import fla, causal_conv1d; print('optional imports ok')"], timeout=120))
    return logs


def write_fla_default_configs(policy, config_dir):
    """Write FLA 0.5.1 default_config files to bypass Triton autotune.

    These are deliberately conservative SM75-friendly picks. They do not claim
    optimality; the survival test decides whether they are compileable.
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    kkt_bk, kkt_warps = parse_kkt_policy(policy)
    kkt_bk = kkt_bk or 32
    kkt_warps = kkt_warps or 1
    defaults = {
        "chunk_gated_delta_rule_fwd_kkt_solve_kernel": {
            "kwargs": {"BK": kkt_bk},
            "num_warps": kkt_warps,
            "num_stages": 3,
            "num_ctas": 1,
        },
        "recompute_w_u_fwd_kernel": {
            "kwargs": {},
            "num_warps": 2,
            "num_stages": 3,
            "num_ctas": 1,
        },
        "chunk_local_cumsum_scalar_kernel": {
            "kwargs": {},
            "num_warps": 1,
            "num_stages": 3,
            "num_ctas": 1,
        },
        "chunk_local_cumsum_vector_kernel": {
            "kwargs": {"BS": 16},
            "num_warps": 2,
            "num_stages": 3,
            "num_ctas": 1,
        },
        "chunk_gated_delta_rule_fwd_kernel_h_blockdim64": {
            "kwargs": {"BV": 32},
            "num_warps": 2,
            "num_stages": 2,
            "num_ctas": 1,
        },
        "chunk_fwd_kernel_o": {
            "kwargs": {"BK": 32, "BV": 32},
            "num_warps": 2,
            "num_stages": 3,
            "num_ctas": 1,
        },
        "gdn_gate_chunk_cumsum_fwd_kernel": {
            "kwargs": {},
            "num_warps": 1,
            "num_stages": 3,
            "num_ctas": 1,
        },
    }
    written = []
    for kernel_name, default_config in defaults.items():
        payload = {
            "kernel_name": kernel_name,
            "triton_version": "probe",
            "default_config": default_config,
        }
        path = config_dir / f"{kernel_name}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        written.append(str(path))
    return written


def config_meta(config):
    return {
        "kwargs": dict(getattr(config, "kwargs", {}) or {}),
        "num_warps": getattr(config, "num_warps", None),
        "num_stages": getattr(config, "num_stages", None),
        "num_ctas": getattr(config, "num_ctas", None),
        "maxnreg": getattr(config, "maxnreg", None),
        "repr": repr(config),
    }


def parse_kkt_policy(policy):
    match = re.search(r"kkt_bk(\d+)_w(\d+)", policy)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def config_score(config):
    meta = config_meta(config)
    kwargs = meta["kwargs"]
    max_kwarg = max((v for v in kwargs.values() if isinstance(v, int)), default=0)
    return (
        meta["num_warps"] if meta["num_warps"] is not None else 999,
        meta["num_stages"] if meta["num_stages"] is not None else 999,
        max_kwarg,
        repr(kwargs),
    )


def choose_config(kernel_name, configs, policy):
    configs = list(configs)
    if not configs:
        return None
    if policy.endswith("_none") or policy == "none":
        return None
    if "index" in policy:
        match = re.search(r"index(\d+)", policy)
        if match:
            return configs[min(int(match.group(1)), len(configs) - 1)]
    kkt_bk, kkt_warps = parse_kkt_policy(policy)
    if "kkt_solve" in kernel_name and kkt_bk is not None:
        for config in configs:
            meta = config_meta(config)
            if meta["kwargs"].get("BK") == kkt_bk and meta["num_warps"] == kkt_warps:
                return config
    if "last" in policy:
        return configs[-1]
    return sorted(configs, key=config_score)[0]


def policy_uses_patch(policy):
    return policy.startswith("patch_") or "index" in policy or policy in {"last", "min"}


def walk_autotuners(obj, owner, found, seen):
    if id(obj) in seen:
        return
    seen.add(id(obj))
    if hasattr(obj, "configs") and hasattr(obj, "run"):
        configs = getattr(obj, "configs", None)
        if isinstance(configs, (list, tuple)) and configs:
            kernel_name = getattr(obj, "kernel_name", None)
            if kernel_name is None:
                base = getattr(obj, "base_fn", None) or getattr(obj, "fn", None)
                kernel_name = getattr(base, "__name__", None)
            found.append((owner, kernel_name or owner, obj))
    for attr in ("fn", "base_fn", "kernel"):
        try:
            child = getattr(obj, attr)
        except Exception:
            continue
        if child is not None:
            walk_autotuners(child, f"{owner}.{attr}", found, seen)


TARGET_MODULES = (
    "fla.ops.gated_delta_rule.chunk_fwd",
    "fla.ops.gated_delta_rule.wy_fast",
    "fla.ops.gated_delta_rule.gate",
    "fla.ops.common.chunk_delta_h",
    "fla.ops.common.chunk_o",
    "fla.ops.common.chunk_scaled_dot_kkt",
    "fla.ops.utils.cumsum",
    "fla.ops.utils.solve_tril",
)


def patch_autotuners(policy):
    found = []
    for modname in TARGET_MODULES:
        try:
            module = importlib.import_module(modname)
        except Exception:
            continue
        for name, obj in vars(module).items():
            walk_autotuners(obj, f"{modname}.{name}", found, set())
    patched = []
    seen = set()
    for owner, kernel_name, autotuner in found:
        if id(autotuner) in seen:
            continue
        seen.add(id(autotuner))
        before = [config_meta(c) for c in list(autotuner.configs)]
        chosen = choose_config(kernel_name, autotuner.configs, policy)
        if chosen is None:
            continue
        autotuner.configs = [chosen]
        if hasattr(autotuner, "cache"):
            try:
                autotuner.cache.clear()
            except Exception:
                pass
        patched.append({
            "owner": owner,
            "kernel_name": kernel_name,
            "config_count_before": len(before),
            "chosen": config_meta(chosen),
            "before_first6": before[:6],
        })
    return patched


def load_qwen35_fast(head=None):
    import torch
    from transformers import AutoTokenizer

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(QWEN35)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    torch.manual_seed(1234)
    model, load_warnings = capture_model_load(QWEN35, torch.float16)
    set_pad_token(model, tokenizer)
    if head is not None:
        load_head_payload(model, head)
    model.to(device).eval()
    if device.type == "cuda":
        model.half()
    return model, tokenizer, device, load_warnings


def run_fast_workaround(args):
    started = time.perf_counter()
    variant_id = f"{args.experiment_id}_{args.fla_candidate}_{args.policy}".replace(".", "p")
    out_path = artifact_path(variant_id)
    payload = {
        "experiment_id": variant_id,
        "parent_experiment_id": args.experiment_id,
        "what": "Qwen3.5 FLA fast-path Triton config/autotune workaround variant",
        "created_utc": utc_now(),
        "fla_candidate": args.fla_candidate,
        "policy": args.policy,
        "correctness_rows": args.correctness_rows,
        "timing_rows": args.timing_rows,
        "max_length": args.max_length,
        "batch_size": args.batch_size,
    }
    try:
        if args.policy.startswith("cache_"):
            os.environ["FLA_CACHE_MODE"] = "default"
            os.environ["FLA_CONFIG_DIR"] = str(FLA_CONFIG_DIR)
            payload["fla_config_files"] = write_fla_default_configs(args.policy, FLA_CONFIG_DIR)
        else:
            os.environ["FLA_CACHE_MODE"] = "disabled"
        os.environ.setdefault("TRITON_CACHE_DIR", TRITON_CACHE_DIR)
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

        import torch

        fallback = torch.load(fallback_pt_path(args.experiment_id), map_location="cpu", weights_only=False)
        model, tokenizer, device, load_warnings = load_qwen35_fast(head=fallback.get("head"))
        payload["stack"] = stack_info({"fla_core_candidate": args.fla_candidate})
        payload["load_warnings"] = load_warnings
        payload["qwen35_flags"] = qwen35_flags()
        payload["fastpath_active"] = fastpath_active_from_warnings(load_warnings)
        if not payload["fastpath_active"]:
            payload["verdict"] = "RED_fastpath_inactive"
            payload["runtime_sec"] = time.perf_counter() - started
            checkpoint(out_path, payload)
            return 40

        payload["patched_autotuners"] = patch_autotuners(args.policy) if policy_uses_patch(args.policy) else []
        if policy_uses_patch(args.policy) and not payload["patched_autotuners"]:
            payload["verdict"] = "RED_no_autotuners_patched"
            payload["runtime_sec"] = time.perf_counter() - started
            checkpoint(out_path, payload)
            return 41

        _, texts, ids = selected_samples(max(args.correctness_rows, args.timing_rows))
        c_texts = texts[: args.correctness_rows]
        features, lengths, _ = tokenize_features(tokenizer, c_texts, args.max_length)
        fast_logits, infer_sec, batch_times = infer_logits(
            model,
            tokenizer,
            features,
            lengths,
            args.batch_size,
            "sorted",
            args.max_length,
            device,
        )
        fallback_logits = fallback["logits"][: args.correctness_rows].float()
        diff = (fast_logits.float() - fallback_logits).abs()
        agreement = (fast_logits.argmax(1).cpu() == fallback_logits.argmax(1).cpu()).float().mean().item()
        payload["correctness"] = {
            "sample_ids_first5": ids[:5],
            "argmax_agreement": agreement,
            "max_abs_diff": float(diff.max().item()),
            "mean_abs_diff": float(diff.mean().item()),
            "p95_abs_diff": float(torch.quantile(diff.flatten(), 0.95).item()),
            "infer_sec": infer_sec,
            "batch_times_first4": batch_times[:4],
        }
        checkpoint(out_path, payload)
        if agreement < args.min_agreement:
            payload["verdict"] = "RED_correctness"
            payload["runtime_sec"] = time.perf_counter() - started
            checkpoint(out_path, payload)
            return 42

        if args.timing_rows > 0:
            t_texts = texts[: args.timing_rows]
            features, lengths, tokenize_sec = tokenize_features(tokenizer, t_texts, args.max_length)
            _, infer_sec, batch_times = infer_logits(
                model,
                tokenizer,
                features,
                lengths,
                args.batch_size,
                "sorted",
                args.max_length,
                device,
            )
            payload["timing"] = {
                "tokenize_sec": tokenize_sec,
                "infer_sec": infer_sec,
                "tokenize_plus_infer_sec": tokenize_sec + infer_sec,
                "batch_count": len(batch_times),
                "batch_times_first8": batch_times[:8],
                "batch_times_slowest5": sorted(batch_times)[-5:],
            }
            if args.with_denominator:
                denom = model_timing(QWEN3, 416, args.batch_size, "sorted", args.timing_rows)
                ratio = (tokenize_sec + infer_sec) / denom["tokenize_plus_infer_sec"]
                payload["qwen3_denominator"] = {
                    "tokenize_plus_infer_sec": denom["tokenize_plus_infer_sec"],
                    "infer_sec": denom["infer_sec"],
                }
                payload["ratio_vs_qwen3"] = ratio
                payload["projected_infer_sec"] = ratio * 478.0

        payload["verdict"] = "GREEN_fastpath_survived"
        payload["runtime_sec"] = time.perf_counter() - started
        checkpoint(out_path, payload)
        return 0
    except Exception:
        payload["verdict"] = "RED_error"
        payload["error"] = traceback.format_exc()[-5000:]
        payload["runtime_sec"] = time.perf_counter() - started
        checkpoint(out_path, payload)
        return 43


def controller(args):
    ensure_venv()
    started = time.perf_counter()
    os.environ.setdefault("TRITON_CACHE_DIR", TRITON_CACHE_DIR)
    ART_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "experiment_id": args.experiment_id,
        "what": "Qwen3.5 FLA Triton config/autotune workaround controller",
        "created_utc": utc_now(),
        "correctness_rows": args.correctness_rows,
        "timing_rows": args.timing_rows,
        "max_length": args.max_length,
        "batch_size": args.batch_size,
        "variants": {},
        "logs_tail": [],
    }
    try:
        gpu = run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], check=False)
        summary["gpu"] = gpu.get("output_tail", "").strip()
        if args.require_t4 and "T4" not in summary["gpu"]:
            summary["verdict"] = "WRONG_GPU"
            checkpoint(artifact_path(args.experiment_id), summary)
            return
        if not args.skip_install:
            summary["logs_tail"].extend(install_base_stack())

        # Create the stock fallback reference before installing optional kernels.
        fb = py_run(
            [
                "colab/m8_qwen35_t4_replica_probe.py",
                "fallback",
                "--experiment-id",
                args.experiment_id,
                "--correctness-rows",
                str(max(args.correctness_rows, 256)),
            ],
            check=False,
            timeout=1800,
        )
        summary["logs_tail"].append(fb)
        if fb["returncode"] != 0 or not fallback_pt_path(args.experiment_id).exists():
            summary["verdict"] = "RED_fallback_failed"
            summary["runtime_sec"] = time.perf_counter() - started
            checkpoint(artifact_path(args.experiment_id), summary)
            append_result_row(summary)
            return

        candidates = [v.strip() for v in args.fla_candidates.split(",") if v.strip()]
        policies = [v.strip() for v in args.policies.split(",") if v.strip()]
        for candidate in candidates:
            try:
                summary["logs_tail"].extend(install_optional_stack(candidate))
            except Exception as exc:
                summary["variants"][candidate] = {"setup_error": repr(exc)}
                checkpoint(artifact_path(args.experiment_id), summary)
                continue
            for policy in policies:
                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"
                cmd = [
                    "colab/m8_fla_autotune_workaround_probe.py",
                    "variant",
                    "--experiment-id",
                    args.experiment_id,
                    "--fla-candidate",
                    candidate,
                    "--policy",
                    policy,
                    "--correctness-rows",
                    str(args.correctness_rows),
                    "--timing-rows",
                    str(args.timing_rows),
                    "--max-length",
                    str(args.max_length),
                    "--batch-size",
                    str(args.batch_size),
                    "--min-agreement",
                    str(args.min_agreement),
                ]
                if args.with_denominator:
                    cmd.append("--with-denominator")
                rc = py_run(cmd, check=False, timeout=args.variant_timeout, env=env)
                summary["logs_tail"].append(rc)
                variant_id = f"{args.experiment_id}_{candidate}_{policy}".replace(".", "p")
                path = artifact_path(variant_id)
                if path.exists():
                    summary["variants"][variant_id] = json.loads(path.read_text(encoding="utf-8"))
                else:
                    summary["variants"][variant_id] = {"returncode": rc["returncode"], "verdict": "RED_no_artifact"}
                checkpoint(artifact_path(args.experiment_id), summary)
                if rc["returncode"] == 0 and not args.keep_going:
                    summary["verdict"] = "GREEN_fastpath_survived"
                    break
            if summary.get("verdict") == "GREEN_fastpath_survived" and not args.keep_going:
                break

        variants = summary["variants"]
        green = [v for v in variants.values() if str(v.get("verdict", "")).startswith("GREEN")]
        projected = [
            v.get("projected_infer_sec")
            for v in variants.values()
            if isinstance(v.get("projected_infer_sec"), (int, float))
        ]
        if green:
            summary["verdict"] = "GREEN_fastpath_survived"
        elif variants:
            summary["verdict"] = "RED_all_workarounds_failed"
        else:
            summary["verdict"] = "RED_no_variants"
        summary["best_projected_infer_sec"] = min(projected) if projected else ""
        summary["stack"] = stack_info()
        summary["runtime_sec"] = time.perf_counter() - started
        summary["notes"] = "First pass answers whether FLA fast path can survive one-batch T4 compile."
        checkpoint(artifact_path(args.experiment_id), summary)
        append_result_row(summary)
    except Exception:
        summary["verdict"] = "RED_controller_error"
        summary["error"] = traceback.format_exc()[-5000:]
        summary["runtime_sec"] = time.perf_counter() - started
        checkpoint(artifact_path(args.experiment_id), summary)
        append_result_row(summary)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_ctrl = sub.add_parser("controller")
    p_ctrl.add_argument("--experiment-id", default=EXPERIMENT_ID)
    p_ctrl.add_argument("--fla-candidates", default="0.5.1")
    p_ctrl.add_argument("--policies", default="cache_kkt_bk32_w1,cache_kkt_bk64_w1,patch_kkt_bk32_w1,patch_kkt_bk64_w1,patch_index0,patch_index3")
    p_ctrl.add_argument("--correctness-rows", type=int, default=64)
    p_ctrl.add_argument("--timing-rows", type=int, default=256)
    p_ctrl.add_argument("--max-length", type=int, default=400)
    p_ctrl.add_argument("--batch-size", type=int, default=64)
    p_ctrl.add_argument("--min-agreement", type=float, default=0.995)
    p_ctrl.add_argument("--variant-timeout", type=int, default=900)
    p_ctrl.add_argument("--with-denominator", action="store_true")
    p_ctrl.add_argument("--skip-install", action="store_true")
    p_ctrl.add_argument("--keep-going", action="store_true")
    p_ctrl.add_argument("--require-t4", action="store_true")

    p_var = sub.add_parser("variant")
    p_var.add_argument("--experiment-id", default=EXPERIMENT_ID)
    p_var.add_argument("--fla-candidate", default="0.5.1")
    p_var.add_argument("--policy", default="cache_kkt_bk32_w1")
    p_var.add_argument("--correctness-rows", type=int, default=64)
    p_var.add_argument("--timing-rows", type=int, default=256)
    p_var.add_argument("--max-length", type=int, default=400)
    p_var.add_argument("--batch-size", type=int, default=64)
    p_var.add_argument("--min-agreement", type=float, default=0.995)
    p_var.add_argument("--with-denominator", action="store_true")

    args = parser.parse_args()
    if args.command == "controller":
        controller(args)
    elif args.command == "variant":
        raise SystemExit(run_fast_workaround(args))


if __name__ == "__main__":
    main()
