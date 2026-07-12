import random
import unittest

import torch
import torch.nn.functional as F

from probe_soft_macro_f1_head import (
    make_macro_windows,
    mixed_base_loss,
    soft_macro_f1_loss,
)
from script import ALL_CLASSES


class SoftMacroF1ProbeTest(unittest.TestCase):
    def test_perfect_logits_have_near_zero_loss(self):
        labels = torch.arange(len(ALL_CLASSES), dtype=torch.long).repeat_interleave(3)
        logits = torch.full((len(labels), len(ALL_CLASSES)), -20.0)
        logits[torch.arange(len(labels)), labels] = 20.0
        loss, per_class, support = soft_macro_f1_loss(logits, labels)
        self.assertLess(float(loss), 1e-6)
        torch.testing.assert_close(per_class, torch.ones_like(per_class), atol=1e-6, rtol=0)
        self.assertEqual(support.tolist(), [3.0] * len(ALL_CLASSES))

    def test_soft_macro_f1_is_differentiable(self):
        labels = torch.arange(len(ALL_CLASSES), dtype=torch.long).repeat(2)
        logits = torch.zeros(len(labels), len(ALL_CLASSES), requires_grad=True)
        loss, _, _ = soft_macro_f1_loss(logits, labels)
        loss.backward()
        self.assertTrue(torch.isfinite(logits.grad).all())
        self.assertGreater(float(logits.grad.abs().sum()), 0.0)

    def test_mixed_base_loss_matches_rowwise_hard_kd_formula(self):
        logits = torch.tensor(
            [
                [[1.2, -0.3], [0.1, 0.8]],
                [[0.7, -0.1], [-0.2, 1.1]],
            ],
            dtype=torch.float32,
        )
        labels = torch.tensor([0, 1], dtype=torch.long)
        weights = torch.tensor([0.75, 1.25], dtype=torch.float32)
        teacher_p = torch.tensor([[0.8, 0.2], [0.3, 0.7]], dtype=torch.float32)
        alpha = torch.tensor([0.25, 0.75], dtype=torch.float32)
        temperature = 2.0

        actual = mixed_base_loss(
            logits,
            labels,
            weights,
            teacher_p,
            alpha,
            label_smoothing=0.0,
            focal_gamma=0.0,
            temperature=temperature,
        )
        expected = []
        for arm_logits in logits:
            hard = F.cross_entropy(
                arm_logits, labels, weight=weights, reduction="none"
            )
            kd = F.kl_div(
                F.log_softmax(arm_logits / temperature, dim=-1),
                teacher_p,
                reduction="none",
            ).sum(dim=-1) * temperature**2
            expected.append(((1.0 - alpha) * hard + alpha * kd).mean())

        torch.testing.assert_close(actual, torch.stack(expected))

    def test_macro_windows_keep_every_row_once_and_merge_small_tail(self):
        repeats = 35
        labels = torch.arange(len(ALL_CLASSES), dtype=torch.long).repeat(repeats)
        indices = list(range(len(labels)))
        windows, meta = make_macro_windows(indices, labels, 112, random.Random(42))
        flattened = [idx for window in windows for idx in window]
        self.assertEqual(sorted(flattened), indices)
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertTrue(meta["tail_merged"])
        self.assertEqual(meta["dropped_rows"], 0)
        self.assertEqual(meta["duplicated_rows"], 0)
        self.assertEqual(meta["all_14_classes_windows"], meta["windows"])
        self.assertEqual(meta["missing_classes_by_window"], [])


if __name__ == "__main__":
    unittest.main()
