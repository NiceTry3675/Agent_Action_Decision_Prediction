"""Fail-closed validation for a terminal-token teacher model and logit payload."""

import argparse
import json
import sys
from pathlib import Path

import torch

# Running ``python colab/verify_terminal_teacher.py`` puts only ``colab/`` on
# sys.path. Add the repository root explicitly before importing shared labels.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from script import ALL_CLASSES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--terminal-token", required=True)
    parser.add_argument("--serializer", default="current_v1")
    parser.add_argument("--max-length", type=int, required=True)
    parser.add_argument("--rows", type=int, default=70000)
    args = parser.parse_args()

    model_dir = Path(args.model_dir)
    payload_path = Path(args.payload)
    meta = json.loads((model_dir / "hf_meta.json").read_text(encoding="utf-8"))
    expected_model = {
        "terminal_token": args.terminal_token,
        "serializer_name": args.serializer,
        "max_length": args.max_length,
    }
    actual_model = {key: meta.get(key) for key in expected_model}
    if actual_model != expected_model:
        raise ValueError(
            f"terminal teacher model metadata mismatch: expected={expected_model} actual={actual_model}"
        )
    if not (model_dir / "hf_model" / "model.safetensors").is_file():
        raise FileNotFoundError(f"missing teacher weights under {model_dir / 'hf_model'}")

    payload = torch.load(payload_path, map_location="cpu", weights_only=False)
    logits = payload.get("logits")
    ids = payload.get("ids") or []
    classes = list(payload.get("classes") or [])
    export_meta = payload.get("metadata") or {}
    if not torch.is_tensor(logits) or tuple(logits.shape) != (args.rows, len(ALL_CLASSES)):
        raise ValueError(f"unexpected teacher logits shape: {getattr(logits, 'shape', None)}")
    if logits.dtype != torch.float16:
        raise ValueError(f"teacher logits must be fp16, got {logits.dtype}")
    if len(ids) != args.rows or len(set(map(str, ids))) != args.rows:
        raise ValueError(f"teacher ids must be {args.rows} unique rows, got {len(ids)}")
    if classes != ALL_CLASSES:
        raise ValueError("teacher payload class order mismatch")
    expected_export = {
        "terminal_token": args.terminal_token,
        "serializer_name": args.serializer,
        "max_length": args.max_length,
        "row_count": args.rows,
    }
    actual_export = {key: export_meta.get(key) for key in expected_export}
    if actual_export != expected_export:
        raise ValueError(
            f"terminal teacher export metadata mismatch: expected={expected_export} actual={actual_export}"
        )
    print(
        "terminal teacher verified:",
        json.dumps(
            {
                "model_dir": str(model_dir),
                "payload": str(payload_path),
                "shape": list(logits.shape),
                "dtype": str(logits.dtype),
                "train_argmax_acc": export_meta.get("train_argmax_acc"),
                **expected_export,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
