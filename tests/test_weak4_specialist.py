import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

import torch
from safetensors.torch import save_file

from build_weak4_pack import build_pack
from package_submission import build_zip, stage
from script import (
    ALL_CLASSES,
    merge_lora_inplace,
    select_weak4_routes,
    serialize_transformer_sample_current,
    serialize_transformer_sample_current_parts,
    serialize_transformer_sample_weak_nav_v1,
    weak4_family_locked_predictions,
    weak_nav_line,
    weak_nav_path_values,
)
from train_transformer import (
    assert_validation_anchor,
    class_weights,
    classification_loss_values,
    filter_train_indices,
    specialist_optimizer_groups,
)
from tune_weak4_router import join_payloads, session_hash_partition


class TinyModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.layer = torch.nn.Module()
        self.layer.q_proj = torch.nn.Linear(3, 4, bias=False)
        self.score = torch.nn.Linear(3, len(ALL_CLASSES), bias=False)


class SerializerTests(unittest.TestCase):
    def test_multiline_prompt_nav_is_inserted_after_current_part(self):
        sample = {
            "id": "s-step_02",
            "current_prompt": "line one\nnav: prompt text\nline three",
            "session_meta": {"turn_index": 2},
            "history": [
                {
                    "role": "assistant_action",
                    "name": "grep_search",
                    "args": {"pattern": "Thing"},
                    "result_summary": "0 matches",
                },
                {
                    "role": "assistant_action",
                    "name": "read_file",
                    "args": {"path": "src/a.py"},
                    "result_summary": "ok; read src/a.py (20L)",
                },
            ],
        }
        current_parts = serialize_transformer_sample_current_parts(sample)
        expected_parts = current_parts[:1] + [weak_nav_line(sample)] + current_parts[1:]
        self.assertEqual(serialize_transformer_sample_current(sample), "\n".join(current_parts))
        self.assertEqual(serialize_transformer_sample_weak_nav_v1(sample), "\n".join(expected_parts))
        self.assertEqual(
            weak_nav_line(sample),
            "nav: prev=grep_search:zero last=read_file:ok repeat=1 "
            "turn=02/early arg=exact_path",
        )

    def test_repeat_distinguishes_no_history_and_three_actions(self):
        empty = {"current_prompt": "x", "history": [], "session_meta": {}}
        self.assertIn("repeat=0", weak_nav_line(empty))
        repeated = {
            "current_prompt": "x",
            "session_meta": {"turn_index": 5},
            "history": [
                {"role": "assistant_action", "name": "grep_search"},
                {"role": "assistant_action", "name": "grep_search"},
                {"role": "assistant_action", "name": "grep_search"},
            ],
        }
        self.assertIn("repeat=3+", weak_nav_line(repeated))

    def test_paths_serializer_keeps_path_like_target(self):
        sample = {
            "history": [
                {
                    "role": "assistant_action",
                    "name": "run_tests",
                    "args": {"target": "src/test/UserTest.java"},
                }
            ]
        }
        self.assertEqual(weak_nav_path_values(sample), ["src/test/UserTest.java"])


class RoutingTests(unittest.TestCase):
    def test_route_cap_uses_weak_margin_and_index_tie_break(self):
        logits = torch.tensor(
            [
                [2.0, 2.0, 0.0, 0.0, -1.0],
                [3.0, 3.0, 0.0, 0.0, -1.0],
                [5.0, 0.0, 0.0, 0.0, -1.0],
                [1.0, 1.0, 1.0, 1.0, 6.0],
            ]
        )
        routed = select_weak4_routes(logits, [0, 1, 2, 3], 0.5)
        self.assertEqual(routed.tolist(), [0, 1])

    def test_family_lock_and_alpha_zero_identity(self):
        main = torch.tensor(
            [
                [3.0, 2.0, 1.0, 0.0, -1.0],
                [2.0, 1.0, 0.0, 0.0, -1.0],
                [0.0, 0.0, 0.0, 0.0, 5.0],
            ]
        )
        routed = torch.tensor([1])
        specialist = torch.tensor([[0.0, 0.0, 8.0, 0.0, 99.0]])
        alpha0 = weak4_family_locked_predictions(main, specialist, routed, [0, 1, 2, 3], 0.0)
        alpha1 = weak4_family_locked_predictions(main, specialist, routed, [0, 1, 2, 3], 1.0)
        self.assertTrue(torch.equal(alpha0, main.argmax(1)))
        self.assertEqual(alpha1.tolist(), [0, 2, 4])

        near_tie = torch.tensor([[0.0, 2e-8, -1.0, -2.0, -3.0]])
        near_tie_spec = torch.zeros(1, 5)
        exact = weak4_family_locked_predictions(
            near_tie, near_tie_spec, torch.tensor([0]), [0, 1, 2, 3], 0.0
        )
        self.assertTrue(torch.equal(exact, near_tie.argmax(1)))


