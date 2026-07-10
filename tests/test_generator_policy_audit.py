import unittest

from audit_generator_policy_mixture import (
    START_ACTION,
    action_context,
    mutual_information_bits,
    session_fold,
    source_prefix,
)


class GeneratorPolicyAuditTests(unittest.TestCase):
    def test_source_prefix(self):
        self.assertEqual(source_prefix("sess_sim_20260522_000001-step_02"), "sess_sim")
        self.assertEqual(source_prefix("sess_au_050092_004-step_04"), "sess_au")
        self.assertEqual(source_prefix("invalid"), "<UNPARSED>")

    def test_action_context_start_and_pair(self):
        self.assertEqual(action_context({"history": []}), (START_ACTION, START_ACTION))
        sample = {
            "history": [
                {"role": "assistant_action", "name": "grep_search"},
                {"role": "user", "content": "continue"},
                {"role": "assistant_action", "name": "read_file"},
            ]
        }
        self.assertEqual(action_context(sample), ("grep_search", "read_file"))

    def test_session_fold_keeps_steps_together(self):
        left = session_fold("sess_sim_20260522_000001-step_01", 5, 42)
        right = session_fold("sess_sim_20260522_000001-step_09", 5, 42)
        self.assertEqual(left, right)

    def test_mutual_information_known_cases(self):
        labels = [0, 0, 1, 1]
        self.assertAlmostEqual(mutual_information_bits(labels, ["x"] * 4), 0.0)
        self.assertAlmostEqual(mutual_information_bits(labels, ["a", "a", "b", "b"]), 1.0)


if __name__ == "__main__":
    unittest.main()
