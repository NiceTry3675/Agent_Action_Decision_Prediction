"""M8 depth-cut layer-saturation probe (lane A G4, inference + linear heads).

Question: at which layer does the trained M8 0.8B refit's classification
signal saturate? Depth-cut economics are ~linear in layers kept (the fallback
is memory-volume bound), so if layer K's linear-probe F1 is within noise of
layer 24's, a K-layer cut + short refit is a measured bet, not a gamble.

Method: forward the refit with output_hidden_states on session-split train
data (current_v1, len400), collect the last-non-pad-token hidden state per
layer, train one linear head per layer (class-weighted CE, power 0.5), report
val macro-F1 per layer. Cut points that end on a full-attention layer
(pattern (LLLF)x6 -> K in {4,8,12,16,20,24}) are flagged.

Needs transformers 5.13 (qwen3_5_text arch) — self-installs into the system
python. Do not run 0.6B (4.51-era) jobs on this runtime afterwards without
reinstalling transformers==4.51.3.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

ART = Path("experiments/artifacts/m8_depthcut_layer_probe.json")
MODEL_DIR = "/content/drive/MyDrive/AADP_exchange/models/m8_qwen35_refit/hf_model"
MAX_LENGTH = 400


def ensure_tf513():
    try:
        import transformers  # noqa: F401

        if transformers.__version__.startswith("5.13"):
            return transformers.__version__
    except Exception:  # noqa: BLE001
        pass
    # reload() breaks on cached submodules — install, then re-exec fresh
    if os.environ.get("TF513_BOOTSTRAPPED"):
        raise RuntimeError("transformers 5.13 not importable after re-exec")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "transformers>=5.13,<5.14"], check=True, timeout=900)
    os.environ["TF513_BOOTSTRAPPED"] = "1"
    os.execv(sys.executable, [sys.executable, "-u", str(Path(__file__).resolve()), *sys.argv[1:]])


def mirror_to_drive(*paths):
    import shutil

    base = Path("/content/drive/MyDrive") / os.environ.get("AADP_EXCHANGE_DIR", "AADP_exchange")
    out = base / "runs" / "maxpack_mirror"
    try:
        out.mkdir(parents=True, exist_ok=True)
        for p in paths:
            p = Path(p)
            if p.exists():
                import shutil as _sh

                _sh.copy2(p, out / p.name)
                print(f"mirrored {p.name} -> Drive", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"drive mirror skipped: {exc!r}", flush=True)


def write_art(payload):
    ART.parent.mkdir(parents=True, exist_ok=True)
    ART.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    mirror_to_drive(ART)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-rows", type=int, default=24000)
    parser.add_argument("--val-rows", type=int, default=8000)
    parser.add_argument("--batch-size", type=int, default=48)
    parser.add_argument("--head-epochs", type=int, default=60)
    args = parser.parse_args()

    payload = {"what": "M8 0.8B depth-cut layer-saturation probe (linear heads on frozen hidden states)",
               "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "model_dir": MODEL_DIR, "max_length": MAX_LENGTH,
               "rows": {"train": args.train_rows, "val": args.val_rows}}
    try:
        payload["transformers"] = ensure_tf513()
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        from script import ALL_CLASSES, load_jsonl, serialize_transformer_sample
        from train import load_labels, session_id

        # --- session-grouped split (hash-based, leakage-safe; internal
        # consistency is all the probe needs)
        samples = load_jsonl("open/data/train.jsonl")
        labels_by_id = load_labels("open/data/train_labels.csv")
        labels = [labels_by_id[s["id"]] for s in samples]
        by_session = {}
        for s, y in zip(samples, labels):
            by_session.setdefault(session_id(s.get("id", "")), []).append((s, y))
        import hashlib

        train_pairs, val_pairs = [], []
        for sid, pairs in by_session.items():
            h = int(hashlib.md5(sid.encode()).hexdigest(), 16) % 100
            (val_pairs if h < 12 else train_pairs).extend(pairs)
        rng = torch.Generator().manual_seed(42)
        t_idx = torch.randperm(len(train_pairs), generator=rng)[: args.train_rows].tolist()
        v_idx = torch.randperm(len(val_pairs), generator=rng)[: args.val_rows].tolist()
        train_pairs = [train_pairs[i] for i in t_idx]
        val_pairs = [val_pairs[i] for i in v_idx]
        label_to_id = {c: i for i, c in enumerate(ALL_CLASSES)}
        payload["split"] = {"train": len(train_pairs), "val": len(val_pairs),
                            "sessions": len(by_session)}
        write_art(payload)

        tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
        model = AutoModelForSequenceClassification.from_pretrained(
            MODEL_DIR, torch_dtype=torch.float16)
        if model.config.pad_token_id is None and tokenizer.pad_token_id is not None:
            model.config.pad_token_id = tokenizer.pad_token_id
        model.to("cuda").eval()
        n_layers = model.config.num_hidden_layers
        payload["layer_types"] = model.config.layer_types
        payload["full_attention_cut_points"] = [
            i + 1 for i, t in enumerate(model.config.layer_types) if t == "full_attention"
        ]

        @torch.no_grad()
        def extract(pairs, tag):
            texts = [serialize_transformer_sample(s, "current_v1") for s, _ in pairs]
            ys = torch.tensor([label_to_id[y] for _, y in pairs])
            enc_len = [len(tokenizer(t, truncation=True, max_length=MAX_LENGTH)["input_ids"]) for t in texts]
            order = sorted(range(len(texts)), key=lambda i: enc_len[i])
            feats = torch.empty(len(texts), n_layers + 1, model.config.hidden_size, dtype=torch.float16)
            t0 = time.perf_counter()
            for start in range(0, len(order), args.batch_size):
                idx = order[start:start + args.batch_size]
                enc = tokenizer([texts[i] for i in idx], return_tensors="pt",
                                truncation=True, max_length=MAX_LENGTH, padding=True).to("cuda")
                out = model(**enc, output_hidden_states=True)
                last = enc["attention_mask"].sum(1) - 1  # right padding
                hs = torch.stack(out.hidden_states, dim=1)  # [B, L+1, T, H]
                gathered = torch.stack([hs[b, :, last[b], :] for b in range(len(idx))])
                feats[torch.tensor(idx)] = gathered.to("cpu", torch.float16)
                if start % (args.batch_size * 40) == 0:
                    done = start + len(idx)
                    print(f"{tag} extract {done}/{len(order)} "
                          f"({done / max(time.perf_counter() - t0, 1e-9) * 60:.0f} rows/min)", flush=True)
            return feats, ys

        tr_x, tr_y = extract(train_pairs, "train")
        va_x, va_y = extract(val_pairs, "val")
        del model
        torch.cuda.empty_cache()
        payload["extract_done"] = True
        write_art(payload)

        # --- per-layer linear heads on GPU
        from sklearn.metrics import f1_score

        counts = torch.bincount(tr_y, minlength=len(ALL_CLASSES)).float()
        weights = (counts.sum() / counts.clamp(min=1)).pow(0.5)
        weights = (weights / weights.mean()).to("cuda")
        results = {}
        for layer in range(n_layers + 1):
            x = tr_x[:, layer].float().to("cuda")
            xv = va_x[:, layer].float().to("cuda")
            y = tr_y.to("cuda")
            head = torch.nn.Linear(x.shape[1], len(ALL_CLASSES)).to("cuda")
            opt = torch.optim.AdamW(head.parameters(), lr=3e-3, weight_decay=1e-4)
            loss_fn = torch.nn.CrossEntropyLoss(weight=weights)
            best = 0.0
            for ep in range(args.head_epochs):
                perm = torch.randperm(len(x), device="cuda")
                for start in range(0, len(x), 4096):
                    b = perm[start:start + 4096]
                    opt.zero_grad()
                    loss = loss_fn(head(x[b]), y[b])
                    loss.backward()
                    opt.step()
                if ep >= args.head_epochs - 15 or ep % 5 == 4:
                    with torch.no_grad():
                        pred = head(xv).argmax(1).cpu()
                    f1 = f1_score(va_y, pred, average="macro")
                    best = max(best, f1)
            results[str(layer)] = round(float(best), 6)
            print(f"layer {layer:>2}: probe macro-F1 {best:.4f}", flush=True)
            payload["layer_probe_f1"] = results
            write_art(payload)

        final = results[str(n_layers)]
        payload["saturation"] = {
            str(k): {"f1": results[str(k)], "frac_of_final": round(results[str(k)] / final, 4)}
            for k in payload["full_attention_cut_points"]
        }
        payload["verdict"] = "OK"
        write_art(payload)
    except Exception:  # noqa: BLE001
        payload["error"] = traceback.format_exc()[-3500:]
        payload.setdefault("verdict", "RED_error")
        write_art(payload)


if __name__ == "__main__":
    main()