class TrainingContractTests(unittest.TestCase):
    def test_conditional_loss_has_zero_gradient_outside_weak4(self):
        logits = torch.randn(4, len(ALL_CLASSES), requires_grad=True)
        labels = torch.tensor([0, 1, 2, 3])
        weights = torch.ones(4)
        loss = classification_loss_values(
            logits,
            labels,
            weights,
            label_smoothing=0.02,
            loss_name="focal",
            focal_gamma=2.0,
            class_ids=torch.tensor([0, 1, 2, 3]),
        ).mean()
        loss.backward()
        self.assertEqual(int(torch.count_nonzero(logits.grad[:, 4:])), 0)
        self.assertGreater(int(torch.count_nonzero(logits.grad[:, :4])), 0)

    def test_absent_class_weights_are_zero(self):
        weights = class_weights([0, 0, 1, 2, 3], torch.device("cpu"), 0.5)
        self.assertTrue(torch.equal(weights[4:], torch.zeros(len(ALL_CLASSES) - 4)))
        self.assertAlmostEqual(float(weights[:4].mean()), 1.0, places=6)

    def test_score_nonweak_rows_are_not_changed_by_adamw_decay(self):
        model = TinyModel()
        groups = specialist_optimizer_groups(model, 0.1, "weak4")
        optimizer = torch.optim.AdamW(groups, lr=0.01, weight_decay=0.1)
        before = model.score.weight.detach().clone()
        inputs = torch.randn(4, 3)
        labels = torch.tensor([0, 1, 2, 3])
        logits = model.score(inputs)
        loss = classification_loss_values(
            logits,
            labels,
            torch.ones(4),
            0.02,
            "focal",
            2.0,
            class_ids=torch.tensor([0, 1, 2, 3]),
        ).mean()
        loss.backward()
        optimizer.step()
        self.assertTrue(torch.equal(model.score.weight[4:], before[4:]))
        self.assertFalse(torch.equal(model.score.weight[:4], before[:4]))

    def test_filter_and_validation_anchor_contract(self):
        samples = [{"id": f"s{i}"} for i in range(6)]
        labels = [0, 1, 2, 3, 4, 5]
        self.assertEqual(filter_train_indices(list(range(6)), labels, "weak4"), [0, 1, 2, 3])
        with tempfile.TemporaryDirectory() as td:
            anchor = Path(td) / "anchor.pt"
            torch.save(
                {
                    "classes": ALL_CLASSES,
                    "split": "session",
                    "seed": 42,
                    "ids": ["s4", "s5"],
                    "y_true": [4, 5],
                },
                anchor,
            )
            args = SimpleNamespace(assert_val_ids=str(anchor), split="session", seed=42)
            assert_validation_anchor(samples, labels, [0, 1, 2, 3], [4, 5], args)


