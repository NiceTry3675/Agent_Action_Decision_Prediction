import unittest

import torch

from export_privileged_mode_cache import llama_sequence_lengths_446
from privileged_mode_residual import (
    SelectiveModeResidual,
    combine_kd_and_mode_loss,
    conditional_mode_loss_values,
)
from probe_privileged_mode_residual import make_permuted_targets
from script import ALL_CLASSES, serialize_transformer_sample
from train_transformer import replay_examples_for_sample


class ResidualHeadTests(unittest.TestCase):
    def test_zero_init_is_exact_parent_identity(self):
        torch.manual_seed(7)
        model = SelectiveModeResidual(
            hidden_size=5,
            num_actions=len(ALL_CLASSES),
            priors_by_action={0: [0.8, 0.2], 4: [0.2, 0.3, 0.5], 7: [0.1, 0.9]},
        )
        hidden = torch.randn(11, 5, dtype=torch.float16)
        parent = torch.randn(11, len(ALL_CLASSES), dtype=torch.float32)
        correction = model.corrections(hidden)
        self.assertEqual(float(correction.detach().abs().max()), 0.0)
        self.assertTrue(torch.equal(model(hidden, parent), parent))

    def test_nonselected_actions_never_change(self):
        model = SelectiveModeResidual(3, len(ALL_CLASSES), {0: [0.5, 0.5]})
        with torch.no_grad():
            model.mode_heads["0"].weight.normal_()
            model.mode_heads["0"].bias.normal_()
        hidden = torch.randn(4, 3)
        parent = torch.randn(4, len(ALL_CLASSES))
        output = model(hidden, parent)
        self.assertTrue(torch.equal(output[:, 1:], parent[:, 1:]))
        self.assertFalse(torch.equal(output[:, 0], parent[:, 0]))

    def test_action_and_mode_losses_reach_residual_parameters(self):
        model = SelectiveModeResidual(4, len(ALL_CLASSES), {0: [0.6, 0.4]})
        hidden = torch.randn(6, 4)
        parent = torch.randn(6, len(ALL_CLASSES))
        labels = torch.tensor([0, 0, 0, 1, 2, 3])
        logits = model(hidden, parent)
        action_loss = torch.nn.functional.cross_entropy(logits, labels, reduction="none")
        mode_loss, known = conditional_mode_loss_values(
            model,
            hidden,
            mode_action_ids=torch.tensor([0, 0, 0, -1, -1, -1]),
            mode_local_ids=torch.tensor([0, 1, 0, -1, -1, -1]),
        )
        (action_loss + 0.15 * known.float() * mode_loss).mean().backward()
        self.assertGreater(int(torch.count_nonzero(model.mode_heads["0"].weight.grad)), 0)

    def test_nonselected_true_rows_supply_negative_action_gradient(self):
        model = SelectiveModeResidual(4, len(ALL_CLASSES), {0: [0.6, 0.4]})
        hidden = torch.randn(5, 4)
        parent = torch.randn(5, len(ALL_CLASSES))
        labels = torch.tensor([1, 2, 3, 4, 5])
        loss = torch.nn.functional.cross_entropy(model(hidden, parent), labels)
        loss.backward()
        self.assertGreater(int(torch.count_nonzero(model.mode_heads["0"].weight.grad)), 0)

    def test_k_one_branch_is_rejected(self):
        with self.assertRaises(ValueError):
            SelectiveModeResidual(3, len(ALL_CLASSES), {10: [1.0]})


class HiddenCacheContractTests(unittest.TestCase):
    def test_pooling_indices_match_transformers_446_right_padding(self):
        input_ids = torch.tensor(
            [
                [10, 11, 12, 99, 99],
                [20, 21, 22, 23, 24],
                [30, 99, 99, 99, 99],
            ]
        )
        self.assertEqual(
            llama_sequence_lengths_446(input_ids, pad_token_id=99).tolist(),
            [2, 4, 0],
        )


