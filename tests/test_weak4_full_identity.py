import unittest

import torch

from weak4_full_residual import (
    FeatureStats,
    Weak4FullResidualNet,
    Weak4ModelConfig,
    Weak4RouteRow,
    apply_residual_actions,
    collate_weak4_full,
    fit_direction_thresholds,
    serialize_weak4_full,
    weak4_full_loss,
)


def row(sample_id, logits, y_true, target_action, change, alt):
    sample = {
        "id": sample_id,
        "current_prompt": "find Widget",
        "history": [],
        "session_meta": {"turn_index": 1, "workspace": {}},
    }
    return Weak4RouteRow(
        sample_id=sample_id,
        session_id=sample_id.split("-step_")[0],
        full_index=0,
        typed=serialize_weak4_full(sample, logits),
        y_true=y_true,
        target_action=target_action,
        change_target=change,
        alt_target=alt,
        identity_weight=1.0,
    )


class Weak4FullIdentityTests(unittest.TestCase):
    def test_default_model_is_independent_specialist_scale(self):
        model = Weak4FullResidualNet()
        parameter_count = sum(parameter.numel() for parameter in model.parameters())
        self.assertGreaterEqual(parameter_count, 5_000_000)
        self.assertLessEqual(parameter_count, 20_000_000)

    def test_nonroute_and_keep_are_exact_identity(self):
        logits = torch.tensor([
            [5.0, 4.0, 0.0, 0.0] + [-1.0] * 10,
            [0.0] * 4 + [9.0] + [0.0] * 9,
        ])
        probabilities = torch.tensor([[0.99, 0.0, 0.01, 0.0, 0.0]])
        pred = apply_residual_actions(logits, [0], probabilities, 0.5)
        self.assertTrue(torch.equal(pred, logits.argmax(1)))

    def test_switch_is_weak4_and_never_switches_to_main(self):
        logits = torch.tensor([[5.0, 4.0, 0.0, 0.0] + [-1.0] * 10])
        probabilities = torch.tensor([[0.05, 0.0, 0.90, 0.03, 0.02]])
        pred = apply_residual_actions(logits, [0], probabilities, 0.5)
        self.assertEqual(pred.tolist(), [1])
        bad = torch.tensor([[0.05, 0.90, 0.03, 0.01, 0.01]])
        with self.assertRaises(ValueError):
            apply_residual_actions(logits, [0], bad, 0.5)

    def test_model_forward_and_keep_loss_are_finite(self):
        logits = torch.tensor([5.0, 4.0, 0.0, 0.0] + [-1.0] * 10)
        rows = [row("sess_sim_a-step_01", logits, 0, 0, 0, -100)]
        stats = FeatureStats((0.0,) * 13, (1.0,) * 13)
        batch = collate_weak4_full(rows, stats)
        model = Weak4FullResidualNet(
            Weak4ModelConfig(
                byte_embed_dim=8,
                byte_hidden_dim=16,
                byte_conv_layers=1,
                event_dim=16,
                event_layers=1,
                event_heads=4,
                fusion_dim=24,
                dropout=0.0,
            )
        )
        outputs = model(batch)
        loss, parts = weak4_full_loss(outputs, batch, rank_lambda=0.0)
        self.assertTrue(torch.isfinite(loss))
        self.assertEqual(outputs["alt_logits"].shape, (1, 4))
        self.assertLess(float(outputs["alt_logits"][0, 0].detach()), -1000)
        self.assertTrue(all(torch.isfinite(value) for value in parts.values()))

    def test_threshold_calibration_is_directional(self):
        main = torch.tensor([0, 0, 0, 1])
        truth = torch.tensor([1, 0, 1, 0])
        probs = torch.tensor([
            [0.1, 0.0, 0.9, 0.0, 0.0],
            [0.6, 0.0, 0.4, 0.0, 0.0],
            [0.2, 0.0, 0.8, 0.0, 0.0],
            [0.2, 0.8, 0.0, 0.0, 0.0],
        ])
        thresholds, metadata = fit_direction_thresholds(main, truth, probs, min_support=1)
        self.assertEqual(thresholds.shape, (4, 4))
        self.assertEqual(float(thresholds.diag().min()), 1.0)
        self.assertIn("read_file->grep_search", metadata["directions"])


if __name__ == "__main__":
    unittest.main()
