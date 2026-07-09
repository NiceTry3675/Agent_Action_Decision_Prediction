"""Merge session_oof fold val-logits into one KD teacher pack.

Each fold model never trained on its own val sessions, so the merged pack gives
every train sample logits from a model that did NOT see it (true OOF soft-labels).
Output schema matches export_teacher_logits.py: {ids, logits fp16, classes, labels,
y_true, metadata}.
"""
import argparse
import math
from datetime import datetime, timezone
from pathlib import Path

import torch

from script import ALL_CLASSES, load_jsonl
from train import CLASS_TO_ID, load_labels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fold-logits", nargs="+", required=True,
                        help="*_val_logits.pt files, one per fold (must cover all folds)")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--source-note", default="")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    samples = load_jsonl(data_dir / "train.jsonl")
    labels_by_id = load_labels(data_dir / "train_labels.csv")
    all_ids = {str(s["id"]) for s in samples}

    merged = {}
    for path in args.fold_logits:
        payload = torch.load(path, map_location="cpu", weights_only=False)
        ids = [str(i) for i in payload["ids"]]
        logits = payload["logits"].float()
        classes = [str(c) for c in payload["classes"]]
        if classes != list(ALL_CLASSES):
            raise ValueError(f"{path}: classes mismatch vs ALL_CLASSES")
        dup = [i for i in ids if i in merged]
        if dup:
            raise ValueError(f"{path}: {len(dup)} ids overlap another fold (e.g. {dup[0]}) -- folds not disjoint")
        for i, row in zip(ids, logits):
            merged[i] = row
        print(f"loaded fold {path}: {len(ids)} rows (fold_id={payload.get('fold_id')}/{payload.get('n_folds')}, seed={payload.get('seed')})")

    missing = all_ids - set(merged)
    extra = set(merged) - all_ids
    if missing or extra:
        raise ValueError(f"coverage mismatch: missing={len(missing)} extra={len(extra)}")

    ids = sorted(all_ids)
    logits_out = torch.stack([merged[i] for i in ids])
    labels = [labels_by_id[i] for i in ids]
    y_true = [CLASS_TO_ID[label] for label in labels]

    pred = torch.argmax(logits_out, dim=1)
    y = torch.tensor(y_true)
    acc = float((pred == y).float().mean())
    probs = torch.softmax(logits_out, dim=1)
    entropy = float((-probs * torch.log(probs.clamp_min(1e-9))).sum(dim=1).mean())
    top1 = float(probs.max(dim=1).values.mean())
    print(f"OOF argmax acc={acc:.4f}  disagreement={1 - acc:.4f}  mean-entropy={entropy:.3f} (max {math.log(len(ALL_CLASSES)):.3f})  mean-top1-prob={top1:.3f}")

    payload = {
        "ids": ids,
        "logits": logits_out.to(torch.float16),
        "classes": list(ALL_CLASSES),
        "labels": labels,
        "y_true": y_true,
        "metadata": {
            "source_model": "session_oof merge: " + ", ".join(args.fold_logits),
            "serializer": "current_v1",
            "max_length": 384,
            "note": args.source_note,
            "train_argmax_acc": acc,
            "oof": True,
            "created_utc": datetime.now(timezone.utc).isoformat(),
        },
    }
    torch.save(payload, args.out)
    print(f"saved OOF teacher pack: {args.out} shape={tuple(logits_out.shape)}")


if __name__ == "__main__":
    main()
