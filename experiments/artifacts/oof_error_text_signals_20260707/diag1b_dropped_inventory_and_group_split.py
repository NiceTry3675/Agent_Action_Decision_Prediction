"""Diagnostic 1b.

Part A: inventory of information current_v1 drops (coverage per drop type).
Part B: within prompt-exact-dup conflicted groups, how much does splitting by
        state features reduce majority-vote error?
        F_v1  : features visible in v1 text (last action, last result bucket,
                dirty/ci, turn, last_user)
        F_v1x : F_v1 + features from info v1 drops (earlier user turns,
                deep action seq, full args/results)
        Reduction(F_v1) = info the model ALREADY receives (exploitation gap).
        Reduction(F_v1x) - Reduction(F_v1) = info a v7 could ADD.
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "/home/tomto/projects/Agent_Action_Decision_Prediction")
from script import safe_text

DATA = "/home/tomto/projects/Agent_Action_Decision_Prediction/open/data"
EXPLORE = {"read_file", "grep_search", "list_directory", "glob_pattern"}
WS_RE = re.compile(r"\s+")
NUM_RE = re.compile(r"\d+")


def norm(text):
    return WS_RE.sub(" ", text.lower()).strip()


def result_bucket(rs):
    if not rs:
        return "na"
    m = NUM_RE.search(rs)
    low = rs.lower()
    if m:
        n = int(m.group())
        if "exit=" in low:
            return "exit0" if "exit=0" in low else "exitN"
        if n == 0:
            return "zero"
        if n == 1:
            return "one"
        if n <= 5:
            return "few"
        return "many"
    if any(w in low for w in ("ok", "pass", "clean", "no issues")):
        return "ok"
    if any(w in low for w in ("fail", "error", "conflict")):
        return "fail"
    return "other"


def extract(s):
    hist = s.get("history") or []
    sm = s.get("session_meta") or {}
    ws = sm.get("workspace") or {}
    users, acts, results, args_n = [], [], [], []
    over120 = 0
    argkeys_dropped = 0
    for ev in hist:
        if ev.get("role") == "user":
            users.append(safe_text(ev.get("content", "")))
        elif ev.get("role") == "assistant_action":
            acts.append(safe_text(ev.get("name")))
            rs = safe_text(ev.get("result_summary"))
            results.append(rs)
            if len(rs) > 120:
                over120 += 1
            a = ev.get("args") or {}
            if isinstance(a, dict):
                args_n.append(len(a))
                if len(a) > 4:
                    argkeys_dropped += 1
    return {
        "users": users,
        "acts": acts,
        "results": results,
        "over120": over120,
        "argkeys_dropped": argkeys_dropped,
        "open_files": ws.get("open_files") or [],
        "dirty": ws.get("git_dirty"),
        "ci": ws.get("last_ci_status"),
        "turn": sm.get("turn_index"),
    }


def group_split_error(groups, labels, feat_of):
    """Majority-vote error after splitting each group by feature key."""
    total = wrong = 0
    for g in groups:
        sub = defaultdict(list)
        for i in g:
            sub[feat_of(i)].append(i)
        for idx in sub.values():
            c = Counter(labels[i] for i in idx)
            total += len(idx)
            wrong += len(idx) - c.most_common(1)[0][1]
    return wrong, total


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

    ex = [extract(s) for s in samples]

    n = len(samples)
    print("== Part A: what does v1 drop, and how often? ==")
    print(f"rows with >1 user turn (earlier turns dropped): "
          f"{sum(1 for e in ex if len(e['users']) > 1)} ({100*sum(1 for e in ex if len(e['users'])>1)/n:.1f}%)")
    print(f"rows with >8 actions (older actions dropped):   "
          f"{sum(1 for e in ex if len(e['acts']) > 8)} ({100*sum(1 for e in ex if len(e['acts'])>8)/n:.1f}%)")
    print(f"rows with >8 results (older results dropped):   "
          f"{sum(1 for e in ex if len([r for r in e['results'] if r]) > 8)}")
    print(f"rows with any result_summary >120 chars:        "
          f"{sum(1 for e in ex if e['over120'] > 0)}")
    print(f"rows with any action having >4 arg keys:        "
          f"{sum(1 for e in ex if e['argkeys_dropped'] > 0)}")
    print(f"rows with >6 open_files:                        "
          f"{sum(1 for e in ex if len(e['open_files']) > 6)}")
    print(f"rows with >10 total arg bits (older args cut):  "
          f"{sum(1 for e in ex if sum(min(4, a) for a in [1]*0) or False)}")

    # Part B
    prompts = [norm(safe_text(s.get("current_prompt", ""))) for s in samples]
    pgroups = defaultdict(list)
    for i, p in enumerate(prompts):
        pgroups[p].append(i)
    conflicted = []
    for g in pgroups.values():
        if len(g) > 1 and len(set(labels[i] for i in g)) > 1:
            conflicted.append(g)
    rows_c = sum(len(g) for g in conflicted)
    base_wrong = sum(len(g) - Counter(labels[i] for i in g).most_common(1)[0][1]
                     for g in conflicted)
    print(f"\n== Part B: prompt-dup conflicted groups ==")
    print(f"groups: {len(conflicted)}, rows: {rows_c}")
    print(f"baseline majority error (prompt-only ceiling): {base_wrong} ({100*base_wrong/rows_c:.1f}%)")

    def f_last_action(i):
        a = ex[i]["acts"]
        return a[-1] if a else "none"

    def f_last_result(i):
        r = [x for x in ex[i]["results"] if x]
        return result_bucket(r[-1]) if r else "na"

    def f_v1(i):
        e = ex[i]
        lu = norm(e["users"][-1]) if e["users"] else ""
        return (f_last_action(i), f_last_result(i), tuple(e["acts"][-8:]),
                str(e["dirty"]), str(e["ci"]), str(e["turn"]),
                tuple(norm(safe_text(x)) for x in e["open_files"][:6]), lu)

    def f_v1x(i):
        e = ex[i]
        return f_v1(i) + (tuple(norm(u) for u in e["users"]), tuple(e["acts"]),
                          tuple(norm(r) for r in e["results"]))

    for name, fn in [("split by last_action only", f_last_action),
                     ("split by last_action+result_bucket", lambda i: (f_last_action(i), f_last_result(i))),
                     ("split by F_v1 (all v1-visible state)", f_v1),
                     ("split by F_v1x (v1 + dropped info)", f_v1x)]:
        wrong, total = group_split_error(conflicted, labels, fn)
        print(f"{name:42s}: error {wrong} ({100*wrong/total:.1f}%)")

    # exploration-cluster-only view
    conf_ex = []
    for g in conflicted:
        gi = [i for i in g if labels[i] in EXPLORE]
        if len(gi) > 1 and len(set(labels[i] for i in gi)) > 1:
            conf_ex.append(gi)
    rows_e = sum(len(g) for g in conf_ex)
    base_e = sum(len(g) - Counter(labels[i] for i in g).most_common(1)[0][1] for g in conf_ex)
    print(f"\nexploration-cluster subset: groups {len(conf_ex)}, rows {rows_e}, "
          f"baseline error {base_e} ({100*base_e/rows_e:.1f}%)")
    for name, fn in [("split by last_action+result_bucket", lambda i: (f_last_action(i), f_last_result(i))),
                     ("split by F_v1", f_v1),
                     ("split by F_v1x", f_v1x)]:
        wrong, total = group_split_error(conf_ex, labels, fn)
        print(f"{name:42s}: error {wrong} ({100*wrong/total:.1f}%)")


if __name__ == "__main__":
    main()