class AdapterTests(unittest.TestCase):
    def test_manual_lora_merge_and_score_replacement(self):
        torch.manual_seed(3)
        model = TinyModel()
        before = model.layer.q_proj.weight.detach().clone()
        a = torch.randn(2, 3)
        b = torch.randn(4, 2)
        score = torch.randn_like(model.score.weight)
        with tempfile.TemporaryDirectory() as td:
            adapter = Path(td)
            (adapter / "adapter_config.json").write_text(
                json.dumps(
                    {
                        "peft_type": "LORA",
                        "r": 2,
                        "lora_alpha": 4,
                        "target_modules": ["q_proj"],
                        "bias": "none",
                        "fan_in_fan_out": False,
                        "use_rslora": False,
                        "use_dora": False,
                    }
                ),
                encoding="utf-8",
            )
            save_file(
                {
                    "base_model.model.layer.q_proj.lora_A.weight": a,
                    "base_model.model.layer.q_proj.lora_B.weight": b,
                    "base_model.model.score.weight": score,
                },
                adapter / "adapter_model.safetensors",
            )
            merge_lora_inplace(model, adapter)
            torch.testing.assert_close(model.layer.q_proj.weight, before + 2.0 * (b @ a))
            torch.testing.assert_close(model.score.weight, score)
            with self.assertRaises(RuntimeError):
                merge_lora_inplace(model, adapter)


