"""Where does the classification token (last token, Llama seq-cls pooling)
actually look? By-line view + distance locality, v1 serializer, 48 rows.
Decides the v7 tail-echo question."""
import json
import random
import sys

import numpy as np
import torch

sys.path.insert(0, "/home/tomto/projects/Agent_Action_Decision_Prediction")
sys.path.insert(0, "/tmp/claude-1000/-home-tomto-projects-Agent-Action-Decision-Prediction/8a7b8d42-0f2c-4348-82e1-f2a99d0362df/scratchpad")
from script import serialize_transformer_sample
from attn_sink_probe import token_categories, MODEL_DIR, REPO

N = 48


def main():
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_DIR, torch_dtype=torch.float32, attn_implementation="eager", num_labels=14)
    model.eval()

    rows = []
    with open(f"{REPO}/open/data/train.jsonl") as fh:
        for line in fh:
            rows.append(json.loads(line))
    random.seed(11)
    sub = random.sample(rows, N)

    by_line, dist_buckets = {}, {"last10": [], "last20": [], "last40": [], "first1": [], "rest": []}
    deep_by_line = {}
    for i, s in enumerate(sub):
        text = serialize_transformer_sample(s, "current_v1")
        enc = tok(text, return_offsets_mapping=True, truncation=True, max_length=384)
        ids = torch.tensor([enc["input_ids"]])
        with torch.no_grad():
            out = model(input_ids=ids, output_attentions=True)
        # last-token query, averaged over layers/heads; also deep layers (last 8) only
        att_all = torch.stack(out.attentions).mean(dim=(0, 2))[0, -1].float().numpy()
        att_deep = torch.stack(out.attentions[-8:]).mean(dim=(0, 2))[0, -1].float().numpy()
        L = len(att_all)
        cats, _, _ = token_categories(text, enc)
        for c in set(cats):
            m = np.array([x == c for x in cats])
            by_line.setdefault(c, []).append(att_all[m].sum())
            deep_by_line.setdefault(c, []).append(att_deep[m].sum())
        dist_buckets["first1"].append(att_all[0])
        dist_buckets["last10"].append(att_all[-10:].sum())
        dist_buckets["last20"].append(att_all[-20:].sum())
        dist_buckets["last40"].append(att_all[-40:].sum())
        dist_buckets["rest"].append(att_all[1:-40].sum() if L > 41 else 0.0)
        if (i + 1) % 16 == 0:
            print(f"  {i+1}/{N}", flush=True)

    print("\nlast-token query, received mass by line (all layers | deep 8 layers):")
    for c in sorted(by_line, key=lambda k: -np.mean(by_line[k])):
        print(f"  {c:10s} {np.mean(by_line[c]):.3f} | {np.mean(deep_by_line[c]):.3f}")
    print("\nlast-token query, mass by position:")
    for k in ("first1", "last10", "last20", "last40", "rest"):
        print(f"  {k:7s} {np.mean(dist_buckets[k]):.3f}")


if __name__ == "__main__":
    main()
