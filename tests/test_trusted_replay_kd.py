import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from script import ALL_CLASSES, serialize_transformer_sample
from train import CLASS_TO_ID
from train_transformer import (
    add_replay_examples,
    apply_trusted_replay_kd,
    cache_path,
)


def pair(prompt, label):
    return [
        {"role": "user", "content": prompt},
        {
            "role": "assistant_action",
            "name": label,
            "args": {"path": f"{prompt}.py"},
            "result_summary": f"result for {prompt}",
        },
    ]


def original(session, step, prompt, label, history=None):
    return {
        "id": f"{session}-step_{step:02d}",
        "current_prompt": prompt,
        "history": copy.deepcopy(history or []),
        "session_meta": {
            "turn_index": step,
            "budget_tokens_remaining": 10_000 - step,
            "elapsed_session_sec": 10 * step,
            "workspace": {"git_dirty": False, "open_files": [f"{session}.py"]},
        },
        "_test_label": label,
    }


def fixture_samples():
    rows = []
    for session, replay_label in (
        ("sess_sim_weak", ALL_CLASSES[0]),
        ("sess_sim_normal", "run_bash"),
        ("sess_sim_low", ALL_CLASSES[1]),
    ):
        rows.append(original(session, 1, f"{session}-before", replay_label))
        rows.append(
            original(
                session,
                2,
                f"{session}-now",
                "edit_file",
                history=pair(f"{session}-before", replay_label),
            )
        )
    # This row still belongs to the unchanged legacy cap, but step 1 is absent.
    rows.append(
        original(
            "sess_sim_missing",
            2,
            "missing-now",
            "edit_file",
            history=pair("missing-before", "grep_search"),
        )
    )
    return rows


def labels(samples):
    return [CLASS_TO_ID[sample["_test_label"]] for sample in samples]


