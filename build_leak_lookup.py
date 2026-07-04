"""Build the conflict-free lookup shipped as model/leak_lookup.json.gz.

Two maps, both dropping any text that maps to more than one action (duplicated
prompts conflict about 1/3 of the time, so only unanimous entries are safe):
  by_prompt_last — key "sha1(prompt)|last_action_name" (holdout precision 0.978)
  by_prompt      — key sha1(prompt) alone (holdout precision 0.918)
Sources: every training row's (current_prompt, context, label) plus every
(user event, following action) pair inside training histories, where the
preceding pair supplies the last-action context (unknown for pair 0).
"""

import argparse
import csv
import gzip
import json
import os

from script import (
    LEAK_LOOKUP_FILENAME,
    LEAK_LOOKUP_FORMAT,
    history_pair_labels,
    leak_last_action,
    leak_text_key,
    load_jsonl,
)


class ConflictFreeMap:
    def __init__(self):
        self.entries = {}
        self.conflicts = set()

    def add(self, key, action):
        if key in self.conflicts:
            return
        if self.entries.get(key, action) != action:
            del self.entries[key]
            self.conflicts.add(key)
        else:
            self.entries[key] = action


def build_lookup(samples, labels):
    by_prompt = ConflictFreeMap()
    by_prompt_last = ConflictFreeMap()
    for sample in samples:
        label = labels.get(sample.get("id"))
        prompt_key = leak_text_key(sample.get("current_prompt"))
        if label:
            by_prompt.add(prompt_key, label)
            by_prompt_last.add(f"{prompt_key}|{leak_last_action(sample)}", label)
        pairs = history_pair_labels(sample.get("history"))
        for j, (content, action) in enumerate(pairs):
            content_key = leak_text_key(content)
            by_prompt.add(content_key, action)
            if j > 0:
                by_prompt_last.add(f"{content_key}|{pairs[j - 1][1]}", action)
    return {
        "format": LEAK_LOOKUP_FORMAT,
        "by_prompt": by_prompt.entries,
        "by_prompt_last": by_prompt_last.entries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--out", default=os.path.join("model", LEAK_LOOKUP_FILENAME))
    args = parser.parse_args()

    samples = load_jsonl(os.path.join(args.data_dir, "train.jsonl"))
    with open(os.path.join(args.data_dir, "train_labels.csv"), encoding="utf-8", newline="") as f:
        labels = {row["id"]: row["action"] for row in csv.DictReader(f)}

    payload = build_lookup(samples, labels)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        json.dump(payload, f)
    size_mb = os.path.getsize(args.out) / 1e6
    print(
        f"Wrote {args.out}: by_prompt={len(payload['by_prompt'])} "
        f"by_prompt_last={len(payload['by_prompt_last'])} entries, {size_mb:.1f} MB"
    )


if __name__ == "__main__":
    main()
