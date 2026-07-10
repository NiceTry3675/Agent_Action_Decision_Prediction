import json
import unittest

from privileged_event_modes import (
    UNKNOWN_MODE_ID,
    SelectiveModeTaxonomy,
    edit_file_signature,
    event_mode_signature,
    fit_selective_taxonomy,
    read_file_signature,
    reconstruct_full_events,
    run_bash_signature,
    transform_mode_targets,
    turn_bin,
)


def action_event(name, args, result="ok"):
    return {
        "role": "assistant_action",
        "name": name,
        "args": args,
        "result_summary": result,
    }


def source_row(session, step, prompt, turn=None):
    return {
        "id": f"{session}-step_{step:02d}",
        "current_prompt": prompt,
        "history": [],
        "session_meta": {"turn_index": step if turn is None else turn},
    }


def witness_row(session, step, pairs, prompt="next"):
    history = []
    for user_prompt, event in pairs:
        history.extend(({"role": "user", "content": user_prompt}, event))
    return {
        "id": f"{session}-step_{step:02d}",
        "current_prompt": prompt,
        "history": history,
        "session_meta": {"turn_index": step},
    }


def paired_session(session, event, prompt="target"):
    return [
        source_row(session, 1, prompt),
        witness_row(session, 2, [(prompt, event)]),
    ]


class FullEventReconstructionTests(unittest.TestCase):
    def test_strict_position_and_prompt_recovers_full_event_and_marks_terminal(self):
        event = action_event(
            "read_file", {"path": "src/service.py"}, "ok; read 20 lines"
        )
        samples = paired_session("session-a", event, "show service")
        labels = ["read_file", "respond_only"]

        result = reconstruct_full_events(samples, labels)

        self.assertEqual(result.statuses, ("recovered", "terminal"))
        self.assertEqual(result.events[0]["args"], {"path": "src/service.py"})
        self.assertEqual(result.events[0]["result_summary"], "ok; read 20 lines")
        self.assertEqual(result.witness_counts, (1, 0))
        self.assertEqual(result.metadata["name_agreement_rate"], 1.0)

    def test_prompt_mismatch_is_not_accepted(self):
        event = action_event("read_file", {"path": "src/service.py"})
        samples = [
            source_row("session-a", 1, "show service"),
            witness_row("session-a", 2, [("different prompt", event)]),
        ]

        result = reconstruct_full_events(samples, ["read_file", "respond_only"])

        self.assertEqual(result.statuses[0], "prompt_mismatch")
        self.assertIsNone(result.events[0])
        self.assertEqual(result.metadata["prompt_mismatches"], 1)

    def test_conflicting_full_event_witnesses_are_masked(self):
        first = action_event("edit_file", {"path": "src/a.py"}, "edit one")
        conflicting = action_event("edit_file", {"path": "src/a.py"}, "edit two")
        step_two_event = action_event("read_file", {"path": "src/b.py"}, "read")
        samples = [
            source_row("session-a", 1, "change a"),
            witness_row("session-a", 2, [("change a", first)], prompt="show b"),
            witness_row(
                "session-a",
                3,
                [("change a", conflicting), ("show b", step_two_event)],
            ),
        ]
        labels = ["edit_file", "read_file", "respond_only"]

        result = reconstruct_full_events(samples, labels)

        self.assertEqual(result.statuses[0], "conflict")
        self.assertIsNone(result.events[0])
        self.assertEqual(result.witness_counts[0], 2)
        self.assertEqual(result.metadata["conflicting_rows"], 1)


class SignatureTests(unittest.TestCase):
    def test_turn_bins_match_the_current_v1_contract(self):
        expected = {
            1: "start",
            2: "early",
            3: "mid",
            4: "mid",
            5: "late",
            6: "late",
            7: "long",
        }
        for turn, name in expected.items():
            self.assertEqual(turn_bin({"session_meta": {"turn_index": turn}}), name)

    def test_signatures_use_args_intention_not_result_or_raw_identity(self):
        self.assertEqual(
            read_file_signature({"path": "src/alpha.py"}),
            read_file_signature({"path": "lib/private_name.rs"}),
        )
        self.assertEqual(read_file_signature({"path": "tests/test_api.py"}), "test_code")
        self.assertEqual(
            edit_file_signature({"path": "secret/a.py", "target_symbol": "Hidden"}),
            "path_target_symbol",
        )
        self.assertEqual(run_bash_signature({"cmd": "npm run build -- --prod"}), "build")
        self.assertEqual(run_bash_signature({"cmd": "pytest -q tests/unit"}), "test")
        left = action_event("run_bash", {"cmd": "cargo clippy"}, "pass")
        right = action_event("run_bash", {"cmd": "cargo clippy"}, "fail with 99 lines")
        self.assertEqual(event_mode_signature(left), event_mode_signature(right))

    def test_result_and_count_fields_do_not_define_a_mode(self):
        zero = {
            "name": "read_file",
            "args": {"path": "src/a.py", "line_count": 0},
            "result_summary": "read zero lines",
            "count_bucket": "zero",
        }
        many = {
            "name": "read_file",
            "args": {"path": "lib/b.rs", "line_count": 9999},
            "result_summary": "read many lines and failed",
            "count_bucket": "many",
        }
        self.assertEqual(event_mode_signature(zero), "source_code")
        self.assertEqual(event_mode_signature(zero), event_mode_signature(many))


