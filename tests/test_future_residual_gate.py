import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import numpy as np
import torch

from probe_future_residual_gate import (
    CLASSES,
    center_rows,
    clip_rows,
    log_softmax_rows,
    permute_within_strata,
    rotation_deltas,
)


class PureFunctionTests(unittest.TestCase):
    def test_log_softmax_rows_is_normalized(self):
        q = log_softmax_rows(np.array([[1.0, 2.0, 3.0], [0.0, 0.0, 0.0]]))
        np.testing.assert_allclose(np.exp(q).sum(axis=1), [1.0, 1.0], atol=1e-12)

    def test_center_rows_has_zero_class_mean(self):
        centered = center_rows(np.random.default_rng(0).normal(size=(5, 14)))
        np.testing.assert_allclose(centered.mean(axis=1), np.zeros(5), atol=1e-12)

    def test_clip_rows_caps_norm_and_keeps_small_rows(self):
        delta = np.array([[3.0, 4.0], [0.3, 0.4]])
        clipped = clip_rows(delta, 1.0)
        np.testing.assert_allclose(np.linalg.norm(clipped[0]), 1.0)
        np.testing.assert_allclose(clipped[1], delta[1])

    def test_identical_actual_and_donor_scores_give_zero_delta(self):
        rng = np.random.default_rng(1)
        q1 = log_softmax_rows(rng.normal(size=(6, 14)))
        q2 = np.repeat(q1[:, None, :], 4, axis=1)
        t1_full, t1_rotations, t2_rotations = rotation_deltas(q1, q2)
        np.testing.assert_allclose(t1_full, np.zeros_like(t1_full), atol=1e-12)
        for t1_rotation, t2_rotation in zip(t1_rotations, t2_rotations):
            np.testing.assert_allclose(t1_rotation, t2_rotation, atol=1e-12)

    def test_rotation_deltas_are_row_centered(self):
        rng = np.random.default_rng(2)
        q1 = log_softmax_rows(rng.normal(size=(8, 14)))
        q2 = log_softmax_rows(rng.normal(size=(8, 4, 14)).reshape(-1, 14)).reshape(8, 4, 14)
        t1_full, t1_rotations, _ = rotation_deltas(q1, q2)
        np.testing.assert_allclose(t1_full.mean(axis=1), np.zeros(8), atol=1e-12)
        np.testing.assert_allclose(t1_rotations[0].mean(axis=1), np.zeros(8), atol=1e-12)

    def test_permutation_preserves_multiset_within_each_stratum(self):
        rng = np.random.default_rng(3)
        values = rng.normal(size=(60, 14))
        strata = [f"s{i % 3}" for i in range(60)]
        permuted = permute_within_strata(values, strata, seed=7)
        for stratum in set(strata):
            mask = np.asarray([s == stratum for s in strata])
            original = sorted(map(tuple, np.round(values[mask], 9)))
            shuffled = sorted(map(tuple, np.round(permuted[mask], 9)))
            self.assertEqual(original, shuffled)
        self.assertFalse(np.allclose(values, permuted))


class SyntheticEndToEndSmoke(unittest.TestCase):
    def test_gate_script_runs_on_synthetic_caches(self):
        rng = np.random.default_rng(0)
        sessions = 240
        steps = 3
        rows = sessions * steps
        hidden_dim = 16
        k = 4

        ids, labels = [], []
        for session in range(sessions):
            for step in range(1, steps + 1):
                ids.append(f"sess_sim_d_{session:04d}-step_{step:02d}")
                labels.append(int(rng.integers(0, len(CLASSES))))
        y = np.asarray(labels)

        # Hidden carries some label signal so heads are non-degenerate.
        class_means = rng.normal(size=(len(CLASSES), hidden_dim))
        h0 = class_means[y] + rng.normal(scale=1.0, size=(rows, hidden_dim))
        z0 = np.eye(len(CLASSES))[y] * 2.0 + rng.normal(scale=1.0, size=(rows, len(CLASSES)))

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cache_path = tmp / "cache.pt"
            torch.save(
                {
                    "ids": ids,
                    "y_true": torch.tensor(y),
                    "hidden": torch.tensor(h0, dtype=torch.float16),
                    "parent_logits": torch.tensor(z0, dtype=torch.float32),
                    "val_indices": torch.arange(rows),
                },
                cache_path,
            )

            payload = {
                "format": "future-nextuser-donor-payload-v1",
                "classes": CLASSES,
                "k": k,
                "ids": ids,
                "status": ["recovered"] * rows,
                "label": [CLASSES[value] for value in y],
                "source": ["sim"] * rows,
                "turn_bin": ["mid"] * rows,
                "next_user": [f"text {i}" for i in range(rows)],
                "donor_ids": [None] * rows,
            }
            payload_path = tmp / "payload.json.gz"
            with gzip.open(payload_path, "wt", encoding="utf-8") as handle:
                json.dump(payload, handle)

            unique_hidden = class_means[y] + rng.normal(scale=1.0, size=(rows, hidden_dim))
            donor_uidx = np.empty((rows, k), dtype=np.int64)
            for index in range(rows):
                same_label = np.where(y == y[index])[0]
                same_label = same_label[same_label != index]
                donor_uidx[index] = rng.choice(same_label, size=k, replace=len(same_label) < k)
            cache_sha = hashlib.sha256(cache_path.read_bytes()).hexdigest()
            future_path = tmp / "future.pt"
            torch.save(
                {
                    "format": "future-nextuser-hidden-cache-v1",
                    "k": k,
                    "val_indices": torch.arange(rows),
                    "actual_uidx": torch.arange(rows),
                    "donor_uidx": torch.tensor(donor_uidx),
                    "unique_hidden": torch.tensor(unique_hidden, dtype=torch.float16),
                    "current_cache_sha256": cache_sha,
                },
                future_path,
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "probe_future_residual_gate.py",
                    "--cache", str(cache_path),
                    "--future-cache", str(future_path),
                    "--payload", str(payload_path),
                    "--perm-seeds", "101",
                    "--lambda-grid", "0.5,1.0",
                    "--output-teacher", str(tmp / "teacher.json"),
                    "--output-student", str(tmp / "student.json"),
                ],
                capture_output=True,
                text=True,
                cwd=Path(__file__).resolve().parent.parent,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])
            teacher = json.loads((tmp / "teacher.json").read_text())
            student = json.loads((tmp / "student.json").read_text())
            self.assertIn(teacher["decision"], ("GO", "REJECT"))
            self.assertIn(student["decision"], ("GO", "REJECT"))
            self.assertEqual(len(teacher["fold_reports"]), 3)
            for report in teacher["fold_reports"]:
                self.assertEqual(len(report["t1_sym_rotation_macros"]), k)
            counts = Counter(teacher["prediction_distribution"]["p0"].keys())
            self.assertTrue(set(counts) <= set(CLASSES))


if __name__ == "__main__":
    unittest.main()
