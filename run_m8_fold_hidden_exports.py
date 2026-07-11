#!/usr/bin/env python3
"""Drive all three M8 session-OOF fold hidden exports in one tracked run.

Each fold model's last-epoch checkpoint lives on Drive; the fold logits are
staged as lane anchors.  Every fold export re-verifies its checkpoint against
the saved fold logits (argmax gate inside export_oof_fold_hidden)."""

from __future__ import annotations

import argparse

from export_oof_fold_hidden import main as export_fold

FOLD_LOGITS = {
    0: "20260705_061219_gpu_transformer_session_oof_current_v1_len400_fold0-of3_replay-last1_m8_qwen35_oof_len400_ep3_fold0_val_logits.pt",
    1: "20260705_073505_gpu_transformer_session_oof_current_v1_len400_fold1-of3_replay-last1_m8_qwen35_oof_len400_ep3_fold1_val_logits.pt",
    2: "20260705_085858_gpu_transformer_session_oof_current_v1_len400_fold2-of3_replay-last1_m8_qwen35_oof_len400_ep3_fold2_val_logits.pt",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ckpt-template",
        default="/content/drive/MyDrive/AADP_exchange/models/m8_qwen35_oof_len400_ep3_fold{fold}_ckpt",
    )
    parser.add_argument(
        "--logits-dir",
        default="/content/drive/MyDrive/AADP_exchange_b/anchors",
    )
    parser.add_argument(
        "--output-template",
        default="/content/drive/MyDrive/AADP_exchange_b/artifacts/reliability_v2/m8_oof_fold{fold}_hidden.pt",
    )
    parser.add_argument("--data-dir", default="open/data")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--folds", default="0,1,2")
    args = parser.parse_args()

    for fold in [int(v) for v in args.folds.split(",")]:
        print(f"=== fold {fold} ===", flush=True)
        export_fold(
            [
                "--checkpoint", args.ckpt_template.format(fold=fold),
                "--fold-logits", f"{args.logits_dir}/{FOLD_LOGITS[fold]}",
                "--data-dir", args.data_dir,
                "--serializer", "current_v1",
                "--max-length", "400",
                "--device", args.device,
                "--output", args.output_template.format(fold=fold),
            ]
        )


if __name__ == "__main__":
    main()
