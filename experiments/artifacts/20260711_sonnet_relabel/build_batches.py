#!/usr/bin/env python3
"""Assemble blinded annotation batches for the Sonnet cross-family relabel.

Strata (hidden from the annotator):
  A: the original 248 casebook rows (GPT-5.6 relabel replication target)
  B: control — kd_hcx_m8_screen fixed-val weak4 rows the student got RIGHT
     (30 per weak class, the control the original experiment lacked)
  C: c=0 five-model unanimous-alternative rows (committee M7/M8/v6 OOF +
     gemma/llama train logits all agree on the same non-official label)

All rows are rebuilt uniformly from train.jsonl with FULL session_meta
(the original inputs trimmed it; deviation noted in the design doc).
Output: shuffled batches without labels + a hidden manifest with labels.
"""
import json
import random
from collections import Counter
from pathlib import Path

import torch

ROOT = Path("/home/tomto/projects/Agent_Action_Decision_Prediction")
OUT = Path(__file__).parent
BATCH_DIR = OUT / "batches"
BATCH_DIR.mkdir(exist_ok=True)
WEAK4 = ["list_directory", "read_file", "grep_search", "glob_pattern"]
SEED = 42
BATCH_SIZE = 16

base = torch.load(ROOT / "experiments/artifacts/20260710_m7_m8_v6_oof_consensus.pt",
                  map_location="cpu", weights_only=False)
CLASSES = list(base["classes"])
ids = [str(v) for v in base["ids"]]
y = base["y_true"]
c = base["correct_counts"]
pos = {sid: i for i, sid in enumerate(ids)}
n = len(ids)

report = json.load(open(ROOT / "experiments/artifacts/20260710_m7_m8_v6_oof_consensus.json"))

def oof_preds(source):
    pred = torch.full((n,), -1, dtype=torch.long)
    for entry in source["files"]:
        pack = torch.load(ROOT / entry["path"], map_location="cpu", weights_only=False)
        assert list(pack["classes"]) == CLASSES
        p = pack["logits"].float().argmax(-1)
        for i, sid in enumerate(pack["ids"]):
            pred[pos[str(sid)]] = p[i]
    assert int((pred < 0).sum()) == 0
    return pred

def train_preds(fname):
    pack = torch.load(ROOT / "teacher_archive_v21_20260710" / fname,
                      map_location="cpu", weights_only=False)
    assert list(pack["classes"]) == CLASSES
    p = pack["logits"].float().argmax(-1)
    pred = torch.full((n,), -1, dtype=torch.long)
    for i, sid in enumerate(pack["ids"]):
        pred[pos[str(sid)]] = p[i]
    return pred

m7, m8, v6 = (oof_preds(s) for s in report["sources"])
gem = train_preds("teacher_gemma_bf16_fixpool_ep3_train70k_fp16.pt")
lla = train_preds("teacher_llama_train70k_fp16.pt")

c0 = c == 0
unan5 = c0 & (m7 == m8) & (m8 == v6) & (gem == m8) & (lla == m8)
print(f"five-model unanimous-alt rows: {int(unan5.sum())}")

# --- Stratum A: original 248 ids + GPT-5.6 final labels ---
a_ids = [json.loads(line)["id"] for line in
         open(ROOT / "experiments/artifacts/20260710_human_relabel/inputs/blinded_unique.jsonl")]
gpt = {}
for line in open(ROOT / "experiments/artifacts/20260710_human_relabel/human_relabels_final.jsonl"):
    row = json.loads(line)
    gpt[row["id"]] = {
        "gpt56_label": row["human_label"],
        "gpt56_acceptable": row["acceptable_labels"],
        "gpt56_identifiability": row["identifiability"],
        "gpt56_confidence": row["confidence"],
    }
assert set(a_ids) == set(gpt), "id mismatch vs final relabels"

# --- Stratum B: student-correct weak4 val rows, 30 per class ---
cache = torch.load(ROOT / "experiments/cache/kd_hcx_m8_screen_cache.pt",
                   map_location="cpu", weights_only=False)
cache_ids = [str(v) for v in cache["ids"]]
val_rows = [int(v) for v in cache["val_indices"].tolist()]
pred_ok = cache["parent_logits"].argmax(dim=-1) == cache["y_true"]
rng = random.Random(SEED)
a_set = set(a_ids)
b_pool = {cls: [] for cls in WEAK4}
for i in val_rows:
    sid = cache_ids[i]
    cls = CLASSES[int(cache["y_true"][i])]
    if cls in b_pool and bool(pred_ok[i]) and sid not in a_set:
        b_pool[cls].append(sid)
b_ids = []
for cls in WEAK4:
    pool = sorted(b_pool[cls])
    rng.shuffle(pool)
    b_ids.extend(pool[:30])
    print(f"B/{cls}: pool={len(pool)} took=30")

# --- Stratum C: 120 unanimous-alt rows, proportional by official label ---
used = a_set | set(b_ids)
c_pool = [i for i in range(n) if bool(unan5[i]) and ids[i] not in used]
by_cls = {}
for i in c_pool:
    by_cls.setdefault(CLASSES[int(y[i])], []).append(i)
total = len(c_pool)
c_rows = []
for cls, rows in sorted(by_cls.items(), key=lambda kv: -len(kv[1])):
    take = max(1, round(120 * len(rows) / total))
    rows = sorted(rows, key=lambda i: ids[i])
    rng.shuffle(rows)
    c_rows.extend(rows[:take])
c_rows = c_rows[:120]
c_ids = [ids[i] for i in c_rows]
print(f"C: {len(c_ids)} rows, official-label dist:",
      Counter(CLASSES[int(y[i])] for i in c_rows).most_common())

# --- Build blinded rows from train.jsonl ---
want = a_set | set(b_ids) | set(c_ids)
rows_by_id = {}
for line in open(ROOT / "open/data/train.jsonl"):
    row = json.loads(line)
    if row["id"] in want:
        rows_by_id[row["id"]] = {
            "id": row["id"],
            "current_prompt": row["current_prompt"],
            "history": row["history"],
            "session_meta": row["session_meta"],
        }
missing = want - set(rows_by_id)
assert not missing, f"missing {len(missing)} ids in train.jsonl"

manifest = {}
for sid in a_ids:
    manifest[sid] = {"stratum": "A", "official": CLASSES[int(y[pos[sid]])],
                     "c": int(c[pos[sid]]), **gpt[sid]}
for sid in b_ids:
    manifest[sid] = {"stratum": "B", "official": CLASSES[int(y[pos[sid]])],
                     "c": int(c[pos[sid]])}
for sid in c_ids:
    i = pos[sid]
    manifest[sid] = {"stratum": "C", "official": CLASSES[int(y[i])],
                     "c": int(c[i]), "committee_alt": CLASSES[int(m8[i])]}

all_ids = sorted(want)
rng2 = random.Random(SEED + 1)
rng2.shuffle(all_ids)
batches = [all_ids[i:i + BATCH_SIZE] for i in range(0, len(all_ids), BATCH_SIZE)]
for bi, batch in enumerate(batches):
    with open(BATCH_DIR / f"batch_{bi:02d}.jsonl", "w") as f:
        for sid in batch:
            f.write(json.dumps(rows_by_id[sid], ensure_ascii=False) + "\n")
with open(OUT / "hidden_manifest.json", "w") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=1)
print(f"total rows={len(all_ids)} batches={len(batches)} (size {BATCH_SIZE})")
print("strata:", Counter(m["stratum"] for m in manifest.values()))
