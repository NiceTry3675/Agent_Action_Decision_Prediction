import copy
import unittest

import torch

from audit_weak4_pair_residual import (
    RECIPE_KEYS,
    binary_auc,
    bounded_reachable_oracle,
    full_session_label_permutation_null,
    gap_auc_report,
    load_delta_surface,
    parse_scales,
    raw_delta_auc_report,
    scaled_gap_auc_curve,
    session_block_permutation_auc_null,
    sign_orientation_contract_audit,
    surface_arrays,
    target_delta_quantiles,
    target_semantics,
    threshold_scale_curve,
)
from script import ALL_CLASSES
from weak4_pair_residual import (
    CLEAN_USAGE_SCOPE,
    CLEAN_VALIDATION_SCOPE,
    DATASET_KIND,
    FEATURE_SCHEMA,
    LIVE_WEAK4_PAIRS,
    MODEL_SCHEMA,
    TARGET_OUTSIDE,
    TARGET_PROTECT,
    TARGET_RESCUE,
    bounded_delta,
    build_pair_targets,
    main_numeric_features,
    select_live_pair_routes,
)


def row(read=-5.0, grep=-5.0, listing=-5.0, glob=-5.0, other=-8.0):
    values = torch.full((len(ALL_CLASSES),), float(other))
    values[:4] = torch.tensor([read, grep, listing, glob])
    return values


def recipe():
    result = {
        "model_schema": MODEL_SCHEMA,
        "feature_schema": FEATURE_SCHEMA,
        "base_model": "tiny-mbert",
        "serializer_name": "weak_policy_pair_v1",
        "seed": 42,
        "max_length": 384,
        "epochs": 3,
        "encoder_lr": 2e-5,
        "head_lr": 1e-4,
        "batch_size": 16,
        "eval_batch_size": 32,
        "weight_decay": 0.01,
        "warmup_ratio": 0.06,
        "grad_clip": 1.0,
        "delta_max": 2.0,
        "fusion_dim": 256,
        "dropout": 0.1,
        "gradient_checkpointing": True,
    }
    assert set(result) == RECIPE_KEYS
    return result


def synthetic_dataset():
    logits = torch.stack(
        [
            row(read=1.0, grep=-2, listing=0.5, glob=-2),   # rescue gap .5
            row(read=3.0, grep=-2, listing=0.5, glob=-2),   # rescue gap 2.5
            row(read=1.0, grep=-2, listing=0.7, glob=-2),   # protect
            row(read=1.0, grep=-2, listing=0.8, glob=-2),   # outside true grep
            row(read=-2, grep=1.0, listing=-2, glob=0.4),   # rescue gap .6
            row(read=-2, grep=1.0, listing=-2, glob=0.6),   # protect
            row(read=-2, grep=-2, listing=-2, glob=-2, other=3.0),
            row(read=-2, grep=-2, listing=-2, glob=-2, other=3.0),
        ]
    )
    y_true = torch.tensor([2, 2, 0, 1, 3, 1, 4, 5])
    route = select_live_pair_routes(logits)
    counts = torch.ones(len(logits), dtype=torch.uint8)
    kinds, signs = build_pair_targets(y_true, route, counts)
    ids = [f"session{idx}-step_01" for idx in range(len(logits))]
    route_indices = route["indices"]
    full_folds = torch.tensor([0, 0, 0, 0, 1, 1, 1, 1])
    return {
        "schema_version": 1,
        "kind": DATASET_KIND,
        "classes": ALL_CLASSES,
        "live_pairs": [list(pair) for pair in LIVE_WEAK4_PAIRS],
        "feature_schema": FEATURE_SCHEMA,
        "usage_scope": CLEAN_USAGE_SCOPE,
        "validation_scope": CLEAN_VALIDATION_SCOPE,
        "parent_split": "session",
        "parent_seed": 42,
        "reliability_source": "none",
        "ids": ids,
        "route_ids": [ids[idx] for idx in route_indices.tolist()],
        "y_true": y_true,
        "parent_logits": logits,
        "correct_counts": counts,
        "full_fold_ids": full_folds,
        "n_folds": 2,
        "route_indices": route_indices,
        "pair_ids": route["pair_ids"],
        "pair_classes": route["pair_classes"],
        "top_sides": route["top_sides"],
        "base_gaps": route["base_gaps"],
        "main_predictions": route["main_predictions"],
        "alternative_predictions": route["alternative_predictions"],
        "numeric_features": main_numeric_features(logits, route),
        "target_kinds": kinds,
        "target_signs": signs,
        "route_fold_ids": full_folds[route_indices],
    }


