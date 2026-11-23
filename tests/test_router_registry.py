"""ツール置き場（router.yaml）の読み込み・検証・実行のテスト。"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from argos.router import registry as registry_module
from argos.router.registry import ToolError, ToolRegistry, default_tool_dirs, load_file

GOOD = """
tools:
  - name: map_zoom
    description: 地図を拡大または縮小する
    params:
      direction: {type: enum, choices: [in, out]}
    command: ["{python}", "-c", "print('{direction}')"]
    reply: {by: direction, in: 拡大したよ。, out: 広域にしたよ。}
    examples: [地図拡大して]
  - name: say
    description: 言葉を返す
    params:
      word: {type: string, max_length: 5}
      count: {type: integer, minimum: 1, maximum: 3, required: false}
    command: ["{python}", "-c", "import sys; print(sys.argv[1:])", "{word}", "{count}"]
    undo: ["{python}", "-c", "print('undo')"]
    reply: "{word}って言ったよ。"
    env: {ROUTER_TEST: "1"}
    timeout_seconds: 5
"""


def _write(directory: Path, name: str, text: str, mode: int = 0o644) -> Path:
    """定義ファイルを書いて権限を整える。"""
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def test_load_and_run_tools(tmp_path):
    """定義を読み、引数を検証して実行し、値ごとの返事を返す。"""
    _write(tmp_path, "maps/router.yaml", GOOD)
    registry = ToolRegistry((tmp_path,))
    tools = registry.tools()
    assert set(tools) == {"map_zoom", "say"}
    assert registry.errors == {}

    result = registry.run("map_zoom", {"direction": "out"})
    assert result.ok and result.reply == "広域にしたよ。"
    assert result.stdout.strip() == "out"

    result = registry.run("say", {"word": "やあ"})
    assert result.ok and result.reply == "やあって言ったよ。"
    # 空の任意引数（count）はコマンドから省く。
    assert result.stdout.strip() == "['やあ']"
    assert registry.run("say", {"word": "やあ", "count": 2}).stdout.strip() == "['やあ', '2']"
    undo = registry.run("say", {"word": "やあ"}, undo=True)
    assert undo.ok and undo.reply == "" and undo.stdout.strip() == "undo"
    with pytest.raises(ToolError):
        registry.run("map_zoom", {"direction": "in"}, undo=True)


def test_tool_schema_for_llm(tmp_path):
    """LLM の tools API に渡す形にする。例は説明に添える。"""
    path = _write(tmp_path, "maps/router.yaml", GOOD)
    spec = {s.name: s for s in load_file(path)}["say"]
    schema = spec.tool_schema()["function"]
    assert schema["name"] == "say"
    assert schema["parameters"]["required"] == ["word"]
    assert schema["parameters"]["properties"]["count"] == {"type": "integer", "minimum": 1, "maximum": 3}
    zoom = {s.name: s for s in load_file(path)}["map_zoom"].tool_schema()["function"]
    assert "（例：地図拡大して）" in zoom["description"]
    assert zoom["parameters"]["properties"]["direction"]["enum"] == ["in", "out"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({}, "指定されていません"),
        ({"word": "あいうえおか"}, "5 文字以内"),
        ({"word": "a\nb"}, "制御文字"),
        ({"word": "  "}, "空でない"),
        ({"word": "a", "count": 9}, "1〜3"),
        ({"word": "a", "count": "x"}, "整数"),
        ({"word": "a", "count": True}, "整数"),
        ({"word": "a", "extra": 1}, "未対応の引数"),
    ],
)
def test_invalid_args_are_rejected(tmp_path, args, message):
    """引数の型・長さ・範囲・余計な引数をはじく。"""
    _write(tmp_path, "t/router.yaml", GOOD)
    with pytest.raises(ToolError, match=message):
        ToolRegistry((tmp_path,)).get("say").validate_args(args)


def test_enum_rejects_unknown_value(tmp_path):
    """enum は決めた値だけ受け付ける。"""
    _write(tmp_path, "t/router.yaml", GOOD)
    with pytest.raises(ToolError, match="どれか"):
        ToolRegistry((tmp_path,)).get("map_zoom").validate_args({"direction": "up"})


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("tools: 1", "tools:"),
        ("tools: [{name: Bad, description: x, command: [a], reply: x}]", "ツール名"),
        ("tools: [{name: a, command: [a], reply: x}]", "description"),
        ("tools: [{name: a, description: x, command: [a]}]", "reply"),
        ("tools: [{name: a, description: x, reply: x}]", "command"),
        ("tools: [{name: a, description: x, command: [a], reply: x, foo: 1}]", "未対応の項目"),
        ("tools: [{name: a, description: x, command: ['{nope}'], reply: x}]", "知らない置き換え"),
        ("tools: [{name: a, description: x, command: [a], reply: x, safety: maybe}]", "safety"),
        ("tools: [{name: a, description: x, command: [a], reply: x, timeout_seconds: 0}]", "timeout_seconds"),
        ("tools: [{name: a, description: x, command: [a], reply: x, env: {A: 1}}]", "env"),
        ("tools: [{name: a, description: x, command: [a], reply: x, cwd: 1}]", "cwd"),
        ("tools: [{name: a, description: x, command: [a], reply: x, examples: [a, b, c, d, e, f]}]", "examples"),
        ("tools: [{name: a, description: x, command: [a], reply: x, params: [1]}]", "params"),
        ("tools: [{name: a, description: x, command: [a], reply: x, params: {python: {}}}]", "引数名"),
        ("tools: [{name: a, description: x, command: [a], reply: x, params: {p: 1}}]", "テーブル"),
        ("tools: [{name: a, description: x, command: [a], reply: x, params: {p: {type: list}}}]", "type"),
        ("tools: [{name: a, description: x, command: [a], reply: x, params: {p: {type: enum}}}]", "choices"),
        ("tools: [{name: a, description: x, command: [a], reply: x, params: {p: {max_length: 999}}}]", "max_length"),
        ("tools: [{name: a, description: x, command: [a], reply: x, params: {p: {type: integer, minimum: a}}}]", "整数"),
        ("tools: [{name: a, description: x, command: [a], reply: {by: p, x: y}, params: {p: {}}}]", "enum の引数名"),
        ("tools: [{name: a, description: x, command: [a], reply: {by: p, b: y}, params: {p: {type: enum, choices: [b, c]}}}]", "default"),
        ("tools: [{name: a, description: x, command: [a], reply: {by: p, b: 1, c: y}, params: {p: {type: enum, choices: [b, c]}}}]", "文字列"),
        ("tools: [1]", "テーブル"),
        ("tools: [{name: a, description: x, command: [1], reply: x}]", "文字列の配列"),
        (": : :", "読み込めません"),
    ],
)
def test_invalid_definitions(tmp_path, body, message):
    """書き間違いは、理由付きの ToolError にする。"""
    path = _write(tmp_path, "router.yaml", body)
    with pytest.raises(ToolError, match=message):
        load_file(path)


def test_reply_default_covers_other_values(tmp_path):
    """値ごとの返事に無い値は default を使う。"""
    body = ("tools: [{name: a, description: x, command: ['{python}', -c, pass], "
            "reply: {by: p, b: びー, default: そのほか}, params: {p: {type: enum, choices: [b, c]}}}]")
    spec = load_file(_write(tmp_path, "router.yaml", body))[0]
    assert spec.format_reply({"p": "b"}) == "びー"
    assert spec.format_reply({"p": "c"}) == "そのほか"


def test_broken_file_is_skipped_and_others_load(tmp_path, caplog):
    """1つのファイルが壊れていても、ほかのツールは使える。重複名は後のほうを捨てる。"""
    _write(tmp_path, "a/router.yaml", GOOD)
    _write(tmp_path, "b/router.yaml", "tools: [{name: bad}]")
    _write(tmp_path, "extra.yaml", "tools: [{name: say, description: x, command: [a], reply: x}]")
    registry = ToolRegistry((tmp_path, tmp_path / "missing"))
    assert set(registry.tools()) == {"map_zoom", "say"}
    assert any("b/router.yaml" in path for path in registry.errors)
    assert any("重複" in error for error in registry.errors.values())


def test_reloads_when_file_changes(tmp_path):
    """ファイルを書き換えると、再起動せずに次の呼び出しで読み直す。"""
    path = _write(tmp_path, "t/router.yaml", "tools: [{name: one, description: x, command: [a], reply: x}]")
    registry = ToolRegistry((tmp_path,))
    assert set(registry.tools()) == {"one"}
    assert set(registry.tools()) == {"one"}  # 変わっていなければ読み直さない
    path.write_text("tools: [{name: two, description: x, command: [a], reply: x}]", encoding="utf-8")
    future = time.time() + 5
    os.utime(path, (future, future))
    assert set(registry.tools()) == {"two"}
    with pytest.raises(ToolError, match="ありません"):
        registry.get("one")


def test_untrusted_files_are_not_loaded(tmp_path):
    """他人が書き換えられるファイルは、コマンドを実行する設定なので読まない。"""
    _write(tmp_path, "t/router.yaml", GOOD, mode=0o666)
    registry = ToolRegistry((tmp_path,))
    assert registry.tools() == {}
    assert "安全でない" in next(iter(registry.errors.values()))


def test_untrusted_owner_is_rejected(tmp_path, monkeypatch):
    """持ち主が自分でないファイルも読まない。"""
    path = _write(tmp_path, "t/router.yaml", GOOD)
    other = os.getuid() + 1
    monkeypatch.setattr(registry_module.os, "getuid", lambda: other)
    assert registry_module._trusted(path) is False
    assert registry_module._trusted(tmp_path / "nope.yaml") is False


def test_run_failure_timeout_and_missing_command(tmp_path):
    """失敗・時間切れ・起動できないコマンドは ok=False で返す（例外にしない）。"""
    body = """
