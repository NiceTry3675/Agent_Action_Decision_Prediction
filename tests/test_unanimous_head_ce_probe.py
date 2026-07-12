import unittest

import torch

from probe_unanimous_head_ce import (
    mixed_loss_values,
    stratified_permutation_mask,
)


class StratifiedPermutationMaskTest(unittest.TestCase):
    def test_preserves_counts_inside_each_stratum(self):
        actual = torch.tensor([1, 0, 0, 1, 0, 1, 0, 0], dtype=torch.bool)
        eligible = torch.tensor([1, 1, 1, 1, 1, 1, 1, 0], dtype=torch.bool)
        strata = ["a", "a", "a", "b", "b", "c", "c", "c"]

        permuted, meta = stratified_permutation_mask(
            actual, eligible, strata, seed=17
        )

        self.assertEqual(int(permuted.sum()), int(actual.sum()))
        for key in set(strata):
            expected = sum(bool(actual[i]) for i, value in enumerate(strata) if value == key)
            observed = sum(bool(permuted[i]) for i, value in enumerate(strata) if value == key)
            self.assertEqual(observed, expected)
        self.assertEqual(meta["rows"], 3)

    def test_rejects_actual_outside_pool(self):
        actual = torch.tensor([1, 0], dtype=torch.bool)
        eligible = torch.tensor([0, 1], dtype=torch.bool)
        with self.assertRaisesRegex(ValueError, "not a subset"):
            stratified_permutation_mask(actual, eligible, ["x", "x"], seed=1)


class MixedLossValuesTest(unittest.TestCase):
    def test_head_off_removes_hard_term_without_rescaling_kd(self):
        logits = torch.tensor(
            [
                [[1.0, -0.5], [0.2, 0.1]],
                [[1.0, -0.5], [0.2, 0.1]],
            ],
            dtype=torch.float32,
        )
        labels = torch.tensor([1, 0], dtype=torch.long)
        weights = torch.ones(2)
        teacher_p = torch.tensor([[0.8, 0.2], [0.3, 0.7]], dtype=torch.float32)
        alpha = torch.tensor([0.7, 0.5], dtype=torch.float32)
        keep = torch.tensor([[1.0, 1.0], [0.0, 1.0]], dtype=torch.float32)

        values, hard, kd = mixed_loss_values(
            logits,
            labels,
            weights,
            teacher_p,
            alpha,
            keep,
            label_smoothing=0.02,
            focal_gamma=2.0,
            temperature=3.0,
        )

        self.assertTrue(
            torch.allclose(values[0], (1.0 - alpha) * hard[0] + alpha * kd[0])
        )
        self.assertTrue(torch.allclose(values[1, 0], alpha[0] * kd[1, 0]))
        self.assertTrue(
            torch.allclose(
                values[1, 1],
                (1.0 - alpha[1]) * hard[1, 1] + alpha[1] * kd[1, 1],
            )
        )
        self.assertTrue(torch.allclose(kd[0], kd[1]))


if __name__ == "__main__":
    unittest.main()
