import unittest

import torch

from pathlib import Path

from audit_oof_dark_tail import (
    align_logit_payload,
    conditional_tail,
    transplant_probabilities,
)


class AlignLogitPayloadTest(unittest.TestCase):
    def test_reorders_by_id_instead_of_position(self):
        payload = {
            "ids": ["b", "a"],
            "logits": torch.tensor([[20.0, 21.0], [10.0, 11.0]]),
            "y_true": torch.tensor([1, 0]),
        }

        aligned = align_logit_payload(
            payload,
            target_ids=["a", "b"],
            target_y=torch.tensor([0, 1]),
            path=Path("synthetic.pt"),
        )

        self.assertEqual(aligned["ids"], ["a", "b"])
        self.assertTrue(
            torch.equal(aligned["logits"], torch.tensor([[10.0, 11.0], [20.0, 21.0]]))
        )


class ConditionalTailTest(unittest.TestCase):
    def test_removes_official_and_renormalizes(self):
        probabilities = torch.tensor(
            [[0.2, 0.5, 0.3], [0.6, 0.1, 0.3]], dtype=torch.float32
        )
        labels = torch.tensor([1, 0], dtype=torch.long)

        tail = conditional_tail(probabilities, labels)

        self.assertTrue(torch.allclose(tail.sum(dim=1), torch.ones(2)))
        self.assertEqual(float(tail[0, 1]), 0.0)
        self.assertEqual(float(tail[1, 0]), 0.0)


class TransplantProbabilitiesTest(unittest.TestCase):
    def setUp(self):
        self.full = torch.tensor(
            [
                [0.20, 0.50, 0.30],
                [0.60, 0.10, 0.30],
                [0.25, 0.40, 0.35],
            ],
            dtype=torch.float32,
        )
        self.oof = torch.tensor(
            [
                [0.10, 0.20, 0.70],
                [0.20, 0.70, 0.10],
                [0.70, 0.20, 0.10],
            ],
            dtype=torch.float32,
        )
        self.labels = torch.tensor([0, 0, 1], dtype=torch.long)
        self.c0 = torch.tensor([True, False, True])

    def test_preserves_official_and_c_gt_zero_exactly(self):
        output, _, _ = transplant_probabilities(
            self.full, self.oof, self.labels, self.c0, rho=0.5
        )
        rows = torch.arange(len(self.labels))

        self.assertTrue(
            torch.allclose(output[rows, self.labels], self.full[rows, self.labels])
        )
        self.assertTrue(torch.equal(output[~self.c0], self.full[~self.c0]))
        self.assertTrue(torch.allclose(output.sum(dim=1), torch.ones(3)))

    def test_endpoints_and_does_not_require_official_argmax(self):
        unchanged, _, _ = transplant_probabilities(
            self.full, self.oof, self.labels, self.c0, rho=0.0
        )
        transplanted, _, oof_tail = transplant_probabilities(
            self.full, self.oof, self.labels, self.c0, rho=1.0
        )
        rows = torch.arange(len(self.labels))
        official = self.full[rows, self.labels]
        expected = oof_tail * (1.0 - official).unsqueeze(1)
        expected[rows, self.labels] = official

        self.assertTrue(torch.allclose(unchanged, self.full))
        self.assertTrue(torch.allclose(transplanted[self.c0], expected[self.c0]))
        self.assertNotEqual(int(transplanted[0].argmax()), int(self.labels[0]))

    def test_rejects_invalid_rho(self):
        with self.assertRaisesRegex(ValueError, "rho"):
            transplant_probabilities(
                self.full, self.oof, self.labels, self.c0, rho=1.1
            )


if __name__ == "__main__":
    unittest.main()
