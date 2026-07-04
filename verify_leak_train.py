"""Verify the leak-override recovery (compute_leak_overrides) against training labels.

Modes:
  full      — treat all 70k train rows as the test set; positional+aligned tiers
              only (no train lookup: it would trivially contain every prompt).
  holdout   — split sessions 50/50, build the train lookup from half A, evaluate
              overrides on half B; measures the true precision of the lookup tiers.
  subsample — keep each row with probability --keep-prob to simulate a server test
              set that contains only a sparse sample of each session's steps.
  noid      — like full, but with ids replaced by opaque tokens so the positional
              tier cannot run; exercises the id-free "aligned" tier.
"""

import argparse
import csv
import os
import random

from build_leak_lookup import build_lookup
from script import LEAK_STEP_RE, compute_leak_overrides, load_jsonl


def load_labels(data_dir):
    with open(os.path.join(data_dir, "train_labels.csv"), encoding="utf-8", newline="") as f:
        return {row["id"]: row["action"] for row in csv.DictReader(f)}


def report(name, samples, labels, train_lookup=None):
    overrides, stats = compute_leak_overrides(samples, train_lookup=train_lookup)
    correct = sum(1 for sample_id, action in overrides.items() if labels.get(sample_id) == action)
    wrong = [(sample_id, action, labels.get(sample_id)) for sample_id, action in overrides.items()
             if labels.get(sample_id) != action]
    total = len(samples)
    print(f"[{name}] rows={total} overridden={len(overrides)} ({len(overrides)/total:.3f}) "
          f"correct={correct} wrong={len(wrong)} tiers={stats}")
    for sample_id, predicted, actual in wrong[:5]:
        print(f"  wrong: {sample_id} predicted={predicted} actual={actual}")
    return overrides


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--mode", choices=["full", "holdout", "subsample", "noid"], default="full")
    parser.add_argument("--keep-prob", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    samples = load_jsonl(os.path.join(args.data_dir, "train.jsonl"))
    labels = load_labels(args.data_dir)

    if args.mode == "full":
        report("full", samples, labels)
        return

    if args.mode == "noid":
        anonymized = []
        for idx, sample in enumerate(samples):
            clone = dict(sample)
            clone["id"] = f"row{idx:06d}"
            labels[clone["id"]] = labels[sample["id"]]
            anonymized.append(clone)
        report("noid/aligned-only", anonymized, labels)
        return

    session_of = {}
    for sample in samples:
        match = LEAK_STEP_RE.match(sample["id"])
        session_of[sample["id"]] = match.group("sess")

    rng = random.Random(args.seed)
    if args.mode == "holdout":
        sessions = sorted({session_of[s["id"]] for s in samples})
        half_a = set(rng.sample(sessions, len(sessions) // 2))
        train_half = [s for s in samples if session_of[s["id"]] in half_a]
        eval_half = [s for s in samples if session_of[s["id"]] not in half_a]
        lookup = build_lookup(train_half, labels)
        print(f"holdout: lookup from {len(train_half)} rows / eval on {len(eval_half)} rows")
        report("holdout/no-lookup", eval_half, labels)
        report("holdout/with-lookup", eval_half, labels, train_lookup=lookup)
        return

    kept = [s for s in samples if rng.random() < args.keep_prob]
    report(f"subsample p={args.keep_prob}", kept, labels)


if __name__ == "__main__":
    main()
