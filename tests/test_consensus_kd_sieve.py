import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from script import ALL_CLASSES
from train_transformer import (
    build_consensus_reliability,
    forward_with_consensus_sieve,
)


class TinySequenceClassifier(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.backbone = torch.nn.Linear(3, 5, bias=False)
        self.score = torch.nn.Linear(5, len(ALL_CLASSES), bias=False)

    def forward(self, input_ids, attention_mask=None):
        hidden = self.backbone(input_ids.float())
        return SimpleNamespace(logits=self.score(hidden))


def reliability_artifact(path):
    torch.save(
        {
            "schema_version": 1,
            "kind": "oof_correct_consensus_reliability",
            "usage_scope": "full_data_refit_only",
            "classes": ALL_CLASSES,
            "ids": ["a", "b", "c", "d"],
            "y_true": torch.tensor([0, 0, 1, 1]),
            "correct_counts": torch.tensor([0, 2, 1, 2]),
            "model_count": 2,
            "backbone_weights": [0.0, 0.5, 1.0],
            "sources": [],
        },
        path,
    )


def build_args(**overrides):
    defaults = {
        "consensus_reliability": "",
        "consensus_backbone_weights": "",
        "consensus_kd_weights": "",
        "consensus_class_normalize": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class KdScaleBuilderTests(unittest.TestCase):
    def test_kd_scales_are_normalized_and_replay_stays_one(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reliability.pt"
            reliability_artifact(path)
            samples = [
                {"id": "a"},
                {"id": "b"},
                {"id": "c"},
                {"id": "d"},
                {"id": "a-replay", "_is_replay": True},
            ]
            y = [0, 0, 1, 1, 0]
            args = build_args(
                consensus_reliability=str(path),
                consensus_kd_weights="0,0.5,1",
            )
            consensus = build_consensus_reliability(
                samples, y, list(range(len(samples))), args
            )
        kd = consensus["kd_gradient_scales"]
        self.assertIsNotNone(kd)
        # class 0 raw kd = [0, 1] -> mean 0.5 -> normalized [0, 2]
        self.assertAlmostEqual(float(kd[0]), 0.0)
        self.assertAlmostEqual(float(kd[1]), 2.0)
        # class 1 raw kd = [0.5, 1] -> mean 0.75 -> [2/3, 4/3]
        self.assertAlmostEqual(float(kd[2]), 0.5 / 0.75, places=6)
        self.assertAlmostEqual(float(kd[3]), 1.0 / 0.75, places=6)
        # replay keeps 1.0 (and is KD-masked upstream anyway)
        self.assertAlmostEqual(float(kd[4]), 1.0)
        self.assertEqual(consensus["meta"]["kd_weights"], [0.0, 0.5, 1.0])
        # hard-branch scales must be unchanged by adding the kd config
        args_hard_only = build_args(consensus_reliability=str(path))
        with tempfile.TemporaryDirectory() as td:
            path2 = Path(td) / "reliability.pt"
            reliability_artifact(path2)
            args_hard_only.consensus_reliability = str(path2)
            baseline = build_consensus_reliability(
                samples, y, list(range(len(samples))), args_hard_only
            )
        torch.testing.assert_close(
            consensus["gradient_scales"], baseline["gradient_scales"]
        )
        self.assertIsNone(baseline["kd_gradient_scales"])

    def test_without_kd_weights_payload_has_none(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reliability.pt"
            reliability_artifact(path)
            samples = [{"id": s} for s in ("a", "b", "c", "d")]
            args = build_args(consensus_reliability=str(path))
            consensus = build_consensus_reliability(samples, [0, 0, 1, 1], [0, 1, 2, 3], args)
        self.assertIsNone(consensus["kd_gradient_scales"])
        self.assertIsNone(consensus["meta"]["kd_weights"])


class KdGradientContractTests(unittest.TestCase):
    def test_kd_head_full_kd_backbone_scaled_hard_branch_unchanged(self):
        torch.manual_seed(23)
        sieve_model = TinySequenceClassifier()
        expected_model = copy.deepcopy(sieve_model)
        encoded = {
            "input_ids": torch.tensor(
                [[0.2, -0.4, 1.0], [1.2, 0.5, -0.7]], dtype=torch.float32
            ),
            "attention_mask": torch.ones(2, 3, dtype=torch.long),
        }
        labels = torch.tensor([0, 1])
        hard_scales = torch.tensor([0.0, 0.25])
        kd_scales = torch.tensor([0.5, 1.0])
        teacher_prob = torch.softmax(
            torch.tensor(
                [
                    [2.0] + [0.0] * (len(ALL_CLASSES) - 1),
                    [0.0, 2.0] + [0.0] * (len(ALL_CLASSES) - 2),
                ]
            ),
            dim=-1,
        )
        alpha = 0.4

        ordinary, hard_logits, kd_logits = forward_with_consensus_sieve(
            sieve_model, encoded, hard_scales, kd_backbone_scales=kd_scales
        )
        torch.testing.assert_close(ordinary, hard_logits, rtol=0, atol=0)
        torch.testing.assert_close(ordinary, kd_logits, rtol=0, atol=0)
        hard = torch.nn.functional.cross_entropy(hard_logits, labels, reduction="none")
        kd = torch.nn.functional.kl_div(
            torch.log_softmax(kd_logits, dim=-1), teacher_prob, reduction="none"
        ).sum(-1)
        ((1.0 - alpha) * hard + alpha * kd).mean().backward()

        expected_logits = expected_model(**encoded).logits
        expected_hard = torch.nn.functional.cross_entropy(
            expected_logits, labels, reduction="none"
        )
        expected_kd = torch.nn.functional.kl_div(
            torch.log_softmax(expected_logits, dim=-1), teacher_prob, reduction="none"
        ).sum(-1)
        expected_head = torch.autograd.grad(
            ((1.0 - alpha) * expected_hard + alpha * expected_kd).mean(),
            expected_model.score.weight,
            retain_graph=True,
        )[0]
        expected_backbone = torch.autograd.grad(
            (
                (1.0 - alpha) * expected_hard * hard_scales
                + alpha * expected_kd * kd_scales
            ).mean(),
            expected_model.backbone.weight,
        )[0]

        torch.testing.assert_close(sieve_model.score.weight.grad, expected_head)
        torch.testing.assert_close(sieve_model.backbone.weight.grad, expected_backbone)

    def test_two_tuple_return_without_kd_scales_is_preserved(self):
        torch.manual_seed(29)
        model = TinySequenceClassifier()
        encoded = {
            "input_ids": torch.randn(2, 3),
            "attention_mask": torch.ones(2, 3, dtype=torch.long),
        }
        result = forward_with_consensus_sieve(model, encoded, torch.tensor([0.5, 1.0]))
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
