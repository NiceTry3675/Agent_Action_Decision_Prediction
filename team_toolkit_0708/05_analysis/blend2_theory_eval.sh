/root/venv211/bin/python - <<'EOF'
import torch
a = torch.load("/root/dacon/m8_qwen35_refit_train70k_fp16.pt", map_location="cpu", weights_only=False)
b = torch.load("/root/dacon/teacher_q35_train70k_fp16.pt", map_location="cpu", weights_only=False)
c = torch.load("/root/dacon/teacher_blend2_train70k_fp16.pt", map_location="cpu", weights_only=False)
ids_a = [str(i) for i in a["ids"]]; ids_b = [str(i) for i in b["ids"]]
la = a["logits"].float()
if ids_a != ids_b:
    row = {i: n for n, i in enumerate(ids_b)}
    lb = b["logits"].float()[[row[i] for i in ids_a]]
else:
    lb = b["logits"].float()
lc = c["logits"].float()
y = torch.tensor(a["y_true"])
pa, pb, pc = torch.softmax(la,1), torch.softmax(lb,1), torch.softmax(lc,1)
aa, ab, ac = pa.argmax(1), pb.argmax(1), pc.argmax(1)

# quality axis 1: among both-wrong samples, do the two teachers point at the SAME alternative?
both = (aa != y) & (ab != y)
same_alt = (aa[both] == ab[both]).float().mean().item()
print(f"both-wrong={both.float().mean().item():.3f}  same-alternative-rate={same_alt:.3f}")

# quality axis 2: Menon-style -- mean probability mass given to the TRUE label
for name, p in [("m8", pa), ("q35", pb), ("blend", pc)]:
    pt = p[torch.arange(len(y)), y]
    print(f"{name}: mean p(true)={pt.mean().item():.4f}  p(true) on wrong-argmax rows={pt[ac != y].mean().item() if name=='blend' else pt[p.argmax(1) != y].mean().item():.4f}")

# where blend fixes / breaks vs m8 alone
fix = ((aa != y) & (ac == y)).float().mean().item()
brk = ((aa == y) & (ac != y)).float().mean().item()
print(f"blend vs m8: fixes={fix:.4f} breaks={brk:.4f}")
EOF
