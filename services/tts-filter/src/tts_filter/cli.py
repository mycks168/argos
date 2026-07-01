from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .filter import TTSFilter, normalize_for_tts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize text for TTS")
    parser.add_argument("text", nargs="?", help="text to normalize; if omitted, read from stdin")
    parser.add_argument("--config", type=Path, default=None, help="path to dictionary yaml")
    parser.add_argument(
        "--code-block-mode",
        choices=["skip", "meta", "first-line", "rule", "ollama-summary", "literal"],
        default="ollama-summary",
    )
    parser.add_argument("--ollama-model", default="qwen2.5:0.5b")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.text is not None:
        source_text = args.text
    else:
        source_text = sys.stdin.read()

    if not source_text:
        parser.error("text argument or stdin is required")

    tts_filter = TTSFilter.from_yaml(args.config)
    tts_filter.code_block_mode = args.code_block_mode
    tts_filter.ollama_model = args.ollama_model
    print(tts_filter.normalize(source_text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
