import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from export_teacher_logits import assert_reference_logits, model_weights_sha256
from script import ALL_CLASSES
from train_transformer import (
    RELATIONAL_HIDDEN_KIND,
    RELATIONAL_HIDDEN_POOLING,
    RELATIONAL_HIDDEN_SCHEMA_VERSION,
    RELATIONAL_HIDDEN_USAGE_SCOPE,
    build_relational_teacher_targets,
    centered_cosine_gram_loss,
    pool_classifier_hidden,
)


class CenteredCosineGramTests(unittest.TestCase):
    def test_independent_rotations_and_positive_scales_are_invariant(self):
        torch.manual_seed(7)
        base = torch.randn(7, 5)
        q_student = torch.linalg.qr(torch.randn(5, 5)).Q
        q_teacher = torch.linalg.qr(torch.randn(5, 5)).Q
        student = 3.25 * (base @ q_student)
        teacher = 0.37 * (base @ q_teacher)
        loss = centered_cosine_gram_loss(student, teacher)
        self.assertLess(float(loss), 1e-6)

    def test_different_relations_produce_nonzero_gradient(self):
        torch.manual_seed(11)
        student = torch.randn(6, 5, requires_grad=True)
        teacher = torch.randn(6, 7)
        loss = centered_cosine_gram_loss(student, teacher)
        self.assertGreater(float(loss.detach()), 1e-5)
        loss.backward()
        self.assertIsNotNone(student.grad)
        self.assertGreater(float(student.grad.abs().sum()), 0.0)

    def test_fewer_than_two_matched_rows_is_differentiable_zero(self):
        student = torch.randn(4, 3, requires_grad=True)
        teacher = torch.randn(4, 5)
        loss = centered_cosine_gram_loss(
            student, teacher, mask=torch.tensor([False, True, False, False])
        )
        self.assertEqual(float(loss.detach()), 0.0)
        loss.backward()
        torch.testing.assert_close(student.grad, torch.zeros_like(student))

    def test_masked_replay_row_has_no_effect_or_gradient(self):
        torch.manual_seed(13)
        student = torch.randn(5, 4, requires_grad=True)
        teacher = torch.randn(5, 6)
        mask = torch.tensor([True, True, True, True, False])
        loss = centered_cosine_gram_loss(student, teacher, mask=mask)
        changed_teacher = teacher.detach().clone()
        changed_teacher[-1] = 10000.0
        changed_loss = centered_cosine_gram_loss(student, changed_teacher, mask=mask)
        torch.testing.assert_close(loss, changed_loss, rtol=0, atol=0)
        loss.backward()
        torch.testing.assert_close(student.grad[-1], torch.zeros_like(student.grad[-1]))


class HiddenPoolingTests(unittest.TestCase):
    def test_last_nonpadding_classifier_input(self):
        hidden = torch.arange(2 * 4 * 3, dtype=torch.float32).view(2, 4, 3)
        logits = torch.zeros(2, len(ALL_CLASSES))
        mask = torch.tensor([[1, 1, 0, 0], [0, 1, 1, 1]])
        pooled = pool_classifier_hidden(
            hidden, logits, {"attention_mask": mask}
        )
        torch.testing.assert_close(pooled[0], hidden[0, 1])
        torch.testing.assert_close(pooled[1], hidden[1, 3])