class LossContractTests(unittest.TestCase):
    def test_prior_entropy_normalization_is_positive_and_unit_at_init(self):
        model = SelectiveModeResidual(4, len(ALL_CLASSES), {0: [0.8, 0.2]})
        hidden = torch.randn(5, 4)
        values, known = conditional_mode_loss_values(
            model,
            hidden,
            mode_action_ids=torch.tensor([0, 0, 0, 0, 0]),
            mode_local_ids=torch.tensor([0, 0, 0, 0, 1]),
            normalization="prior_entropy",
        )
        self.assertTrue(bool(known.all()))
        self.assertTrue(bool((values > 0).all()))
        torch.testing.assert_close(values.mean(), torch.tensor(1.0), atol=1e-6, rtol=1e-6)

    def test_mode_loss_is_outside_kd_interpolation(self):
        action = torch.tensor([2.0, 2.0])
        kd = torch.tensor([4.0, 4.0])
        alpha = torch.tensor([0.5, 0.9])
        mode = torch.tensor([3.0, 3.0])
        known = torch.tensor([True, True])
        result = combine_kd_and_mode_loss(
            action, kd, alpha, mode, known, mode_weight=0.2
        )
        expected = (1.0 - alpha) * action + alpha * kd + 0.2 * mode
        torch.testing.assert_close(result, expected)

    def test_zero_mode_weight_is_existing_kd_loss(self):
        action = torch.tensor([1.0, 2.0])
        kd = torch.tensor([3.0, 4.0])
        alpha = torch.tensor([0.0, 0.5])
        mode = torch.tensor([9.0, 9.0])
        known = torch.tensor([True, True])
        result = combine_kd_and_mode_loss(
            action, kd, alpha, mode, known, mode_weight=0.0
        )
        torch.testing.assert_close(result, (1.0 - alpha) * action + alpha * kd)


class PermutationContractTests(unittest.TestCase):
    def test_shuffle_preserves_each_action_turn_stratum_counts(self):
        local = torch.tensor([0, 0, 1, 1, 0, 1, 0, 1, -100])
        actions = torch.tensor([0, 0, 0, 0, 4, 4, 4, 4, -100])
        turns = ["start"] * 4 + ["early"] * 4 + ["long"]
        shuffled, metadata = make_permuted_targets(
            local, actions, list(range(len(local))), turns, seed=101
        )
        self.assertEqual(sorted(shuffled[:4].tolist()), sorted(local[:4].tolist()))
        self.assertEqual(sorted(shuffled[4:8].tolist()), sorted(local[4:8].tolist()))
        self.assertEqual(int(shuffled[8]), -100)
        self.assertEqual(metadata["mutable_groups"], 2)


class ReplayMetadataTests(unittest.TestCase):
    def test_replay_preserves_private_target_event_without_serializing_it(self):
        sample = {
            "id": "sess_sim_1-step_03",
            "session_meta": {"turn_index": 3},
            "history": [
                {"role": "user", "content": "find Thing"},
                {
                    "role": "assistant_action",
                    "name": "grep_search",
                    "args": {"pattern": "Thing", "scope": "src/"},
                    "result_summary": "5 matches",
                },
                {"role": "user", "content": "edit it"},
                {
                    "role": "assistant_action",
                    "name": "edit_file",
                    "args": {"path": "src/a.py", "target_symbol": "Thing"},
                    "result_summary": "ok",
                },
            ],
            "current_prompt": "continue",
        }
        replay = replay_examples_for_sample(sample, pair_limit=1)
        self.assertEqual(len(replay), 1)
        _, replay_sample = replay[0]
        self.assertEqual(
            replay_sample["_privileged_target_event"],
            {
                "name": "edit_file",
                "args": {"path": "src/a.py", "target_symbol": "Thing"},
                "result_summary": "ok",
            },
        )
        text = serialize_transformer_sample(replay_sample, "current_v1")
        self.assertNotIn("target_symbol", text)
        self.assertNotIn("_privileged", text)


if __name__ == "__main__":
    unittest.main()
