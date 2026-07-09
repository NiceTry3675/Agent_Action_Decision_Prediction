"""blend v2 teacher pack: probability-average of the two winning teachers (m8 + q35).

p_blend = 0.5*softmax(m8) + 0.5*softmax(q35); stored logits = log(p_blend)
(additive constants are irrelevant to the KD softmax). Also prints per-teacher
stats and the classes where each teacher disagrees with the label most.
"""
import torch

M8 = "/root/dacon/m8_qwen35_refit_train70k_fp16.pt"
Q35 = "/root/dacon/teacher_q35_train70k_fp16.pt"
OUT = "/root/dacon/teacher_blend2_train70k_fp16.pt"

a = torch.load(M8, map_location="cpu", weights_only=False)
b = torch.load(Q35, map_location="cpu", weights_only=False)
assert [str(c) for c in a["classes"]] == [str(c) for c in b["classes"]], "classes mismatch"
ids_a = [str(i) for i in a["ids"]]
ids_b = [str(i) for i in b["ids"]]
if ids_a != ids_b:
    row_b = {i: n for n, i in enumerate(ids_b)}
    order = [row_b[i] for i in ids_a]
    b_logits = b["logits"].float()[order]
else:
    b_logits = b["logits"].float()
a_logits = a["logits"].float()
y = torch.tensor(a["y_true"])
classes = [str(c) for c in a["classes"]]

pa = torch.softmax(a_logits, 1)
pb = torch.softmax(b_logits, 1)
p = 0.5 * pa + 0.5 * pb
logits_out = p.clamp_min(1e-9).log()

for name, lo in [("m8", a_logits), ("q35", b_logits), ("blend", logits_out)]:
    pred = lo.argmax(1)
    acc = (pred == y).float().mean().item()
    pr = torch.softmax(lo, 1)
    ent = (-pr * pr.clamp_min(1e-9).log()).sum(1).mean().item()
    print(f"{name}: train_acc={acc:.4f} wrong={1-acc:.4f} entropy={ent:.3f}")

# where do the two teachers disagree with the label (and with each other)?
da, db = pa.argmax(1) != y, pb.argmax(1) != y
both = (da & db).float().mean().item()
only_a = (da & ~db).float().mean().item()
only_b = (~da & db).float().mean().item()
print(f"disagree-with-label: both={both:.3f} m8-only={only_a:.3f} q35-only={only_b:.3f}")
for name, mask in [("m8", da), ("q35", db)]:
    counts = torch.bincount(y[mask], minlength=len(classes))
    top = counts.argsort(descending=True)[:5]
    print(f"{name} top disagree classes: " + ", ".join(f"{classes[i]}({counts[i]})" for i in top))

payload = {
    "ids": ids_a,
    "logits": logits_out.to(torch.float16),
    "classes": classes,
    "labels": a.get("labels"),
    "y_true": a["y_true"],
    "metadata": {
        "source_model": "blend v2: 0.5*m8 + 0.5*q35 (prob space)",
        "serializer": "current_v1",
        "max_length": 384,
        "note": "winning-teachers blend (m8 +0.0039, q35 +0.0033), seed42 lineage",
    },
}
torch.save(payload, OUT)
print(f"saved blend pack: {OUT} shape={tuple(logits_out.shape)}")
