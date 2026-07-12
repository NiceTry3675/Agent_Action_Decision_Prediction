import csv
import json
import tempfile
import unittest
from pathlib import Path

import torch

from audit_weak4_residual_surfaces import (
    CLEAN_FIXED_SESSION,
    CONSTRUCTION_ONLY_FULL_OOF,
    SurfaceAuditError,
    audit_surface,
    oracle_headroom,
    route_assignments,
)
from script import ALL_CLASSES
from train import CLASS_TO_ID, f1_metrics, split_indices


def make_logits(top1, weak_top2, *, overall_top2=None):
    values = torch.full((len(ALL_CLASSES),), -20.0, dtype=torch.float32)
    values[top1] = 10.0
    values[weak_top2] = 8.0
    if overall_top2 is not None:
        values[overall_top2] = 9.0
    return values


class Weak4ResidualSurfaceAuditTest(unittest.TestCase):
    def write_data(self, root, rows):
        data_dir = Path(root) / "data"
        data_dir.mkdir()
        with (data_dir / "train.jsonl").open("w", encoding="utf-8") as handle:
            for sample_id, _ in rows:
                handle.write(
                    json.dumps(
                        {
                            "id": sample_id,
                            "current_prompt": f"prompt for {sample_id}",
                            "history": [],
                            "session_meta": {},
                        }
                    )
                    + "\n"
                )
        with (data_dir / "train_labels.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "action"])
            writer.writerows(rows)
        return data_dir

    def write_full_oof_fixture(self, root):
        # The second row deliberately has a non-Weak4 overall runner-up.  Its
        # conditional Weak4 top2 is still glob, so it must enter the live pair.
        specs = [
            ("sess_sim_000-step_01", "read_file", 0, 1, None),
            ("sess_sim_000-step_02", "glob_pattern", 1, 3, 4),
            ("sess_sim_001-step_01", "run_bash", 3, 2, None),
            ("sess_sim_001-step_02", "list_directory", 4, 0, None),
            ("sess_sim_002-step_01", "read_file", 2, 0, None),
            ("sess_sim_002-step_02", "list_directory", 0, 2, None),
            ("sess_sim_003-step_01", "grep_search", 3, 1, None),
            ("sess_sim_003-step_02", "glob_pattern", 3, 0, None),
        ]
        rows = [(sample_id, label) for sample_id, label, *_ in specs]
        data_dir = self.write_data(root, rows)
        source_logits = torch.stack(
            [
                make_logits(top1, weak_top2, overall_top2=overall_top2)
                for _, _, top1, weak_top2, overall_top2 in specs
            ]
        )
        source_y = torch.tensor([CLASS_TO_ID[label] for _, label, *_ in specs])

        # Prove that the implementation joins by ID instead of position.
        order = list(reversed(range(len(specs))))
        payload = {
            "ids": [specs[index][0] for index in order],
            "logits": source_logits[order],
            "classes": list(ALL_CLASSES),
            "labels": [specs[index][1] for index in order],
            "y_true": source_y[order],
            "metadata": {
                "source": "payload_repackage",
                "input_payload": "blend_fold_all_val_logits.pt",
            },
        }
        parent_path = Path(root) / "parent.pt"
        torch.save(payload, parent_path)
        return data_dir, parent_path, payload

    def test_full_oof_is_construction_only_and_routes_use_conditional_weak_top2(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir, parent_path, _ = self.write_full_oof_fixture(tmp)

            report = audit_surface(data_dir, parent_path, n_folds=3, fold_seed=17)

            self.assertEqual(
                report["surface"]["kind"], CONSTRUCTION_ONLY_FULL_OOF
            )
            self.assertEqual(
                report["surface"]["promotion_scope"],
                "construction_only_not_nested_validation",
            )
            self.assertTrue(report["join"]["full_train_coverage"])

            full = report["route_histograms"]["full_weak4"]
            self.assertEqual(full["route_rows"], 7)
            self.assertEqual(
                full["target_histogram"],
                {
                    "KEEP_MAIN": 3,
                    "SWITCH_READ": 1,
                    "SWITCH_GREP": 1,
                    "SWITCH_LIST": 1,
                    "SWITCH_GLOB": 1,
                },
            )
            self.assertEqual(full["relation_histogram"]["true_nonweak_identity"], 1)

            live = report["route_histograms"]["live_pair"]
            self.assertEqual(live["route_rows"], 6)
            self.assertEqual(
                live["target_histogram"],
                {
                    "KEEP_MAIN": 1,
                    "SWITCH_TOP2": 4,
                    "OUTSIDE_PAIR_IDENTITY": 1,
                },
            )
            # grep->glob is routed even though edit_file was the overall top2.
            self.assertEqual(live["direction_histogram"]["grep_search->glob_pattern"], 1)

            folds = report["session_hash_folds"]
            self.assertEqual(sum(item["rows"] for item in folds["folds"].values()), 8)
            self.assertEqual(
                sum(item["sessions"] for item in folds["folds"].values()), 4
            )

    def test_pre_registered_oracles_correct_only_their_allowed_routes(self):
        logits = torch.stack(
            [
                # Dead read/grep pair: full Weak4 oracle only.
                make_logits(0, 1),
                # Live read/list pair: both oracles.
                make_logits(0, 2),
                # Live glob/list pair, but truth is outside Weak4: neither.
                make_logits(3, 2),
                # Nonroute, already-correct edit_file row: neither.
                make_logits(4, 0),
            ]
        )
        y_true = torch.tensor([1, 2, 7, 4], dtype=torch.long)
        assignments = route_assignments(logits, y_true)

        oracle = oracle_headroom(assignments, y_true)

        baseline_pred = [0, 0, 3, 4]
        full_pred = [1, 2, 3, 4]
        live_pred = [0, 2, 3, 4]
        baseline_metrics = f1_metrics(y_true.tolist(), baseline_pred)
        full_metrics = f1_metrics(y_true.tolist(), full_pred)
        live_metrics = f1_metrics(y_true.tolist(), live_pred)

        raw = oracle["raw_baseline"]
        full = oracle["full_weak4_internal_oracle"]
        live = oracle["live_pair_top2_oracle"]
        self.assertAlmostEqual(raw["full_macro_f1"], baseline_metrics["macro_f1"])
        self.assertEqual(raw["full_macro_delta"], 0.0)
        self.assertEqual(raw["weak4_macro_delta"], 0.0)
        self.assertEqual(raw["route_correction_count"], 0)

        self.assertEqual(full["route_rows"], 3)
        self.assertEqual(full["oracle_eligible_rows"], 2)
        self.assertEqual(full["route_correction_count"], 2)
        self.assertAlmostEqual(full["full_macro_f1"], full_metrics["macro_f1"])
        self.assertAlmostEqual(
            full["full_macro_delta"],
            full_metrics["macro_f1"] - baseline_metrics["macro_f1"],
        )

        self.assertEqual(live["route_rows"], 2)
        self.assertEqual(live["oracle_eligible_rows"], 1)
        self.assertEqual(live["route_correction_count"], 1)
        self.assertAlmostEqual(live["full_macro_f1"], live_metrics["macro_f1"])
        self.assertGreater(full["weak4_macro_delta"], live["weak4_macro_delta"])
        self.assertGreater(live["weak4_macro_delta"], 0.0)

    def test_exact_fixed_session_surface_is_classified_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = []
            for session_no in range(20):
                for step in (1, 2):
                    sample_id = f"sess_sim_{session_no:03d}-step_{step:02d}"
                    label = ALL_CLASSES[(session_no + step) % len(ALL_CLASSES)]
                    rows.append((sample_id, label))
            data_dir = self.write_data(tmp, rows)
            samples = [
                {"id": sample_id, "current_prompt": "", "history": [], "session_meta": {}}
                for sample_id, _ in rows
            ]
            y = [CLASS_TO_ID[label] for _, label in rows]
            _, val_idx = split_indices(samples, y, "session", seed=42)
            order = list(reversed(val_idx))
            logits = torch.stack(
                [
                    make_logits(y[index], (y[index] + 1) % 4)
                    if y[index] < 4
                    else make_logits(y[index], 0)
                    for index in order
                ]
            )
            payload = {
                "ids": [rows[index][0] for index in order],
                "logits": logits,
                "classes": list(ALL_CLASSES),
                "y_true": torch.tensor([y[index] for index in order]),
                "indices": order,
                "split": "session",
                "seed": 42,
                "fold_id": None,
                "n_folds": None,
            }
            parent_path = Path(tmp) / "fixed.pt"
            torch.save(payload, parent_path)

            report = audit_surface(
                data_dir,
                parent_path,
                expected_surface=CLEAN_FIXED_SESSION,
                n_folds=3,
            )

            self.assertEqual(report["surface"]["kind"], CLEAN_FIXED_SESSION)
            self.assertEqual(report["surface"]["main_validation_rows"], len(val_idx))
            self.assertFalse(report["join"]["full_train_coverage"])
            self.assertEqual(report["join"]["parent_rows"], len(val_idx))
            fold_report = report["session_hash_folds"]
            self.assertEqual(
                sum(item["rows"] for item in fold_report["folds"].values()),
                len(val_idx),
            )
            self.assertEqual(
                sum(item["sessions"] for item in fold_report["folds"].values()),
                report["surface"]["main_validation_sessions"],
            )

    def test_fail_closed_payload_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir, parent_path, base_payload = self.write_full_oof_fixture(tmp)

            cases = []
            duplicate = dict(base_payload)
            duplicate["ids"] = list(base_payload["ids"])
            duplicate["ids"][-1] = duplicate["ids"][0]
            cases.append(("duplicate", duplicate, "duplicate parent ids"))

            bad_classes = dict(base_payload)
            bad_classes["classes"] = list(reversed(ALL_CLASSES))
            cases.append(("classes", bad_classes, "canonical class order mismatch"))

            nonfinite = dict(base_payload)
            nonfinite["logits"] = base_payload["logits"].clone()
            nonfinite["logits"][0, 0] = float("nan")
            cases.append(("nonfinite", nonfinite, "non-finite logits"))

            bad_y = dict(base_payload)
            bad_y["y_true"] = base_payload["y_true"].clone()
            bad_y["y_true"][0] = (int(bad_y["y_true"][0]) + 1) % len(ALL_CLASSES)
            cases.append(("labels", bad_y, "y_true disagrees"))

            ambiguous = dict(base_payload)
            ambiguous.pop("metadata")
            cases.append(("ambiguous", ambiguous, "explicit OOF provenance"))

            for name, payload, message in cases:
                with self.subTest(name=name):
                    torch.save(payload, parent_path)
                    with self.assertRaisesRegex(SurfaceAuditError, message):
                        audit_surface(data_dir, parent_path)


if __name__ == "__main__":
    unittest.main()
