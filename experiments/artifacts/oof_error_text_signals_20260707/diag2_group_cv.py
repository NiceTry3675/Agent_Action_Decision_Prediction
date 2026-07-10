"""Diagnostic 2: on OOF confusion-boundary rows, do normalized state/text
features carry label signal BEYOND the model's own logits?

Population per pair: the extraction jsonl (bidirectional wrong rows + hard-
correct contrast rows). Binary target: true == class_a.
Models (LogisticRegression, 5-fold stratified CV):
  M  : model-side only (pre-rule logit diff of the pair classes, margin)
  M+S: model-side + state/text features
  S  : state/text only
If AUC(M+S) - AUC(M) ~ 0, the model already extracts everything these
features encode -> representation-aid v7 and gated rules are both dead.
"""
import json
import re
import sys
from collections import defaultdict

import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, "/home/tomto/projects/Agent_Action_Decision_Prediction")
from script import safe_text

ART = ("/home/tomto/projects/Agent_Action_Decision_Prediction/experiments/"
       "artifacts/oof_error_text_signals_20260707")
DATA = "/home/tomto/projects/Agent_Action_Decision_Prediction/open/data"

PAIRS = [
    ("read_file", "grep_search"),
    ("list_directory", "read_file"),
    ("list_directory", "grep_search"),
    ("glob_pattern", "read_file"),
    ("glob_pattern", "list_directory"),
    ("glob_pattern", "grep_search"),
    ("run_bash", "run_tests"),
    ("run_bash", "lint_or_typecheck"),
    ("run_tests", "lint_or_typecheck"),
]

NUM_RE = re.compile(r"\d+")
SLASH_PATH_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
BARE_FILE_RE = re.compile(r"\b[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,8}\b")
WILDCARD_RE = re.compile(r"\*|\bglob\b|패턴|\bpattern\b|\bmatching\b")
QUOTED_RE = re.compile(r"['\"`][^'\"`]{2,60}['\"`]")
SYMBOL_RE = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b|\b[a-z0-9]+_[a-z0-9_]+\b|\w+\(\)")
VIEW_RE = re.compile(r"\b(open|read|show|view|cat|pull up|look at)\b|열어|보여|까보|봐보|읽어")
USAGE_RE = re.compile(r"\b(usage|usages|reference|references|occurrence|used|called|caller|defined|definition)\b"
                      r"|어디서|쓰는지|호출|정의|참조|사용처|어디에")
INVENTORY_RE = re.compile(r"\b(tree|contents|folder|directory|structure|under|inside)\b"
                          r"|구조|목록|뭐뭐|뭐 있|펼쳐|디렉토리|폴더|루트|밑에")
FILESET_RE = re.compile(r"\b(all|every)\b.{0,20}\bfiles\b|files? scattered|전부|모든 파일|싹|흩어|어디어디")
TEST_RE = re.compile(r"\b(test|tests|suite|spec|regression)\b|테스트|스위트|리그레션")
LINT_RE = re.compile(r"\b(lint|typecheck|type-check|static|unused|format|type hints?)\b|린트|타입체크|정적|포맷")
LITCMD_RE = re.compile(r"\b(npm run|npx|pytest|jest|vitest|go test|cargo|tsc|mypy|ruff|eslint|python|node|bash|pip install|docker|make|uvicorn|dbt|airflow)\b")
RERUN_RE = re.compile(r"\b(again|rerun|re-run|once more)\b|다시|한번 더|한 번 더|방금 그|아까 그|재실행")


def result_bucket(rs):
    if not rs:
        return "na"
    low = rs.lower()
    m = NUM_RE.search(rs)
    if "exit=" in low:
        return "exit0" if "exit=0" in low else "exitN"
    if m:
        n = int(m.group())
        return "zero" if n == 0 else "one" if n == 1 else "few" if n <= 5 else "many"
    if any(w in low for w in ("ok", "pass", "clean", "no issues")):
        return "ok"
    if any(w in low for w in ("fail", "error", "conflict")):
        return "fail"
    return "other"


