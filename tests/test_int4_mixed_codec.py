import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import torch
from safetensors.torch import save_file

from script import load_int4_state_dict


ROOT = Path(__file__).resolve().parents[1]
CODEC_PATH = ROOT / "team_toolkit_0708/04_packaging/quantize_int4.py"


def load_codec_module():
    spec = importlib.util.spec_from_file_location("aadp_quantize_int4", CODEC_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MixedInt4CodecTests(unittest.TestCase):
    def test_group_int4_fp16_full_int8_and_split_int8_roundtrip(self):
        codec = load_codec_module()
        torch.manual_seed(17)
        state = {
            "model.embed_tokens.weight": torch.randn(13, 17),
            "model.layers.1.self_attn.q_proj.weight": torch.randn(9, 13),
            "model.layers.2.mlp.down_proj.weight": torch.randn(7, 5),
            "model.norm.weight": torch.randn(17),
            "score.weight": torch.randn(3, 17),
        }
        packed, meta = codec.quantize_state_dict(
            state,
            group_size=11,
            keep_fp16=["score.weight"],
            keep_int8=["model.layers.1.self_attn.q_proj.weight"],
            group_overrides=[("model.layers.2.*", 9)],
            int8_rows=[("model.embed_tokens.weight", [0, 2, 5, 12])],
        )

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "model.int4.safetensors"
            save_file(packed, path)
            Path(str(path) + ".meta.json").write_text(json.dumps(meta), encoding="utf-8")
            restored = load_int4_state_dict(path, dtype=torch.float32)

        self.assertEqual(set(restored), set(state))
        self.assertEqual(meta["format"], "int4-mixed-v1")
        self.assertEqual(meta["kept_fp16"], ["score.weight"])
        self.assertEqual(
            meta["rowwise_int8"], ["model.layers.1.self_attn.q_proj.weight"]
        )
        self.assertEqual(meta["split_rowwise_int8"], ["model.embed_tokens.weight"])
        for name in state:
            self.assertEqual(restored[name].shape, state[name].shape)

        torch.testing.assert_close(
            restored["score.weight"], state["score.weight"].half().float(), rtol=0, atol=0
        )
        int8_error = (
            restored["model.layers.1.self_attn.q_proj.weight"]
            - state["model.layers.1.self_attn.q_proj.weight"]
        ).abs().mean()
        self.assertLess(float(int8_error), 0.02)
        for name in ("model.embed_tokens.weight", "model.layers.2.mlp.down_proj.weight"):
            self.assertLess(float((restored[name] - state[name]).abs().mean()), 0.2)

    def test_unknown_format_is_rejected(self):
        codec = load_codec_module()
        packed, meta = codec.quantize_state_dict({"weight": torch.randn(3, 5)}, group_size=11)
        meta["format"] = "int4-unknown-v0"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "model.int4.safetensors"
            save_file(packed, path)
            Path(str(path) + ".meta.json").write_text(json.dumps(meta), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown int4 codec format"):
                load_int4_state_dict(path)


if __name__ == "__main__":
    unittest.main()
