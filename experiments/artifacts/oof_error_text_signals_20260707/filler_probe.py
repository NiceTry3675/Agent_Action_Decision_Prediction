"""Does uniform '$' filler mechanically reproduce the attention role of
natural low-info tokens, on the deployed v1-trained model?

Conditions (same 48 rows):
  C1 v1          : anchor
  C2 v1-$meta    : meta line values replaced by a token-count-matched $-wall
  C3 v5+$pad     : v5 text + trailing $-wall pad line to match v1 token count

Measures: normalized attention entropy, mass absorbed by the meta/$ region
(all-queries and last-token views), first-token sink mass.
"""
import json
import random
import re
import sys

import numpy as np
import torch

sys.path.insert(0, "/home/tomto/projects/Agent_Action_Decision_Prediction")
sys.path.insert(0, "/tmp/claude-1000/-home-tomto-projects-Agent-Action-Decision-Prediction/8a7b8d42-0f2c-4348-82e1-f2a99d0362df/scratchpad")
from script import serialize_transformer_sample
from attn_sink_probe import MODEL_DIR, REPO

N = 48


def dollar_wall(tok, n_tokens):
    """Build a '$'-string totalling ~n_tokens tokens."""
    best = "$"
    for m in range(1, n_tokens * 4):
        s = "$" * m
        c = len(tok(s, add_special_tokens=False)["input_ids"])
        if c >= n_tokens:
            return s
        best = s
    return best


def region_spans(text, marker):
    """Char span of the line starting with marker."""
    for line in text.split("\n"):
        if line.startswith(marker):
            a = text.index(line)
            return a, a + len(line)
    return None


def probe(model, tok, text, region):
    enc = tok(text, return_offsets_mapping=True, truncation=True, max_length=384)
    ids = torch.tensor([enc["input_ids"]])
    with torch.no_grad():
        out = model(input_ids=ids, output_attentions=True)
    att = torch.stack(out.attentions).mean(dim=(0, 2))[0].float().numpy()
    L = att.shape[0]
    ents = []
    for q in range(3, L):
        p = att[q, : q + 1]
        p = p / p.sum()
        ents.append(-(p * np.log(p + 1e-12)).sum() / np.log(q + 1))
    mask = np.zeros(L, bool)
    if region:
        a, b = region
        for i, (s, e) in enumerate(enc["offset_mapping"]):
            if e > a and s < b:
                mask[i] = True
    recv_all = att.mean(axis=0)
    return {
        "L": L,
        "entropy": float(np.mean(ents)),
        "sink": float(recv_all[0]),
        "region_mass_all": float(recv_all[mask].sum()),
        "region_mass_last": float(att[-1][mask].sum()),
        "region_ntok": int(mask.sum()),
    }


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

    res = {"C1": [], "C2": [], "C3": []}
    for i, s in enumerate(sub):
        v1 = serialize_transformer_sample(s, "current_v1")
        v5 = serialize_transformer_sample(s, "current_v5")
        lines = v1.split("\n")
        meta_line = next(l for l in lines if l.startswith("meta: "))
        meta_content = meta_line[len("meta: "):]
        n_meta = len(tok(meta_content, add_special_tokens=False)["input_ids"])
        c2_text = v1.replace(meta_line, "meta: " + dollar_wall(tok, n_meta))
        n_v1 = len(tok(v1)["input_ids"])
        n_v5 = len(tok(v5)["input_ids"])
        pad = dollar_wall(tok, max(1, n_v1 - n_v5 - 3))
        c3_text = v5 + "\npad: " + pad

        res["C1"].append(probe(model, tok, v1, region_spans(v1, "meta: ")))
        res["C2"].append(probe(model, tok, c2_text, region_spans(c2_text, "meta: ")))
        res["C3"].append(probe(model, tok, c3_text, region_spans(c3_text, "pad: ")))
        if (i + 1) % 16 == 0:
            print(f"  {i+1}/{N}", flush=True)

    def m(cond, key):
        return float(np.mean([r[key] for r in res[cond]]))

    print(f"\n{'cond':22s} {'L':>5s} {'entropy':>8s} {'sink':>6s} {'region(all)':>11s} {'region(last)':>12s} {'reg.ntok':>8s}")
    names = {"C1": "v1 (meta natural)", "C2": "v1 ($-meta)", "C3": "v5 + $-pad"}
    for c in ("C1", "C2", "C3"):
        print(f"{names[c]:22s} {m(c,'L'):5.0f} {m(c,'entropy'):8.4f} {m(c,'sink'):6.3f} "
              f"{m(c,'region_mass_all'):11.3f} {m(c,'region_mass_last'):12.3f} {m(c,'region_ntok'):8.1f}")
    d21 = [a["entropy"] - b["entropy"] for a, b in zip(res["C2"], res["C1"])]
    d31 = [a["entropy"] - b["entropy"] for a, b in zip(res["C3"], res["C1"])]
    print(f"\npaired entropy delta C2-C1: {np.mean(d21):+.4f} (flatter rows: {sum(x>0 for x in d21)}/{N})")
    print(f"paired entropy delta C3-C1: {np.mean(d31):+.4f} (flatter rows: {sum(x>0 for x in d31)}/{N})")
    # v5 without pad for reference (from earlier run: +0.0243). Recompute quickly here:
    ents5 = []
    for s in sub[:16]:
        v5 = serialize_transformer_sample(s, "current_v5")
        ents5.append(probe(model, tok, v5, None)["entropy"])
    print(f"v5 bare entropy (16-row ref): {np.mean(ents5):.4f} vs C1 {m('C1','entropy'):.4f}")


if __name__ == "__main__":
    main()
