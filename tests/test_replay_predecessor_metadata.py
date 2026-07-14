import copy
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from train import CLASS_TO_ID
from train_transformer import (
    add_replay_examples,
    build_replay_predecessor_index,
    cache_path,
    replay_examples_for_sample,
)


def original(session, step, prompt, label, *, history=None, meta_extra=None):
    meta = {
        "turn_index": step,
        "budget_tokens_remaining": 100_000 - step * 1_000,
        "elapsed_session_sec": step * 10,
        "workspace": {
            "git_dirty": bool(step % 2),
            "open_files": [f"step{step}.py"],
        },
    }
    if meta_extra:
        meta.update(meta_extra)
    return {
        "id": f"{session}-step_{step:02d}",
        "current_prompt": prompt,
        "history": copy.deepcopy(history or []),
        "session_meta": meta,
        "_test_label": label,
    }


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


def labels(samples):
    return [CLASS_TO_ID[sample["_test_label"]] for sample in samples]


def replay_args(**overrides):
    values = {
        "replay_mode": "last1",
        "replay_meta_mode": "predecessor",
        "split": "session",
        "max_replay_samples": 10_000,
        "replay_sample_weight": 0.5,
        "seed": 42,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ReplayPredecessorMetadataTests(unittest.TestCase):
    def test_default_current_mode_preserves_historical_source_metadata(self):
        source = original(
            "sess_sim_source",
            2,
            "now",
            "edit_file",
            history=pair("before", "read_file"),
        )
        replay = replay_examples_for_sample(source, pair_limit=1)
        self.assertEqual(len(replay), 1)
        self.assertIs(replay[0][1]["session_meta"], source["session_meta"])
        self.assertNotIn("_replay_predecessor_id", replay[0][1])

    def test_exact_predecessor_supplies_meta_without_changing_history_or_label(self):
        first = original("sess_sim_exact", 1, "before", "read_file")
        source = original(
            "sess_sim_exact",
            2,
            "now",
            "edit_file",
            history=pair("before", "read_file"),
        )
        samples = [first, source]
        y = labels(samples)
        index, _ = build_replay_predecessor_index(samples, y, [0, 1])
        audit = Counter()
        replay = replay_examples_for_sample(
            source,
            pair_limit=1,
            replay_meta_mode="predecessor",
            predecessor_index=index,
            audit=audit,
        )

        self.assertEqual(len(replay), 1)
        label_id, replay_sample = replay[0]
        self.assertEqual(label_id, CLASS_TO_ID["read_file"])
        self.assertEqual(replay_sample["history"], [])
        self.assertEqual(replay_sample["current_prompt"], "before")
        self.assertEqual(replay_sample["session_meta"], first["session_meta"])
        self.assertIsNot(replay_sample["session_meta"], first["session_meta"])
        self.assertEqual(replay_sample["_replay_predecessor_id"], first["id"])
        self.assertEqual(replay_sample["_replay_predecessor_step"], 1)
        self.assertEqual(audit["matched_candidates"], 1)

    def test_missing_predecessor_is_dropped_before_balanced_cap(self):
        source = original(
            "sess_sim_missing",
            2,
            "now",
            "edit_file",
            history=pair("missing", "read_file"),
        )
        args = replay_args()
        new_samples, new_y, new_idx, weights, replay_size = add_replay_examples(
            [source],
            labels([source]),
            [0],
            args,
        )
        self.assertEqual(replay_size, 0)
        self.assertEqual(len(new_samples), 1)
        self.assertEqual(len(new_y), 1)
        self.assertEqual(new_idx, [0])
        self.assertEqual(weights, [1.0])
        self.assertEqual(args.replay_predecessor_audit["dropped_predecessor_missing"], 1)
        self.assertEqual(args.replay_predecessor_audit["dropped_candidates"], 1)
        self.assertEqual(
            args.replay_predecessor_audit["drop_policy"],
            "drop_before_balanced_cap_on_missing_or_nonexact_predecessor",
        )

    def test_prompt_and_label_mismatches_are_fail_closed(self):
        predecessor = original("sess_sim_mismatch", 1, "recorded", "read_file")
        source_prompt = original(
            "sess_sim_mismatch",
            2,
            "now",
            "edit_file",
            history=pair("different", "read_file"),
        )
        prompt_samples = [predecessor, source_prompt]
        prompt_index, _ = build_replay_predecessor_index(
            prompt_samples, labels(prompt_samples), [0, 1]
        )
        prompt_audit = Counter()
        self.assertEqual(
            replay_examples_for_sample(
                source_prompt,
                1,
                "predecessor",
                prompt_index,
                prompt_audit,
            ),
            [],
        )
        self.assertEqual(prompt_audit["dropped_prompt_mismatch"], 1)

        source_label = original(
            "sess_sim_mismatch",
            2,
            "now",
            "edit_file",
            history=pair("recorded", "grep_search"),
        )
        label_audit = Counter()
        self.assertEqual(
            replay_examples_for_sample(
                source_label,
                1,
                "predecessor",
                prompt_index,
                label_audit,
            ),
            [],
        )
        self.assertEqual(label_audit["dropped_label_mismatch"], 1)

    def test_valid_candidates_are_balanced_then_capped_with_weight_unchanged(self):
        samples = []
        for number, label in enumerate(
            ["read_file", "read_file", "grep_search", "grep_search"], start=1
        ):
            session = f"sess_sim_cap{number}"
            first = original(session, 1, f"before{number}", label)
            second = original(
                session,
                2,
                f"now{number}",
                "edit_file",
                history=pair(f"before{number}", label),
            )
            samples.extend([first, second])
        y = labels(samples)
        args = replay_args(max_replay_samples=2)
        new_samples, new_y, new_idx, weights, replay_size = add_replay_examples(
            samples, y, list(range(len(samples))), args
        )

        self.assertEqual(replay_size, 2)
        self.assertEqual(len(new_samples), len(samples) + 2)
        self.assertEqual(len(new_y), len(y) + 2)
        self.assertEqual(len(new_idx), len(samples) + 2)
        self.assertEqual(weights[-2:], [0.5, 0.5])
        self.assertEqual(
            {new_samples[idx]["_test_label"] for idx in range(len(samples))},
            {"read_file", "grep_search", "edit_file"},
        )
        self.assertEqual(
            {new_y[-2], new_y[-1]},
            {CLASS_TO_ID["read_file"], CLASS_TO_ID["grep_search"]},
        )
        self.assertEqual(args.replay_predecessor_audit["matched_before_cap"], 4)
        self.assertEqual(args.replay_predecessor_audit["selected_after_cap"], 2)

    def test_duplicate_session_step_is_rejected_as_ambiguous(self):
        left = original("sess_sim_duplicate", 1, "left", "read_file")
        right = original("sess_sim_duplicate", 1, "right", "grep_search")
        with self.assertRaisesRegex(ValueError, "ambiguous replay predecessor key"):
            build_replay_predecessor_index([left, right], labels([left, right]), [0, 1])

    def test_predecessor_mode_has_a_distinct_cache_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "train.jsonl"
            source.write_text("{}\n", encoding="utf-8")
            common = dict(
                base_model="hcx",
                serializer="current_v1",
                terminal_token="",
                replay_mode="last1",
                max_replay_samples=10_000,
                replay_sample_weight=0.5,
                split="session",
                fold_id=0,
                n_folds=3,
                seed=42,
                cache_dir=tmp,
                max_length=384,
            )
            current = cache_path(
                SimpleNamespace(**common, replay_meta_mode="current"),
                source,
                80_000,
                "texts",
            )
            predecessor = cache_path(
                SimpleNamespace(**common, replay_meta_mode="predecessor"),
                source,
                80_000,
                "texts",
            )
            self.assertNotEqual(current, predecessor)
            self.assertNotIn("meta-current", current.name)
            self.assertIn("meta-predecessor", predecessor.name)


if __name__ == "__main__":
    unittest.main()
