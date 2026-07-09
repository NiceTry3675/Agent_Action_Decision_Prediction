"""Compare full-FT vs LoRA teacher logit distributions and their KD target shapes."""
import torch
import torch.nn.functional as F

FILES = {
    "m8 (full-FT, 0.8B)": "/root/dacon/m8_qwen35_refit_train70k_fp16.pt",
    "t15 (LoRA r16, 1.5B)": "/root/dacon/teacher_hcx15_train70k_fp16.pt",
}

for name, path in FILES.items():
    d = torch.load(path, map_location="cpu", weights_only=False)
    logits = d["logits"].float()
    y = torch.tensor(d["y_true"])
    p1 = F.softmax(logits, dim=-1)
    top1 = p1.argmax(-1)
    acc = (top1 == y).float().mean().item()
    maxp = p1.max(-1).values
    margin = logits.topk(2, dim=-1).values
    gap = (margin[:, 0] - margin[:, 1])
    ent1 = -(p1 * (p1 + 1e-9).log()).sum(-1)
    rows = [f"== {name} =="]
    rows.append(f"train top1 acc          : {acc:.4f}")
    rows.append(f"mean max-prob (T=1)     : {maxp.mean():.4f}   median: {maxp.median():.4f}")
    rows.append(f"mean top1-top2 logit gap: {gap.mean():.2f}   median: {gap.median():.2f}")
    rows.append(f"mean entropy (T=1)      : {ent1.mean():.3f} nats (uniform={torch.log(torch.tensor(14.)):.3f})")
    for T in (2.0, 3.0):
        pT = F.softmax(logits / T, dim=-1)
        entT = -(pT * (pT + 1e-9).log()).sum(-1)
        rows.append(f"T={T}: mean max-prob {pT.max(-1).values.mean():.4f} | mean entropy {entT.mean():.3f}")
    # where teacher is WRONG: how confident is it?
    wrong = top1 != y
    rows.append(f"wrong-sample share      : {wrong.float().mean():.4f} | wrong mean max-prob(T=3): "
                f"{F.softmax(logits[wrong]/3.0, dim=-1).max(-1).values.mean():.4f}")
    print("\n".join(rows))
    print()
