import contextlib
import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import torch

from script import ALL_CLASSES
from train_transformer import apply_consensus_distill_alpha, parse_args


class ConsensusDistillAlphaTest(unittest.TestCase):
    def _fixture(self):
        logits = torch.randn(6, len(ALL_CLASSES))
        # Alpha is already encoded by build_teacher_targets:
        # c0 weak=.7, c0 rest=.5, non-c0 weak=.7, non-c0 rest=.5,
        # replay=0, unmatched original=0.
        teacher = (
            logits,
            torch.tensor([1.4, 1.0, 1.4, 1.0, 0.0, 0.0]),
        )
        consensus = {
            "correct_counts": torch.tensor([0, 0, 2, 1, -1, 0]),
            "meta": {},
        }
        args = SimpleNamespace(distill_alpha=0.5, distill_alpha_c0=0.8)
        return teacher, consensus, args

    def test_c0_floor_composes_after_weak_alpha_and_preserves_masked_rows(self):
        teacher, consensus, args = self._fixture()
        logits, mask = apply_consensus_distill_alpha(teacher, consensus, args)

        self.assertIs(logits, teacher[0])
        torch.testing.assert_close(
            args.distill_alpha * mask,
            torch.tensor([0.8, 0.8, 0.7, 0.5, 0.0, 0.0]),
        )
        self.assertEqual(
            consensus["meta"]["distill_alpha_c0"],
            {"target": 0.8, "matched_c0_rows": 2, "boosted_rows": 2},
        )

    def test_floor_never_reduces_a_higher_existing_conditional_alpha(self):
        teacher, consensus, args = self._fixture()
        teacher = (teacher[0], teacher[1].clone())
        teacher[1][0] = 1.8  # existing per-row alpha=.9

        _, mask = apply_consensus_distill_alpha(teacher, consensus, args)

        self.assertAlmostEqual(float(args.distill_alpha * mask[0]), 0.9, places=6)
        self.assertAlmostEqual(float(args.distill_alpha * mask[1]), 0.8, places=6)
        self.assertEqual(consensus["meta"]["distill_alpha_c0"]["boosted_rows"], 1)

    def test_unset_option_returns_the_original_teacher_unchanged(self):
        teacher, _, _ = self._fixture()
        original_mask = teacher[1].clone()
        args = SimpleNamespace(distill_alpha=0.5, distill_alpha_c0=None)

        result = apply_consensus_distill_alpha(teacher, None, args)

        self.assertIs(result, teacher)
        torch.testing.assert_close(teacher[1], original_mask, rtol=0, atol=0)

    def test_rejects_teacher_consensus_length_mismatch(self):
        teacher, consensus, args = self._fixture()
        consensus["correct_counts"] = consensus["correct_counts"][:-1]

        with self.assertRaisesRegex(ValueError, "row length mismatch"):
            apply_consensus_distill_alpha(teacher, consensus, args)


class ConsensusDistillAlphaCliTest(unittest.TestCase):
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
            "--distill-alpha-c0",
            "0.8",
            "--distill-logits",
            "teacher.pt",
            "--consensus-reliability",
            "consensus.pt",
            "--distill-alpha",
            "0.5",
        )
        self.assertEqual(args.distill_alpha_c0, 0.8)

    def test_requires_teacher_consensus_and_positive_base_alpha(self):
        self._assert_parse_error(
            "--distill-alpha-c0", "0.8", "--consensus-reliability", "consensus.pt"
        )
        self._assert_parse_error(
            "--distill-alpha-c0", "0.8", "--distill-logits", "teacher.pt"
        )
        self._assert_parse_error(
            "--distill-alpha-c0",
            "0.8",
            "--distill-logits",
            "teacher.pt",
            "--consensus-reliability",
            "consensus.pt",
            "--distill-alpha",
            "0",
        )

    def test_rejects_nonfinite_or_out_of_range_target(self):
        for value in ("nan", "-0.1", "1.1"):
            with self.subTest(value=value):
                self._assert_parse_error(
                    "--distill-alpha-c0",
                    value,
                    "--distill-logits",
                    "teacher.pt",
                    "--consensus-reliability",
                    "consensus.pt",
                )


if __name__ == "__main__":
    unittest.main()
