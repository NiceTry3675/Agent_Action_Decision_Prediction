import copy
import unittest
from types import SimpleNamespace

import torch

from aggregate_sim_policy_decoder_oof import aggregate_fold_payloads
from build_sim_policy_decoder_dataset import (
    PARENT_PROVENANCE_STITCHED,
    build_dataset_payload,
    validate_dataset,
    validate_parent_provenance,
)
from script import ALL_CLASSES
from sim_policy_decoder import (
    DATASET_FORMAT,
    DIAGNOSTIC_USAGE_SCOPE,
    FEATURE_SCHEMA,
    FOLD_FORMAT,
    MODEL_SCHEMA,
    OUTPUT_NAMES,
    SERIALIZER_NAME,
    SimPolicyDecoderModel,
    action_trajectory,
    apply_specialist_actions,
    build_recorded_policy_targets,
    encode_sim_policy_sample,
    hierarchical_joint_log_probabilities,
    hierarchical_policy_loss,
    ids_sha256,
    main_posterior_features,
    project_predictions_to_logits,
    pool_last_nonpadding,
    select_sim_weak4_routes,
    serialize_sim_policy_decoder_v1,
    stable_inner_fold,
    target_class_weights,
)
from train_sim_policy_decoder import (
    LOCKED_RECIPE_DEFAULTS,
    scaler_step_and_maybe_schedule,
    validate_locked_recipe,
)


class CharacterTokenizer:
    pad_token_id = 0
    eos_token_id = 2

    def encode(self, text, add_special_tokens=False):
        values = [ord(char) + 3 for char in str(text)]
        return ([1] + values + [2]) if add_special_tokens else values

    def decode(self, ids, skip_special_tokens=False, clean_up_tokenization_spaces=False):
        return "".join(chr(int(value) - 3) for value in ids if int(value) >= 3)

    def __call__(self, text, add_special_tokens=True, truncation=False):
        ids = self.encode(text, add_special_tokens=add_special_tokens)
        return {"input_ids": ids, "attention_mask": [1] * len(ids)}


def parent_row(top=0):
    row = torch.full((len(ALL_CLASSES),), -4.0)
    row[top] = 3.0
    row[(top + 1) % 4] = 1.0
    return row


class SerializerTests(unittest.TestCase):
    def test_golden_minimal_text_and_exclusions(self):
        tokenizer = CharacterTokenizer()
        sample = {
            "current_prompt": "open the spec",
            "session_meta": {"workspace": {"secret": "WORKSPACE_LEAK"}, "turn_index": 3},
            "history": [
                {"role": "user", "content": "USER_HISTORY_LEAK"},
                {
                    "role": "assistant_action",
                    "name": "grep_search",
                    "args": {"pattern": "ARGS_LEAK"},
                    "result_summary": "RESULT_LEAK",
                },
                {"role": "assistant_action", "name": "read_file"},
            ],
        }
        text, audit = serialize_sim_policy_decoder_v1(sample, tokenizer)
        self.assertEqual(
            text,
            "<SIM_POLICY>\n<P>open the spec</P>\n<A>grep_search>read_file</A>",
        )
        self.assertEqual(action_trajectory(sample), ("grep_search", "read_file"))
        self.assertEqual(audit["action_count"], 2)
        for forbidden in ("USER_HISTORY_LEAK", "ARGS_LEAK", "RESULT_LEAK", "WORKSPACE_LEAK", "turn_index"):
            self.assertNotIn(forbidden, text)

    def test_empty_trajectory_is_explicit_none(self):
        text, _ = serialize_sim_policy_decoder_v1(
            {"current_prompt": "x", "history": []}, CharacterTokenizer()
        )
        self.assertEqual(text, "<SIM_POLICY>\n<P>x</P>\n<A>none</A>")

    def test_korean_multiline_prompt_and_repeated_actions_golden(self):
        sample = {
            "current_prompt": "첫 줄입니다\n둘째 줄도 읽어줘",
            "history": [
                {"role": "assistant_action", "name": "read_file"},
                {"role": "assistant_action", "name": "read_file"},
                {"role": "assistant_action", "name": "grep_search"},
            ],
        }
        text, _ = serialize_sim_policy_decoder_v1(sample, CharacterTokenizer())
        self.assertEqual(
            text,
            "<SIM_POLICY>\n<P>첫 줄입니다\n둘째 줄도 읽어줘</P>\n"
            "<A>read_file>read_file>grep_search</A>",
        )

    def test_prompt_reduces_before_trajectory_and_preserves_both_ends(self):
        tokenizer = CharacterTokenizer()
        sample = {
            "current_prompt": "H" * 100 + "T" * 100,
            "history": [
                {"role": "assistant_action", "name": "read_file"},
                {"role": "assistant_action", "name": "glob_pattern"},
            ],
        }
        encoded, audit = encode_sim_policy_sample(
            sample,
            tokenizer,
            max_length=96,
            prompt_token_cap=192,
            prompt_head_tokens=128,
        )
        decoded = tokenizer.decode(encoded["input_ids"])
        self.assertLessEqual(len(encoded["input_ids"]), 96)
        self.assertIn("<A>read_file>glob_pattern</A>", decoded)
        self.assertIn("HH", decoded)
        self.assertIn("TT", decoded)
        self.assertTrue(audit["prompt_truncated"])
        self.assertEqual(audit["action_count"], 2)


