import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from build_oof_consensus import build_consensus_payload
from script import ALL_CLASSES
from train_transformer import (
    build_consensus_reliability,
    forward_with_consensus_sieve,
    parse_consensus_backbone_weights,
)


class TinySequenceClassifier(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.backbone = torch.nn.Linear(3, 5, bias=False)
        self.score = torch.nn.Linear(5, len(ALL_CLASSES), bias=False)

    def forward(self, input_ids, attention_mask=None):
        hidden = self.backbone(input_ids.float())
        return SimpleNamespace(logits=self.score(hidden))


class TinyTokenSequenceClassifier(TinySequenceClassifier):
    def forward(self, input_ids, attention_mask=None):
        hidden = self.backbone(input_ids.float())
        token_logits = self.score(hidden)
        positions = torch.arange(input_ids.shape[1]).view(1, -1)
        last = positions.expand_as(attention_mask).masked_fill(
            ~attention_mask.bool(), -1
        ).max(dim=1).values
        rows = torch.arange(input_ids.shape[0])
        return SimpleNamespace(logits=token_logits[rows, last])


def write_oof_fold(path, ids, labels, predictions, fold_id, n_folds, model_name):
    logits = torch.full((len(ids), len(ALL_CLASSES)), -4.0)
    for row, prediction in enumerate(predictions):
        logits[row, prediction] = 4.0
    torch.save(
        {
            "ids": ids,
            "y_true": labels,
            "logits": logits,
            "classes": ALL_CLASSES,
            "split": "session_oof",
            "fold_id": fold_id,
            "n_folds": n_folds,
            "seed": 42,
            "base_model": model_name,
            "serializer_name": "current_v1",
        },
        path,
    )


class ConsensusArtifactTests(unittest.TestCase):
    def test_builder_aligns_shuffled_fold_rows_by_id(self):
        samples = [{"id": sample_id} for sample_id in ("a", "b", "c", "d")]
        labels_by_id = {
            "a": ALL_CLASSES[0],
            "b": ALL_CLASSES[1],
            "c": ALL_CLASSES[0],
            "d": ALL_CLASSES[1],
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            m1f0, m1f1 = root / "m1f0.pt", root / "m1f1.pt"
            m2f0, m2f1 = root / "m2f0.pt", root / "m2f1.pt"
            write_oof_fold(m1f0, ["c", "a"], [0, 0], [0, 1], 0, 2, "m1")
            write_oof_fold(m1f1, ["d", "b"], [1, 1], [0, 1], 1, 2, "m1")
            write_oof_fold(m2f0, ["a", "c"], [0, 0], [0, 1], 0, 2, "m2")
            write_oof_fold(m2f1, ["b", "d"], [1, 1], [1, 0], 1, 2, "m2")

            payload = build_consensus_payload(
                samples,
                labels_by_id,
                [[m1f0, m1f1], [m2f0, m2f1]],
                "0,0.5,1",
            )

        self.assertEqual(payload["ids"], ["a", "b", "c", "d"])
        self.assertEqual(payload["usage_scope"], "full_data_refit_only")
        self.assertEqual(payload["correct_counts"].tolist(), [1, 2, 1, 0])
        self.assertEqual(
            payload["correct_count_histogram"], {"0": 1, "1": 2, "2": 1}
        )

    def test_builder_rejects_overlapping_oof_folds(self):
        samples = [{"id": "a"}, {"id": "b"}]
        labels_by_id = {"a": ALL_CLASSES[0], "b": ALL_CLASSES[1]}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f0, f1 = root / "f0.pt", root / "f1.pt"
            write_oof_fold(f0, ["a"], [0], [0], 0, 2, "m")
            write_oof_fold(f1, ["a", "b"], [0, 1], [0, 1], 1, 2, "m")
            with self.assertRaisesRegex(ValueError, "overlap"):
                build_consensus_payload(
                    samples, labels_by_id, [[f0, f1]], "0,1"
                )


class ReliabilityAlignmentTests(unittest.TestCase):
    def _artifact(self, path):
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

    def test_class_normalization_and_replay_contract(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reliability.pt"
            self._artifact(path)
            samples = [
                {"id": "a"},
                {"id": "b"},
                {"id": "c"},
                {"id": "d"},
                {"id": "a::replay_0_x", "_is_replay": True},
            ]
            labels = [0, 0, 1, 1, 0]
            args = SimpleNamespace(
                consensus_reliability=str(path),
                consensus_backbone_weights="",
                consensus_class_normalize=True,
            )
            reliability = build_consensus_reliability(
                samples, labels, list(range(5)), args
            )

        torch.testing.assert_close(
            reliability["gradient_scales"],
            torch.tensor([0.0, 2.0, 2.0 / 3.0, 4.0 / 3.0, 1.0]),
        )
        self.assertEqual(reliability["correct_counts"].tolist(), [0, 2, 1, 2, -1])
        self.assertEqual(reliability["meta"]["train_replay_rows"], 1)
        self.assertAlmostEqual(
            reliability["meta"]["class_stats"][ALL_CLASSES[0]][
                "effective_original_mean"
            ],
            1.0,
        )

    def test_label_mismatch_fails_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reliability.pt"
            self._artifact(path)
            args = SimpleNamespace(
                consensus_reliability=str(path),
                consensus_backbone_weights="",
                consensus_class_normalize=True,
            )
            with self.assertRaisesRegex(ValueError, "labels do not match"):
                build_consensus_reliability(
                    [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}],
                    [0, 0, 1, 2],
                    [0, 1, 2, 3],
                    args,
                )

    def test_full_data_artifact_rejects_heldout_validation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reliability.pt"
            self._artifact(path)
            args = SimpleNamespace(
                consensus_reliability=str(path),
                consensus_backbone_weights="",
                consensus_class_normalize=True,
            )
            with self.assertRaisesRegex(ValueError, "held-out validation"):
                build_consensus_reliability(
                    [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}],
                    [0, 0, 1, 1],
                    [0, 1, 2],
                    args,
                )

    def test_weight_parser_rejects_invalid_order(self):
        with self.assertRaisesRegex(ValueError, "nondecreasing"):
            parse_consensus_backbone_weights("0,1,0.5")


class GradientContractTests(unittest.TestCase):
    def test_decoder_token_head_recomputation_uses_last_nonpadding_token(self):
        torch.manual_seed(11)
        model = TinyTokenSequenceClassifier()
        encoded = {
            "input_ids": torch.randn(2, 4, 3),
            "attention_mask": torch.tensor([[1, 1, 0, 0], [0, 1, 1, 1]]),
        }
        ordinary, hard = forward_with_consensus_sieve(
            model, encoded, torch.tensor([0.0, 1.0])
        )
        torch.testing.assert_close(ordinary, hard, rtol=0, atol=0)

    def test_hard_head_is_full_backbone_is_scaled_and_kd_is_untouched(self):
        torch.manual_seed(17)
        sieve_model = TinySequenceClassifier()
        expected_model = copy.deepcopy(sieve_model)
        encoded = {
            "input_ids": torch.tensor(
                [[0.2, -0.4, 1.0], [1.2, 0.5, -0.7]], dtype=torch.float32
            ),
            "attention_mask": torch.ones(2, 3, dtype=torch.long),
        }
        labels = torch.tensor([0, 1])
        scales = torch.tensor([0.0, 0.25])
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

        ordinary_logits, hard_logits = forward_with_consensus_sieve(
            sieve_model, encoded, scales
        )
        torch.testing.assert_close(ordinary_logits, hard_logits, rtol=0, atol=0)
        hard = torch.nn.functional.cross_entropy(
            hard_logits, labels, reduction="none"
        )
        kd = torch.nn.functional.kl_div(
            torch.log_softmax(ordinary_logits, dim=-1),
            teacher_prob,
            reduction="none",
        ).sum(-1)
        ((1.0 - alpha) * hard + alpha * kd).mean().backward()

        expected_logits = expected_model(**encoded).logits
        expected_hard = torch.nn.functional.cross_entropy(
            expected_logits, labels, reduction="none"
        )
        expected_kd = torch.nn.functional.kl_div(
            torch.log_softmax(expected_logits, dim=-1),
            teacher_prob,
            reduction="none",
        ).sum(-1)
        # A single expression cannot give different head/backbone weighting, so
        # collect the two analytically expected parameter gradients separately.
        expected_head = torch.autograd.grad(
            ((1.0 - alpha) * expected_hard + alpha * expected_kd).mean(),
            expected_model.score.weight,
            retain_graph=True,
        )[0]
        expected_backbone = torch.autograd.grad(
            (
                (1.0 - alpha) * expected_hard * scales
                + alpha * expected_kd
            ).mean(),
            expected_model.backbone.weight,
        )[0]

        torch.testing.assert_close(sieve_model.score.weight.grad, expected_head)
        torch.testing.assert_close(
            sieve_model.backbone.weight.grad, expected_backbone
        )


if __name__ == "__main__":
    unittest.main()