class RelationalCacheContractTests(unittest.TestCase):
    def _fixture(self, root):
        data_dir = root / "data"
        data_dir.mkdir()
        train_path = data_dir / "train.jsonl"
        train_path.write_text(
            '{"id":"a","current_prompt":"a"}\n'
            '{"id":"b","current_prompt":"b"}\n',
            encoding="utf-8",
        )
        data_sha = hashlib.sha256(train_path.read_bytes()).hexdigest()
        distill_path = root / "teacher_logits.pt"
        torch.save({"fixture": True}, distill_path)
        distill_sha = hashlib.sha256(distill_path.read_bytes()).hexdigest()
        cache_path = root / "hidden.pt"
        payload = {
            "schema_version": RELATIONAL_HIDDEN_SCHEMA_VERSION,
            "kind": RELATIONAL_HIDDEN_KIND,
            "usage_scope": RELATIONAL_HIDDEN_USAGE_SCOPE,
            "ids": ["b", "a"],
            "hidden": torch.tensor(
                [[2.0, 0.0, 1.0], [1.0, 3.0, 0.0]], dtype=torch.float16
            ),
            "classes": list(ALL_CLASSES),
            "y_true": torch.tensor([1, 0]),
            "metadata": {
                "serializer_name": "current_v1",
                "base_model": "m8-test",
                "terminal_token": "",
                "max_length": 400,
                "pooling": RELATIONAL_HIDDEN_POOLING,
                "hidden_size": 3,
                "row_count": 2,
                "model_weights_sha256": "a" * 64,
                "model_weight_files": ["model.safetensors"],
                "prefer_fp16_weights": True,
                "dtype": "fp16",
                "classifier_head": "score",
                "asserted_reference_logits": {
                    "sha256": distill_sha,
                    "argmax_agreement": 1.0,
                    "max_abs": 0.01,
                },
                "data_sha256": data_sha,
            },
        }
        torch.save(payload, cache_path)
        args = SimpleNamespace(
            relational_teacher_hidden=str(cache_path),
            relational_teacher_base_model="m8-test",
            relational_teacher_max_length=400,
            relational_teacher_terminal_token="",
            relational_kd_weight=0.05,
            serializer="current_v1",
            data_dir=str(data_dir),
            distill_logits=str(distill_path),
        )
        samples = [
            {"id": "a"},
            {"id": "b"},
            {"id": "a::replay_0_x", "_is_replay": True},
        ]
        y = [0, 1, 0]
        logit_teacher = (
            torch.zeros(3, len(ALL_CLASSES)),
            torch.tensor([1.0, 1.4, 0.0]),
        )
        return cache_path, payload, args, samples, y, logit_teacher

    def test_aligns_ids_labels_and_masks_replay(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, _, args, samples, y, logit_teacher = self._fixture(root)
            result = build_relational_teacher_targets(
                samples, y, args, teacher=logit_teacher
            )
        self.assertEqual(result["mask"].tolist(), [True, True, False])
        torch.testing.assert_close(
            result["hidden"][0], torch.tensor([1.0, 3.0, 0.0], dtype=torch.float16)
        )
        torch.testing.assert_close(result["hidden"][2], torch.zeros(3, dtype=torch.float16))

    def test_fails_closed_on_class_serializer_length_and_duplicate_ids(self):
        mutations = [
            ("class order", lambda payload: payload.update(classes=list(reversed(ALL_CLASSES)))),
            (
                "serializer mismatch",
                lambda payload: payload["metadata"].update(serializer_name="current_v7r"),
            ),
            (
                "max_length mismatch",
                lambda payload: payload["metadata"].update(max_length=399),
            ),
            ("duplicate ids", lambda payload: payload.update(ids=["a", "a"])),
        ]
        for expected, mutate in mutations:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                cache_path, payload, args, samples, y, logit_teacher = self._fixture(root)
                broken = copy.deepcopy(payload)
                mutate(broken)
                torch.save(broken, cache_path)
                with self.assertRaisesRegex(ValueError, expected):
                    build_relational_teacher_targets(
                        samples, y, args, teacher=logit_teacher
                    )

    def test_logit_kd_mask_must_match_exactly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, _, args, samples, y, logit_teacher = self._fixture(root)
            logit_teacher = (logit_teacher[0], torch.tensor([1.0, 0.0, 0.0]))
            with self.assertRaisesRegex(ValueError, "exactly match"):
                build_relational_teacher_targets(
                    samples, y, args, teacher=logit_teacher
                )


class ReferenceLogitGateTests(unittest.TestCase):
    def test_aligns_shuffled_ids_and_reports_fidelity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reference_path = root / "reference.pt"
            reference_logits = torch.tensor(
                [[2.0, 0.0], [0.0, 3.0]], dtype=torch.float16
            )
            reference = {
                "ids": ["b", "a"],
                "classes": ["x", "y"],
                "logits": reference_logits,
                "y_true": torch.tensor([1, 0]),
                "metadata": {
                    "base_model": "m8-test",
                    "serializer_name": "current_v1",
                    "max_length": 400,
                },
            }
            torch.save(reference, reference_path)
            current = {
                "ids": ["a", "b"],
                "classes": ["x", "y"],
                "logits": torch.tensor([[0.01, 3.01], [2.01, 0.01]]),
                "y_true": torch.tensor([0, 1]),
                "metadata": dict(reference["metadata"]),
            }
            metrics = assert_reference_logits(
                current, reference_path, min_agreement=1.0, max_abs=0.02
            )
        self.assertEqual(metrics["argmax_agreement"], 1.0)
        self.assertLessEqual(metrics["max_abs"], 0.02)


class ExportWeightProvenanceTests(unittest.TestCase):
    def test_adapter_only_teacher_hashes_adapter_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "adapter_config.json").write_text("{}", encoding="utf-8")
            weights = b"adapter-only-test-weights"
            (root / "adapter_model.safetensors").write_bytes(weights)

            observed, files = model_weights_sha256(root)

            digest = hashlib.sha256()
            name = b"adapter_model.safetensors"
            digest.update(len(name).to_bytes(4, "big"))
            digest.update(name)
            digest.update(weights)
            self.assertEqual(observed, digest.hexdigest())
            self.assertEqual(files, ["adapter_model.safetensors"])

    def test_hidden_export_preference_rejects_adapter_only_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "adapter_config.json").write_text("{}", encoding="utf-8")
            (root / "adapter_model.safetensors").write_bytes(b"adapter")

            with self.assertRaises(FileNotFoundError):
                model_weights_sha256(root, prefer_fp16=True)


if __name__ == "__main__":
    unittest.main()
