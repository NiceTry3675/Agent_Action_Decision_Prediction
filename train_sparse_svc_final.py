import argparse
import json
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion
from sklearn.svm import LinearSVC

from script import ALL_CLASSES, load_jsonl, serialize_transformer_sample_current
from train import CLASS_TO_ID, load_labels


def make_vectorizer(args):
    word = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 2),
        min_df=args.word_min_df,
        max_features=args.word_features,
        sublinear_tf=True,
        strip_accents="unicode",
        dtype=np.float32,
    )
    char = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(3, 5),
        min_df=args.char_min_df,
        max_features=args.char_features,
        sublinear_tf=True,
        strip_accents="unicode",
        dtype=np.float32,
    )
    return FeatureUnion([("word", word), ("char", char)], n_jobs=1)


def load_retune_payload(path):
    if not path:
        return {}, 0.0, [0.0] * len(ALL_CLASSES)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    class_bias = payload.get("class_bias", {})
    if isinstance(class_bias, dict):
        bias_values = [float(class_bias.get(label, 0.0)) for label in ALL_CLASSES]
    else:
        bias_values = [float(value) for value in class_bias]
    return payload, float(payload.get("sparse_weight", 0.0)), bias_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--output-dir", default="model")
    parser.add_argument("--retune-artifact", default="")
    parser.add_argument("--sparse-weight", type=float, default=None)
    parser.add_argument("--c", type=float, default=0.05)
    parser.add_argument("--class-weight", choices=["balanced", "none"], default="balanced")
    parser.add_argument("--max-iter", type=int, default=3000)
    parser.add_argument("--word-features", type=int, default=180000)
    parser.add_argument("--char-features", type=int, default=220000)
    parser.add_argument("--word-min-df", type=int, default=2)
    parser.add_argument("--char-min-df", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    samples = load_jsonl(data_dir / "train.jsonl")
    labels_by_id = load_labels(data_dir / "train_labels.csv")
    y = np.array([CLASS_TO_ID[labels_by_id[sample["id"]]] for sample in samples], dtype=np.int64)
    texts = [serialize_transformer_sample_current(sample) for sample in samples]

    retune_payload, artifact_weight, class_bias = load_retune_payload(args.retune_artifact)
    sparse_weight = artifact_weight if args.sparse_weight is None else args.sparse_weight

    start = time.perf_counter()
    vectorizer = make_vectorizer(args)
    x_train = vectorizer.fit_transform(texts)
    model = LinearSVC(
        C=args.c,
        class_weight="balanced" if args.class_weight == "balanced" else None,
        dual="auto",
        max_iter=args.max_iter,
        random_state=args.seed,
    )
    model.fit(x_train, y)
    elapsed = time.perf_counter() - start

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "sparse_svc.pkl").open("wb") as f:
        pickle.dump({"vectorizer": vectorizer, "model": model}, f, protocol=pickle.HIGHEST_PROTOCOL)

    meta = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "classes": ALL_CLASSES,
        "sparse_weight": float(sparse_weight),
        "class_bias": [float(value) for value in class_bias],
        "retune_artifact": args.retune_artifact,
        "retune_macro_f1": retune_payload.get("two_stage_macro_f1"),
        "args": vars(args),
        "train_rows": len(samples),
        "feature_count": int(x_train.shape[1]),
        "fit_elapsed_sec": elapsed,
    }
    with (output_dir / "sparse_meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(
        f"saved sparse SVC artifact to {output_dir} "
        f"rows={len(samples)} features={x_train.shape[1]} elapsed={elapsed:.1f}s weight={sparse_weight}"
    )


if __name__ == "__main__":
    main()
