"""Inject tuned rule/sparse artifacts into a pulled val-model dir for packaging.

Completes the Public-gated stack assembly (script.py chain:
logits + hf_meta.class_bias -> rule_boosts -> + w*sparse + sparse_meta.class_bias):

- hf_meta.json gains "rule_boosts" from the rules artifact (its own 2-stage
  class_bias stays -- the rules were tuned on top of it).
- sparse_meta.json is written into the model dir: a copy of --sparse-meta-src
  with sparse_weight/class_bias/retune_* replaced from the sparse artifact.
- sparse_svc.pkl (encoder-independent, full-data fit) is copied alongside, so
  `package_submission.py --hf-dir <dir> --sparse-dir <dir>` picks everything up.

Usage:
    .venv/bin/python inject_stack_meta.py --model-dir experiments/incoming/models/NAME \
        --rule-artifact experiments/artifacts/..._rule_boosts.json \
        --sparse-artifact experiments/artifacts/..._sparse_svc.json
"""
import argparse
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--rule-artifact", required=True)
    parser.add_argument("--sparse-artifact", default="",
                        help="omit to package rules-only (no sparse blend)")
    parser.add_argument("--sparse-meta-src", default="model/sparse_meta.json")
    parser.add_argument("--sparse-pkl-src", default="model/sparse_svc.pkl")
    args = parser.parse_args()

    model_dir = Path(args.model_dir)
    meta_path = model_dir / "hf_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    rules = json.loads(Path(args.rule_artifact).read_text(encoding="utf-8"))
    meta["rule_boosts"] = rules["rules"]
    meta["rule_boosts_source"] = str(args.rule_artifact)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"hf_meta.json: +{len(rules['rules'])} rule_boosts (class_bias kept: run's 2-stage)")

    if args.sparse_artifact:
        sp = json.loads(Path(args.sparse_artifact).read_text(encoding="utf-8"))
        sparse_meta = json.loads(Path(args.sparse_meta_src).read_text(encoding="utf-8"))
        classes = sparse_meta["classes"]
        if list(sp["classes"]) != list(classes):
            raise SystemExit("class order mismatch between sparse artifact and sparse_meta src")
        sparse_meta["sparse_weight"] = float(sp["best_sparse_weight"])
        sparse_meta["class_bias"] = [float(sp["best_class_bias"][c]) for c in classes]
        sparse_meta["retune_artifact"] = str(args.sparse_artifact)
        sparse_meta["retune_macro_f1"] = float(sp["metrics"]["macro_f1"])
        (model_dir / "sparse_meta.json").write_text(
            json.dumps(sparse_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.copy2(args.sparse_pkl_src, model_dir / "sparse_svc.pkl")
        print(f"sparse: weight={sparse_meta['sparse_weight']} retune_macro={sparse_meta['retune_macro_f1']:.6f} "
              f"(pkl copied from {args.sparse_pkl_src})")


if __name__ == "__main__":
    main()
