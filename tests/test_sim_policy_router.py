import numpy as np

from probe_sim_policy_router import inner_utility_tables
from sim_policy_router import (
    ExpertConfig,
    SimPolicyTables,
    SimRow,
    candidate_examples,
    label_probabilities_to_candidate_states,
    macro_f1,
    policy_row_examples,
    routed_predictions,
    state_utility_table,
)


def row(name, y, turn, actions):
    return SimRow(
        sample_id=name,
        session=name,
        y=y,
        turn=turn,
        actions=tuple(actions),
        events=tuple(f"{action}|none|ok" for action in actions),
    )


def test_table_uses_suffix_and_abstains_without_matched_depth():
    rows = [
        row("a", 2, 2, ["read_file"]),
        row("b", 2, 3, ["read_file"]),
        row("c", 1, 3, ["grep_search"]),
        row("q", 0, 3, ["read_file"]),
        row("z", 0, 3, ["respond_only"]),
    ]
    config = ExpertConfig("suffix", "action", "turn_bucket", 1, 2, 1.0)
    table = SimPolicyTables(rows, [0, 1, 2], experts=[config])
    output = table.query([3, 4])["suffix"]
    assert output["pred"][0] == 2
    assert output["depth"][0] == 1
    assert output["support"][0] == 2
    assert output["depth"][1] == 0
    assert output["support"][1] == 0


def test_candidate_examples_offer_unique_non_main_alternatives_only():
    rows = [row("q", 2, 3, ["read_file"])]
    logits = np.asarray([[3.0, 2.0, 1.0, 0.0] + [-2.0] * 10])
    experts = [
        ExpertConfig("one", "action", "global", 1, 1),
        ExpertConfig("two", "action", "global", 1, 1),
    ]
    output = {
        "one": {
            "pred": np.asarray([2]),
            "prob": np.asarray([[0.1, 0.1, 0.7, 0.1]]),
            "support": np.asarray([10]),
            "depth": np.asarray([1]),
            "entropy": np.asarray([0.5]),
            "gap": np.asarray([0.6]),
        },
        "two": {
            "pred": np.asarray([2]),
            "prob": np.asarray([[0.1, 0.1, 0.7, 0.1]]),
            "support": np.asarray([8]),
            "depth": np.asarray([1]),
            "entropy": np.asarray([0.6]),
            "gap": np.asarray([0.6]),
        },
    }
    examples = candidate_examples(rows, [0], logits, output, experts=experts)
    assert examples["alternative"].tolist() == [2]
    assert examples["target"].tolist() == [1]
    assert examples["features"][0]["vote_fraction"] == 1.0


def test_router_identity_and_positive_expected_utility():
    y = np.asarray([0, 1, 2, 3])
    main = np.asarray([0, 0, 2, 2])
    utility = state_utility_table(y, main)
    examples = {
        "row_local": np.asarray([1, 3]),
        "alternative": np.asarray([1, 3]),
    }
    probability = np.asarray([
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ])
    prediction, selected, _ = routed_predictions(
        main, examples, probability, utility, threshold=0.0
    )
    assert prediction.tolist() == [0, 1, 2, 2]
    assert selected.tolist() == [0]
    assert macro_f1(y, prediction) > macro_f1(y, main)


def test_five_way_policy_posterior_projects_to_minimal_switch_states():
    rows = [row("q", 2, 3, ["read_file"])]
    logits = np.asarray([[3.0, 2.0, 1.0, 0.0] + [-2.0] * 10])
    experts = [ExpertConfig("one", "action", "global", 1, 1)]
    output = {
        "one": {
            "pred": np.asarray([2]),
            "prob": np.asarray([[0.1, 0.1, 0.7, 0.1]]),
            "support": np.asarray([10]),
            "depth": np.asarray([1]),
            "entropy": np.asarray([0.5]),
            "gap": np.asarray([0.6]),
        }
    }
    examples = policy_row_examples(rows, [0], logits, output, experts=experts)
    assert examples["target"].tolist() == [2]
    projected, state = label_probabilities_to_candidate_states(
        examples, np.asarray([[0.6, 0.1, 0.2, 0.05, 0.05]])
    )
    assert projected["row_local"].tolist() == [0]
    # main=0, alternative=2: neutral=0.2, rescue=0.2, harm=0.6.
    assert np.allclose(state[0], [0.2, 0.2, 0.6])


def test_inner_utility_excludes_its_validation_labels():
    global_positions = np.asarray([1, 3, 5, 7])
    metric_indices = np.arange(8)
    y = np.asarray([0, 0, 1, 1, 2, 2, 3, 3])
    main = np.asarray([0, 1, 1, 0, 2, 3, 3, 2])
    validation_parts = [np.asarray([0, 2]), np.asarray([1, 3])]
    utilities = inner_utility_tables(
        validation_parts, metric_indices, global_positions, y, main
    )

    first_heldout = global_positions[validation_parts[0]]
    first_fit = metric_indices[~np.isin(metric_indices, first_heldout)]
    assert np.allclose(utilities[0], state_utility_table(y[first_fit], main[first_fit]))

    # Changing only the first partition's held-out labels cannot affect its utility.
    changed_y = y.copy()
    changed_y[first_heldout] = np.asarray([3, 0])
    changed = inner_utility_tables(
        validation_parts, metric_indices, global_positions, changed_y, main
    )
    assert np.allclose(utilities[0], changed[0])


def test_main_context_label_features_exclude_table_scale_fields():
    rows = [row("q", 2, 3, ["read_file", "grep_search"])]
    logits = np.asarray([[3.0, 2.0, 1.0, 0.0] + [-2.0] * 10])
    experts = [ExpertConfig("one", "action", "global", 1, 1)]
    output = {
        "one": {
            "pred": np.asarray([2]),
            "prob": np.asarray([[0.1, 0.1, 0.7, 0.1]]),
            "support": np.asarray([10]),
            "depth": np.asarray([1]),
            "entropy": np.asarray([0.5]),
            "gap": np.asarray([0.6]),
        }
    }
    examples = policy_row_examples(
        rows, [0], logits, output, experts=experts, feature_family="main_context"
    )
    keys = set(examples["features"][0])
    assert "lag2=read_file" in keys
    assert "main_w4_p0" in keys
    assert not any(key.startswith("one:") for key in keys)
    assert not any(key.startswith("vote_p") for key in keys)
    assert "matched_experts" not in keys
