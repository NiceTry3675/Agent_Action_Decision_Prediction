import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
import torch.nn.functional as F

from train_transformer import (
    action_margin_kd_loss,
    calibrate_action_margin_weight,
    parse_args,
    save_epoch_checkpoint,
)


class ActionMarginLossTests(unittest.TestCase):
    def test_identical_teacher_and_student_margins_are_zero(self):
        torch.manual_seed(3)
        logits = torch.randn(5, 14, requires_grad=True)
        labels = torch.tensor([0, 1, 2, 3, 4])
        loss = action_margin_kd_loss(
            logits,
            logits.detach().clone(),
            labels,
            torch.ones(5),
            topk=3,
            temperature=3.0,
        )
        self.assertEqual(float(loss.detach()), 0.0)

    def test_teacher_selects_top_negatives_and_excludes_true_class(self):
        teacher = torch.zeros(1, 14)
        teacher[0, 0] = 100.0  # true class must never be selected as a negative
        teacher[0, 2] = 9.0
        teacher[0, 1] = 8.0
        student = torch.zeros(1, 14, requires_grad=True)
        student.data[0, 0] = 4.0
        student.data[0, 2] = 3.0
        student.data[0, 1] = 1.0
        labels = torch.tensor([0])

        loss = action_margin_kd_loss(
            student,
            teacher,
            labels,
            torch.tensor([True]),
            topk=2,
            temperature=1.0,
        )
        student_margin = torch.tensor([[1.0, 3.0]])
        teacher_margin = torch.tensor([[91.0, 92.0]])
        expected = F.smooth_l1_loss(
            student_margin, teacher_margin, reduction="none"
        ).mean()
        torch.testing.assert_close(loss, expected)

    def test_masked_replay_row_has_no_effect_or_gradient(self):
        torch.manual_seed(5)
        student = torch.randn(3, 14, requires_grad=True)
        teacher = torch.randn(3, 14)
        labels = torch.tensor([0, 1, 2])
        mask = torch.tensor([True, True, False])
        loss = action_margin_kd_loss(
            student, teacher, labels, mask, topk=3, temperature=3.0
        )
        changed_teacher = teacher.clone()
        changed_teacher[-1] = 10000.0
        changed_loss = action_margin_kd_loss(
            student, changed_teacher, labels, mask, topk=3, temperature=3.0
        )
        torch.testing.assert_close(loss, changed_loss, rtol=0, atol=0)
        loss.backward()
        torch.testing.assert_close(student.grad[-1], torch.zeros_like(student.grad[-1]))

    def test_calibration_hits_target_without_populating_grad(self):
        torch.manual_seed(7)
        student = torch.randn(8, 14, requires_grad=True)
        teacher = torch.randn(8, 14)
        labels = torch.arange(8) % 14
        base_loss = F.cross_entropy(student, labels)
        margin_loss = action_margin_kd_loss(
            student,
            teacher,
            labels,
            torch.ones(8),
            topk=3,
            temperature=3.0,
        )
        weight, meta = calibrate_action_margin_weight(
            base_loss, margin_loss, student, 0.10
        )
        self.assertIsNone(student.grad)
        self.assertGreater(weight, 0.0)
        self.assertAlmostEqual(meta["achieved_grad_ratio"], 0.10, places=7)


class ActionMarginContractTests(unittest.TestCase):
    def _parse(self, *extra):
        with patch.object(sys, "argv", ["train_transformer.py", *extra]):
            return parse_args()

    def test_parser_defaults_are_inactive(self):
        args = self._parse()
        self.assertEqual(args.action_margin_kd_weight, 0.0)
        self.assertEqual(args.action_margin_kd_target_grad_ratio, 0.0)
        self.assertEqual(args.action_margin_kd_topk, 3)

    def test_active_mode_requires_teacher_and_rejects_two_weight_modes(self):
        with self.assertRaises(SystemExit):
            self._parse("--action-margin-kd-target-grad-ratio", "0.1")
        with self.assertRaises(SystemExit):
            self._parse(
                "--distill-logits",
                "teacher.pt",
                "--action-margin-kd-weight",
                "0.2",
                "--action-margin-kd-target-grad-ratio",
                "0.1",
            )

    def test_checkpoint_persists_calibration_state(self):
        class DummyModel:
            def half(self):
                return self

            def cpu(self):
                return self

            def save_pretrained(self, path, safe_serialization=True):
                Path(path, "weights.ok").write_text("ok", encoding="utf-8")

        class DummyTokenizer:
            def save_pretrained(self, path):
                Path(path, "tokenizer.ok").write_text("ok", encoding="utf-8")

        state = {
            "action_margin_kd": {
                "topk": 3,
                "temperature": 3.0,
                "target_grad_ratio": 0.1,
                "calibrated_weight": 0.42,
            }
        }
        with tempfile.TemporaryDirectory() as td:
            save_epoch_checkpoint(
                DummyModel(), DummyTokenizer(), td, 1, training_state=state
            )
            payload = json.loads(
                (Path(td) / "checkpoint_state.json").read_text(encoding="utf-8")
            )
        self.assertEqual(payload["last_completed_epoch"], 1)
        self.assertEqual(payload["action_margin_kd"], state["action_margin_kd"])


if __name__ == "__main__":
    unittest.main()