class PayloadAndPackTests(unittest.TestCase):
    def test_payload_join_is_by_id(self):
        main = {
            "logits": torch.zeros(2, len(ALL_CLASSES)),
            "y_true": [0, 1],
            "ids": ["a-step_01", "b-step_01"],
            "classes": ALL_CLASSES,
            "split": "session",
            "seed": 42,
        }
        specialist = {
            **main,
            "logits": torch.stack(
                [
                    torch.arange(len(ALL_CLASSES), dtype=torch.float32) + 20.0,
                    torch.arange(len(ALL_CLASSES), dtype=torch.float32) + 10.0,
                ]
            ),
            "y_true": [1, 0],
            "ids": ["b-step_01", "a-step_01"],
        }
        _, joined, ids, y = join_payloads(main, specialist, "main", "specialist")
        self.assertEqual(ids, ["a-step_01", "b-step_01"])
        self.assertEqual(y, [0, 1])
        self.assertEqual(joined[0].tolist(), torch.arange(len(ALL_CLASSES), dtype=torch.float32).add(10.0).tolist())
        self.assertEqual(joined[1].tolist(), torch.arange(len(ALL_CLASSES), dtype=torch.float32).add(20.0).tolist())
        tune, confirm = session_hash_partition(
            [f"session{i}-step_01" for i in range(20)]
        )
        self.assertTrue(tune and confirm)

    def test_pack_builder_and_submission_stage_copy_lora(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "base"
            (base / "hf_model").mkdir(parents=True)
            (base / "hf_model" / "model.int8.safetensors").write_bytes(b"int8")
            (base / "hf_model" / "model.int8.safetensors.meta.json").write_text(
                json.dumps({"format": "int8-rowwise-v1"})
            )
            base_config = {
                "hidden_size": 8,
                "intermediate_size": 16,
                "num_hidden_layers": 1,
                "num_attention_heads": 2,
                "num_key_value_heads": 1,
                "head_dim": 4,
            }
            (base / "hf_model" / "config.json").write_text(json.dumps(base_config))
            base_meta = {
                "classes": ALL_CLASSES,
                "class_bias": [0.0] * len(ALL_CLASSES),
                "final_refit": True,
                "base_model": "hcx-test",
            }
            (base / "hf_meta.json").write_text(json.dumps(base_meta))

            lora = root / "lora"
            (lora / "hf_model").mkdir(parents=True)
            targets = [
                "q_proj", "k_proj", "v_proj", "o_proj",
                "gate_proj", "up_proj", "down_proj",
            ]
            adapter_config = {
                "peft_type": "LORA",
                "peft_version": "0.19.1",
                "r": 16,
                "lora_alpha": 32,
                "lora_dropout": 0.05,
                "target_modules": targets,
                "modules_to_save": ["score"],
                "task_type": "SEQ_CLS",
                "bias": "none",
                "fan_in_fan_out": False,
                "use_rslora": False,
                "use_dora": False,
                "base_model_name_or_path": "/drive/models/kd_m8_refit/hf_model",
            }
            (lora / "hf_model" / "adapter_config.json").write_text(json.dumps(adapter_config))
            shapes = {
                "q_proj": (8, 8), "k_proj": (4, 8), "v_proj": (4, 8),
                "o_proj": (8, 8), "gate_proj": (16, 8),
                "up_proj": (16, 8), "down_proj": (8, 16),
            }
            adapter_state = {}
            for target, (out_features, in_features) in shapes.items():
                family = "self_attn" if target in {"q_proj", "k_proj", "v_proj", "o_proj"} else "mlp"
                stem = f"base_model.model.model.layers.0.{family}.{target}"
                adapter_state[f"{stem}.lora_A.weight"] = torch.zeros(16, in_features)
                adapter_state[f"{stem}.lora_B.weight"] = torch.zeros(out_features, 16)
            adapter_state["base_model.model.score.weight"] = torch.zeros(len(ALL_CLASSES), 8)
            save_file(adapter_state, lora / "hf_model" / "adapter_model.safetensors")
            (lora / "hf_model" / "weak4_training_provenance.json").write_text(
                json.dumps(
                    {
                        "resume_from": "/drive/models/kd_m8_refit/hf_model",
                        "packages": {"peft": "0.19.1"},
                    }
                )
            )
            lora_meta = {
                "classes": ALL_CLASSES,
                "class_bias": [0.0] * len(ALL_CLASSES),
                "train_label_filter": "weak4",
                "lora_r": 16,
                "replay_mode": "none",
                "serializer_name": "weak_nav_v1",
                "final_refit": True,
                "base_model": "hcx-test",
                "seed": 42,
                "epochs": 2,
                "train_batch_size": 16,
                "grad_accum_steps": 1,
                "gradient_checkpointing": True,
                "learning_rate": 1e-4,
                "weight_decay": 0.01,
                "label_smoothing": 0.02,
                "loss": "focal",
                "focal_gamma": 2.0,
                "class_weight_power": 0.5,
                "optim": "adamw",
                "bf16": False,
                "max_length": 384,
                "batch_size": 64,
            }
            (lora / "hf_meta.json").write_text(json.dumps(lora_meta))

            tuner = root / "tuner.json"
            tuner.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "classes": ALL_CLASSES,
                        "weak4_classes": ALL_CLASSES[:4],
                        "rows": 14001,
                        "split": "session",
                        "seed": 42,
                        "gate": {
                            "passed": True,
                            "quality_gate_evaluated": True,
                            "checks": {
                                "confirm_delta_positive": True,
                                "confirm_rescue_gt_harm": True,
                                "full_delta_at_least_0.005": True,
                                "no_weak4_drop_at_least_0.005": True,
                                "list_directory_no_drop": True,
                                "alpha_plateau_has_neighbor": True,
                                "nonweak_identity": True,
                            },
                        },
                        "serializer_name": "weak_nav_v1",
                        "selected_alpha": 0.65,
                        "route_fraction": 0.30,
                        "max_length": 384,
                        "batch_size": 64,
                    }
                )
            )
            pack = root / "pack"
            build_pack(base, lora, tuner, pack)
            meta = json.loads((pack / "hf_meta.json").read_text())
            self.assertEqual(meta["weak4_specialist"]["lora_dir"], "lora_weak")
            self.assertTrue((pack / "lora_weak" / "adapter_model.safetensors").is_file())

            staging = root / "staging"
            staging.mkdir()
            stage(pack, None, staging)
            self.assertTrue((staging / "model" / "lora_weak" / "adapter_config.json").is_file())
            with self.assertRaises(SystemExit):
                stage(pack, root, root / "bad_staging")
            zip_path = root / "weak4.zip"
            build_zip(staging, zip_path)
            with zipfile.ZipFile(zip_path) as archive:
                roots = {name.split("/")[0] for name in archive.namelist()}
            self.assertEqual(roots, {"script.py", "requirements.txt", "model"})

            bad_tuner = json.loads(tuner.read_text())
            bad_tuner["gate"]["checks"].pop("nonweak_identity")
            bad_tuner_path = root / "bad_tuner.json"
            bad_tuner_path.write_text(json.dumps(bad_tuner))
            with self.assertRaises(ValueError):
                build_pack(base, lora, bad_tuner_path, root / "bad_pack")


if __name__ == "__main__":
    unittest.main()