def fold_payload(dataset, fold_id, raw_values, dataset_sha="dataset-sha"):
    route_folds = torch.as_tensor(dataset["route_fold_ids"])
    rows = (route_folds == fold_id).nonzero(as_tuple=False).view(-1)
    raw = torch.tensor(raw_values, dtype=torch.float32)
    assert len(rows) == len(raw)
    cfg = recipe()
    return {
        "kind": "weak4_pair_residual_fold_delta",
        "dataset_sha256": dataset_sha,
        "usage_scope": CLEAN_USAGE_SCOPE,
        "validation_scope": CLEAN_VALIDATION_SCOPE,
        "fold_id": fold_id,
        "recipe": cfg,
        "route_local_indices": rows,
        "raw_delta": raw,
        "delta": bounded_delta(raw, cfg["delta_max"]),
        "ids": [dataset["route_ids"][idx] for idx in rows.tolist()],
        "experiment_id": f"fold{fold_id}",
    }


class AucTests(unittest.TestCase):
    def test_rank_auc_handles_perfect_reverse_and_ties(self):
        self.assertEqual(binary_auc([0, 0, 1, 1], [0, 1, 2, 3]), 1.0)
        self.assertEqual(binary_auc([0, 0, 1, 1], [3, 2, 1, 0]), 0.0)
        self.assertEqual(binary_auc([0, 1], [1, 1]), 0.5)
        self.assertIsNone(binary_auc([1, 1], [0, 1]))

    def test_direction_correction_and_gap_auc(self):
        dataset = synthetic_dataset()
        surface = {
            "name": "synthetic",
            "route_local_indices": torch.arange(len(dataset["route_ids"])),
            # left-main rescues receive negative raw; protects positive.
            "raw_delta": torch.tensor([-0.6, -0.4, 0.4, 0.0, -0.5, 0.5]),
            "delta": bounded_delta(torch.tensor([-0.6, -0.4, 0.4, 0.0, -0.5, 0.5]), 2.0),
            "fold_ids": dataset["route_fold_ids"],
            "expected_folds": [0, 1],
            "recipe": recipe(),
            "sources": [],
        }
        arrays = surface_arrays(dataset, surface)
        report = raw_delta_auc_report(arrays)
        self.assertGreater(report["overall"]["auc"], 0.9)
        gap = gap_auc_report(arrays)["overall"]
        self.assertIsNotNone(gap["baseline_gap_auc"])
        self.assertGreaterEqual(gap["gap_plus_delta_auc"], gap["baseline_gap_auc"])
        quantiles = target_delta_quantiles(arrays)
        self.assertEqual(quantiles["rescue"]["rows"], 3)
        self.assertEqual(quantiles["protect"]["rows"], 2)
        self.assertEqual(quantiles["outside"]["rows"], 1)


