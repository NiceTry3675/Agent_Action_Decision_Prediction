#!/usr/bin/env python3
"""Run the locked nine-fit parent-inner diagnostic and aggregate it.

Designed for one detached cloud process: each fit is a separate child process,
so GPU memory is released before the next HCX model is loaded.  Fold model
checkpoints are not saved.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="experiments/artifacts/20260713_sim_policy_decoder_dataset.pt",
    )
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument(
        "--base-model",
        default="naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-0.5B",
    )
    parser.add_argument("--experiment-id", default="20260713_sim_policy_decoder_hcx05b")
    parser.add_argument("--aggregate-id", default="20260713_sim_policy_decoder_hcx05b_oof")
    parser.add_argument(
        "--objective", choices=("direct5", "hierarchical"), default="direct5"
    )
    parser.add_argument("--conditional-loss-weight", type=float, default=0.5)
    parser.add_argument("--gate-pos-weight", type=float, default=1.0)
    parser.add_argument("--allow-recipe-ablation", action="store_true")
    parser.add_argument("--device", choices=("cuda", "auto"), default="cuda")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--no-results-csv", action="store_true")
    return parser.parse_args()


def run(command: list[str], cwd: Path) -> None:
    print("[run] " + " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=cwd)


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent
    logits_dir = root / "experiments/logits"
    for parent_fold in range(3):
        for inner_fold in range(3):
            suffix = f"pf{parent_fold}_if{inner_fold}"
            expected = logits_dir / f"{args.experiment_id}_{suffix}_val.pt"
            if args.skip_existing and expected.is_file():
                print(f"[skip] {expected}", flush=True)
                continue
            command = [
                sys.executable,
                str(root / "train_sim_policy_decoder.py"),
                "--dataset",
                args.dataset,
                "--data-dir",
                args.data_dir,
                "--base-model",
                args.base_model,
                "--objective",
                args.objective,
                "--conditional-loss-weight",
                str(args.conditional_loss_weight),
                "--gate-pos-weight",
                str(args.gate_pos_weight),
                "--cv-mode",
                "parent_inner3",
                "--parent-fold",
                str(parent_fold),
                "--inner-fold",
                str(inner_fold),
                "--device",
                args.device,
                "--precision",
                "fp16",
                "--experiment-id",
                args.experiment_id,
            ]
            if args.local_files_only:
                command.append("--local-files-only")
            if args.no_results_csv:
                command.append("--no-results-csv")
            if args.allow_recipe_ablation:
                command.append("--allow-recipe-ablation")
            run(command, root)
    pattern = str(logits_dir / f"{args.experiment_id}_pf*_if*_val.pt")
    aggregate = [
        sys.executable,
        str(root / "aggregate_sim_policy_decoder_oof.py"),
        "--dataset",
        args.dataset,
        "--fold-pattern",
        pattern,
        "--experiment-id",
        args.aggregate_id,
    ]
    if args.no_results_csv:
        aggregate.append("--no-results-csv")
    run(aggregate, root)


if __name__ == "__main__":
    main()
