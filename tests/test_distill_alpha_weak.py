import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from script import ALL_CLASSES
from train_transformer import build_teacher_targets

WEAK4 = ALL_CLASSES[:4]


class BuildTeacherTargetsCondAlphaTest(unittest.TestCase):
    def _write_fixture(self, tmp):
        tmp = Path(tmp)
        rows = [
            ("sess_sim_1-step_01", WEAK4[0]),        # matched, weak4 -> alpha_weak
            ("sess_sim_1-step_02", "run_bash"),      # matched, non-weak -> alpha
            ("sess_sim_2-step_01", WEAK4[3]),        # NOT in teacher payload -> mask 0
        ]
        with (tmp / "train_labels.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "action"])
            writer.writerows(rows)
        samples = [{"id": sample_id} for sample_id, _ in rows]
        samples.append({"id": "replay::sess_sim_1-step_02::h0"})  # replay pseudo-sample
        teacher = {
            "ids": ["sess_sim_1-step_01", "sess_sim_1-step_02"],
            "logits": torch.randn(2, len(ALL_CLASSES)),
        }
        teacher_path = tmp / "teacher.pt"
        torch.save(teacher, teacher_path)
        return samples, teacher, teacher_path

    def test_mask_carries_conditional_alpha_scale(self):
        with tempfile.TemporaryDirectory() as tmp:
            samples, teacher, teacher_path = self._write_fixture(tmp)
            args = SimpleNamespace(
                distill_logits=str(teacher_path),
                distill_alpha=0.5,
                distill_alpha_weak=0.7,
                distill_temp=3.0,
                data_dir=tmp,
            )
            logprobs, mask = build_teacher_targets(samples, args)

            per_row_alpha = args.distill_alpha * mask
            self.assertAlmostEqual(float(per_row_alpha[0]), 0.7, places=6)  # weak4 matched
            self.assertAlmostEqual(float(per_row_alpha[1]), 0.5, places=6)  # non-weak matched
            self.assertEqual(float(per_row_alpha[2]), 0.0)                  # unmatched original
            self.assertEqual(float(per_row_alpha[3]), 0.0)                  # replay pseudo-sample
            torch.testing.assert_close(logprobs[0], teacher["logits"][0])
            torch.testing.assert_close(logprobs[1], teacher["logits"][1])

    def test_unset_option_is_bit_identical_binary_mask(self):
        with tempfile.TemporaryDirectory() as tmp:
            samples, _, teacher_path = self._write_fixture(tmp)
            args = SimpleNamespace(
                distill_logits=str(teacher_path),
                distill_alpha=0.5,
                distill_alpha_weak=None,
                distill_temp=3.0,
                data_dir=tmp,
            )
            _, mask = build_teacher_targets(samples, args)
            self.assertEqual(mask.tolist(), [1.0, 1.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
