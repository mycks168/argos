"""高速振り分け（ollama の tools API）と argos-tool コマンドのテスト。"""

from __future__ import annotations

import json

import pytest
import requests

from argos.router.fast_router import ROUTER_SYSTEM_PROMPT, FastRouter
from argos.router.registry import ToolRegistry
from argos.tools import argos_tool

TOOLS = """
tools:
  - name: map_zoom
    description: 地図を拡大または縮小する
    params:
      direction: {type: enum, choices: [in, out]}
    command: ["{python}", "-c", "print('{direction}')"]
    reply: {by: direction, in: 拡大したよ。, out: 広域にしたよ。}
  - name: map_navigate
    description: ナビを開始する
    safety: confirm
    params:
      destination: {type: string, max_length: 10}
    command: ["{python}", "-c", "import sys; sys.exit(1)"]
    reply: "{destination}へナビするね。"
"""


class FakeResponse:
    """requests.Response の偽物。"""

    def __init__(self, data, status=200):
        """返すJSONと状態コードを保持する。"""
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        """失敗の状態コードなら例外にする。"""
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        """JSONを返す。値が例外なら送出する。"""
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


def _registry(tmp_path) -> ToolRegistry:
    """テスト用のツール置き場を作る。"""
    folder = tmp_path / "tools"
    folder.mkdir()
    path = folder / "router.yaml"
    path.write_text(TOOLS, encoding="utf-8")
    path.chmod(0o644)
    return ToolRegistry((tmp_path,))


def _call(name, arguments):
    """ollama が返す tool_calls の形を作る。"""
    return {"message": {"tool_calls": [{"function": {"name": name, "arguments": arguments}}]}}


def test_decide_tool_and_request_body(tmp_path):
    """ツールと引数を返し、ollama へは tools・think:false・temperature 0 で問い合わせる。"""
    sent = {}

    def post(url, json=None, timeout=None):
        """送った内容を記録して、ツール呼び出しを返す。"""
        sent.update(url=url, body=json, timeout=timeout)
        return FakeResponse(_call("map_zoom", {"direction": "in"}))

    router = FastRouter(_registry(tmp_path), url="http://clove:11434/", model="gemma4:e4b",
                        timeout_seconds=0.5, post=post)
    decision = router.decide("地図拡大して")
    assert (decision.tool, decision.args, decision.reason) == ("map_zoom", {"direction": "in"}, "tool")
    assert decision.elapsed_ms >= 0
    assert sent["url"] == "http://clove:11434/api/chat"
    assert sent["timeout"] == 0.5
    body = sent["body"]
    assert body["model"] == "gemma4:e4b" and body["think"] is False and body["stream"] is False
    assert body["options"] == {"temperature": 0}
    assert body["messages"] == [{"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                                {"role": "user", "content": "地図拡大して"}]
    assert {tool["function"]["name"] for tool in body["tools"]} == {"map_zoom", "map_navigate"}
    assert router.spec("map_navigate").safety == "confirm"


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ({"message": {"content": ""}}, "agent"),
        (_call("unknown", {}), "unknown_tool"),
        (_call("map_zoom", "not json"), "bad_args"),
        (_call("map_zoom", json.dumps({"direction": "up"})), "invalid_args"),
        (_call("map_navigate", {}), "invalid_args"),
        (_call("map_zoom", ["in"]), "unknown_tool"),
    ],
)
def test_decide_falls_back_to_agent(tmp_path, response, reason):
    """ツールが決まらない・知らない・引数がおかしいときは、エージェントへ渡す。"""
    router = FastRouter(_registry(tmp_path), url="http://x", model="m", post=lambda *a, **k: FakeResponse(response))
    decision = router.decide("何か")
    assert decision.tool is None
    assert decision.reason.startswith(reason)


def test_decide_accepts_json_string_arguments(tmp_path):
    """引数が JSON 文字列で返ってきても読む。"""
    router = FastRouter(_registry(tmp_path), url="http://x", model="m",
                        post=lambda *a, **k: FakeResponse(_call("map_zoom", json.dumps({"direction": "out"}))))
    assert router.decide("広域にして").args == {"direction": "out"}


@pytest.mark.parametrize(
    ("post", "reason"),
    [
        (lambda *a, **k: (_ for _ in ()).throw(requests.Timeout()), "timeout"),
        (lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError()), "error"),
        (lambda *a, **k: FakeResponse({}, status=500), "error"),
        (lambda *a, **k: FakeResponse(ValueError("bad json")), "error"),
    ],
)
def test_decide_survives_network_errors(tmp_path, post, reason):
    """時間切れ・接続失敗・壊れた応答は、例外にせずエージェントへ渡す。"""
    router = FastRouter(_registry(tmp_path), url="http://x", model="m", post=post)
    assert router.decide("地図拡大して").reason == reason


def test_decide_without_tools_or_text(tmp_path):
    """ツールが無い、発話が空なら問い合わせない。"""
    def post(*args, **kwargs):
        """呼ばれたら失敗にする。"""
        raise AssertionError("問い合わせてはいけない")

    assert FastRouter(ToolRegistry((tmp_path,)), url="http://x", model="m", post=post).decide("a").reason == "no_tools"
    assert FastRouter(_registry(tmp_path), url="http://x", model="m", post=post).decide("  ").reason == "empty"


def test_argos_tool_list_and_run(tmp_path, monkeypatch, capsys):
    """argos-tool で一覧を出し、同じ検証でツールを実行する。"""
    registry = _registry(tmp_path)
    (tmp_path / "broken.yaml").write_text("tools: [{name: x}]", encoding="utf-8")
    (tmp_path / "broken.yaml").chmod(0o644)
    monkeypatch.setattr(argos_tool, "ToolRegistry", lambda: registry)

    assert argos_tool.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "- map_zoom(direction:in|out): 地図を拡大または縮小する" in out
    assert "- map_navigate(destination:string): ナビを開始する（実行前に利用者へ確認）" in out
    assert "! 読み飛ばし" in out

    assert argos_tool.main(["list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {item["name"] for item in listed} == {"map_zoom", "map_navigate"}
    assert {item["safety"] for item in listed} == {"display", "confirm"}

    assert argos_tool.main(["run", "map_zoom", "--args", '{"direction": "in"}']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True and result["reply"] == "拡大したよ。"

    assert argos_tool.main(["run", "map_navigate", "--args", '{"destination": "博多"}']) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False

    assert argos_tool.main(["run", "map_zoom", "--args", '{"direction": "up"}']) == 2
    assert "どれか" in json.loads(capsys.readouterr().out)["error"]
    assert argos_tool.main(["run", "map_zoom", "--args", "[1]"]) == 2
    assert argos_tool.main(["run", "map_zoom", "--args", "{bad"]) == 2
    capsys.readouterr()


def test_argos_tool_list_empty(tmp_path, monkeypatch, capsys):
    """ツールが無ければ、そう出す。"""
    monkeypatch.setattr(argos_tool, "ToolRegistry", lambda: ToolRegistry((tmp_path,)))
    assert argos_tool.main(["list"]) == 0
    assert "使えるツールはありません" in capsys.readouterr().out
