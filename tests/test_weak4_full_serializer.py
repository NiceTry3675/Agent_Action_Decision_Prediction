import unittest

import torch

from weak4_full_residual import (
    OP_VALUES,
    derive_task_categories,
    encode_utf8_bytes,
    main_numeric_features,
    serialize_weak4_full,
)


def sample():
    return {
        "id": "sess_sim_demo-step_03",
        "current_prompt": "find every reference to Widget in src/pkg.py",
        "session_meta": {
            "user_tier": "enterprise",
            "language_pref": "en",
            "turn_index": 3,
            "budget_tokens_remaining": 999,
            "elapsed_session_sec": 88,
            "workspace": {
                "git_dirty": True,
                "last_ci_status": "passed",
                "loc": 123456,
                "language_mix": {"py": 1.0},
                "open_files": ["src/pkg.py"],
            },
        },
        "history": [
            {"role": "user", "content": "show config"},
            {
                "role": "assistant_action",
                "name": "read_file",
                "args": {"path": "src/config.py"},
                "result_summary": "ok; read src/config.py (20L)",
            },
            {"role": "user", "content": "search Widget"},
            {
                "role": "assistant_action",
                "name": "grep_search",
                "args": {"scope": "src", "pattern": "Widget"},
                "result_summary": "14 matches in 5 files",
            },
        ],
    }


class Weak4FullSerializerTests(unittest.TestCase):
    def test_byte_encoding_is_bounded_and_preserves_both_ends(self):
        encoded = encode_utf8_bytes("abcdefghij", 6)
        self.assertEqual(encoded[0], 1)
        self.assertEqual(encoded[-1], 2)
        raw = bytes(value - 3 for value in encoded[1:-1])
        self.assertEqual(raw, b"abchij")

    def test_serializer_pairs_recent_events_and_excludes_private_scalars(self):
        logits = torch.tensor([4.0, 3.0, 2.0, 1.0] + [0.0] * 10)
        left = serialize_weak4_full(sample(), logits)
        changed = sample()
        changed["session_meta"]["user_tier"] = "free"
        changed["session_meta"]["budget_tokens_remaining"] = 1
        changed["session_meta"]["elapsed_session_sec"] = 99999
        changed["session_meta"]["workspace"]["loc"] = 1
        changed["session_meta"]["workspace"]["language_mix"] = {"go": 1.0}
        right = serialize_weak4_full(changed, logits)
        self.assertEqual(left, right)
        self.assertEqual(left.event_action_ids, (0, 1))
        self.assertEqual(len(left.event_text_bytes), 2)
        self.assertEqual(left.main_pred, 0)

    def test_task_abstraction_recognizes_content_search(self):
        task = derive_task_categories(sample())
        self.assertEqual(OP_VALUES[task[0]], "content_search")

    def test_main_features_are_logit_shift_invariant(self):
        logits = torch.arange(14, dtype=torch.float32) / 3
        left = torch.tensor(main_numeric_features(logits))
        right = torch.tensor(main_numeric_features(logits + 123.0))
        torch.testing.assert_close(left, right, atol=1e-5, rtol=0)


if __name__ == "__main__":
    unittest.main()

