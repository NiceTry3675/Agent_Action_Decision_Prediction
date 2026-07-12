import copy
import unittest
from types import SimpleNamespace

import torch

from aggregate_weak4_pair_oof import aggregate_fold_payloads
from build_weak4_pair_dataset import build_dataset_payload
from script import ALL_CLASSES
from train_weak4_pair_residual import (
    DEFAULT_MAX_LENGTH,
    resolve_run_identity,
    token_length_report,
    validate_dataset as validate_pair_dataset,
)
from weak4_pair_residual import (
    CLEAN_USAGE_SCOPE,
    CLEAN_VALIDATION_SCOPE,
    DATASET_KIND,
    DIAGNOSTIC_USAGE_SCOPE,
    FEATURE_SCHEMA,
    LIVE_WEAK4_PAIRS,
    TARGET_NOISY_ALT,
    TARGET_OUTSIDE,
    TARGET_PROTECT,
    TARGET_RESCUE,
    Weak4PairResidualModel,
    apply_pair_residual,
    assign_session_folds,
    balanced_target_weights,
    build_pair_targets,
    main_numeric_features,
    pair_residual_loss,
    select_live_pair_routes,
    serialize_weak_policy_pair_v1,
)


SYNTHETIC_RECIPE = {
    "model_schema": "mbert-live-pair-residual-v1",
    "feature_schema": "weak4-main-probabilities-v1",
    "base_model": "tiny-multilingual",
    "serializer_name": "weak_policy_pair_v1",
    "seed": 42,
    "max_length": 384,
    "epochs": 3,
    "encoder_lr": 2e-5,
    "head_lr": 1e-4,
    "batch_size": 32,
    "eval_batch_size": 64,
    "weight_decay": 0.01,
    "warmup_ratio": 0.06,
    "grad_clip": 1.0,
    "delta_max": 2.0,
    "fusion_dim": 256,
    "dropout": 0.1,
    "gradient_checkpointing": True,
}


def logits_row(read=0.0, grep=0.0, listing=0.0, glob=0.0, other=-5.0):
    row = torch.full((len(ALL_CLASSES),), float(other))
    row[:4] = torch.tensor([read, grep, listing, glob])
    return row


class RoutingAndFeatureTests(unittest.TestCase):
    def test_only_declared_live_pairs_are_routed(self):
        logits = torch.stack(
            [
                logits_row(read=4, listing=3),             # read/list
                logits_row(grep=3, glob=4),                # grep/glob
                logits_row(read=4, grep=3),                # dead read/grep
                logits_row(read=4, listing=3, other=5),    # non-Weak4 full top
                logits_row(listing=3, glob=4),             # list/glob
            ]
        )
        route = select_live_pair_routes(logits)
        self.assertEqual(route["indices"].tolist(), [0, 1, 4])
        self.assertEqual(route["pair_classes"].tolist(), [[0, 2], [1, 3], [2, 3]])
        self.assertEqual(route["top_sides"].tolist(), [0, 1, 1])

    def test_numeric_features_are_shift_invariant(self):
        logits = torch.stack(
            [logits_row(read=3.2, listing=2.8), logits_row(grep=1.5, glob=2.1)]
        )
        route = select_live_pair_routes(logits)
        features = main_numeric_features(logits, route)
        shifted_route = select_live_pair_routes(logits + 11.0)
        shifted = main_numeric_features(logits + 11.0, shifted_route)
        torch.testing.assert_close(features, shifted, atol=1e-6, rtol=0)
        self.assertEqual(tuple(features.shape), (2, 10))

    def test_zero_is_exact_identity_and_nonzero_stays_in_pair(self):
        logits = torch.stack(
            [
                logits_row(read=0.5, listing=0.4, other=0.45),
                logits_row(grep=0.3, glob=0.2),
            ]
        )
        route = select_live_pair_routes(logits)
        adjusted0, pred0, delta0 = apply_pair_residual(logits, route, torch.zeros(2))
        self.assertTrue(torch.equal(adjusted0, logits.float()))
        self.assertTrue(torch.equal(pred0, logits.argmax(1)))
        self.assertTrue(torch.equal(delta0, torch.zeros(2)))

        adjusted, pred, delta = apply_pair_residual(logits, route, torch.tensor([-20.0, 20.0]))
        self.assertLessEqual(float(delta.abs().max()), 2.0)
        for local, full_row in enumerate(route["indices"].tolist()):
            pair = set(route["pair_classes"][local].tolist())
            self.assertIn(int(pred[full_row]), pair)
            for class_id in set(range(len(ALL_CLASSES))) - pair:
                self.assertEqual(float(adjusted[full_row, class_id]), float(logits[full_row, class_id]))

    def test_family_lock_projects_switched_pair_above_nonweak_runner_up(self):
        # read is the original full argmax, list is far below the non-Weak4
        # runner-up, and a near-max negative residual barely switches to list.
        row = logits_row(read=1.0, grep=-2.0, listing=-0.9, glob=-2.0, other=0.95)
        logits = row.unsqueeze(0)
        route = select_live_pair_routes(logits)
        adjusted, pred, delta = apply_pair_residual(
            logits,
            route,
            torch.tensor([-4.0]),
            delta_max=2.0,
        )
        self.assertLess(float(route["base_gaps"][0] + delta[0]), 0.0)
        self.assertEqual(int(pred[0]), 2)
        self.assertEqual(int(adjusted.argmax(dim=1)[0]), 2)
        # Common pair offset preserves the bounded residual gap exactly up to
        # float32 arithmetic, while the other twelve logits remain untouched.
        self.assertAlmostEqual(
            float(adjusted[0, 0] - adjusted[0, 2]),
            float(route["base_gaps"][0] + delta[0]),
            places=6,
        )
        for class_id in set(range(len(ALL_CLASSES))) - {0, 2}:
            self.assertEqual(float(adjusted[0, class_id]), float(logits[0, class_id]))


