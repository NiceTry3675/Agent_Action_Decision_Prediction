#!/usr/bin/env python3
"""Score the Sonnet cross-family relabel against the preregistered criteria."""
import json
from collections import Counter
from pathlib import Path

OUT = Path(__file__).parent
ROOT = Path("/home/tomto/projects/Agent_Action_Decision_Prediction")
ART = ROOT / "experiments/artifacts/20260711_sonnet_relabel"

VALID = {"read_file", "grep_search", "list_directory", "glob_pattern",
         "edit_file", "write_file", "apply_patch", "run_bash", "run_tests",
         "lint_or_typecheck", "ask_user", "plan_task", "web_search",
         "respond_only", "underdetermined"}
WEAK6 = {"read_file", "grep_search", "list_directory", "glob_pattern",
         "underdetermined"}

def to6(label):
    """Map 14+underdetermined onto the original 6-label scheme."""
    return label if label in WEAK6 else "other"

manifest = json.load(open(OUT / "hidden_manifest.json"))
ann = {}
problems = []
for path in sorted((OUT / "results").glob("batch_*_out.jsonl")):
    for line in open(path):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        row = json.loads(line)
        sid = row["id"]
        if sid in ann:
            problems.append(f"duplicate {sid} in {path.name}")
        if row["top_label"] not in VALID:
            problems.append(f"invalid top_label {row['top_label']!r} ({sid})")
        bad = [l for l in row.get("acceptable_labels", []) if l not in VALID]
        if bad:
            problems.append(f"invalid acceptable {bad} ({sid})")
        ann[sid] = row
missing = sorted(set(manifest) - set(ann))
extra = sorted(set(ann) - set(manifest))
print(f"annotated={len(ann)} missing={len(missing)} extra={len(extra)} problems={len(problems)}")
for p in problems[:10]:
    print("  !", p)
if missing:
    print("  missing:", missing[:10])

def acc_set(row):
    s = set(row.get("acceptable_labels", []))
    s.add(row["top_label"])
    return s

summary = {"n_annotated": len(ann), "n_missing": len(missing), "problems": problems}

# ---- R1: stratum A, Sonnet vs GPT-5.6 (mapped to the original 6-label scheme) ----
A = [sid for sid, m in manifest.items() if m["stratum"] == "A" and sid in ann]
r1_exact = sum(to6(ann[sid]["top_label"]) == manifest[sid]["gpt56_label"] for sid in A)
r1_accept = sum(
    to6(ann[sid]["top_label"]) in {to6(l) for l in manifest[sid]["gpt56_acceptable"]}
    or manifest[sid]["gpt56_label"] in {to6(l) for l in acc_set(ann[sid])}
    for sid in A)
conf = Counter((to6(ann[sid]["top_label"]), manifest[sid]["gpt56_label"]) for sid in A)
a_ds_exact = sum(ann[sid]["top_label"] == manifest[sid]["official"] for sid in A)
a_ds_accept = sum(manifest[sid]["official"] in acc_set(ann[sid]) for sid in A)
summary["R1"] = {
    "n": len(A),
    "sonnet_vs_gpt56_exact": r1_exact, "sonnet_vs_gpt56_exact_pct": r1_exact / len(A),
    "sonnet_vs_gpt56_either_acceptable": r1_accept,
    "sonnet_vs_dataset_exact": a_ds_exact, "sonnet_vs_dataset_exact_pct": a_ds_exact / len(A),
    "sonnet_vs_dataset_acceptable": a_ds_accept,
    "gpt56_vs_dataset_exact_ref": "52/248 (21.0%) from the original report",
    "confusion_top": [
        {"sonnet6": a, "gpt56": b, "n": c} for (a, b), c in conf.most_common(15)],
}

# ---- R2: stratum B (student-correct weak4 control), Sonnet vs dataset ----
B = [sid for sid, m in manifest.items() if m["stratum"] == "B" and sid in ann]
b_exact = sum(ann[sid]["top_label"] == manifest[sid]["official"] for sid in B)
b_accept = sum(manifest[sid]["official"] in acc_set(ann[sid]) for sid in B)
by_cls = {}
for sid in B:
    cls = manifest[sid]["official"]
    d = by_cls.setdefault(cls, {"n": 0, "exact": 0, "acceptable": 0})
    d["n"] += 1
    d["exact"] += ann[sid]["top_label"] == cls
    d["acceptable"] += cls in acc_set(ann[sid])
summary["R2"] = {
    "n": len(B), "exact": b_exact, "exact_pct": b_exact / len(B),
    "acceptable": b_accept, "acceptable_pct": b_accept / len(B),
    "by_class": by_cls,
    "b_underdetermined": sum(ann[sid]["top_label"] == "underdetermined" for sid in B),
}

# ---- R3: stratum C (five-model unanimous-alt) ----
C = [sid for sid, m in manifest.items() if m["stratum"] == "C" and sid in ann]
c_alt = sum(ann[sid]["top_label"] == manifest[sid]["committee_alt"] for sid in C)
c_alt_acc = sum(manifest[sid]["committee_alt"] in acc_set(ann[sid]) for sid in C)
c_off = sum(ann[sid]["top_label"] == manifest[sid]["official"] for sid in C)
c_off_acc = sum(manifest[sid]["official"] in acc_set(ann[sid]) for sid in C)
c_und = sum(ann[sid]["identifiability"] == "underdetermined" for sid in C)
c_multi = sum(ann[sid]["identifiability"] == "plausible_multi" for sid in C)
summary["R3"] = {
    "n": len(C),
    "top_eq_committee_alt": c_alt, "top_eq_committee_alt_pct": c_alt / len(C),
    "committee_alt_acceptable": c_alt_acc,
    "top_eq_official": c_off, "official_acceptable": c_off_acc,
    "official_acceptable_pct": c_off_acc / len(C),
    "identifiability_underdetermined": c_und, "plausible_multi": c_multi,
    "und_or_multi_pct": (c_und + c_multi) / len(C),
}

# distribution sanity
summary["sonnet_label_dist"] = dict(Counter(a["top_label"] for a in ann.values()))
summary["identifiability_dist"] = dict(Counter(a["identifiability"] for a in ann.values()))
summary["confidence_dist"] = dict(Counter(a["confidence"] for a in ann.values()))

ART.mkdir(exist_ok=True)
with open(ART / "summary.json", "w") as f:
    json.dump(summary, f, ensure_ascii=False, indent=1)
merged = [{**ann[sid], **{f"hidden_{k}": v for k, v in manifest[sid].items()}}
          for sid in sorted(ann) if sid in manifest]
with open(ART / "sonnet_relabels.jsonl", "w") as f:
    for row in merged:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
print(json.dumps({k: summary[k] for k in ("R1", "R2", "R3")}, ensure_ascii=False, indent=1))
