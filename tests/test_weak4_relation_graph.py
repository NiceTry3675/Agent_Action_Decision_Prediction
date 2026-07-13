import copy
import unittest

import numpy as np

from weak4_relation_graph import (
    extract_coarse_features,
    extract_relation_features,
    glob_matches,
    normalize_path,
    path_relation,
    scenario_group,
    symbol_relation,
    vectorize_feature_dicts,
)


def sample_with(history, prompt="open ./src/pkg/foo.py"):
    return {
        "id": "sess_sim_20260522_000001-step_06",
        "current_prompt": prompt,
        "history": history,
        "session_meta": {
            "turn_index": 6,
            "workspace": {
                "open_files": ["src/pkg/foo.py"],
                "git_dirty": False,
                "last_ci_status": "passed",
            },
        },
    }


class Weak4RelationGraphTests(unittest.TestCase):
    def test_scenario_group_collapses_only_au_siblings(self):
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

    def test_path_normalization_and_relations(self):
        self.assertEqual(normalize_path("./src/a/../foo.py"), "src/foo.py")
        self.assertEqual(normalize_path(r"src\\pkg\\foo.py"), "src/pkg/foo.py")
        self.assertEqual(path_relation("src/foo.py", "src/foo.py"), "exact")
        self.assertEqual(path_relation("./src/foo.py", r"src\\foo.py"), "normalized")
        self.assertEqual(path_relation("SRC/Foo.py", "src/foo.py"), "casefold")
        self.assertEqual(path_relation("other/foo.py", "src/foo.py"), "basename")
        self.assertEqual(path_relation("foo.py", "foo.ts"), "stem")
        self.assertEqual(path_relation("src", "src/pkg/foo.py"), "ancestor")
        self.assertEqual(path_relation("src/pkg/foo.py", "src"), "descendant")
        self.assertEqual(path_relation("a.py", "b.py"), "extension")
        self.assertEqual(path_relation("", "b.py"), "unknown")

    def test_glob_and_symbol_relations(self):
        self.assertTrue(glob_matches("**/*.py", "src/pkg/foo.py"))
        self.assertTrue(glob_matches("*.py", "src/pkg/foo.py"))
        self.assertFalse(glob_matches("tests/**/*.py", "src/pkg/foo.py"))
        self.assertEqual(symbol_relation("pkg.Foo()", "Foo"), "qualified_tail")
        self.assertEqual(symbol_relation("Foo()", "Foo"), "normalized")
        self.assertEqual(symbol_relation("foo", "FOO"), "casefold")
        self.assertEqual(symbol_relation("", "Foo"), "unknown")

    def test_count_only_glob_membership_stays_unknown(self):
        sample = sample_with(
            [
                {"role": "user", "content": "find python files"},
                {
                    "role": "assistant_action",
                    "name": "glob_pattern",
                    "args": {"pattern": "src/**/*.py"},
                    "result_summary": "2 files matched 'src/**/*.py'",
                },
            ]
        )
        features, metadata = extract_relation_features(sample, pair_id=1, main_pred=3)
        glob_state = metadata["tools"]["glob_pattern"]
        self.assertEqual(glob_state["completeness"], "count_only")
        self.assertEqual(glob_state["candidate_paths"], [])
        self.assertEqual(metadata["relations"]["glob_pattern"]["membership"], "unknown")
        self.assertEqual(metadata["relations"]["glob_pattern"]["tool_glob_vs_target"], "yes")
        self.assertEqual(metadata["transition"]["candidate_selected"], "unknown")
        self.assertEqual(metadata["transition"]["file_set_to_single_file"], "unknown")
        self.assertEqual(features["rel.transition.file_set_to_single_file=unknown"], 1.0)
        self.assertEqual(features["rel.main.completeness=count_only"], 1.0)

    def test_exact_read_candidate_and_full_history_age(self):
        history = [
            {"role": "user", "content": "open it"},
            {
                "role": "assistant_action",
                "name": "read_file",
                "args": {"path": "src/pkg/foo.py"},
                "result_summary": "ok; read src/pkg/foo.py (120L)",
            },
        ]
        for index in range(5):
            history.extend(
                [
                    {"role": "user", "content": f"step {index}"},
                    {
                        "role": "assistant_action",
                        "name": "run_bash",
                        "args": {"cmd": "true"},
                        "result_summary": "ok",
                    },
                ]
            )
        sample = sample_with(history)
        features, metadata = extract_relation_features(sample, pair_id=1, main_pred=3)
        read_state = metadata["tools"]["read_file"]
        self.assertTrue(read_state["present"])
        self.assertEqual(read_state["completeness"], "exact")
        self.assertEqual(read_state["provenance"], "args")
        self.assertEqual(read_state["candidate_paths"], ["src/pkg/foo.py"])
        self.assertEqual(metadata["relations"]["read_file"]["membership"], "yes")
        self.assertEqual(metadata["relations"]["read_file"]["target_vs_candidate"], "exact")
        self.assertEqual(metadata["completeness_histogram"]["exact"], 1)
        self.assertEqual(features["rel.alt.present"], 1.0)
        self.assertGreater(features["rel.alt.age_log1p"], 0.0)

    def test_returned_list_children_are_not_removed_as_scope_echoes(self):
        sample = sample_with(
            [
                {"role": "user", "content": "list src"},
                {
                    "role": "assistant_action",
                    "name": "list_directory",
                    "args": {"path": "src"},
                    "result_summary": "2 entries: src/foo.py, src/bar.py",
                },
            ],
            prompt="open src/foo.py",
        )
        _, metadata = extract_relation_features(sample, pair_id=0, main_pred=2)
        state = metadata["tools"]["list_directory"]
        self.assertIn("src/foo.py", state["candidate_paths"])
        self.assertIn("src/bar.py", state["candidate_paths"])
        self.assertEqual(metadata["relations"]["list_directory"]["membership"], "yes")

    def test_latest_state_does_not_borrow_stale_candidate_completeness(self):
        sample = sample_with(
            [
                {"role": "user", "content": "list src"},
                {
                    "role": "assistant_action",
                    "name": "list_directory",
                    "args": {"path": "src"},
                    "result_summary": "2 entries: src/foo.py, src/bar.py",
                },
                {"role": "user", "content": "list tests"},
                {
                    "role": "assistant_action",
                    "name": "list_directory",
                    "args": {"path": "tests"},
                    "result_summary": "8 entries (7 files, 1 dirs)",
                },
            ],
            prompt="open src/foo.py",
        )
        features, metadata = extract_relation_features(sample, pair_id=0, main_pred=2)
        state = metadata["tools"]["list_directory"]
        relation = metadata["relations"]["list_directory"]
        self.assertEqual(state["candidate_paths"], [])
        self.assertEqual(state["completeness"], "count_only")
        self.assertEqual(relation["membership"], "unknown")
        self.assertEqual(relation["historical_membership"], "yes")
        self.assertGreater(relation["historical_match_age"], 0)
        self.assertEqual(features["rel.main.membership=unknown"], 1.0)
        self.assertEqual(features["rel.main.historical_membership=yes"], 1.0)

    def test_features_are_label_independent_numeric_and_deterministic(self):
        sample = sample_with(
            [
                {"role": "user", "content": "list src"},
                {
                    "role": "assistant_action",
                    "name": "list_directory",
                    "args": {"path": "src"},
                    "result_summary": "3 entries (2 files, 1 dirs)",
                },
            ],
            prompt="show src/pkg/foo.py",
        )
        first, first_meta = extract_relation_features(sample, pair_id=0, main_pred=2)
        contaminated = copy.deepcopy(sample)
        contaminated["y_true"] = "read_file"
        contaminated["action"] = "read_file"
        second, second_meta = extract_relation_features(contaminated, pair_id=0, main_pred=2)
        self.assertEqual(first, second)
        self.assertEqual(first_meta, second_meta)
        self.assertTrue(all(isinstance(value, float) for value in first.values()))
        self.assertFalse(any("foo.py" in name for name in first))
        matrix, names = vectorize_feature_dicts([first, second])
        self.assertEqual(matrix.shape, (2, len(names)))
        np.testing.assert_array_equal(matrix[0], matrix[1])

    def test_coarse_control_has_separate_stable_schema(self):
        sample = sample_with([])
        first, first_meta = extract_coarse_features(sample, pair_id=1, main_pred=3)
        second, second_meta = extract_coarse_features(sample, pair_id=1, main_pred=3)
        self.assertEqual(first, second)
        self.assertEqual(first_meta, second_meta)
        self.assertTrue(all(name.startswith("coarse.") for name in first))
        self.assertEqual(first_meta["schema"], "weak4-coarse-control-v1")


if __name__ == "__main__":
    unittest.main()