class TargetAndLossTests(unittest.TestCase):
    def setUp(self):
        self.logits = torch.stack([logits_row(read=3, listing=2) for _ in range(4)])
        self.route = select_live_pair_routes(self.logits)

    def test_target_kinds_and_c0_mask(self):
        # main, alt+c>0, alt+c0, outside-pair
        kinds, signs = build_pair_targets([0, 2, 2, 1], self.route, [3, 1, 0, 0])
        self.assertEqual(
            kinds.tolist(),
            [TARGET_PROTECT, TARGET_RESCUE, TARGET_NOISY_ALT, TARGET_OUTSIDE],
        )
        self.assertEqual(signs.tolist(), [1.0, -1.0, -1.0, 0.0])

    def test_group_weights_preserve_kind_mass_ratios(self):
        kinds = torch.tensor(
            [TARGET_PROTECT] * 4 + [TARGET_RESCUE] * 2 + [TARGET_OUTSIDE] * 3 + [TARGET_NOISY_ALT]
        )
        pairs = torch.tensor([0, 0, 1, 1, 0, 1, 0, 0, 1, 0])
        sides = torch.tensor([0, 1, 0, 1, 0, 1, 0, 1, 0, 0])
        weights = balanced_target_weights(kinds, pairs, sides)
        protect = float(weights[kinds == TARGET_PROTECT].sum())
        rescue = float(weights[kinds == TARGET_RESCUE].sum())
        outside = float(weights[kinds == TARGET_OUTSIDE].sum())
        self.assertAlmostEqual(protect / rescue, 1.0, places=6)
        self.assertAlmostEqual(outside / rescue, 0.25, places=6)
        self.assertEqual(float(weights[kinds == TARGET_NOISY_ALT].sum()), 0.0)

    def test_rescue_gradient_moves_gap_toward_alternative(self):
        raw = torch.zeros(1, requires_grad=True)
        loss, details = pair_residual_loss(
            raw,
            torch.tensor([1.0]),
            torch.tensor([-1.0]),
            torch.tensor([TARGET_RESCUE]),
            torch.ones(1),
        )
        loss.backward()
        self.assertGreater(float(raw.grad), 0.0)  # gradient descent makes delta negative
        self.assertEqual(float(details["delta"].detach()), 0.0)


class SerializerAndFoldTests(unittest.TestCase):
    def test_policy_serializer_keeps_multiline_prompt_and_latest_first_events(self):
        sample = {
            "current_prompt": "first line\nsecond line",
            "session_meta": {
                "turn_index": 4,
                "workspace": {"git_dirty": True, "last_ci_status": "pass", "open_files": ["src/a.py"]},
            },
            "history": [
                {"role": "user", "content": "find it"},
                {"role": "assistant_action", "name": "grep_search", "args": {"pattern": "A"}, "result_summary": "2 matches"},
                {"role": "assistant_action", "name": "read_file", "args": {"path": "src/a.py"}, "result_summary": "ok"},
            ],
        }
        text = serialize_weak_policy_pair_v1(sample, 0, 0)
        self.assertIn("current: first line\nsecond line", text)
        self.assertLess(text.index("event_-1:"), text.index("event_-2:"))
        self.assertIn('args={"path":"src/a.py"}', text.split("event_-1:", 1)[1])
        self.assertIn("result_bucket=ok", text)

    def test_session_folds_are_disjoint_and_deterministic(self):
        ids = ["s1-step_01", "s1-step_02", "s2-step_01", "s3-step_01", "s4-step_01", "s5-step_01"]
        strata = [0, 1, 0, 1, 0, 1]
        first = assign_session_folds(ids, strata, n_folds=3, seed=42)
        second = assign_session_folds(ids, strata, n_folds=3, seed=42)
        self.assertTrue(torch.equal(first, second))
        self.assertEqual(int(first[0]), int(first[1]))
        self.assertEqual(set(first.tolist()), {0, 1, 2})


