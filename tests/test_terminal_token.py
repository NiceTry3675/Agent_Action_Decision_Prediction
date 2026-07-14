import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from script import tokenize_texts_with_terminal
from train_transformer import cache_path


class TinyTokenizer:
    def __init__(self, terminal_ids=None, pad_token_id=0, with_token_types=False):
        self.terminal_ids = terminal_ids or {"<|im_end|>": [99]}
        self.pad_token_id = pad_token_id
        self.with_token_types = with_token_types
        self.calls = []

    def __call__(
        self,
        value,
        *,
        add_special_tokens=True,
        padding=False,
        truncation=False,
        max_length=None,
    ):
        self.calls.append(
            {
                "value": value,
                "add_special_tokens": add_special_tokens,
                "padding": padding,
                "truncation": truncation,
                "max_length": max_length,
            }
        )
        if isinstance(value, str):
            ids = list(self.terminal_ids.get(value, [7, 8]))
            return {"input_ids": ids, "attention_mask": [1] * len(ids)}

        rows = []
        for text in value:
            ids = list(range(10, 10 + len(text.split())))
            if truncation and max_length is not None:
                ids = ids[:max_length]
            rows.append(ids)
        encoded = {
            "input_ids": rows,
            "attention_mask": [[1] * len(ids) for ids in rows],
        }
        if self.with_token_types:
            encoded["token_type_ids"] = [[3] * len(ids) for ids in rows]
        return encoded


class TerminalTokenTests(unittest.TestCase):
    def test_terminal_reserves_last_position_after_content_truncation(self):
        tokenizer = TinyTokenizer(with_token_types=True)

        encoded = tokenize_texts_with_terminal(
            tokenizer,
            ["a b c d e", "a b"],
            max_length=4,
            terminal_token="<|im_end|>",
        )

        self.assertEqual(encoded["input_ids"], [[10, 11, 12, 99], [10, 11, 99]])
        self.assertEqual(encoded["attention_mask"], [[1, 1, 1, 1], [1, 1, 1]])
        self.assertEqual(encoded["token_type_ids"], [[3, 3, 3, 3], [3, 3, 3]])
        self.assertEqual(tokenizer.calls[-1]["max_length"], 3)

    def test_empty_terminal_preserves_historical_tokenizer_call(self):
        tokenizer = TinyTokenizer()

        encoded = tokenize_texts_with_terminal(tokenizer, ["a b c"], 2, "")

        self.assertEqual(encoded["input_ids"], [[10, 11]])
        self.assertEqual(len(tokenizer.calls), 1)
        self.assertEqual(tokenizer.calls[0]["max_length"], 2)

    def test_terminal_must_be_one_non_pad_token(self):
        multi = TinyTokenizer(terminal_ids={"bad": [4, 5]})
        with self.assertRaisesRegex(ValueError, "exactly one id"):
            tokenize_texts_with_terminal(multi, ["a"], 4, "bad")

        pad = TinyTokenizer(terminal_ids={"pad": [0]}, pad_token_id=0)
        with self.assertRaisesRegex(ValueError, "pad_token_id"):
            tokenize_texts_with_terminal(pad, ["a"], 4, "pad")

    def test_terminal_token_separates_training_cache_paths(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "train.jsonl"
            source.write_text("{}\n", encoding="utf-8")
            common = dict(
                base_model="model",
                serializer="current_v1",
                replay_mode="none",
                max_replay_samples=10000,
                replay_sample_weight=0.5,
                seed=42,
                split="session",
                fold_id=0,
                n_folds=3,
                cache_dir=td,
                max_length=384,
            )
            control = cache_path(
                SimpleNamespace(**common, terminal_token=""), source, 1, "tokens"
            )
            terminal = cache_path(
                SimpleNamespace(**common, terminal_token="<|im_end|>"),
                source,
                1,
                "tokens",
            )

        self.assertNotEqual(control, terminal)
        self.assertIn("terminal-im_end", terminal.name)


if __name__ == "__main__":
    unittest.main()
