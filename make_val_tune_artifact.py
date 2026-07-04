"""Wrap a single run's val logits as an aggregate-style artifact for the OOF tuners.

The rule/sparse tuners (tune_oof_rule_boosts.py, tune_sparse_svc_oof.py) read
{"fold_logits": [...], "class_bias": {...}} from --oof-artifact. Under the
Public-gated protocol (research_log 2026-07-04) post-processing is tuned on one
run's ~14k fixed-session val logits instead of a 3-fold OOF; this emits the
adapter JSON from a *_val_logits.pt. Expect the tuned gains to be MORE
optimistic vs Public than OOF-tuned ones (smaller data, same split every run).

Usage:
    .venv/bin/python make_val_tune_artifact.py experiments/logits/<run>_val_logits.pt
"""
import argparse
import json
from pathlib import Path

import torch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("val_logits_pt")
    parser.add_argument("--out", default=None,
                        help="default: alongside input as *_val_tune_artifact.json under experiments/artifacts/")
    args = parser.parse_args()
    pt = Path(args.val_logits_pt)
    payload = torch.load(pt, map_location="cpu", weights_only=False)
    classes = payload["classes"]
    bias = payload.get("class_bias") or [0.0] * len(classes)
    if args.out:
        out = Path(args.out)
    else:
        out = Path("experiments/artifacts") / pt.name.replace("_val_logits.pt", "_val_tune_artifact.json")
    out.write_text(json.dumps({
        "fold_logits": [str(pt)],
        "class_bias": {c: float(b) for c, b in zip(classes, bias)},
        "source": "single-run fixed val logits (Public-gated protocol)",
        "val_rows": int(payload["logits"].shape[0]),
        "base_model": payload.get("base_model"),
        "max_length": payload.get("max_length"),
    }, indent=2, ensure_ascii=False))
    print(f"wrote {out} | rows={payload['logits'].shape[0]} | "
          f"bias_nonzero={any(abs(float(b)) > 1e-9 for b in bias)}")


if __name__ == "__main__":
    main()
