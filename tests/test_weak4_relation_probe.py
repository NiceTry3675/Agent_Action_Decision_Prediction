import unittest

import numpy as np

from probe_weak4_relation_graph import (
    _balanced_binary_weights,
    choose_direction_thresholds,
    choose_residual_alpha,
    fuse_residual_scores,
    session_block_permute,
    stratified_group_fold_ids,
)


class Weak4RelationProbeTests(unittest.TestCase):
    def test_group_folds_never_split_a_group(self):
        groups = np.asarray(
            [f"session_{group}" for group in range(12) for _ in range(3)],
            dtype=object,
        )
        strata = np.asarray(
            [(group + row) % 4 for group in range(12) for row in range(3)],
            dtype=np.int64,
        )
        folds = stratified_group_fold_ids(strata, groups, n_folds=3, seed=42)
        self.assertEqual(set(folds.tolist()), {0, 1, 2})
        for group in set(groups.tolist()):
            self.assertEqual(len(set(folds[groups == group].tolist())), 1)

    def test_balanced_weights_equalize_class_and_direction_mass(self):
        labels = np.asarray([0, 0, 0, 0, 1, 1, 1], dtype=np.int64)
        directions = np.asarray([0, 0, 0, 1, 0, 1, 1], dtype=np.int64)
        weights = _balanced_binary_weights(labels, directions)
        masses = {
            (target, direction): float(weights[(labels == target) & (directions == direction)].sum())
            for target in (0, 1)
            for direction in (0, 1)
        }
        self.assertAlmostEqual(masses[(0, 0)], masses[(0, 1)], places=7)
        self.assertAlmostEqual(masses[(1, 0)], masses[(1, 1)], places=7)
        self.assertAlmostEqual(float(weights[labels == 0].sum()), float(weights[labels == 1].sum()), places=7)

    def test_session_block_permutation_is_deterministic_and_cross_session(self):
        groups = np.asarray(["a", "a", "b", "b", "c", "c"], dtype=object)
        matrix = np.asarray([[1], [1], [2], [2], [3], [3]], dtype=np.float64)
        exact = np.asarray(["same"] * 6, dtype=object)
        first, first_audit = session_block_permute(
            matrix, groups, (exact,), seed=7
        )
        second, second_audit = session_block_permute(
            matrix, groups, (exact,), seed=7
        )
        np.testing.assert_array_equal(first, second)
        self.assertEqual(first_audit, second_audit)
        self.assertEqual(first_audit["zeroed_singleton"], 0)
        for group, original_value in (("a", 1), ("b", 2), ("c", 3)):
            self.assertTrue(np.all(first[groups == group, 0] != original_value))

    def test_session_block_permutation_zeroes_true_singletons(self):
        matrix = np.asarray([[4.0], [5.0]], dtype=np.float64)
        groups = np.asarray(["a", "b"], dtype=object)
        strata = np.asarray(["x", "y"], dtype=object)
        output, audit = session_block_permute(matrix, groups, (strata,), seed=1)
        np.testing.assert_array_equal(output, np.zeros_like(matrix))
        self.assertEqual(audit["zeroed_singleton"], 2)

    def test_threshold_calibration_can_choose_exact_noop(self):
        # Every high score is a false switch; every true rescue has a low score.
        scores = np.asarray([0.95, 0.90, 0.85, 0.20, 0.15], dtype=np.float64)
        labels = np.asarray([0, 0, 0, 1, 1], dtype=np.int64)
        directions = np.zeros(5, dtype=np.int64)
        thresholds, report = choose_direction_thresholds(
            scores,
            labels,
            directions,
            min_support=1,
            min_positive=1,
        )
        self.assertGreater(thresholds[0], 1.0)
        self.assertEqual(report["directions"]["0"]["rescue"], 0)
        self.assertEqual(report["directions"]["0"]["false_switch"], 0)

    def test_relation_residual_has_exact_noop(self):
        baseline = np.asarray([0.1, 0.4, 0.8], dtype=np.float64)
        relation = np.asarray([0.9, 0.2, 0.3], dtype=np.float64)
        np.testing.assert_allclose(
            fuse_residual_scores(baseline, relation, 0.0),
            baseline,
            rtol=0,
            atol=1e-12,
        )

    def test_relation_alpha_requires_material_inner_auc_gain(self):
        labels = np.asarray([0, 0, 1, 1], dtype=np.int64)
        baseline = np.asarray([0.1, 0.2, 0.8, 0.9], dtype=np.float64)
        relation = np.asarray([0.9, 0.8, 0.2, 0.1], dtype=np.float64)
        alpha, report = choose_residual_alpha(
            baseline, relation, labels, min_auc_gain=0.005
        )
        self.assertEqual(alpha, 0.0)
        self.assertTrue(report["fell_back_to_noop"])


if __name__ == "__main__":
    unittest.main()
