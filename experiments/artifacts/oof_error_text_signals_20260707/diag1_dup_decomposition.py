"""Diagnostic 1: exact-normalized duplicate decomposition on train 70k.

Question: at the duplicate margin, how much label information does v1 drop?
Keys, coarse -> fine:
  K_prompt : normalized current_prompt only
  K_v1     : normalized full current_v1 serialized text (what the model sees)
  K_v1x    : K_v1 + info v1 DROPS (full result summaries, full action seq,
             all user turns, all args, all open_files) -- the ceiling for any
             "add info to serializer" v7
  K_raw    : canonical JSON of the whole sample -- simulator stochasticity floor

For each key: rows in multi-row groups, label-conflict share, and
majority-vote irreducible error (rows that even a perfect per-group
classifier gets wrong), overall and for the exploration cluster.
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "/home/tomto/projects/Agent_Action_Decision_Prediction")
from script import serialize_transformer_sample_current, safe_text

DATA = "/home/tomto/projects/Agent_Action_Decision_Prediction/open/data"
EXPLORE = {"read_file", "grep_search", "list_directory", "glob_pattern"}
WS_RE = re.compile(r"\s+")


def norm(text):
    return WS_RE.sub(" ", text.lower()).strip()


def dropped_info_key(s):
    """Everything current_v1 truncates away, canonicalized."""
    hist = s.get("history") or []
    sm = s.get("session_meta") or {}
    ws = sm.get("workspace") or {}
    users, actions, results, args = [], [], [], []
    for ev in hist:
        if ev.get("role") == "user":
            users.append(safe_text(ev.get("content", "")))
        elif ev.get("role") == "assistant_action":
            name = safe_text(ev.get("name"))
            actions.append(name)
            results.append(f"{name}:{safe_text(ev.get('result_summary'))}")  # no 120c cut
            a = ev.get("args") or {}
            if isinstance(a, dict):
                for k, v in a.items():  # no caps
                    args.append(f"{name}.{safe_text(k)}={safe_text(v)}")
    return norm(
        " || ".join(users)  # all user turns (v1 keeps last only)
        + " ## " + " > ".join(actions)  # full seq (v1 keeps last 8)
        + " ## " + " | ".join(results)  # full summaries (v1 cuts 120c, last 8)
        + " ## " + " | ".join(args)  # all args (v1 caps 4/action, last 10)
        + " ## " + " | ".join(safe_text(x) for x in (ws.get("open_files") or []))
    )


def stats(key_of, samples, labels):
    groups = defaultdict(list)
    for i, s in enumerate(samples):
        groups[key_of(i, s)].append(i)
    n = len(samples)
    multi = [idx for idx in groups.values() if len(idx) > 1]
    in_multi = sum(len(g) for g in multi)
    conflict_rows = 0
    irreducible = 0
    exp_in_multi = exp_conflict = exp_irred = 0
    for g in multi:
        labs = [labels[i] for i in g]
        c = Counter(labs)
        conflicted = len(c) > 1
        maj = c.most_common(1)[0][1]
        if conflicted:
            conflict_rows += len(g)
            irreducible += len(g) - maj
        for i in g:
            if labels[i] in EXPLORE:
                exp_in_multi += 1
                if conflicted:
                    exp_conflict += 1
        if conflicted:
            # irreducible rows attributed to explore if minority label in explore
            for lab, cnt in c.items():
                if lab != c.most_common(1)[0][0] and lab in EXPLORE:
                    exp_irred += cnt
    return {
        "groups_multi": len(multi),
        "rows_in_multi": in_multi,
        "rows_in_multi_pct": 100.0 * in_multi / n,
        "conflict_rows": conflict_rows,
        "conflict_share_of_multi_pct": 100.0 * conflict_rows / in_multi if in_multi else 0.0,
        "irreducible_rows": irreducible,
        "irreducible_pct_of_all": 100.0 * irreducible / n,
        "explore_rows_in_multi": exp_in_multi,
        "explore_conflict_pct": 100.0 * exp_conflict / exp_in_multi if exp_in_multi else 0.0,
        "explore_irreducible_rows": exp_irred,
    }


def main():
    labels_by_id = {}
    with open(f"{DATA}/train_labels.csv") as f:
        for row in csv.DictReader(f):
            labels_by_id[row["id"]] = row["action"]

    samples, labels = [], []
    with open(f"{DATA}/train.jsonl") as f:
        for line in f:
            s = json.loads(line)
            samples.append(s)
            labels.append(labels_by_id[s["id"]])

    v1_texts = [norm(serialize_transformer_sample_current(s)) for s in samples]
    prompts = [norm(safe_text(s.get("current_prompt", ""))) for s in samples]
    dropped = [dropped_info_key(s) for s in samples]

    keys = {
        "K_prompt (prompt only)": lambda i, s: prompts[i],
        "K_v1 (full v1 text)": lambda i, s: v1_texts[i],
        "K_v1x (v1 + dropped info)": lambda i, s: v1_texts[i] + " @@ " + dropped[i],
        "K_raw (entire sample)": lambda i, s: json.dumps(
            {k: v for k, v in s.items() if k != "id"}, sort_keys=True, ensure_ascii=False
        ),
    }
    print(f"total rows: {len(samples)}")
    for name, fn in keys.items():
        r = stats(fn, samples, labels)
        print(f"\n== {name} ==")
        for k, v in r.items():
            print(f"  {k}: {v:.2f}" if isinstance(v, float) else f"  {k}: {v}")


if __name__ == "__main__":
    main()