class TaxonomyTests(unittest.TestCase):
    def _build_edit_samples(self, modes):
        samples = []
        labels = []
        source_indices = []
        for number, mode in enumerate(modes):
            args = {"path": f"src/raw_identity_{number}.py"}
            if mode == "symbol":
                args["target_symbol"] = f"Symbol{number}"
            pair = paired_session(
                f"edit-session-{number}",
                action_event("edit_file", args, result=f"raw result {number}"),
                prompt=f"edit prompt {number}",
            )
            source_indices.append(len(samples))
            samples.extend(pair)
            labels.extend(("edit_file", "respond_only"))
        return samples, labels, source_indices

    def test_taxonomy_fit_is_train_index_only_and_validation_mode_is_unseen(self):
        samples, labels, source_indices = self._build_edit_samples(
            ["plain", "plain", "symbol"]
        )
        reconstruction = reconstruct_full_events(samples, labels)

        taxonomy = fit_selective_taxonomy(
            samples,
            reconstruction,
            [0, 1, 2, 3],
            labels,
            selected_actions=("edit_file",),
            min_support=1,
        )

        self.assertFalse(taxonomy.has_branch("edit_file"))
        self.assertEqual(taxonomy.support_by_action["edit_file"], {"path_only": 2})
        serialized = json.dumps(taxonomy.to_dict(), sort_keys=True)
        self.assertNotIn("raw_identity", serialized)
        self.assertNotIn("raw result", serialized)
        self.assertNotIn("result_summary", serialized)
        transformed = transform_mode_targets(
            samples,
            reconstruction,
            taxonomy,
            labels,
            indices=[source_indices[2]],
        )
        self.assertEqual(transformed.local_mode_ids, (UNKNOWN_MODE_ID,))
        self.assertEqual(
            transformed.metadata["masked_reason_counts"], {"no_active_branch": 1}
        )

    def test_train_target_cannot_consume_a_validation_witness(self):
        samples, labels, source_indices = self._build_edit_samples(["symbol"])
        reconstruction = reconstruct_full_events(samples, labels)

        taxonomy = fit_selective_taxonomy(
            samples,
            reconstruction,
            [source_indices[0]],
            labels,
            selected_actions=("edit_file",),
            min_support=1,
        )

        self.assertEqual(taxonomy.support_by_action["edit_file"], {})
        self.assertEqual(
            taxonomy.fit_metadata["cross_split_witness_rows_masked"], 1
        )

    def test_support_prunes_rare_mode_instead_of_merging_to_other(self):
        samples, labels, source_indices = self._build_edit_samples(
            ["plain", "plain", "symbol", "symbol"]
        )
        # Add a third read-file signature that will be below support.  It must be
        # masked, not folded into a catch-all mode.
        extra = []
        extra_labels = []
        read_indices = []
        for number, path in enumerate(
            ["src/a.py", "lib/b.rs", "tests/test_a.py", "tests/test_b.py", "README.md"]
        ):
            read_indices.append(len(samples) + len(extra))
            extra.extend(
                paired_session(
                    f"read-session-{number}",
                    action_event("read_file", {"path": path}),
                    prompt=f"read {number}",
                )
            )
            extra_labels.extend(("read_file", "respond_only"))
        samples.extend(extra)
        labels.extend(extra_labels)
        reconstruction = reconstruct_full_events(samples, labels)

        taxonomy = fit_selective_taxonomy(
            samples,
            reconstruction,
            range(len(samples)),
            labels,
            selected_actions=("edit_file", "read_file"),
            min_support=2,
        )

        self.assertTrue(taxonomy.has_branch("read_file"))
        self.assertEqual(
            set(taxonomy.modes_by_action["read_file"]), {"source_code", "test_code"}
        )
        read_targets = transform_mode_targets(
            samples,
            reconstruction,
            taxonomy,
            labels,
            indices=read_indices,
        )
        self.assertEqual(read_targets.mode_mask, (True, True, True, True, False))
        self.assertEqual(read_targets.local_mode_ids[-1], UNKNOWN_MODE_ID)
        self.assertEqual(
            read_targets.metadata["masked_reason_counts"],
            {"pruned_or_unseen_signature": 1},
        )

    def test_smoothed_priors_use_supported_train_counts_and_round_trip(self):
        samples, labels, source_indices = self._build_edit_samples(
            ["plain", "plain", "plain", "symbol"]
        )
        reconstruction = reconstruct_full_events(samples, labels)
        taxonomy = fit_selective_taxonomy(
            samples,
            reconstruction,
            range(len(samples)),
            labels,
            selected_actions=("edit_file",),
            min_support=1,
            prior_smoothing=1.0,
        )

        self.assertTrue(taxonomy.has_branch("edit_file"))
        priors = taxonomy.prior_map("edit_file")
        self.assertAlmostEqual(priors["path_only"], 4.0 / 6.0)
        self.assertAlmostEqual(priors["path_target_symbol"], 2.0 / 6.0)
        restored = SelectiveModeTaxonomy.from_dict(taxonomy.to_dict())
        self.assertEqual(restored.modes_by_action, taxonomy.modes_by_action)
        self.assertEqual(restored.priors_by_action, taxonomy.priors_by_action)

        targets = transform_mode_targets(
            samples,
            reconstruction,
            restored,
            labels,
            indices=source_indices,
        )
        self.assertEqual(sum(targets.mode_mask), 4)
        self.assertEqual(targets.metadata["recovered_rows"], 4)
        self.assertIn("start", targets.metadata["by_turn_bin"])


if __name__ == "__main__":
    unittest.main()
