import unittest

import numpy as np

from audit_generator_fsm import (
    apply_residual,
    arg_schema,
    normalize_result,
    normalize_text,
    scenario_group,
)


class GeneratorFsmAuditTests(unittest.TestCase):
    def test_prompt_template_removes_identity_but_keeps_type(self):
        value = normalize_text(
            "`components/Button.tsx`에서 useAuth 17번, '*.yml'도 찾아줘"
        )
        self.assertIn("<path>", value)
        self.assertIn("<glob:yml>", value)
        self.assertIn("<num>", value)
        self.assertNotIn("button", value)
        self.assertNotIn("useauth", value)

    def test_event_signature_uses_schema_not_identity(self):
        left_args = {"pattern": "useAuth", "scope": "components/"}
        right_args = {"pattern": "otherSymbol", "scope": "src/"}
        self.assertEqual(arg_schema(left_args), arg_schema(right_args))
        self.assertEqual(
            normalize_result("found 17 occurrences of useAuth"),
            normalize_result("found 21 occurrences of otherSymbol"),
        )

    def test_au_siblings_share_scenario_group(self):
        self.assertEqual(
            scenario_group("sess_au_050092_004-step_04"),
            scenario_group("sess_au_050092_009-step_01"),
        )
        self.assertNotEqual(
            scenario_group("sess_au_050092_004-step_04"),
            scenario_group("sess_au_050093_004-step_04"),
        )
        self.assertNotEqual(
            scenario_group("sess_sim_20260522_000001-step_01"),
            scenario_group("sess_sim_20260522_000002-step_01"),
        )

    def test_residual_cannot_flip_to_action_outside_main_top3(self):
        logits = np.full((1, 14), -5.0)
        logits[0, [0, 1, 4, 2]] = [3.0, 2.9, 2.8, 0.0]
        policy = np.asarray([[0.01, 0.01, 0.97, 0.01] + [0.0] * 10])
        backoff = np.asarray([[0.25] * 4 + [0.0] * 10])
        pred, accepted = apply_residual(
            logits,
            policy,
            backoff,
            np.asarray([100]),
            np.asarray([0.97]),
            10.0,
            5,
            0.75,
        )
        self.assertEqual(pred.tolist(), [0])
        self.assertEqual(accepted.tolist(), [False])


if __name__ == "__main__":
    unittest.main()
