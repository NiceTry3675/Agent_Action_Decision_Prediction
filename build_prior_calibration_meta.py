"""Build test-batch prior-calibration metadata from held-out logits.

The output is meant to be embedded under hf_meta.json["prior_calibration"].
It stores a soft confusion matrix P(model_prob_class | true_class) and the
held-out true-class prior.  script.py then estimates a shrunk test prior from
the whole test batch and adds a small clipped log-prior-ratio bias.
"""
import argparse
import json
from pathlib import Path

import torch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("val_logits_pt")
    parser.add_argument("--out", required=True)
    parser.add_argument("--prior-blend", type=float, default=0.25)
    parser.add_argument("--bias-scale", type=float, default=0.35)
    parser.add_argument("--bias-cap", type=float, default=0.18)
    parser.add_argument("--ridge", type=float, default=0.05)
    parser.add_argument("--prior-floor", type=float, default=1e-4)
    parser.add_argument(
        "--protected-classes",
        default="edit_file,write_file,apply_patch,respond_only",
        help="comma-separated class names with separately clipped bias",
    )
    parser.add_argument("--protected-cap", type=float, default=0.0)
    args = parser.parse_args()

    payload = torch.load(args.val_logits_pt, map_location="cpu", weights_only=False)
    logits = payload["logits"].float()
    y_true = torch.tensor(payload["y_true"], dtype=torch.long)
    classes = list(payload["classes"])
    n_classes = len(classes)
    probs = torch.softmax(logits, dim=-1)

    counts = torch.bincount(y_true, minlength=n_classes).float()
    reference_prior = counts / counts.sum().clamp_min(1.0)
    soft_confusion = torch.zeros((n_classes, n_classes), dtype=torch.float32)
    hard_confusion = torch.zeros((n_classes, n_classes), dtype=torch.float32)
    hard_pred = logits.argmax(dim=1)
    for class_id in range(n_classes):
        mask = y_true == class_id
        if not bool(mask.any()):
            soft_confusion[:, class_id] = 1.0 / n_classes
            hard_confusion[:, class_id] = 1.0 / n_classes
            continue
        soft_confusion[:, class_id] = probs[mask].mean(dim=0)
        pred_counts = torch.bincount(hard_pred[mask], minlength=n_classes).float()
        hard_confusion[:, class_id] = pred_counts / pred_counts.sum().clamp_min(1.0)

    protected = [item.strip() for item in args.protected_classes.split(",") if item.strip()]
    bad = [item for item in protected if item not in classes]
    if bad:
        raise ValueError(f"unknown protected class names: {bad}")

    out = {
        "enabled": True,
        "source_val_logits": args.val_logits_pt,
        "classes": classes,
        "reference_prior": [float(x) for x in reference_prior.tolist()],
        "soft_confusion": [[float(x) for x in row] for row in soft_confusion.tolist()],
        "hard_confusion": [[float(x) for x in row] for row in hard_confusion.tolist()],
        "prior_blend": args.prior_blend,
        "bias_scale": args.bias_scale,
        "bias_cap": args.bias_cap,
        "ridge": args.ridge,
        "prior_floor": args.prior_floor,
        "protected_classes": protected,
        "protected_cap": args.protected_cap,
        "notes": "Opt-in test-batch label-shift calibration; no row-level overrides.",
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path} rows={len(y_true)} classes={n_classes}")


if __name__ == "__main__":
    main()
