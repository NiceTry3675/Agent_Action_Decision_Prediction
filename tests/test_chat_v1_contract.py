import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from script import (
    ALL_CLASSES,
    CHAT_V1_CONTRACT_TOOL_LIST,
    chat_v1_contract_messages,
    serialize_transformer_sample,
    serialize_transformer_sample_current,
    serialize_transformer_sample_current_parts,
)
from train_transformer import build_serialized_texts


SAMPLE = {
    "current_prompt": "Fix the parser and run its tests.",
    "history": [
        {"role": "user", "content": "Please inspect src/parser.py first."},
        {
            "role": "assistant_action",
            "name": "read_file",
            "args": {"path": "src/parser.py", "line": 12},
            "result_summary": "Loaded 80 lines",
        },
    ],
    "session_meta": {
        "user_tier": "pro",
        "language_pref": "en",
        "turn_index": 3,
        "budget_tokens_remaining": 12000,
        "elapsed_session_sec": 90,
        "workspace": {
            "git_dirty": True,
            "last_ci_status": "failed",
            "loc": 2048,
            "language_mix": {"python": 0.8, "yaml": 0.2},
            "open_files": ["src/parser.py", "tests/test_parser.py"],
        },
    },
}


class RecordingTokenizer:
    chat_template = "configured"

    def __init__(self, result="tokenizer-rendered"):
        self.result = result
        self.calls = []

    def apply_chat_template(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.result


class ForbiddenTokenizer:
    chat_template = "configured"

    def apply_chat_template(self, messages, **kwargs):
        raise AssertionError("non-chat serializers must not consult the tokenizer")


class ChatV1ContractTests(unittest.TestCase):
    def test_messages_preserve_current_v1_parts_and_put_current_prompt_last(self):
        parts = serialize_transformer_sample_current_parts(SAMPLE)
        messages = chat_v1_contract_messages(SAMPLE)

        self.assertEqual(
            [message["role"] for message in messages],
            ["tool_list", "system", "user"],
        )
        self.assertEqual(messages[0]["content"].split(" "), ALL_CLASSES)
        self.assertEqual(messages[1]["content"], "\n".join(parts[1:]))
        self.assertEqual(messages[2]["content"], SAMPLE["current_prompt"])

    def test_tokenizer_chat_template_is_authoritative(self):
        tokenizer = RecordingTokenizer()

        rendered = serialize_transformer_sample(
            SAMPLE,
            "chat_v1_contract",
            tokenizer=tokenizer,
        )

        self.assertEqual(rendered, "tokenizer-rendered")
        self.assertEqual(len(tokenizer.calls), 1)
        messages, kwargs = tokenizer.calls[0]
        self.assertEqual(messages, chat_v1_contract_messages(SAMPLE))
        self.assertEqual(kwargs, {"add_generation_prompt": True, "tokenize": False})

    def test_fallback_is_exact_hcx_chatml_generation_prompt(self):
        parts = serialize_transformer_sample_current_parts(SAMPLE)
        system_content = "\n".join(parts[1:])
        expected = (
            f"<|im_start|>tool_list\n{CHAT_V1_CONTRACT_TOOL_LIST}<|im_end|>\n"
            f"<|im_start|>system\n{system_content}<|im_end|>\n"
            f"<|im_start|>user\n{SAMPLE['current_prompt']}<|im_end|>\n"
            "<|im_start|>assistant\n"
        )

        self.assertEqual(serialize_transformer_sample(SAMPLE, "chat_v1_contract"), expected)

    def test_current_v1_default_remains_byte_identical_and_tokenizer_free(self):
        expected = serialize_transformer_sample_current(SAMPLE)

        self.assertEqual(serialize_transformer_sample(SAMPLE), expected)
        self.assertEqual(
            serialize_transformer_sample(SAMPLE, "current_v1", tokenizer=ForbiddenTokenizer()),
            expected,
        )

    def test_non_string_chat_template_result_fails_loudly(self):
        with self.assertRaisesRegex(TypeError, "must return text"):
            serialize_transformer_sample(
                SAMPLE,
                "chat_v1_contract",
                tokenizer=RecordingTokenizer(result=[1, 2, 3]),
            )

    def test_training_text_builder_uses_loaded_tokenizer_template(self):
        tokenizer = RecordingTokenizer(result="training-rendered")
        with tempfile.TemporaryDirectory() as td:
            args = SimpleNamespace(
                base_model="hcx-test",
                serializer="chat_v1_contract",
                replay_mode="none",
                cache_dir=td,
                max_length=448,
                no_text_cache=True,
                rebuild_cache=False,
            )
            texts, cache_path = build_serialized_texts(
                [SAMPLE],
                args,
                Path(td) / "train.jsonl",
                tokenizer=tokenizer,
            )

        self.assertEqual(texts, ["training-rendered"])
        self.assertEqual(cache_path, "")
        self.assertEqual(len(tokenizer.calls), 1)


if __name__ == "__main__":
    unittest.main()