class ContractAndOracleTests(unittest.TestCase):
    def test_fold_loader_enforces_heldout_coverage(self):
        dataset = synthetic_dataset()
        fold0 = fold_payload(dataset, 0, [-0.6, -0.4, 0.4, 0.0])
        fold1 = fold_payload(dataset, 1, [-0.5, 0.5])
        # Loader takes paths in production; exercise its contract through temp files.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, payload in enumerate((fold0, fold1)):
                path = Path(directory) / f"fold{index}.pt"
                torch.save(payload, path)
                paths.append(path)
            surface = load_delta_surface(
                dataset,
                paths,
                dataset_sha256="dataset-sha",
                expected_folds=[0, 1],
                name="test",
            )
            self.assertEqual(len(surface["raw_delta"]), 6)
            broken = copy.deepcopy(fold1)
            broken["route_local_indices"][0] = 0
            torch.save(broken, paths[1])
            with self.assertRaises(ValueError):
                load_delta_surface(
                    dataset,
                    paths,
                    dataset_sha256="dataset-sha",
                    expected_folds=[0, 1],
                    name="broken",
                )

    def test_bounded_oracle_and_curve(self):
        dataset = synthetic_dataset()
        raw = torch.tensor([-0.6, -0.4, 0.4, 0.0, -0.5, 0.5])
        surface = {
            "name": "synthetic",
            "route_local_indices": torch.arange(6),
            "raw_delta": raw,
            "delta": bounded_delta(raw, 2.0),
            "fold_ids": dataset["route_fold_ids"],
            "expected_folds": [0, 1],
            "recipe": recipe(),
            "sources": [],
        }
        oracle = bounded_reachable_oracle(dataset, surface)
        self.assertEqual(oracle["bounds"]["le_1"]["reachable_corrections_upper"], 2)
        self.assertEqual(oracle["bounds"]["le_2"]["reachable_corrections_upper"], 2)
        self.assertEqual(oracle["bounds"]["le_3"]["reachable_corrections_upper"], 3)
        self.assertGreater(oracle["bounds"]["le_2"]["macro_delta_upper"], 0)
        curve = threshold_scale_curve(dataset, surface, scales=[-1, 0, 1])
        no_op = next(
            cell for cell in curve["cells"]
            if cell["scale"] == 0 and cell["threshold_name"] == "no_gate"
        )
        self.assertEqual(no_op["changed"], 0)
        self.assertEqual(no_op["macro_delta"], 0.0)
        self.assertIn("low", {cell["tail"] for cell in curve["cells"]})
        self.assertTrue(any(cell["scale"] < 0 for cell in curve["cells"]))
        scaled_auc = scaled_gap_auc_curve(surface_arrays(dataset, surface), [-1, 1], 2.0)
        self.assertEqual([cell["scale"] for cell in scaled_auc["cells"]], [-1.0, 1.0])

    def test_sign_orientation_contract_on_synthetic_and_real_rows(self):
        dataset = synthetic_dataset()
        raw = torch.tensor([-0.6, -0.4, 0.4, 0.0, -0.5, 0.5])
        surface = {
            "name": "synthetic",
            "route_local_indices": torch.arange(6),
            "raw_delta": raw,
            "delta": bounded_delta(raw, 2.0),
            "fold_ids": dataset["route_fold_ids"],
            "expected_folds": [0, 1],
            "recipe": recipe(),
            "sources": [],
        }
        audit = sign_orientation_contract_audit(dataset, surface)
        self.assertTrue(audit["synthetic_all_passed"])
        real = audit["real_rows"]
        self.assertEqual(real["target_sign_mismatch_rows"], 0)
        self.assertEqual(real["target_kind_mismatch_rows"], 0)
        self.assertEqual(real["rescue_loss_descent_wrong_orientation_rows"], 0)
        self.assertEqual(real["protect_loss_descent_wrong_orientation_rows"], 0)
        self.assertEqual(real["apply_prediction_vs_adjusted_argmax_mismatch_rows"], 0)

    def test_negative_scale_parser(self):
        self.assertEqual(parse_scales("-2,-0.25,0,1"), [-2.0, -0.25, 0.0, 1.0])

    def test_clean_target_semantics_are_explicit(self):
        semantics = target_semantics(synthetic_dataset())
        self.assertEqual(semantics["c_conditioning"], "disabled")
        self.assertEqual(semantics["correct_counts_unique"], [1])
        self.assertIn("outside", semantics)
        self.assertEqual(semantics["target_histogram"]["noisy_alt"], 0)


class PermutationTests(unittest.TestCase):
    def test_session_block_null_is_deterministic(self):
        ids = [
            "a-step_01", "a-step_02", "b-step_01", "b-step_02",
            "c-step_01", "c-step_02", "d-step_01", "d-step_02",
        ]
        labels = torch.tensor([0, 0, 1, 1, 0, 1, 1, 0])
        scores = torch.tensor([-2.0, -1.0, 2.0, 1.0, -0.5, 0.5, 1.5, -1.5])
        first = session_block_permutation_auc_null(
            ids, labels, scores, permutations=100, seed=7
        )
        second = session_block_permutation_auc_null(
            ids, labels, scores, permutations=100, seed=7
        )
        self.assertEqual(first, second)
        self.assertGreater(first["observed_auc"], first["null_mean"])
        self.assertEqual(first["permutable_sessions"], 4)

    def test_full_session_permutation_rederives_targets(self):
        dataset = synthetic_dataset()
        raw = torch.tensor([-0.6, -0.4, 0.4, 0.0, -0.5, 0.5])
        surface = {
            "name": "synthetic",
            "route_local_indices": torch.arange(6),
            "raw_delta": raw,
            "delta": bounded_delta(raw, 2.0),
            "fold_ids": dataset["route_fold_ids"],
            "expected_folds": [0, 1],
            "recipe": recipe(),
            "sources": [],
        }
        arrays = surface_arrays(dataset, surface)
        report = full_session_label_permutation_null(
            dataset, surface, arrays, permutations=50, seed=9
        )
        self.assertIn("pooled_auc", report)
        self.assertIn("conditional_fold_pair_direction_auc", report)
        self.assertEqual(report["observed_target_rows"]["rescue"], 3)


if __name__ == "__main__":
    unittest.main()
