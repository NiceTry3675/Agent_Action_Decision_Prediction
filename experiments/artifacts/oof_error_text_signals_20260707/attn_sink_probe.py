"""Attention-sink probe on the deployed kd_m8_refit HCX-0.5B student.

Questions:
  1. Does the v1-trained model park attention mass on low-information tokens
     (first token, meta-line numerals budget/elapsed/loc, newlines, field
     labels)? -> premise of the "filler tokens matter" hypothesis.
  2. On the same rows serialized as v5 (filler removed), does attention get
     flatter (higher normalized entropy) over the remaining tokens?
     (Caveat: v1-trained model on v5 text is OOD — mechanical probe only.)

CPU fp32, eager attention, one row at a time. N=64 rows.
"""
import json
import random
import re
import sys

import numpy as np
import torch

sys.path.insert(0, "/home/tomto/projects/Agent_Action_Decision_Prediction")
from script import serialize_transformer_sample  # noqa: E402

REPO = "/home/tomto/projects/Agent_Action_Decision_Prediction"
MODEL_DIR = f"{REPO}/experiments/incoming/models/kd_m8_refit/hf_model"
N_ROWS = 64

LINE_CATS = ("current", "meta", "workspace", "actions", "last_user", "args", "results", "state")
NOISE_SPAN_RE = re.compile(r"(?:budget|elapsed|loc)=\S+")


def token_categories(text, enc):
    """Per-token category via offset mapping."""
    offsets = enc["offset_mapping"]
    line_spans = []
    pos = 0
    for line in text.split("\n"):
        start, end = pos, pos + len(line)
        prefix = line.split(":", 1)[0] if ":" in line else ""
        line_spans.append((start, end, prefix if prefix in LINE_CATS else "other"))
        pos = end + 1
    noise_spans = [m.span() for m in NOISE_SPAN_RE.finditer(text)]
    cats, noise_mask, nl_mask = [], [], []
    for i, (a, b) in enumerate(offsets):
        if b <= a:
            cats.append("special"); noise_mask.append(False); nl_mask.append(False)
            continue
        cat = "other"
        for s, e, c in line_spans:
            if a >= s and a < e:
                cat = c
                break
        else:
            if text[a:b].strip() == "":
                cat = "newline"
        if text[a:b] == "\n" or (a < len(text) and text[a] == "\n"):
            cat = "newline"
        cats.append(cat)
        noise_mask.append(any(a < e and b > s for s, e in noise_spans))
        nl_mask.append(cat == "newline")
    return cats, np.array(noise_mask), np.array(nl_mask)


def probe_row(model, tok, text):
    enc = tok(text, return_offsets_mapping=True, return_tensors=None, truncation=True, max_length=384)
    ids = torch.tensor([enc["input_ids"]])
    with torch.no_grad():
        out = model(input_ids=ids, output_attentions=True)
    # average attention over layers and heads -> [L, L] (rows=queries)
    att = torch.stack(out.attentions).mean(dim=(0, 2))[0].float().numpy()
    L = att.shape[0]
    cats, noise_mask, nl_mask = token_categories(text, enc)
    # received mass per key token: average over all queries (causal: query i sees keys <= i)
    recv_all = att.mean(axis=0)          # avg over queries
    recv_last = att[-1]                   # the classification token's view
    # normalized entropy per query (skip first 3 queries, tiny contexts)
    ents = []
    for q in range(3, L):
        p = att[q, : q + 1]
        p = p / p.sum()
        ents.append(-(p * np.log(p + 1e-12)).sum() / np.log(q + 1))
    res = {"L": L, "entropy": float(np.mean(ents)),
           "first_tok_all": float(recv_all[0]), "first_tok_last": float(recv_last[0]),
           "noise_all": float(recv_all[noise_mask].sum()) if noise_mask.any() else 0.0,
           "noise_last": float(recv_last[noise_mask].sum()) if noise_mask.any() else 0.0,
           "noise_ntok": int(noise_mask.sum()),
           "newline_all": float(recv_all[nl_mask].sum()) if nl_mask.any() else 0.0}
    by_line_all, by_line_last = {}, {}
    for c in set(cats):
        m = np.array([x == c for x in cats])
        by_line_all[c] = float(recv_all[m].sum())
        by_line_last[c] = float(recv_last[m].sum())
    res["by_line_all"] = by_line_all
    res["by_line_last"] = by_line_last
    return res


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
    random.seed(7)
    sub = random.sample(rows, N_ROWS)

    agg = {"v1": [], "v5": []}
    for i, s in enumerate(sub):
        for ser in ("v1", "v5"):
            text = serialize_transformer_sample(s, f"current_{ser}")
            agg[ser].append(probe_row(model, tok, text))
        if (i + 1) % 16 == 0:
            print(f"  {i+1}/{N_ROWS} rows done", flush=True)

    def mean(key, ser):
        return float(np.mean([r[key] for r in agg[ser]]))

    print("\n== v1 (deployed serializer): where does attention mass sit? ==")
    print(f"tokens/row: {mean('L','v1'):.0f}")
    print(f"first-token mass:        all-queries {mean('first_tok_all','v1'):.3f} | last-token-query {mean('first_tok_last','v1'):.3f}")
    print(f"budget/elapsed/loc mass: all-queries {mean('noise_all','v1'):.3f} | last-token-query {mean('noise_last','v1'):.3f} "
          f"(avg {mean('noise_ntok','v1'):.1f} tokens = {100*mean('noise_ntok','v1')/mean('L','v1'):.1f}% of tokens)")
    print(f"newline/whitespace mass: all-queries {mean('newline_all','v1'):.3f}")
    lines_all = {}
    for r in agg["v1"]:
        for c, v in r["by_line_all"].items():
            lines_all.setdefault(c, []).append(v)
    print("received mass by line (all-queries avg):")
    for c, vs in sorted(lines_all.items(), key=lambda kv: -np.mean(kv[1])):
        print(f"  {c:10s} {np.mean(vs):.3f}")

    print("\n== v1 vs v5: attention flatness (normalized entropy, same rows) ==")
    print(f"v1: L={mean('L','v1'):.0f}  entropy={mean('entropy','v1'):.4f}")
    print(f"v5: L={mean('L','v5'):.0f}  entropy={mean('entropy','v5'):.4f}")
    d = [a["entropy"] - b["entropy"] for a, b in zip(agg["v5"], agg["v1"])]
    print(f"paired delta v5-v1: mean {np.mean(d):+.4f}, rows flatter in v5: {sum(x>0 for x in d)}/{N_ROWS}")
    print(f"v5 first-token mass: {mean('first_tok_all','v5'):.3f} (vs v1 {mean('first_tok_all','v1'):.3f})")


if __name__ == "__main__":
    main()
