import csv
import json
import tempfile
import unittest
from pathlib import Path

import torch

from build_weak4_full_dataset import build_dataset
from script import ALL_CLASSES
from weak4_full_residual import (
    ResidualAction,
    build_residual_target,
    stratified_group_fold_ids,
)


class Weak4FullTargetTests(unittest.TestCase):
    def test_all_switch_targets_and_keep_contract(self):
        parent = torch.tensor([9.0, 2.0, 1.0, 0.0] + [-1.0] * 10)
        self.assertEqual(build_residual_target(parent, 0), (ResidualAction.KEEP_MAIN, 0, -100))
        self.assertEqual(build_residual_target(parent, 1), (ResidualAction.SWITCH_GREP, 1, 1))
        self.assertEqual(build_residual_target(parent, 2), (ResidualAction.SWITCH_LIST, 1, 2))
        self.assertEqual(build_residual_target(parent, 3), (ResidualAction.SWITCH_GLOB, 1, 3))
        self.assertEqual(build_residual_target(parent, 9), (ResidualAction.KEEP_MAIN, 0, -100))

    def test_nonweak_parent_is_not_route_eligible(self):
        parent = torch.tensor([0.0] * 4 + [5.0] + [0.0] * 9)
        with self.assertRaises(ValueError):
            build_residual_target(parent, 0)

    def test_group_folds_never_split_a_session(self):
        groups = ["a", "a", "b", "b", "c", "d", "e", "f"]
        folds = stratified_group_fold_ids(groups, [0, 1, 0, 1, 2, 2, 3, 4], 3, 42)
        for group in set(groups):
            self.assertEqual(len({fold for fold, value in zip(folds, groups) if value == group}), 1)

    def test_builder_joins_parent_rows_by_id_and_is_generic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            samples = [
                {"id": "sess_sim_a-step_01", "current_prompt": "read a.py", "history": [], "session_meta": {"workspace": {}}},
                {"id": "sess_sim_b-step_01", "current_prompt": "search B", "history": [], "session_meta": {"workspace": {}}},
            ]
            with (root / "train.jsonl").open("w", encoding="utf-8") as handle:
                for row in samples:
                    handle.write(json.dumps(row) + "\n")
            with (root / "train_labels.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["id", "action"])
                writer.writeheader()
                writer.writerow({"id": samples[0]["id"], "action": "read_file"})
                writer.writerow({"id": samples[1]["id"], "action": "grep_search"})
            payload = {
                "classes": ALL_CLASSES,
                "ids": [samples[1]["id"], samples[0]["id"]],
                "y_true": [1, 0],
                "logits": torch.tensor([[3.0, 4.0] + [0.0] * 12, [5.0, 1.0] + [0.0] * 12]),
                "split": "session",
                "seed": 7,
            }
            parent = root / "parent.pt"
            torch.save(payload, parent)
            with self.assertRaises(ValueError):
                build_dataset(
                    data_dir=root,
                    parent_logits_path=parent,
                    parent_surface="unit_test",
                    validation_scope="construction_only_oof",
                )
            artifact = build_dataset(
                data_dir=root,
                parent_logits_path=parent,
                parent_surface="unit_test",
                validation_scope="unit_test",
                require_split="session",
                require_seed=7,
                require_rows=2,
            )
            self.assertEqual(artifact["ids"], payload["ids"])
            self.assertEqual([row["sample_id"] for row in artifact["route_rows"]], payload["ids"])
            self.assertEqual(artifact["target_histogram"]["0"], 2)


if __name__ == "__main__":
    unittest.main()