tools:
  - {name: fail, description: x, command: ["{python}", -c, "import sys; sys.exit(3)"], reply: x}
  - {name: slow, description: x, command: ["{python}", -c, "import time; time.sleep(5)"], reply: x, timeout_seconds: 0.2}
  - {name: gone, description: x, command: [/nonexistent/command], reply: x}
  - {name: here, description: x, command: ["{python}", -c, "import os; print(os.getcwd())"], reply: x, cwd: "{argos_root}"}
"""
    _write(tmp_path, "t/router.yaml", body)
    registry = ToolRegistry((tmp_path,))
    failed = registry.run("fail")
    assert not failed.ok and failed.returncode == 3 and failed.reply == ""
    slow = registry.run("slow")
    assert not slow.ok and slow.returncode is None
    gone = registry.run("gone")
    assert not gone.ok and gone.stderr
    here = registry.run("here")
    assert here.ok and Path(here.stdout.strip()) == registry_module._argos_root()


def test_build_argv_fills_fixed_placeholders(tmp_path):
    """{python}・{skill_dir}・{argos_root} を埋める。"""
    body = "tools: [{name: a, description: x, command: ['{python}', '{skill_dir}/s.py', '{argos_root}'], reply: x}]"
    spec = load_file(_write(tmp_path, "skill/router.yaml", body))[0]
    argv = spec.build_argv({})
    assert argv[0] == sys.executable
    assert argv[1] == str(tmp_path / "skill" / "s.py")
    assert argv[2] == str(registry_module._argos_root())


def test_default_dirs_and_bundled_definitions():
    """既定の置き場は skills と ~/.config/argos/router-tools。同梱の定義はそのまま読める。"""
    dirs = default_tool_dirs()
    assert dirs[0] == registry_module._argos_root() / "skills"
    assert dirs[1] == Path("~/.config/argos/router-tools").expanduser()
    registry = ToolRegistry((dirs[0],))
    tools = registry.tools()
    assert {"map_zoom", "map_overview", "map_tracking", "map_navigate", "screen_layout", "dashboard_clear", "rain_radar"} <= set(tools)
    assert registry.errors == {}
    assert tools["map_navigate"].safety == "confirm"
    assert tools["map_zoom"].format_reply({"direction": "in"}) == "地図を拡大したよ。"
    radar = tools["rain_radar"]
    assert radar.build_argv(radar.validate_args({}))[-1] == "center"  # 地名がなければ --query を渡さない
    assert radar.build_argv(radar.validate_args({"place": "テスト駅"}))[-1] == "--query=テスト駅"
