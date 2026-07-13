"""One-shot Hugging Face authentication helper for an existing Colab VM."""

import argparse
from pathlib import Path

from huggingface_hub import HfApi, login


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("token_file")
    parser.add_argument("model_id")
    args = parser.parse_args()
    token_path = Path(args.token_file)
    try:
        token = token_path.read_text(encoding="utf-8").strip()
        if not token.startswith("hf_"):
            raise ValueError("token file does not contain an hf_ token")
        login(token=token, add_to_git_credential=False)
        info = HfApi().model_info(args.model_id, token=True)
        print(f"HF_AUTH_OK model={info.id}", flush=True)
    finally:
        token = None
        token_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