def replay_args(**overrides):
    values = {
        "replay_mode": "last1",
        "replay_meta_mode": "current",
        "replay_kd_source": "predecessor",
        "replay_kd_min_consensus": 2,
        "replay_kd_expected_predecessors": 3,
        "replay_kd_expected_unmatched": 1,
        "replay_kd_expected_active": 2,
        "replay_kd_expected_replay_id_sha256": "",
        "split": "session",
        "serializer": "current_v1",
        "max_replay_samples": 10_000,
        "replay_sample_weight": 0.5,
        "seed": 202,
        "distill_alpha": 0.5,
        "distill_alpha_weak": 0.7,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TrustedReplayKDTests(unittest.TestCase):
    def _add(self, args=None):
        samples = fixture_samples()
        y = labels(samples)
        args = args or replay_args()
        return (
            samples,
            y,
            args,
            add_replay_examples(samples, y, list(range(len(samples))), args),
        )

    def test_post_cap_linking_preserves_legacy_ids_order_text_and_metadata(self):
        control_samples = fixture_samples()
        control_y = labels(control_samples)
        control_args = replay_args(
            replay_kd_source="none",
            replay_kd_expected_predecessors=-1,
            replay_kd_expected_unmatched=-1,
            replay_kd_expected_active=-1,
        )
        control = add_replay_examples(
            control_samples,
            control_y,
            list(range(len(control_samples))),
            control_args,
        )

        _, _, args, treatment = self._add()
        control_rows = control[0][len(control_samples) :]
        treatment_rows = treatment[0][len(control_samples) :]
        self.assertEqual(
            [row["id"] for row in treatment_rows],
            [row["id"] for row in control_rows],
        )
        self.assertEqual(
            [serialize_transformer_sample(row, "current_v1") for row in treatment_rows],
            [serialize_transformer_sample(row, "current_v1") for row in control_rows],
        )
        self.assertEqual(
            [row["session_meta"] for row in treatment_rows],
            [row["session_meta"] for row in control_rows],
        )
        self.assertEqual(treatment[1], control[1])
        self.assertEqual(treatment[2], control[2])
        self.assertEqual(treatment[3], control[3])
        self.assertEqual(args.replay_kd_audit["exact_predecessors"], 3)
        self.assertEqual(args.replay_kd_audit["unmatched_predecessors"], 1)
        self.assertTrue(args.replay_kd_audit["input_and_order_invariant"])
        self.assertEqual(
            args.replay_kd_audit["serialized_replay_text_sha256_before"],
            args.replay_kd_audit["serialized_replay_text_sha256_after"],
        )

    def test_trusted_rows_inherit_teacher_only_above_consensus_threshold(self):
        original_samples, original_y, args, augmented = self._add()
        samples, y, train_idx, _, _ = augmented
        row_count = len(samples)
        teacher_logits = torch.arange(
            row_count * len(ALL_CLASSES), dtype=torch.float32
        ).reshape(row_count, len(ALL_CLASSES))
        teacher_mask = torch.zeros(row_count)
        teacher_mask[: len(original_samples)] = 1.0

        original_by_id = {
            sample["id"]: idx for idx, sample in enumerate(original_samples)
        }
        counts_by_session = {
            "sess_sim_weak-step_01": 2,
            "sess_sim_normal-step_01": 3,
            "sess_sim_low-step_01": 1,
        }
        correct_counts = torch.full((row_count,), -1, dtype=torch.long)
        for idx in range(len(original_samples)):
            correct_counts[idx] = counts_by_session.get(original_samples[idx]["id"], 0)
        consensus = {
            "correct_counts": correct_counts,
            "gradient_scales": torch.ones(row_count),
            "kd_gradient_scales": None,
            "meta": {"model_count": 3},
        }

        (updated_logits, updated_mask), updated_consensus = apply_trusted_replay_kd(
            (teacher_logits, teacher_mask),
            consensus,
            samples,
            y,
            train_idx,
            args,
        )

        active = []
        hard_only = []
        for replay_idx in range(len(original_samples), row_count):
            predecessor_id = samples[replay_idx].get("_replay_predecessor_id")
            predecessor_idx = original_by_id.get(predecessor_id)
            count = (
                int(correct_counts[predecessor_idx])
                if predecessor_idx is not None
                else -1
            )
            if count >= 2:
                active.append(replay_idx)
                torch.testing.assert_close(
                    updated_logits[replay_idx], teacher_logits[predecessor_idx]
                )
                expected_alpha = (
                    0.7 if int(y[replay_idx]) < 4 else 0.5
                )
                self.assertAlmostEqual(
                    float(updated_mask[replay_idx] * args.distill_alpha),
                    expected_alpha,
                    places=6,
                )
            else:
                hard_only.append(replay_idx)
                self.assertEqual(float(updated_mask[replay_idx]), 0.0)
        self.assertEqual(len(active), 2)
        self.assertEqual(len(hard_only), 2)
        torch.testing.assert_close(
            updated_logits[: len(original_samples)],
            teacher_logits[: len(original_samples)],
        )
        torch.testing.assert_close(
            updated_mask[: len(original_samples)],
            teacher_mask[: len(original_samples)],
        )
        self.assertIs(updated_consensus, consensus)
        self.assertEqual(correct_counts[len(original_samples) :].tolist(), [-1] * 4)
        self.assertEqual(args.replay_kd_audit["active_replay_kd_rows"], 2)
        self.assertEqual(args.replay_kd_audit["active_weak4_rows"], 1)
        self.assertEqual(args.replay_kd_audit["below_consensus_threshold"], 1)

    def test_expected_predecessor_count_mismatch_fails_closed(self):
        with self.assertRaisesRegex(AssertionError, "predecessor-count mismatch"):
            self._add(replay_args(replay_kd_expected_predecessors=99))

    def test_replay_kd_does_not_change_text_cache_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "train.jsonl"
            source.write_text("{}\n", encoding="utf-8")
            common = dict(
                base_model="hcx",
                serializer="current_v1",
                terminal_token="",
                replay_mode="last1",
                replay_meta_mode="current",
                max_replay_samples=10_000,
                replay_sample_weight=0.5,
                split="session",
                fold_id=0,
                n_folds=3,
                seed=202,
                cache_dir=tmp,
                max_length=384,
            )
            control = cache_path(
                SimpleNamespace(**common, replay_kd_source="none"),
                source,
                80_000,
                "texts",
            )
            treatment = cache_path(
                SimpleNamespace(**common, replay_kd_source="predecessor"),
                source,
                80_000,
                "texts",
            )
            self.assertEqual(control, treatment)


if __name__ == "__main__":
    unittest.main()