class FeatureTargetTests(unittest.TestCase):
    def test_row_affine_invariance_and_shape(self):
        logits = torch.stack([parent_row(0), parent_row(2)])
        base = main_posterior_features(logits)
        shifted_scaled = main_posterior_features(logits * 7.25 + 19.0)
        torch.testing.assert_close(base, shifted_scaled, atol=2e-6, rtol=0)
        self.assertEqual(tuple(base.shape), (2, 7))
        torch.testing.assert_close(base[:, :4].sum(dim=1), torch.ones(2), atol=1e-6, rtol=0)

    def test_route_and_target_contract(self):
        ids = ["sess_sim_a-step_01", "sess_au_b-step_01", "sess_sim_c-step_01"]
        logits = torch.stack([parent_row(0), parent_row(1), parent_row(3)])
        route = select_sim_weak4_routes(ids, logits)
        self.assertEqual(route.tolist(), [0, 2])
        targets = build_recorded_policy_targets(
            [0, 2, 8, 1], [0, 0, 3, 3]
        )
        self.assertEqual(targets.tolist(), [0, 3, 0, 2])

    def test_unweighted_recipe_produces_exact_ones(self):
        weights = target_class_weights([0, 0, 1, 2, 3, 4], power=0.0)
        torch.testing.assert_close(weights, torch.ones(5), atol=0, rtol=0)

    def test_hierarchical_loss_masks_main_and_smooths_only_valid_three(self):
        raw = torch.tensor(
            [[0.2, 0.0, 1.0, 2.0, 3.0], [-0.5, 2.0, 1.0, 0.0, -1.0]],
            requires_grad=True,
        )
        targets = torch.tensor([4, 0])
        main = torch.tensor([1, 0])
        loss, details = hierarchical_policy_loss(
            raw,
            targets,
            main,
            conditional_loss_weight=0.5,
            gate_pos_weight=1.0,
            label_smoothing=0.02,
        )
        self.assertTrue(torch.isfinite(loss))
        self.assertEqual(
            float(details["masked_alt_logits"][0, 1].detach()), -float("inf")
        )
        valid_logits = torch.tensor([0.0, 2.0, 3.0])
        valid_logp = torch.log_softmax(valid_logits, dim=0)
        expected_conditional = 0.98 * (-valid_logp[2]) + 0.02 * (-valid_logp.mean())
        self.assertAlmostEqual(
            float(details["conditional_loss"].detach()),
            float(expected_conditional),
            places=6,
        )
        loss.backward()
        self.assertTrue(torch.isfinite(raw.grad).all())
        self.assertEqual(float(raw.grad[0, 2]), 0.0)

    def test_hierarchical_joint_is_normalized_and_uses_factorized_argmax(self):
        raw = torch.zeros((2, 5))
        raw[1] = torch.tensor([5.0, 0.0, 0.0, 0.0, 10.0])
        joint = hierarchical_joint_log_probabilities(raw, [0, 0])
        torch.testing.assert_close(
            joint.exp().sum(dim=1), torch.ones(2), atol=1e-6, rtol=0
        )
        self.assertTrue(torch.isfinite(joint).all())
        self.assertTrue((joint[:, 1] < -1.0e8).all())
        self.assertEqual(joint.argmax(dim=1).tolist(), [0, 4])