class TinyEncoder(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(hidden_size=6)
        self.embedding = torch.nn.Embedding(20, 6)

    def forward(self, input_ids, attention_mask=None, return_dict=True, **kwargs):
        return SimpleNamespace(last_hidden_state=self.embedding(input_ids))


class ModelTests(unittest.TestCase):
    def test_head_is_exactly_zero_initialized(self):
        torch.manual_seed(7)
        model = Weak4PairResidualModel(TinyEncoder(), fusion_dim=12, dropout=0.0)
        raw = model(
            input_ids=torch.tensor([[1, 2], [3, 4]]),
            attention_mask=torch.ones(2, 2, dtype=torch.long),
            numeric_features=torch.randn(2, 10),
            pair_ids=torch.tensor([0, 3]),
            top_sides=torch.tensor([0, 1]),
        )
        self.assertTrue(torch.equal(raw, torch.zeros(2)))
        self.assertEqual(int(torch.count_nonzero(model.output.weight)), 0)
        self.assertEqual(int(torch.count_nonzero(model.output.bias)), 0)

    def test_default_fold_run_identities_do_not_collide(self):
        exp0, out0 = resolve_run_identity("weak_policy_pair_v1", 0, "pair_screen")
        exp1, out1 = resolve_run_identity("weak_policy_pair_v1", 1, "pair_screen")
        expf, outf = resolve_run_identity("weak_policy_pair_v1", -1, "pair_screen")
        self.assertEqual(exp0, "pair_screen_fold0")
        self.assertEqual(exp1, "pair_screen_fold1")
        self.assertEqual(expf, "pair_screen_final")
        self.assertEqual(len({out0, out1, outf}), 3)

    def test_default_length_and_truncation_report(self):
        self.assertEqual(DEFAULT_MAX_LENGTH, 384)
        report = token_length_report([100, 384, 385, 448], DEFAULT_MAX_LENGTH)
        self.assertEqual(report["rows"], 4)
        self.assertEqual(report["truncated_rows"], 2)
        self.assertEqual(report["truncation_rate"], 0.5)
        self.assertEqual(report["max"], 448)


class DatasetAndAggregateTests(unittest.TestCase):
    @staticmethod
    def synthetic_payload():
        samples = []
        labels = {}
        ids = []
        logits = []
        y_true = []
        # Every row is a live read/list route; sessions remain independent.
        for idx in range(12):
            sample_id = f"session{idx}-step_01"
            label_id = [0, 2, 0, 1][idx % 4]
            samples.append({"id": sample_id, "current_prompt": f"prompt {idx}", "history": []})
            labels[sample_id] = ALL_CLASSES[label_id]
            ids.append(sample_id)
            logits.append(logits_row(read=3.0, listing=2.0))
            y_true.append(label_id)
        parent = {
            "classes": ALL_CLASSES,
            "ids": ids,
            "logits": torch.stack(logits),
            "y_true": y_true,
            "split": "session",
            "seed": 42,
            "base_model": "tiny-parent",
            "serializer_name": "current_v1",
        }
        consensus = {
            "classes": ALL_CLASSES,
            "ids": list(reversed(ids)),
            "y_true": torch.tensor(list(reversed(y_true))),
            "correct_counts": torch.tensor([1] * len(ids)),
        }
        return build_dataset_payload(
            samples,
            labels,
            parent,
            consensus,
            n_folds=3,
            fold_seed=42,
            require_clean_fixed=False,
        )

    def test_dataset_is_id_joined_and_fold_aggregate_zero_is_identity(self):
        dataset = self.synthetic_payload()
        self.assertEqual(dataset["kind"], DATASET_KIND)
        self.assertEqual(dataset["feature_schema"], FEATURE_SCHEMA)
        self.assertEqual(len(dataset["route_ids"]), 12)
        folds = []
        route_folds = torch.as_tensor(dataset["route_fold_ids"])
        for fold_id in range(3):
            rows = (route_folds == fold_id).nonzero(as_tuple=False).view(-1)
            folds.append(
                {
                    "kind": "weak4_pair_residual_fold_delta",
                    "fold_id": fold_id,
                    "n_folds": 3,
                    "route_local_indices": rows,
                    "ids": [dataset["route_ids"][row] for row in rows.tolist()],
                    "raw_delta": torch.zeros(len(rows)),
                    "delta_max": 2.0,
                    "serializer_name": "weak_policy_pair_v1",
                    "usage_scope": dataset["usage_scope"],
                    "validation_scope": dataset["validation_scope"],
                    "recipe": dict(SYNTHETIC_RECIPE),
                    "base_model": SYNTHETIC_RECIPE["base_model"],
                    "seed": SYNTHETIC_RECIPE["seed"],
                    "max_length": SYNTHETIC_RECIPE["max_length"],
                    "epochs": SYNTHETIC_RECIPE["epochs"],
                    "encoder_lr": SYNTHETIC_RECIPE["encoder_lr"],
                    "head_lr": SYNTHETIC_RECIPE["head_lr"],
                    "batch_size": SYNTHETIC_RECIPE["batch_size"],
                    "eval_batch_size": SYNTHETIC_RECIPE["eval_batch_size"],
                    "fusion_dim": SYNTHETIC_RECIPE["fusion_dim"],
                    "experiment_id": f"fold{fold_id}",
                }
            )
        with self.assertRaises(ValueError):
            aggregate_fold_payloads(dataset, folds)
        result = aggregate_fold_payloads(dataset, folds, allow_diagnostic=True)
        self.assertAlmostEqual(result["macro_delta"], 0.0, places=12)
        self.assertEqual(result["changed"], 0)
        self.assertEqual(result["scope_mode"], "diagnostic")
        self.assertEqual(result["base_model"], "tiny-multilingual")
        self.assertEqual(result["seed"], 42)
        self.assertTrue(torch.equal(result["predictions"], dataset["parent_logits"].argmax(1)))
        declared_clean = copy.deepcopy(dataset)
        declared_clean["usage_scope"] = CLEAN_USAGE_SCOPE
        declared_clean["validation_scope"] = CLEAN_VALIDATION_SCOPE
        declared_clean["reliability_source"] = "none"
        clean_folds = copy.deepcopy(folds)
        for fold in clean_folds:
            fold["usage_scope"] = CLEAN_USAGE_SCOPE
            fold["validation_scope"] = CLEAN_VALIDATION_SCOPE
        clean_result = aggregate_fold_payloads(declared_clean, clean_folds)
        self.assertEqual(clean_result["scope_mode"], "clean")
        mismatched = copy.deepcopy(folds)
        mismatched[1]["recipe"]["seed"] = 777
        with self.assertRaises(ValueError):
            aggregate_fold_payloads(dataset, mismatched, allow_diagnostic=True)

    def test_clean_fixed_contract_uses_no_crossfold_reliability(self):
        samples = [
            {"id": f"s{idx}-step_01", "current_prompt": "x", "history": []}
            for idx in range(6)
        ]
        labels = {sample["id"]: ALL_CLASSES[2] for sample in samples}
        parent = {
            "classes": ALL_CLASSES,
            "ids": [sample["id"] for sample in samples],
            "logits": torch.stack([logits_row(read=3, listing=2) for _ in samples]),
            "y_true": [2] * len(samples),
            "split": "session",
            "seed": 42,
        }
        consensus = {
            "classes": ALL_CLASSES,
            "ids": parent["ids"],
            "y_true": torch.tensor(parent["y_true"]),
            "correct_counts": torch.zeros(len(samples), dtype=torch.uint8),
        }
        clean = build_dataset_payload(
            samples, labels, parent, None, n_folds=3, require_clean_fixed=False
        )
        self.assertEqual(clean["reliability_source"], "none")
        self.assertEqual(clean["usage_scope"], DIAGNOSTIC_USAGE_SCOPE)
        self.assertTrue(clean["validation_scope"].startswith("construction_diagnostic_"))
        self.assertEqual(set(clean["target_kinds"].tolist()), {TARGET_RESCUE})
        with self.assertRaises(ValueError):
            validate_pair_dataset(clean)
        self.assertEqual(
            validate_pair_dataset(clean, allow_diagnostic=True), "diagnostic"
        )
        declared_clean = dict(clean)
        declared_clean["usage_scope"] = CLEAN_USAGE_SCOPE
        declared_clean["validation_scope"] = CLEAN_VALIDATION_SCOPE
        self.assertEqual(validate_pair_dataset(declared_clean), "clean")
        with self.assertRaises(ValueError):
            build_dataset_payload(
                samples,
                labels,
                parent,
                consensus,
                n_folds=3,
                require_clean_fixed=True,
            )


if __name__ == "__main__":
    unittest.main()
