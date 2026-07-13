import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from train_transformer import apply_sim_early_turn_loss_scale, parse_args


def sample(sample_id, turn, replay=False):
    return {
        "id": sample_id,
        "_is_replay": replay,
        "session_meta": {"turn_index": turn},
    }


class SimEarlyTurnLossScaleTest(unittest.TestCase):
    def test_class_normalization_and_scope(self):
        samples = [
            sample("sess_sim_a-step_01", 1),
            sample("sess_sim_b-step_02", 2),
            sample("sess_sim_c-step_03", 3),
            sample("sess_sim_d-step_01", 1),
            sample("sess_sim_e-step_03", 3),
            sample("sess_sim_f-step_04", 4),
            sample("sess_sim_g-step_05", 5),
            sample("sess_au_h-step_01", 1),
            sample("sess_sim_i-step_01::replay", 1, replay=True),
            sample("sess_sim_val-step_01", 1),
        ]
        labels = [0, 0, 0, 1, 1, 1, 1, 0, 0, 0]
        train_idx = list(range(9))
        base = [1.0] * 8 + [0.5, 1.0]
        args = SimpleNamespace(sim_early_turn_loss_scale=0.25)

        weights = apply_sim_early_turn_loss_scale(
            samples, labels, train_idx, base, args
        )

        # Class 0 SIM raw weights [.25, .25, 1] have mean .5.
        self.assertEqual(weights[:3], [0.5, 0.5, 2.0])
        # Class 1 SIM raw weights [.25, 1, 1, 1] have mean .8125.
        self.assertAlmostEqual(weights[3], 0.25 / 0.8125)
        for idx in (4, 5, 6):
            self.assertAlmostEqual(weights[idx], 1.0 / 0.8125)
        self.assertAlmostEqual(sum(weights[:3]), 3.0)
        self.assertAlmostEqual(sum(weights[3:7]), 4.0)

        # AU, replay, and a row outside train_idx stay unchanged.
        self.assertEqual(weights[7:], [1.0, 0.5, 1.0])
        self.assertEqual(args.sim_early_turn_loss_meta["target_rows"], 3)
        self.assertEqual(args.sim_early_turn_loss_meta["sim_rows"], 7)

    def test_default_is_identity(self):
        samples = [sample("sess_sim_a-step_01", 1)]
        args = SimpleNamespace(sim_early_turn_loss_scale=1.0)
        weights = apply_sim_early_turn_loss_scale(samples, [0], [0], [0.7], args)
        self.assertEqual(weights, [0.7])
        self.assertFalse(args.sim_early_turn_loss_meta["enabled"])

    def test_missing_turn_fails_loudly_when_enabled(self):
        samples = [{"id": "sess_sim_a-step_01", "session_meta": {}}]
        args = SimpleNamespace(sim_early_turn_loss_scale=0.25)
        with self.assertRaisesRegex(ValueError, "integer turn_index"):
            apply_sim_early_turn_loss_scale(samples, [0], [0], [1.0], args)

    def test_helper_rejects_zero_scale(self):
        samples = [sample("sess_sim_a-step_01", 1)]
        args = SimpleNamespace(sim_early_turn_loss_scale=0.0)
        with self.assertRaisesRegex(ValueError, "finite and in"):
            apply_sim_early_turn_loss_scale(samples, [0], [0], [1.0], args)

    def test_cli_rejects_invalid_scale(self):
        for value in ("0", "1.1", "nan"):
            with self.subTest(value=value):
                with patch.object(
                    sys,
                    "argv",
                    ["train_transformer.py", "--sim-early-turn-loss-scale", value],
                ):
                    with self.assertRaises(SystemExit):
                        parse_args()


if __name__ == "__main__":
    unittest.main()
