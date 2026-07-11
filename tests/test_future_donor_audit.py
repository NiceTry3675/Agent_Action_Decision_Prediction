import unittest

from audit_future_donors import (
    build_donors,
    donor_seed,
    recover_next_user,
    scenario_group,
    turn_bin_value,
)
from privileged_event_modes import turn_bin


def make_row(session, step, label, prompt, users=(), lang="en", turn=None):
    return {
        "id": f"{session}-step_{step:02d}",
        "session": session,
        "scenario": scenario_group(f"{session}-step_{step:02d}"),
        "source": session.split("_")[1],
        "step": step,
        "label": label,
        "prompt": prompt,
        "turn_bin": turn_bin_value(step if turn is None else turn),
        "lang": lang,
        "witness_users": tuple(users),
        "malformed_history": False,
    }


class NextUserRecoveryTests(unittest.TestCase):
    def test_direct_next_row_prompt_is_recovered_and_last_step_is_terminal(self):
        rows = [
            make_row("sess_sim_d_1", 1, "read_file", "first prompt"),
            make_row("sess_sim_d_1", 2, "run_bash", "second prompt"),
        ]
        statuses, texts = recover_next_user(rows)
        self.assertEqual(statuses, ["recovered", "terminal"])
        self.assertEqual(texts[0], "second prompt")
        self.assertIsNone(texts[1])

    def test_witness_history_recovers_a_missing_next_row(self):
        # Row at step 2 is absent; row 3's history covers user turns 1..2.
        rows = [
            make_row("sess_sim_d_2", 1, "read_file", "u1"),
            make_row("sess_sim_d_2", 3, "run_bash", "u3", users=("u1", "u2")),
        ]
        statuses, texts = recover_next_user(rows)
        self.assertEqual(statuses[0], "recovered")
        self.assertEqual(texts[0], "u2")

    def test_witness_and_direct_disagreement_is_fail_closed(self):
        rows = [
            make_row("sess_sim_d_3", 1, "read_file", "u1"),
            make_row("sess_sim_d_3", 2, "run_bash", "u2-direct"),
            make_row("sess_sim_d_3", 3, "run_bash", "u3", users=("u1", "u2-witness")),
        ]
        statuses, _ = recover_next_user(rows)
        self.assertEqual(statuses[0], "conflict")

    def test_gap_without_witness_is_no_witness_not_terminal(self):
        rows = [
            make_row("sess_sim_d_4", 1, "read_file", "u1"),
            make_row("sess_sim_d_4", 3, "run_bash", "u3"),
        ]
        statuses, _ = recover_next_user(rows)
        self.assertEqual(statuses[0], "no_witness")

    def test_turn_bins_match_privileged_event_modes(self):
        for turn in (0, 1, 2, 3, 4, 5, 6, 7, 20, -1, None, True):
            sample = {"id": "x-step_01", "session_meta": {"turn_index": turn}}
            self.assertEqual(turn_bin_value(turn, fallback_step=1), turn_bin(sample))


class DonorSelectionTests(unittest.TestCase):
    def _pool(self):
        rows = []
        # Target row: AU scenario primary 100, variant 0.
        rows.append(make_row("sess_au_100_0", 1, "read_file", "t1"))
        rows.append(make_row("sess_au_100_0", 2, "read_file", "after-target"))
        # Same-session extra step, same-scenario sibling variant: both excluded.
        rows.append(make_row("sess_au_100_1", 1, "read_file", "sib1"))
        rows.append(make_row("sess_au_100_1", 2, "read_file", "after-sib"))
        # Valid donors: same label/source/turn_bin/lang, other scenarios.
        for primary in (200, 201, 202, 203, 204, 205):
            rows.append(make_row(f"sess_au_{primary}_0", 1, "read_file", f"d{primary}"))
            rows.append(
                make_row(f"sess_au_{primary}_0", 2, "read_file", f"after-{primary}")
            )
        # Wrong label / wrong lang rows must never be chosen.
        rows.append(make_row("sess_au_300_0", 1, "run_bash", "x1"))
        rows.append(make_row("sess_au_300_0", 2, "run_bash", "x2"))
        rows.append(make_row("sess_au_301_0", 1, "read_file", "k1", lang="ko"))
        rows.append(make_row("sess_au_301_0", 2, "read_file", "k2", lang="ko"))
        return rows

    def test_exclusions_key_match_and_determinism(self):
        rows = self._pool()
        statuses, texts = recover_next_user(rows)
        donors_a = build_donors(rows, statuses, texts, k=4)
        donors_b = build_donors(rows, statuses, texts, k=4)
        self.assertEqual(donors_a, donors_b)
        target = 0
        chosen = donors_a[target]
        self.assertEqual(len(chosen), 4)
        for donor_index in chosen:
            donor = rows[donor_index]
            self.assertNotEqual(donor["session"], rows[target]["session"])
            self.assertNotEqual(donor["scenario"], rows[target]["scenario"])
            self.assertEqual(donor["label"], rows[target]["label"])
            self.assertEqual(donor["source"], rows[target]["source"])
            self.assertEqual(donor["turn_bin"], rows[target]["turn_bin"])
            self.assertEqual(donor["lang"], rows[target]["lang"])
            self.assertNotEqual(texts[donor_index], texts[target])
            self.assertEqual(statuses[donor_index], "recovered")

    def test_identical_next_user_text_is_excluded(self):
        rows = self._pool()
        # Give one otherwise-valid donor the same next-user text as the target.
        rows[5]["prompt"] = "after-target"  # step-2 row of sess_au_200_0
        statuses, texts = recover_next_user(rows)
        donors = build_donors(rows, statuses, texts, k=4)
        donor_sessions = {rows[i]["session"] for i in donors[0]}
        self.assertNotIn("sess_au_200_0", donor_sessions)

    def test_insufficient_pool_reports_short_donor_list_without_backoff(self):
        rows = [
            make_row("sess_au_100_0", 1, "read_file", "t1"),
            make_row("sess_au_100_0", 2, "read_file", "t2"),
            make_row("sess_au_400_0", 1, "read_file", "d1"),
            make_row("sess_au_400_0", 2, "read_file", "d2"),
        ]
        statuses, texts = recover_next_user(rows)
        donors = build_donors(rows, statuses, texts, k=4)
        self.assertEqual(len(donors[0]), 1)

    def test_seed_rule_is_id_stable(self):
        self.assertEqual(donor_seed("a-step_01"), donor_seed("a-step_01"))
        self.assertNotEqual(donor_seed("a-step_01"), donor_seed("a-step_02"))


if __name__ == "__main__":
    unittest.main()