class TinyEncoder(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(hidden_size=5)
        self.embedding = torch.nn.Embedding(32, 5)

    def forward(self, input_ids, attention_mask=None, return_dict=True, **kwargs):
        return SimpleNamespace(last_hidden_state=self.embedding(input_ids))


class ModelActionTests(unittest.TestCase):
    def test_last_nonpadding_pool_is_equal_for_left_and_right_padding(self):
        hidden = torch.tensor([[[99.0], [1.0], [2.0]], [[1.0], [2.0], [99.0]]])
        mask = torch.tensor([[0, 1, 1], [1, 1, 0]])
        pooled = pool_last_nonpadding(hidden, mask)
        torch.testing.assert_close(pooled, torch.tensor([[2.0], [2.0]]), atol=0, rtol=0)

    def test_model_is_exact_keep_at_initialization_with_left_padding(self):
        model = SimPolicyDecoderModel(TinyEncoder(), fusion_dim=9, dropout=0.0)
        logits = model(
            input_ids=torch.tensor([[0, 3, 4], [5, 6, 7]]),
            attention_mask=torch.tensor([[0, 1, 1], [1, 1, 1]]),
            numeric_features=torch.randn(2, 7),
        )
        self.assertTrue(torch.equal(logits, torch.zeros(2, 5)))
        self.assertEqual(logits.argmax(dim=1).tolist(), [0, 0])

    def test_apply_is_identity_outside_route_and_projection_matches(self):
        parent = torch.stack([parent_row(0), parent_row(1), parent_row(2)])
        prediction = apply_specialist_actions(parent, [0, 2], [3, 0])
        self.assertEqual(prediction.tolist(), [2, 1, 2])
        projected = project_predictions_to_logits(parent, prediction)
        self.assertEqual(projected.argmax(dim=1).tolist(), prediction.tolist())
        torch.testing.assert_close(projected[1], parent[1], atol=0, rtol=0)


def synthetic_fold_payloads(samples, labels):
    by_fold = {0: [], 1: [], 2: []}
    for index, sample in enumerate(samples):
        by_fold[index % 3].append(index)
    payloads = []
    for fold in range(3):
        rows = by_fold[fold]
        payloads.append(
            {
                "classes": ALL_CLASSES,
                "split": "session_oof",
                "n_folds": 3,
                "seed": 42,
                "serializer_name": "current_v1",
                "base_model": "hcx-test",
                "max_length": 384,
                "class_bias": [0.0] * len(ALL_CLASSES),
                "fold_id": fold,
                "ids": [samples[row]["id"] for row in rows],
                "indices": rows,
                "y_true": [labels[samples[row]["id"]] for row in rows],
                "logits": torch.stack([parent_row(row % 4) for row in rows]),
            }
        )
    return payloads


class DatasetProvenanceTests(unittest.TestCase):
    def test_real_builder_contract_is_explicitly_diagnostic(self):
        samples = [
            {"id": f"sess_sim_s{index}-step_01", "current_prompt": "x", "history": []}
            for index in range(12)
        ]
        labels = {sample["id"]: index % 4 for index, sample in enumerate(samples)}
        payload = build_dataset_payload(samples, labels, synthetic_fold_payloads(samples, labels))
        validate_dataset(payload)
        self.assertEqual(payload["format"], DATASET_FORMAT)
        self.assertEqual(payload["usage_scope"], DIAGNOSTIC_USAGE_SCOPE)
        self.assertEqual(payload["parent"]["provenance_mode"], PARENT_PROVENANCE_STITCHED)
        eligibility = validate_parent_provenance(payload["parent"], payload["usage_scope"])
        self.assertFalse(eligibility["promotion_eligible"])
        self.assertEqual(len(payload["route_ids"]), 12)
        corrupted = copy.deepcopy(payload)
        corrupted["targets"][0] = (int(corrupted["targets"][0]) + 1) % len(OUTPUT_NAMES)
        with self.assertRaisesRegex(ValueError, "targets do not reproduce"):
            validate_dataset(corrupted)
        corrupted = copy.deepcopy(payload)
        corrupted["numeric_features"][0, 0] += 1e-3
        with self.assertRaisesRegex(ValueError, "numeric features do not reproduce"):
            validate_dataset(corrupted)

    def test_parent_inner_aggregate_covers_every_row_and_cannot_promote(self):
        # Build 27 unique sessions so all 3x3 parent/inner partitions are
        # represented under the locked hash, then synthesize identity outputs.
        samples = []
        labels = {}
        parent_assignment = []
        for parent in range(3):
            found = {0: [], 1: [], 2: []}
            candidate = 0
            while any(len(values) < 3 for values in found.values()):
                session = f"sess_sim_p{parent}_{candidate}"
                inner = stable_inner_fold(session, parent, 3, 42013)
                if len(found[inner]) < 3:
                    found[inner].append(session)
                candidate += 1
            for inner in range(3):
                for session in found[inner]:
                    sample_id = f"{session}-step_01"
                    samples.append({"id": sample_id, "current_prompt": "x", "history": []})
                    labels[sample_id] = 0
                    parent_assignment.append(parent)
        # Custom source folds matching parent_assignment.
        source_folds = []
        for parent in range(3):
            rows = [index for index, value in enumerate(parent_assignment) if value == parent]
            source_folds.append(
                {
                    "classes": ALL_CLASSES,
                    "split": "session_oof",
                    "n_folds": 3,
                    "seed": 42,
                    "serializer_name": "current_v1",
                    "base_model": "hcx-test",
                    "max_length": 384,
                    "class_bias": [0.0] * 14,
                    "fold_id": parent,
                    "ids": [samples[row]["id"] for row in rows],
                    "indices": rows,
                    "y_true": [0] * len(rows),
                    "logits": torch.stack([parent_row(0) for _ in rows]),
                }
            )
        dataset = build_dataset_payload(samples, labels, source_folds)
        recipe = {
            "model_schema": MODEL_SCHEMA,
            "feature_schema": FEATURE_SCHEMA,
            "serializer_name": SERIALIZER_NAME,
            "base_model": "hcx-test",
            "cv_mode": "parent_inner3",
            "n_inner_folds": 3,
            "inner_seed": 42013,
        }
        folds = []
        route_indices = torch.as_tensor(dataset["route_indices"])
        for parent in range(3):
            for inner in range(3):
                full_rows = [
                    row
                    for row, session in enumerate(dataset["session_ids"])
                    if int(dataset["full_fold_ids"][row]) == parent
                    and stable_inner_fold(session, parent, 3, 42013) == inner
                ]
                full_set = set(full_rows)
                route_rows = [
                    row for row, full in enumerate(route_indices.tolist()) if full in full_set
                ]
                logits = torch.zeros((len(route_rows), 5))
                folds.append(
                    {
                        "format": FOLD_FORMAT,
                        "dataset_sha256": "sha",
                        "usage_scope": dataset["usage_scope"],
                        "parent_provenance": dataset["parent"],
                        "promotion_eligible": False,
                        "split_spec": {
                            "cv_mode": "parent_inner3",
                            "parent_fold": parent,
                            "inner_fold": inner,
                            "n_inner_folds": 3,
                            "inner_seed": 42013,
                        },
                        "n_folds": 9,
                        "val_full_indices": torch.tensor(full_rows),
                        "route_local_indices": torch.tensor(route_rows),
                        "route_ids": [dataset["route_ids"][row] for row in route_rows],
                        "action_logits": logits,
                        "actions": torch.zeros(len(route_rows), dtype=torch.long),
                        "outputs": list(OUTPUT_NAMES),
                        "recipe": recipe,
                        "metrics": {},
                    }
                )
        result = aggregate_fold_payloads(dataset, folds, dataset_sha256="sha")
        self.assertEqual(result["changed"], 0)
        self.assertFalse(result["promotion_eligible"])
        self.assertFalse(result["gate"]["passed"])
        self.assertFalse(result["gate"]["provenance_promotion_eligible"])
        corrupted_folds = copy.deepcopy(folds)
        corrupted_folds[0]["action_logits"][0, 0] = float("nan")
        corrupted_folds[0]["actions"] = corrupted_folds[0]["action_logits"].argmax(dim=1)
        with self.assertRaisesRegex(ValueError, "non-finite"):
            aggregate_fold_payloads(dataset, corrupted_folds, dataset_sha256="sha")


class RecipeLockTests(unittest.TestCase):
    def test_recipe_mismatch_fails_closed_without_explicit_ablation(self):
        args = SimpleNamespace(**LOCKED_RECIPE_DEFAULTS, allow_recipe_ablation=False)
        self.assertEqual(validate_locked_recipe(args), {})
        args.epochs = 2
        with self.assertRaises(ValueError):
            validate_locked_recipe(args)
        args.allow_recipe_ablation = True
        self.assertEqual(validate_locked_recipe(args), {"epochs": (2, 3)})

    def test_hierarchical_objective_requires_ablation_flag(self):
        args = SimpleNamespace(**LOCKED_RECIPE_DEFAULTS, allow_recipe_ablation=False)
        args.objective = "hierarchical"
        with self.assertRaises(ValueError):
            validate_locked_recipe(args)
        args.allow_recipe_ablation = True
        self.assertEqual(
            validate_locked_recipe(args), {"objective": ("hierarchical", "direct5")}
        )

    def test_scheduler_does_not_advance_when_scaler_skips_optimizer(self):
        class FakeScaler:
            def __init__(self, old, new):
                self.scale = old
                self.new = new

            def get_scale(self):
                return self.scale

            def step(self, optimizer):
                pass

            def update(self):
                self.scale = self.new

            def is_enabled(self):
                return True

        class FakeScheduler:
            def __init__(self):
                self.steps = 0

            def step(self):
                self.steps += 1

        skipped_scheduler = FakeScheduler()
        self.assertFalse(
            scaler_step_and_maybe_schedule(
                FakeScaler(65536.0, 32768.0), object(), skipped_scheduler
            )
        )
        self.assertEqual(skipped_scheduler.steps, 0)
        successful_scheduler = FakeScheduler()
        self.assertTrue(
            scaler_step_and_maybe_schedule(
                FakeScaler(32768.0, 32768.0), object(), successful_scheduler
            )
        )
        self.assertEqual(successful_scheduler.steps, 1)


if __name__ == "__main__":
    unittest.main()
