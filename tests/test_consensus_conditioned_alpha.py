import contextlib
import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import torch

from script import ALL_CLASSES
from train_transformer import (
    apply_consensus_conditioned_weak_alpha,
    parse_args,
)


class ConsensusConditionedWeakAlphaTest(unittest.TestCase):
    def _fixture(self, slope=0.15):
        counts_by_class = [
            [0, 0, 1, 2, 3, 3],
            [0, 1, 1, 2, 3],
            [0, 2, 3, 3],
            [0, 1, 2, 2, 3, 3, 3],
        ]
        raw_table = torch.tensor([0.0, 0.25, 0.75, 1.0])
        y = []
        counts = []
        scales = []
        raw_scales = []
        weak_rows = {}
        for class_id, class_counts in enumerate(counts_by_class):
            start = len(y)
            class_raw = raw_table[torch.tensor(class_counts)]
            class_scales = class_raw / class_raw.mean()
            y.extend([class_id] * len(class_counts))
            counts.extend(class_counts)
            raw_scales.extend(class_raw.tolist())
            scales.extend(class_scales.tolist())
            weak_rows[class_id] = list(range(start, len(y)))

        nonweak_rows = [len(y), len(y) + 1]
        y.extend([ALL_CLASSES.index("run_bash")] * 2)
        counts.extend([0, 3])
        raw_scales.extend([0.4, 1.6])
        scales.extend([0.4, 1.6])

        replay_row = len(y)
        y.append(0)
        counts.append(-1)
        raw_scales.append(1.0)
        scales.append(1.0)

        row_count = len(y)
        teacher_mask = torch.ones(row_count)
        for class_rows in weak_rows.values():
            teacher_mask[class_rows] = 1.4  # base .5 -> Weak4 alpha .7
        teacher_mask[replay_row] = 0.0
        teacher = (
            torch.randn(row_count, len(ALL_CLASSES)),
            teacher_mask,
        )
        consensus = {
            "gradient_scales": torch.tensor(scales),
            "raw_scales": torch.tensor(raw_scales),
            "kd_gradient_scales": torch.linspace(0.2, 1.2, row_count),
            "kd_raw_scales": torch.linspace(0.1, 0.9, row_count),
            "correct_counts": torch.tensor(counts),
            "meta": {
                "model_count": 3,
                "class_normalize": True,
                "sentinel": "keep",
            },
        }
        args = SimpleNamespace(
            distill_alpha=0.5,
            distill_alpha_weak=0.7,
            distill_alpha_c0=None,
            distill_alpha_weak_consensus_lambda=slope,
            consensus_class_normalize=True,
        )
        return teacher, consensus, y, list(range(row_count)), weak_rows, nonweak_rows, replay_row, args

    def test_centers_alpha_and_preserves_hard_backbone_mass_per_class(self):
        teacher, consensus, y, train_idx, weak_rows, _, _, args = self._fixture()
        original_mask = teacher[1].clone()
        original_scales = consensus["gradient_scales"].clone()

        updated_teacher, updated_consensus = apply_consensus_conditioned_weak_alpha(
            teacher, consensus, y, train_idx, args
        )

        self.assertIs(updated_teacher[0], teacher[0])
        self.assertIsNot(updated_consensus, consensus)
        alpha = args.distill_alpha * updated_teacher[1]
        for class_id, rows in weak_rows.items():
            idx = torch.tensor(rows)
            class_alpha = alpha[idx]
            class_counts = consensus["correct_counts"][idx]
            class_scales = updated_consensus["gradient_scales"][idx]
            self.assertAlmostEqual(float(class_alpha.mean()), 0.7, places=6)
            self.assertAlmostEqual(
                float(((1.0 - class_alpha) * class_scales).mean()),
                0.3,
                places=6,
            )
            by_count = [
                float(class_alpha[class_counts == count].mean())
                for count in sorted(set(class_counts.tolist()))
            ]
            self.assertTrue(
                all(left > right for left, right in zip(by_count, by_count[1:]))
            )
            expected_uncompensated = float(
                ((1.0 - class_alpha) * original_scales[idx]).mean()
            )
            expected_factor = 0.3 / expected_uncompensated
            meta = updated_consensus["meta"]["distill_alpha_weak_consensus"][
                "classes"
            ][ALL_CLASSES[class_id]]
            self.assertAlmostEqual(meta["compensation_factor"], expected_factor)
            self.assertAlmostEqual(meta["compensated_hard_backbone_mass"], 0.3)

        torch.testing.assert_close(teacher[1], original_mask, rtol=0, atol=0)
        torch.testing.assert_close(
            consensus["gradient_scales"], original_scales, rtol=0, atol=0
        )

    def test_nonweak_replay_raw_and_kd_tensors_are_unchanged(self):
        teacher, consensus, y, train_idx, _, nonweak_rows, replay_row, args = self._fixture()
        updated_teacher, updated_consensus = apply_consensus_conditioned_weak_alpha(
            teacher, consensus, y, train_idx, args
        )

        untouched = torch.tensor(nonweak_rows + [replay_row])
        torch.testing.assert_close(
            updated_teacher[1][untouched], teacher[1][untouched], rtol=0, atol=0
        )
        torch.testing.assert_close(
            updated_consensus["gradient_scales"][untouched],
            consensus["gradient_scales"][untouched],
            rtol=0,
            atol=0,
        )
        self.assertEqual(float(updated_teacher[1][replay_row]), 0.0)
        self.assertEqual(float(updated_consensus["gradient_scales"][replay_row]), 1.0)
        self.assertIs(updated_consensus["raw_scales"], consensus["raw_scales"])
        self.assertIs(
            updated_consensus["kd_gradient_scales"],
            consensus["kd_gradient_scales"],
        )
        self.assertIs(updated_consensus["kd_raw_scales"], consensus["kd_raw_scales"])
        self.assertIs(updated_consensus["correct_counts"], consensus["correct_counts"])

    def test_zero_lambda_is_an_object_identity_noop(self):
        teacher, consensus, y, train_idx, _, _, _, args = self._fixture(slope=0.0)
        updated_teacher, updated_consensus = apply_consensus_conditioned_weak_alpha(
            teacher, consensus, y, train_idx, args
        )
        self.assertIs(updated_teacher, teacher)
        self.assertIs(updated_consensus, consensus)
        self.assertNotIn("distill_alpha_weak_consensus", consensus["meta"])

    def test_fails_on_missing_teacher_coverage_or_incompatible_c0_option(self):
        teacher, consensus, y, train_idx, weak_rows, _, _, args = self._fixture()
        teacher[1][weak_rows[0][0]] = 0.0
        with self.assertRaisesRegex(ValueError, "full teacher coverage"):
            apply_consensus_conditioned_weak_alpha(
                teacher, consensus, y, train_idx, args
            )

        teacher, consensus, y, train_idx, _, _, _, args = self._fixture()
        args.distill_alpha_c0 = 0.8
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            apply_consensus_conditioned_weak_alpha(
                teacher, consensus, y, train_idx, args
            )

    def test_fails_if_incoming_scales_are_not_class_normalized(self):
        teacher, consensus, y, train_idx, weak_rows, _, _, args = self._fixture()
        consensus["gradient_scales"][weak_rows[0]] *= 0.9
        with self.assertRaisesRegex(ValueError, "class-normalized scales"):
            apply_consensus_conditioned_weak_alpha(
                teacher, consensus, y, train_idx, args
            )


