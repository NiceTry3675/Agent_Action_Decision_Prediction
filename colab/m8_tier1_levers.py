"""M8 tier-1 lever probes: M7 server-cost decomposition, DeltaNet fallback
introspection (chunk-size patchability), eager op-level profile.

Goal: find unexplored speed levers for Qwen3.5-0.8B serving and sharpen the
projection's fixed-cost terms. Runs in the lane-B py3.11 venv (server stack).

Outputs -> experiments/artifacts/m8_tier1_levers.json (+ Drive mirror).
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from colab.m8_qwen35_t4_replica_probe import (  # noqa: E402
    selected_samples,
    stack_info,
    tokenize_features,
    utc_now,
)
from colab.m8_qwen35_compile_probe import load_qwen35, make_batches, run_batches  # noqa: E402
from colab.m8_qwen35_maxpack import mirror_to_drive, write_json  # noqa: E402

QWEN35 = "igorktech/Qwen3.5-0.8B-Base-LM"
M7_DIR = "/content/drive/MyDrive/AADP_exchange/models/m7_qwen3_refit"
ART = Path("experiments/artifacts/m8_tier1_levers.json")


def m7_decomposition(payload, rows):
    """Load the ACTUAL M7 pack (int8) and split load/tokenize/infer costs."""
    import torch
    from transformers import AutoTokenizer

    from script import load_hf_model, model_logits_sorted

    hf_dir = os.path.join(M7_DIR, "hf_model")
    meta = json.loads(Path(M7_DIR, "hf_meta.json").read_text(encoding="utf-8"))
    device = torch.device("cuda")

    t0 = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(hf_dir, local_files_only=True)
    model = load_hf_model(hf_dir, device)
    model.half().eval()
    load_sec = time.perf_counter() - t0

    _, texts, _ = selected_samples(rows)
    t0 = time.perf_counter()
    logits = model_logits_sorted(model, tokenizer, texts,
                                 int(meta.get("max_length", 416)),
                                 int(meta.get("batch_size", 64)), device)
    infer_sec = time.perf_counter() - t0
    entry = {
        "rows": rows,
        "load_sec": load_sec,
        "tokenize_plus_infer_sec": infer_sec,
        "max_length": meta.get("max_length"),
        "batch_size": meta.get("batch_size"),
        "note": "server 530s total = load(fixed) + this scaled by test_rows/probe_rows",
    }
    payload["m7_decomposition"] = entry
    import gc

    del model
    gc.collect()
    torch.cuda.empty_cache()
    return entry


def introspect_fallback(payload):
    """Dump the DeltaNet fallback implementation so chunk-size / vectorization
    levers can be designed offline. Records symbols, signatures, and source."""
    import importlib

    found = {}
    for modname in (
        "transformers.models.qwen3_5.modeling_qwen3_5",
        "transformers.models.qwen3_5_text.modeling_qwen3_5_text",
        "transformers.models.qwen3_next.modeling_qwen3_next",
    ):
        try:
            module = importlib.import_module(modname)
        except Exception as exc:  # noqa: BLE001
            found[modname] = {"import_error": repr(exc)[:200]}
            continue
        info = {}
        for name in dir(module):
            low = name.lower()
            if not any(t in low for t in ("delta", "chunk", "recurrent", "conv")):
                continue
            obj = getattr(module, name)
            item = {"type": type(obj).__name__}
            if callable(obj):
                try:
                    item["signature"] = str(inspect.signature(obj))
                except Exception:  # noqa: BLE001
                    pass
                try:
                    src = inspect.getsource(obj)
                    item["source_len"] = len(src)
                    item["source"] = src[:6000]
                except Exception:  # noqa: BLE001
                    pass
            info[name] = item
        found[modname] = info
    payload["fallback_introspection"] = found


def eager_profile(payload, rows):
    """Op-level attribution of the eager fallback (rows sized to whole batches)."""
    from torch.profiler import ProfilerActivity, profile

    model, tokenizer, device, _ = load_qwen35()
    _, texts, _ = selected_samples(rows)
    features, lengths, _ = tokenize_features(tokenizer, texts, 400)
    batches = make_batches(features, lengths, 64, None)
    # warm pass, then profile a full identical pass
    run_batches(model, tokenizer, features, batches, 64, None, device, len(features))
    with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
        run_batches(model, tokenizer, features, batches, 64, None, device, len(features))
    table = prof.key_averages().table(sort_by="self_cuda_time_total", row_limit=30)
    payload["eager_profile_top30"] = table
    print(table[:3000], flush=True)


VENV_PY = "/content/venv311/bin/python"


def main():
    # the launcher uses the system python; hop into the server-stack venv
    if sys.executable != VENV_PY and Path(VENV_PY).exists():
        os.execv(VENV_PY, [VENV_PY, "-u", str(Path(__file__).resolve()), *sys.argv[1:]])
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=4096)
    parser.add_argument("--profile-rows", type=int, default=192)
    args = parser.parse_args()

    payload = {"what": "tier-1 lever probes (M7 decomposition, fallback introspection, eager profile)",
               "created_utc": utc_now(), "stack": stack_info()}
    for step, fn, fnargs in (
        ("m7_decomposition", m7_decomposition, (payload, args.rows)),
        ("fallback_introspection", introspect_fallback, (payload,)),
        ("eager_profile", eager_profile, (payload, args.profile_rows)),
    ):
        try:
            fn(*fnargs)
            print(f"step done: {step}", flush=True)
        except Exception:  # noqa: BLE001
            payload[f"{step}_error"] = traceback.format_exc()[-3000:]
            print(f"step failed: {step}", flush=True)
        write_json(ART, payload)
        mirror_to_drive(ART)


if __name__ == "__main__":
    main()