def state_features(sample):
    prompt = safe_text(sample.get("current_prompt", ""))
    hist = sample.get("history") or []
    sm = sample.get("session_meta") or {}
    ws = sm.get("workspace") or {}
    acts, results, arg_vals = [], [], []
    for ev in hist:
        if ev.get("role") == "assistant_action":
            acts.append(safe_text(ev.get("name")))
            results.append(safe_text(ev.get("result_summary")))
            a = ev.get("args") or {}
            if isinstance(a, dict):
                for v in a.values():
                    arg_vals.append(safe_text(v).lower())
    open_files = [safe_text(x) for x in (ws.get("open_files") or [])]
    plow = prompt.lower()
    f = {
        "last_action=" + (acts[-1] if acts else "none"): 1,
        "prev_action=" + (acts[-2] if len(acts) > 1 else "none"): 1,
        "last_result=" + (result_bucket(results[-1]) if results else "na"): 1,
        "ci=" + str(ws.get("last_ci_status")): 1,
        "dirty=" + str(ws.get("git_dirty")): 1,
    }
    flags = {
        "slash_path": SLASH_PATH_RE.search(prompt),
        "bare_file": BARE_FILE_RE.search(prompt),
        "wildcard": WILDCARD_RE.search(plow),
        "quoted": QUOTED_RE.search(prompt),
        "symbol": SYMBOL_RE.search(prompt),
        "view": VIEW_RE.search(plow),
        "usage": USAGE_RE.search(plow),
        "inventory": INVENTORY_RE.search(plow),
        "fileset": FILESET_RE.search(plow),
        "test_w": TEST_RE.search(plow),
        "lint_w": LINT_RE.search(plow),
        "litcmd": LITCMD_RE.search(plow),
        "rerun": RERUN_RE.search(plow),
    }
    for k, v in flags.items():
        f[k] = 1.0 if v else 0.0
    base_open = {of.rsplit("/", 1)[-1].lower() for of in open_files}
    f["overlap_open"] = 1.0 if any(b and b in plow for b in base_open) else 0.0
    f["overlap_args"] = 1.0 if any(v and len(v) > 3 and v in plow for v in arg_vals[-8:]) else 0.0
    f["n_actions"] = float(len(acts))
    return f


def model_features(row, a, b):
    scores = dict(zip(row["base_top5_before_rules"], row["base_top5_scores_before_rules"]))
    floor = min(row["base_top5_scores_before_rules"]) - 1.0
    return {
        "logit_diff": scores.get(a, floor) - scores.get(b, floor),
        "base_margin": row["base_margin_top1_top2"],
    }


def cv_auc(X_dicts, y, groups, folds=5):
    vec = DictVectorizer(sparse=False)
    X = vec.fit_transform(X_dicts)
    y = np.asarray(y)
    skf = GroupKFold(n_splits=folds)
    aucs, accs = [], []
    for tr, te in skf.split(X, y, groups):
        clf = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
        clf.fit(X[tr], y[tr])
        p = clf.predict_proba(X[te])[:, 1]
        aucs.append(roc_auc_score(y[te], p))
        accs.append(((p > 0.5).astype(int) == y[te]).mean())
    return float(np.mean(aucs)), float(np.mean(accs))


def main():
    train = {}
    with open(f"{DATA}/train.jsonl") as fh:
        for line in fh:
            s = json.loads(line)
            train[s["id"]] = s

    print(f"{'pair':38s} {'n':>6s} {'wrong%':>7s} | {'AUC M':>6s} {'M+S':>6s} {'S':>6s} | {'ACC M':>6s} {'M+S':>6s} {'dAUC':>7s}")
    agg = []
    for a, b in PAIRS:
        path = f"{ART}/{a}__{b}.jsonl"
        rows = []
        try:
            with open(path) as fh:
                for line in fh:
                    rows.append(json.loads(line))
        except FileNotFoundError:
            print(f"{a}__{b}: file missing, skipped")
            continue
        Xd, y, wrong, grp = [], [], 0, []
        for r in rows:
            if r["true_label"] not in (a, b):
                continue
            s = train.get(r["id"])
            if s is None:
                continue
            feats = model_features(r, a, b)
            feats.update(state_features(s))
            Xd.append(feats)
            y.append(1 if r["true_label"] == a else 0)
            grp.append(r["id"].rsplit("-step", 1)[0])
            if not r["is_correct"]:
                wrong += 1
        if len(set(y)) < 2:
            continue
        mkeys = {"logit_diff", "base_margin"}
        Xm = [{k: v for k, v in d.items() if k in mkeys} for d in Xd]
        Xs = [{k: v for k, v in d.items() if k not in mkeys} for d in Xd]
        auc_m, acc_m = cv_auc(Xm, y, grp)
        auc_ms, acc_ms = cv_auc(Xd, y, grp)
        auc_s, _ = cv_auc(Xs, y, grp)
        d = auc_ms - auc_m
        agg.append((f"{a}__{b}", len(y), d, acc_ms - acc_m))
        print(f"{a+'__'+b:38s} {len(y):6d} {100*wrong/len(y):6.1f}% | "
              f"{auc_m:.3f} {auc_ms:.3f} {auc_s:.3f} | {acc_m:.3f} {acc_ms:.3f} {d:+.4f}")

    print("\nweighted mean dAUC:",
          f"{sum(n*d for _, n, d, _ in agg)/sum(n for _, n, _, _ in agg):+.4f}")
    print("weighted mean dACC:",
          f"{sum(n*da for _, n, _, da in agg)/sum(n for _, n, _, _ in agg):+.4f}")


if __name__ == "__main__":
    main()