class ConsensusConditionedWeakAlphaCliTest(unittest.TestCase):
    def _parse(self, *argv):
        with patch.object(sys, "argv", ["train_transformer.py", *argv]):
            return parse_args()

    def _assert_parse_error(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                self._parse(*argv)
        self.assertEqual(raised.exception.code, 2)

    def test_valid_option_contract(self):
        args = self._parse(
            "--distill-alpha-weak-consensus-lambda",
            "0.15",
            "--distill-logits",
            "teacher.pt",
            "--distill-alpha",
            "0.5",
            "--distill-alpha-weak",
            "0.7",
            "--consensus-reliability",
            "consensus.pt",
        )
        self.assertEqual(args.distill_alpha_weak_consensus_lambda, 0.15)

    def test_requires_weak_alpha_teacher_consensus_and_normalization(self):
        base = [
            "--distill-alpha-weak-consensus-lambda",
            "0.15",
            "--distill-logits",
            "teacher.pt",
            "--distill-alpha-weak",
            "0.7",
            "--consensus-reliability",
            "consensus.pt",
        ]
        self._assert_parse_error(*base, "--no-consensus-class-normalize")
        self._assert_parse_error(
            "--distill-alpha-weak-consensus-lambda",
            "0.15",
            "--distill-logits",
            "teacher.pt",
            "--consensus-reliability",
            "consensus.pt",
        )
        self._assert_parse_error(
            "--distill-alpha-weak-consensus-lambda",
            "0.15",
            "--distill-alpha-weak",
            "0.7",
            "--consensus-reliability",
            "consensus.pt",
        )

    def test_rejects_c0_combination_or_invalid_lambda(self):
        common = [
            "--distill-logits",
            "teacher.pt",
            "--distill-alpha-weak",
            "0.7",
            "--consensus-reliability",
            "consensus.pt",
        ]
        self._assert_parse_error(
            "--distill-alpha-weak-consensus-lambda",
            "0.15",
            "--distill-alpha-c0",
            "0.8",
            *common,
        )
        for value in ("-0.1", "nan"):
            with self.subTest(value=value):
                self._assert_parse_error(
                    "--distill-alpha-weak-consensus-lambda", value, *common
                )


if __name__ == "__main__":
    unittest.main()
