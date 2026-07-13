import csv
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import torch

from audit_weak4_residual_surfaces import sha256_file
from build_weak4_clean_panel import (
    ARTIFACT_KIND,
    USAGE_SCOPE,
    CleanPanelAuditError,
    VoterSpec,
    audit_clean_panel_artifact,
    build_card_b_route_overlay_audit,
    build_clean_panel,
)
from script import ALL_CLASSES
from train import CLASS_TO_ID, f1_metrics, split_indices


class Weak4CleanPanelTest(unittest.TestCase):
    def make_fixture(self, root: Path, *, epochs: int = 2):
        data_dir = root / "open" / "data"
        logits_dir = root / "experiments" / "logits"
        artifacts_dir = root / "experiments" / "artifacts"
        data_dir.mkdir(parents=True)
        logits_dir.mkdir(parents=True)
        artifacts_dir.mkdir(parents=True)

        rows = []
        samples = []
        for session_no in range(24):
            for step in (1, 2):
                sample_id = f"sess_sim_{session_no:03d}-step_{step:02d}"
                label = ALL_CLASSES[(session_no + step) % len(ALL_CLASSES)]
                rows.append((sample_id, label))
                samples.append(
                    {
                        "id": sample_id,
                        "current_prompt": f"prompt {sample_id}",
                        "history": [],
                        "session_meta": {},
                    }
                )
        with (data_dir / "train.jsonl").open("w", encoding="utf-8") as handle:
            for sample in samples:
                handle.write(json.dumps(sample) + "\n")
        with (data_dir / "train_labels.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "action"])
            writer.writerows(rows)

        y = [CLASS_TO_ID[label] for _, label in rows]
        _, val_idx = split_indices(samples, y, "session", seed=42)
        specs = []
        result_rows = []
        expected_predictions = []
        for voter_no in range(2):
            name = f"voter_{voter_no}"
            experiment_id = f"fixed_voter_{voter_no}"
            base_model = f"base-model-{voter_no}"
            serializer = "current_v1" if voter_no == 0 else "current_v6"
            logits_path = Path("experiments/logits") / f"{experiment_id}.pt"
            metrics_path = Path("experiments/artifacts") / f"{experiment_id}.json"

            ordered_idx = list(reversed(val_idx)) if voter_no == 0 else list(val_idx)
            logits = torch.full((len(ordered_idx), len(ALL_CLASSES)), -5.0)
            predictions = []
            for row_no, source_idx in enumerate(ordered_idx):
                prediction = y[source_idx]
                if (row_no + voter_no) % 5 == 0:
                    prediction = (prediction + 1) % len(ALL_CLASSES)
                predictions.append(prediction)
                logits[row_no, prediction] = 5.0
            y_val = torch.tensor([y[index] for index in ordered_idx], dtype=torch.long)
            raw_metrics = f1_metrics(y_val.tolist(), predictions)
            payload = {
                "base_model": base_model,
                "serializer_name": serializer,
                "split": "session",
                "seed": 42,
                "max_length": 64 + voter_no,
                "fold_id": None,
                "n_folds": None,
                "classes": list(ALL_CLASSES),
                "ids": [rows[index][0] for index in ordered_idx],
                "indices": ordered_idx,
                "y_true": y_val,
                "logits": logits,
                # A huge post-hoc bias would alter every prediction.  The
                # panel must ignore it and use raw logits only.
                "class_bias": [100.0] + [0.0] * (len(ALL_CLASSES) - 1),
                "raw_metrics": raw_metrics,
            }
            full_logits_path = root / logits_path
            torch.save(payload, full_logits_path)
            sidecar = {
                "experiment_id": experiment_id,
                "serializer_name": serializer,
                "val_logits_path": str(logits_path),
                "validation_rows": len(ordered_idx),
                "full_validation_rows": len(ordered_idx),
                "raw_metrics": raw_metrics,
            }
            full_metrics_path = root / metrics_path
            full_metrics_path.write_text(json.dumps(sidecar), encoding="utf-8")
            command = (
                f"train_transformer.py --base-model {base_model} --device cuda "
                f"--split session --serializer {serializer} --max-length {64 + voter_no} "
                f"--epochs {epochs} --seed 42 --replay-mode last1"
            )
            result_rows.append(
                {
                    "experiment_id": experiment_id,
                    "model_family": "torch_gpu_transformer",
                    "base_model": base_model,
                    "serializer_name": serializer,
                    "split_type": "session",
                    "seed": "42",
                    "fold_id": "",
                    "max_length": str(64 + voter_no),
                    "epochs": str(epochs),
                    "replay_mode": "last1",
                    "replay_size": "10000",
                    "macro_f1_raw": f"{raw_metrics['macro_f1']:.6f}",
                    "val_logits_path": str(logits_path),
                    "train_command": command,
                }
            )
            specs.append(
                VoterSpec(
                    name=name,
                    experiment_id=experiment_id,
                    logits_path=str(logits_path),
                    logits_sha256=sha256_file(full_logits_path),
                    metrics_path=str(metrics_path),
                    metrics_sha256=sha256_file(full_metrics_path),
                    base_model=base_model,
                    serializer=serializer,
                    max_length=64 + voter_no,
                )
            )
            expected_predictions.append(
                {
                    sample_id: prediction
                    for sample_id, prediction in zip(payload["ids"], predictions)
                }
            )

        results_csv = root / "experiments" / "results.csv"
        with results_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(result_rows[0]))
            writer.writeheader()
            writer.writerows(result_rows)
        return {
            "root": root,
            "data_dir": Path("open/data"),
            "results_csv": Path("experiments/results.csv"),
            "specs": specs,
            "rows": rows,
            "y": y,
            "val_idx": val_idx,
            "expected_predictions": expected_predictions,
        }

    def build(self, fixture):
        return build_clean_panel(
            repo_root=fixture["root"],
            data_dir=fixture["data_dir"],
            results_csv=fixture["results_csv"],
            voter_specs=fixture["specs"],
            expected_rows=len(fixture["val_idx"]),
        )

    def rewrite_result_command(self, fixture, voter_no, command, *, epochs=None):
        path = fixture["root"] / fixture["results_csv"]
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
            fieldnames = list(rows[0])
        rows[voter_no]["train_command"] = command
        if epochs is not None:
            rows[voter_no]["epochs"] = str(epochs)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_build_aligns_by_id_and_uses_raw_logits_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.make_fixture(Path(tmp))
            payload = self.build(fixture)

            self.assertEqual(payload["kind"], ARTIFACT_KIND)
            self.assertEqual(payload["usage_scope"], USAGE_SCOPE)
            self.assertFalse(payload["card_b_connection"])
            self.assertFalse(payload["training_target"])
            self.assertEqual(payload["rows"], len(fixture["val_idx"]))
            self.assertEqual(payload["voter_count"], 2)
            self.assertEqual(payload["classes"], ALL_CLASSES)
            self.assertEqual(payload["ids"], sorted(payload["ids"]))

            official = {
                fixture["rows"][index][0]: fixture["y"][index]
                for index in fixture["val_idx"]
            }
            expected_columns = torch.tensor(
                [
                    [mapping[sample_id] for mapping in fixture["expected_predictions"]]
                    for sample_id in payload["ids"]
                ],
                dtype=torch.uint8,
            )
            self.assertTrue(torch.equal(payload["raw_predictions"], expected_columns))
            expected_correct = expected_columns.to(torch.long).eq(
                torch.tensor([official[sample_id] for sample_id in payload["ids"]]).view(-1, 1)
            )
            self.assertTrue(torch.equal(payload["correct_matrix"], expected_correct))
            self.assertTrue(
                torch.equal(payload["c_clean"], expected_correct.sum(dim=1).to(torch.uint8))
            )
            for source in payload["sources"]:
                self.assertEqual(source["prediction_contract"], "argmax(payload.logits); class_bias/rules ignored")
                self.assertEqual(source["train_val_id_overlap"], 0)
                self.assertEqual(source["train_val_session_overlap"], 0)

    def test_fail_closed_on_changed_logit_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.make_fixture(Path(tmp))
            path = fixture["root"] / fixture["specs"][0].logits_path
            payload = torch.load(path, map_location="cpu", weights_only=False)
            payload["logits"][0, 0] += 0.25
            torch.save(payload, path)
            with self.assertRaisesRegex(CleanPanelAuditError, "logits SHA mismatch"):
                self.build(fixture)

    def test_fail_closed_on_noncanonical_classes_even_with_matching_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.make_fixture(Path(tmp))
            path = fixture["root"] / fixture["specs"][0].logits_path
            payload = torch.load(path, map_location="cpu", weights_only=False)
            payload["classes"][0], payload["classes"][1] = (
                payload["classes"][1],
                payload["classes"][0],
            )
            torch.save(payload, path)
            fixture["specs"][0] = replace(
                fixture["specs"][0], logits_sha256=sha256_file(path)
            )
            with self.assertRaisesRegex(ValueError, "canonical class order mismatch"):
                self.build(fixture)

    def test_fail_closed_on_cross_path_training_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.make_fixture(Path(tmp))
            original = (
                "train_transformer.py --base-model base-model-0 --device cuda "
                "--split session --serializer current_v1 --max-length 64 --epochs 2 "
                "--seed 42 --distill-logits full_refit_teacher.pt"
            )
            self.rewrite_result_command(fixture, 0, original)
            with self.assertRaisesRegex(CleanPanelAuditError, "forbidden cross-path"):
                self.build(fixture)

    def test_fail_closed_on_eval_only_checkpoint_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.make_fixture(Path(tmp))
            command = (
                "train_transformer.py --base-model base-model-0 --device cuda "
                "--split session --serializer current_v1 --max-length 64 --epochs 0 --seed 42"
            )
            self.rewrite_result_command(fixture, 0, command, epochs=0)
            with self.assertRaisesRegex(CleanPanelAuditError, "eval-only"):
                self.build(fixture)

    def test_saved_artifact_audit_rebuilds_sources_and_rejects_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.make_fixture(Path(tmp))
            payload = self.build(fixture)
            artifact = fixture["root"] / "panel.pt"
            torch.save(payload, artifact)
            # Unrelated future ledger appends must not invalidate the selected
            # rows' exact command provenance.
            results_path = fixture["root"] / fixture["results_csv"]
            with results_path.open(encoding="utf-8", newline="") as handle:
                ledger_rows = list(csv.DictReader(handle))
                fieldnames = list(ledger_rows[0])
            unrelated = dict(ledger_rows[0])
            unrelated["experiment_id"] = "unrelated_future_row"
            with results_path.open("a", encoding="utf-8", newline="") as handle:
                csv.DictWriter(handle, fieldnames=fieldnames).writerow(unrelated)
            result = audit_clean_panel_artifact(
                artifact,
                repo_root=fixture["root"],
                data_dir=fixture["data_dir"],
                results_csv=fixture["results_csv"],
                voter_specs=fixture["specs"],
                expected_rows=len(fixture["val_idx"]),
            )
            self.assertEqual(result["audit"], "passed")

            mutated = torch.load(artifact, map_location="cpu", weights_only=False)
            mutated["c_clean"][0] = (int(mutated["c_clean"][0]) + 1) % 3
            torch.save(mutated, artifact)
            with self.assertRaisesRegex(CleanPanelAuditError, "c_clean differs"):
                audit_clean_panel_artifact(
                    artifact,
                    repo_root=fixture["root"],
                    data_dir=fixture["data_dir"],
                    results_csv=fixture["results_csv"],
                    voter_specs=fixture["specs"],
                    expected_rows=len(fixture["val_idx"]),
                )

    def test_card_b_overlay_is_diagnostic_only_and_rederives_target_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.make_fixture(Path(tmp))
            panel = self.build(fixture)
            ids = list(reversed(panel["ids"]))
            y_by_id = {
                sample_id: int(label)
                for sample_id, label in zip(panel["ids"], panel["y_true"].tolist())
            }
            weak_ids = [sample_id for sample_id in ids if y_by_id[sample_id] < 4]
            nonweak_ids = [sample_id for sample_id in ids if y_by_id[sample_id] >= 4]
            protect_id = weak_ids[0]
            rescue_id = next(
                sample_id
                for sample_id in weak_ids[1:]
                if y_by_id[sample_id] != y_by_id[protect_id]
            )
            outside_id = nonweak_ids[0]
            protect_y = y_by_id[protect_id]
            rescue_y = y_by_id[rescue_id]
            protect_alt = (protect_y + 1) % 4
            rescue_main = (rescue_y + 1) % 4
            route_ids = [protect_id, rescue_id, outside_id]
            route_indices = torch.tensor([ids.index(sample_id) for sample_id in route_ids])
            dataset = {
                "kind": "weak4_live_pair_residual_dataset",
                "usage_scope": "specialist_clean_cv",
                "validation_scope": "clean_fixed_parent_session_cv",
                "classes": list(ALL_CLASSES),
                "parent_split": "session",
                "parent_seed": 42,
                "ids": ids,
                "y_true": torch.tensor([y_by_id[sample_id] for sample_id in ids]),
                "route_ids": route_ids,
                "route_indices": route_indices,
                "main_predictions": torch.tensor([protect_y, rescue_main, 0]),
                "alternative_predictions": torch.tensor([protect_alt, rescue_y, 1]),
                "target_kinds": torch.tensor([0, 1, 3]),
                "provenance": {"parent_path": "parent.pt", "parent_sha256": "abc"},
            }
            path = fixture["root"] / "card_b.pt"
            torch.save(dataset, path)
            overlay = build_card_b_route_overlay_audit(
                panel,
                path,
                repo_root=fixture["root"],
                expected_sha256=sha256_file(path),
            )
            self.assertEqual(
                overlay["usage_scope"],
                "diagnostic_overlay_only_not_a_training_target",
            )
            self.assertTrue(overlay["training_consumption_forbidden"])
            self.assertFalse(overlay["card_b_connection"])
            self.assertEqual(overlay["by_target_kind"]["protect"]["rows"], 1)
            self.assertEqual(overlay["by_target_kind"]["rescue"]["rows"], 1)
            self.assertEqual(overlay["by_target_kind"]["outside"]["rows"], 1)

            dataset["target_kinds"][1] = 0
            torch.save(dataset, path)
            with self.assertRaisesRegex(
                CleanPanelAuditError, "target kinds disagree"
            ):
                build_card_b_route_overlay_audit(
                    panel,
                    path,
                    repo_root=fixture["root"],
                )


if __name__ == "__main__":
    unittest.main()
