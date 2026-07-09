import argparse
import os
import subprocess
import sys
from pathlib import Path


def run(cmd, env=None):
    print("+", " ".join(str(part) for part in cmd), flush=True)
    subprocess.run([str(part) for part in cmd], check=True, env=env)


def gpu_name():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        return out
    except Exception as exc:
        return f"unavailable: {exc!r}"


def parse_args():
    parser = argparse.ArgumentParser(description="Colab lane runner for the Gemma-4-12B LoRA teacher handoff.")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--grad-accum-steps", type=int, default=1)
    parser.add_argument("--export-batch-size", type=int, default=64)
    parser.add_argument("--resume-from", default="")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--allow-non-a100", action="store_true")
    parser.add_argument("--exchange-dir", default=os.environ.get("AADP_EXCHANGE_DIR", "AADP_exchange_c"))
    return parser.parse_args()


def main():
    args = parse_args()
    work = Path.cwd()
    exchange_root = Path("/content/drive/MyDrive") / args.exchange_dir
    model_dir = exchange_root / "models" / "teacher_gemma"
    ckpt_dir = exchange_root / "models" / "teacher_gemma_ckpt"
    logits_dir = work / "experiments" / "logits"
    logits_dir.mkdir(parents=True, exist_ok=True)
    out_pt = logits_dir / "teacher_gemma_train70k_fp16.pt"
    out_npz = logits_dir / "teacher_gemma_train70k_fp16.npz"

    env = os.environ.copy()
    env.setdefault("HF_HUB_DISABLE_XET", "1")

    name = gpu_name()
    print(f"gpu: {name}", flush=True)
    if "A100" not in name and not args.allow_non_a100:
        raise SystemExit("Gemma-4-12B teacher lane requires an A100-class runtime; pass --allow-non-a100 only for debugging.")
    if not env.get("HF_TOKEN"):
        print("warning: HF_TOKEN is not set; google/gemma-4-12B must already be accessible in the runtime cache/login.", flush=True)

    if not args.skip_install:
        run([sys.executable, "-m", "pip", "uninstall", "-y", "torchao"], env=env)
        run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "transformers>=5.13,<5.14",
                "peft==0.19.1",
                "accelerate",
                "safetensors==0.8.0",
                "sentencepiece",
                "protobuf",
                "bitsandbytes",
            ],
            env=env,
        )

    if not args.export_only:
        train_cmd = [
            sys.executable,
            "-u",
            "train_transformer.py",
            "--base-model",
            "google/gemma-4-12B",
            "--model-class",
            "gemma4custom",
            "--lr",
            "1e-4",
            "--device",
            "cuda",
            "--split",
            "session",
            "--serializer",
            "current_v1",
            "--max-length",
            "384",
            "--epochs",
            "3",
            "--batch-size",
            str(args.batch_size),
            "--grad-accum-steps",
            str(args.grad_accum_steps),
            "--gradient-checkpointing",
            "--eval-batch-size",
            "32",
            "--pad-to-multiple-of",
            "64",
            "--lora-r",
            "16",
            "--class-weight-power",
            "0.5",
            "--label-smoothing",
            "0.02",
            "--loss",
            "focal",
            "--focal-gamma",
            "2.0",
            "--replay-mode",
            "last1",
            "--max-replay-samples",
            "10000",
            "--replay-sample-weight",
            "0.5",
            "--tune-bias",
            "--keep-threshold",
            "0.0",
            "--tokenize-batch-size",
            "1024",
            "--no-research-log",
            "--seed",
            "42",
            "--save-fp16",
            "--data-dir",
            "./open/data",
            "--epoch-checkpoint-dir",
            str(ckpt_dir),
            "--snapshot-epoch-checkpoints",
            "--output-dir",
            str(model_dir),
            "--experiment-suffix",
            "teacher_gemma_colab",
            "--notes",
            "Gemma-4-12B custom seq-cls LoRA r16 teacher, seed42 current_v1; train70k logits only, not a student package",
            "--final-model",
            "--final-only",
        ]
        if args.resume_from:
            train_cmd.extend(["--resume-from", args.resume_from])
        run(train_cmd, env=env)

    export_cmd = [
        sys.executable,
        "-u",
        "export_teacher_logits.py",
        "--hf-model",
        str(model_dir / "hf_model"),
        "--model-class",
        "gemma4custom",
        "--base-model",
        "google/gemma-4-12B",
        "--data-dir",
        "./open/data",
        "--serializer",
        "current_v1",
        "--max-length",
        "384",
        "--batch-size",
        str(args.export_batch_size),
        "--pad-to-multiple-of",
        "64",
        "--source-note",
        "gemma-4-12b lora-r16 seed42 current_v1 colab teacher",
        "--out",
        str(out_pt),
        "--also-npz",
        str(out_npz),
    ]
    run(export_cmd, env=env)
    print(f"done: {out_pt}", flush=True)


if __name__ == "__main__":
    main()
