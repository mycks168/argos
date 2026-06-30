from pathlib import Path
from subprocess import run


def test_cli_argument_mode():
    result = run(
        ["uv", "run", "tts-filter", "README.md と LLM"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "リードミー エムディー" in result.stdout


def test_cli_stdin_mode():
    result = run(
        ["uv", "run", "tts-filter"],
        cwd=Path(__file__).resolve().parents[1],
        input="README.md と LLM",
        capture_output=True,
        text=True,
        check=True,
    )
    assert "リードミー エムディー" in result.stdout
