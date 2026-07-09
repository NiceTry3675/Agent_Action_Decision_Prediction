"""Memcheck the s777 int8 pack exactly as the server would load it."""
import sys

import torch
from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer

sys.path.insert(0, "/root/dacon")
from eval_pseudo_holdout import second_last_pairs
from quantize_checkpoint import load_int8_state_dict
from script import load_jsonl, serialize_transformer_sample
from train import f1_metrics

INT8 = "/root/dacon/models/s777_pack_eval/model.int8.safetensors"
CFG_DIR = "/root/dacon/models/s777_eval/hf_model"

device = torch.device("cuda")
state = load_int8_state_dict(INT8, dtype=torch.float16)
config = AutoConfig.from_pretrained(CFG_DIR)
model = AutoModelForSequenceClassification.from_config(config)
model = model.half()
missing, unexpected = model.load_state_dict(state, strict=False)
print("missing:", len(missing), "unexpected:", len(unexpected))
model.to(device).eval()
tokenizer = AutoTokenizer.from_pretrained(CFG_DIR)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

samples = load_jsonl("/root/dacon/open/data/train.jsonl")
pairs = second_last_pairs(samples, 5000, -1)
y_true = [label for label, _ in pairs]
texts = [serialize_transformer_sample(s, "current_v1") for _, s in pairs]
enc_all = tokenizer(texts, padding=False, truncation=True, max_length=384)
keys = list(enc_all.keys())
features = [{k: enc_all[k][j] for k in keys} for j in range(len(texts))]
order = sorted(range(len(features)), key=lambda i: len(features[i]["input_ids"]))
preds = [0] * len(features)
with torch.inference_mode():
    for bstart in range(0, len(order), 32):
        bidx = order[bstart:bstart + 32]
        enc = tokenizer.pad([features[i] for i in bidx], padding=True, return_tensors="pt")
        enc = {k: v.to(device) for k, v in enc.items()}
        with torch.amp.autocast(device_type="cuda", dtype=torch.float16):
            logits = model(**enc).logits.float().cpu()
        for row, i in zip(torch.argmax(logits, dim=1).tolist(), bidx):
            preds[i] = row
print(f"INT8_PACK_MEMCHECK macro_f1={f1_metrics(y_true, preds)['macro_f1']:.6f}")
