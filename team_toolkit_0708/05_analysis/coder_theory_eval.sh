/root/venv211/bin/python - <<'EOF'
import torch
def load(p):
    d = torch.load(p, map_location="cpu", weights_only=False)
    ids = [str(i) for i in d["ids"]]
    return ids, d["logits"].float(), d["y_true"]

ids_m, lm, yt = load("/root/dacon/m8_qwen35_refit_train70k_fp16.pt")
ids_2, l2, _ = load("/root/dacon/teacher_coder_ep2_train70k_fp16.pt")
ids_3, l3, _ = load("/root/dacon/teacher_coder_train70k_fp16.pt")
row2 = {i: n for n, i in enumerate(ids_2)}; l2 = l2[[row2[i] for i in ids_m]]
row3 = {i: n for n, i in enumerate(ids_3)}; l3 = l3[[row3[i] for i in ids_m]]
y = torch.tensor(yt)
d = torch.load("/root/dacon/m8_qwen35_refit_train70k_fp16.pt", map_location="cpu", weights_only=False)
classes = [str(c) for c in d["classes"]]

packs = {"m8": lm, "coder_ep2": l2, "coder_ep3": l3}
probs = {k: torch.softmax(v, 1) for k, v in packs.items()}
args = {k: p.argmax(1) for k, p in probs.items()}
for k in packs:
    p = probs[k]; a = args[k]
    acc = (a == y).float().mean().item()
    ent = (-p * p.clamp_min(1e-9).log()).sum(1).mean().item()
    pt = p[torch.arange(len(y)), y]
    print(f"{k}: acc={acc:.4f} wrong={1-acc:.4f} entropy={ent:.3f} p_true={pt.mean().item():.4f} p_true_on_wrong={pt[a!=y].mean().item():.4f}")

# heterogeneity vs m8 (the domain hypothesis)
dm, d2 = args["m8"] != y, args["coder_ep2"] != y
both = (dm & d2); only_c = (~dm & d2); only_m = (dm & ~d2)
print(f"\nep2 vs m8 disagreement overlap: both={both.float().mean():.3f} coder-only={only_c.float().mean():.3f} m8-only={only_m.float().mean():.3f}")
print(f"same-alternative among both-wrong: {(args['m8'][both]==args['coder_ep2'][both]).float().mean().item():.3f}")

# coder-only new opinions: which classes?
counts = torch.bincount(y[only_c], minlength=len(classes))
top = counts.argsort(descending=True)[:6]
print("coder-only disagree classes: " + ", ".join(f"{classes[i]}({counts[i]})" for i in top))

# what did ep3's extra epoch do vs ep2
mem = (d2 & (args["coder_ep3"] == y)).float().mean().item()
print(f"\nep2-wrong -> ep3-correct (memorized by extra epoch): {mem:.3f}")
EOF
