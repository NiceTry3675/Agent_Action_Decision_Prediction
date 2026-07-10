import argparse
import hashlib
import json
import sys
from pathlib import Path

import peft
import torch
import transformers

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from script import ALL_CLASSES


EXPECTED_IDS_SHA256 = "6f86a30da2845c1452df7030ee73718be856423248e4f2e65442a60373904985"
EXPECTED_RAW_MACRO_F1 = 0.7838517354251339


def torch_load(path):
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmstart", required=True, help="full-weight hf_model directory")
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--gpu-name", default="A100")
    parser.add_argument("--expect-final-refit", action="store_true")
    args = parser.parse_args()

    if transformers.__version__ != "4.46.3":
        raise RuntimeError(f"expected transformers==4.46.3, got {transformers.__version__}")
    if peft.__version__ != "0.19.1":
        raise RuntimeError(f"expected peft==0.19.1, got {peft.__version__}")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    gpu_name = torch.cuda.get_device_name(0)
    if args.gpu_name.lower() not in gpu_name.lower():
        raise RuntimeError(f"expected GPU name containing {args.gpu_name!r}, got {gpu_name!r}")

    warmstart = Path(args.warmstart)
    if (warmstart / "checkpoint_state.json").exists():
        raise RuntimeError("warm-start contains checkpoint_state.json")
    if not (warmstart / "model.safetensors").is_file():
        raise RuntimeError(f"warm-start full weights are missing: {warmstart / 'model.safetensors'}")
    meta_path = warmstart.parent / "hf_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if list(meta.get("classes") or []) != ALL_CLASSES:
        raise RuntimeError("warm-start class order mismatch")
    if bool(meta.get("final_refit", False)) != args.expect_final_refit:
        raise RuntimeError(
            f"warm-start final_refit={meta.get('final_refit')} "
            f"!= expected {args.expect_final_refit}"
        )
    if not bool(meta.get("saved_fp16", False)):
        raise RuntimeError("warm-start is not marked saved_fp16")

    anchor = torch_load(args.anchor)
    ids = [str(value) for value in anchor.get("ids") or []]
    ids_hash = hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()
    if len(ids) != 14001 or ids_hash != EXPECTED_IDS_SHA256:
        raise RuntimeError(f"anchor id contract mismatch: rows={len(ids)} sha256={ids_hash}")
    if list(anchor.get("classes") or []) != ALL_CLASSES:
        raise RuntimeError("anchor class order mismatch")
    if anchor.get("split") != "session" or int(anchor.get("seed", -1)) != 42:
        raise RuntimeError("anchor split/seed mismatch")
    raw_macro = float((anchor.get("raw_metrics") or {}).get("macro_f1", -1.0))
    if abs(raw_macro - EXPECTED_RAW_MACRO_F1) > 1e-12:
        raise RuntimeError(f"anchor raw macro mismatch: {raw_macro}")

    print(
        "Weak4 lane preflight OK: "
        f"gpu={gpu_name} torch={torch.__version__} transformers={transformers.__version__} "
        f"peft={peft.__version__} anchor_rows={len(ids)} "
        f"warmstart_final_refit={meta.get('final_refit')}"
    )


if __name__ == "__main__":
    main()
