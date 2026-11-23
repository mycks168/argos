"""ARGOS 本体と高速振り分け（ツールで片付けばエージェントを呼ばない）のつなぎ込みのテスト。"""

from __future__ import annotations

import dataclasses
import datetime
import threading

from test_app import _patch_app, _settings

from argos.config import load_settings
from argos.core.app import ArgosApp, _format_local_turns, _relative_time
from argos.router.fast_router import RouteDecision
from argos.router.registry import ToolError, ToolResult, ToolSpec


class FakeRouter:
    """FastRouter の偽物。決めた結果を返す。"""

    def __init__(self, decision: RouteDecision, safety: str = "display", missing: bool = False):
        """返す判断と、ツールの安全の段階を保持する。"""
        self.decision = decision
        self.safety = safety
        self.missing = missing
        self.asked: list[str] = []
        self.done = threading.Event()

    def decide(self, text):
        """判断を返す。"""
        self.asked.append(text)
        self.done.set()
        return self.decision

    def spec(self, name):
        """ツールの定義を返す。"""
        if self.missing:
            raise ToolError("ありません")
        return ToolSpec(name=name, description="x", command=("x",), reply="x", source=None, safety=self.safety)


class FakeRegistry:
    """ToolRegistry の偽物。実行を記録して決めた結果を返す。"""

    def __init__(self, result: ToolResult | None = None, error: Exception | None = None):
        """返す結果か例外を保持する。"""
        self.result = result
        self.error = error
        self.ran: list[tuple[str, dict]] = []

    def run(self, name, args=None, *, undo=False):
        """実行を記録する。"""
        self.ran.append((name, dict(args or {})))
        if self.error is not None:
            raise self.error
        return self.result


def _app(monkeypatch, router, registry=None, mode="active", history_limit=5):
    """振り分けを使う ARGOS を作る。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    app._settings = dataclasses.replace(app._settings, router_mode=mode, router_history_limit=history_limit)
    app._fast_router = router
    if registry is not None:
        app._tool_registry = registry
    return app


def _messages(app, role):
    """ダッシュボードの会話のうち、指定の役割の本文を返す。"""
    return [m["text"] for m in app._dashboard_state.snapshot()["messages"] if m.get("role") == role]


def test_active_tool_handles_turn_without_agent(monkeypatch, capsys):
    """ツールで片付いたら、エージェントを呼ばずに結果を読み上げ、最初の返事は捨てる。"""
    router = FakeRouter(RouteDecision("map_zoom", {"direction": "in"}))
    registry = FakeRegistry(ToolResult("map_zoom", True, "地図を拡大したよ。", 0))
    app = _app(monkeypatch, router, registry)
    discarded = []

    app._handle_text("地図拡大して", first_reply=lambda: "見てみるね。", discard_first_reply=lambda: discarded.append(1))

    assert app._agent.asked == []
    assert registry.ran == [("map_zoom", {"direction": "in"})]
    assert discarded == [1]
    assert "ARGOS> 地図を拡大したよ。" in capsys.readouterr().out
    assert _messages(app, "user")[-1] == "地図拡大して"
    assert _messages(app, "assistant")[-1] == "地図を拡大したよ。"


def test_local_turns_are_prepended_to_next_agent_request(monkeypatch):
    """ARGOSだけで済ませた会話は、次にエージェントへ頼むとき時刻付きの会話履歴として付け、1回で消す。"""
    router = FakeRouter(RouteDecision("map_zoom", {"direction": "in"}))
    registry = FakeRegistry(ToolResult("map_zoom", True, "地図を拡大したよ。", 0))
    app = _app(monkeypatch, router, registry)
    app._handle_text("地図拡大して")

    router.decision = RouteDecision(None, reason="agent")
    app._handle_text("さっきの続きだけど")
    app._handle_text("もう一つ")

    first, second = app._agent.asked
    assert first.startswith("（前の会話：\n- ")
    assert "利用者「地図拡大して」 ARGOS「地図を拡大したよ。」" in first
    assert first.endswith("）\nさっきの続きだけど")
    assert "振り分け" not in first  # エージェントには振り分けのことを言わない
    assert second == "もう一つ"


def test_local_turns_are_limited(monkeypatch):
    """付ける会話は新しいものから設定の件数まで。0なら付けない。"""
    registry = FakeRegistry(ToolResult("map_zoom", True, "拡大したよ。", 0))
    app = _app(monkeypatch, FakeRouter(RouteDecision("map_zoom", {"direction": "in"})), registry, history_limit=2)
    for index in range(4):
        app._handle_text(f"拡大{index}")
    assert [turn[1] for turn in app._local_turns] == ["拡大2", "拡大3"]

    app = _app(monkeypatch, FakeRouter(RouteDecision("map_zoom", {"direction": "in"})), registry, history_limit=0)
    app._handle_text("拡大")
    app._fast_router.decision = RouteDecision(None)
    app._handle_text("質問")
    assert app._agent.asked == ["質問"]


def test_failures_fall_back_to_agent_unchanged(monkeypatch):
    """振り分けできない・確認が必要・定義が消えた・実行に失敗したときは、元の発話をそのまま渡す。"""
    cases = [
        (FakeRouter(RouteDecision(None, reason="timeout")), FakeRegistry()),
        (FakeRouter(RouteDecision("map_navigate", {"destination": "博多"}), safety="confirm"), FakeRegistry()),
        (FakeRouter(RouteDecision("gone", {}), missing=True), FakeRegistry()),
        (FakeRouter(RouteDecision("map_zoom", {"direction": "in"})), FakeRegistry(error=ToolError("引数"))),
        (FakeRouter(RouteDecision("map_zoom", {"direction": "in"})),
         FakeRegistry(ToolResult("map_zoom", False, "", 1, stderr="adb error"))),
    ]
    for router, registry in cases:
        app = _app(monkeypatch, router, registry)
        app._handle_text("地図拡大して")
        assert app._agent.asked == ["地図拡大して"]
        # ダッシュボードの発話は1回だけ（振り分けの分と二重にしない）。
        assert _messages(app, "user") == ["地図拡大して"]


def test_shadow_mode_only_logs(monkeypatch, caplog):
    """様子見モードでは判断を別スレッドで記録するだけで、必ずエージェントへ渡す。"""
    router = FakeRouter(RouteDecision("map_zoom", {"direction": "in"}, 50.0, "tool"))
    registry = FakeRegistry(ToolResult("map_zoom", True, "x", 0))
    app = _app(monkeypatch, router, registry, mode="shadow")
    with caplog.at_level("INFO", logger="argos.core.app"):
        app._handle_text("地図拡大して")
        assert router.done.wait(2)
        for thread in threading.enumerate():
            if thread.name == "router-shadow":
                thread.join(2)
    assert app._agent.asked == ["地図拡大して"]
    assert registry.ran == []
    assert any("振り分け（様子見）" in record.message and "map_zoom" in record.message for record in caplog.records)


def test_shadow_log_without_router_is_noop(monkeypatch):
    """振り分けが無ければ何もしない。"""
    app = _app(monkeypatch, None)
    app._log_shadow_route("x")
    assert app._route_with_tools("x", 0, None) is False


def test_relative_time_and_format():
    """何分前・何時間前・何日前を短く書き、会話履歴の行を作る。"""
    now = datetime.datetime(2026, 11, 22, 10, 0)
    assert _relative_time(now - datetime.timedelta(seconds=30), now) == "1分以内"
    assert _relative_time(now - datetime.timedelta(minutes=5), now) == "5分前"
    assert _relative_time(now - datetime.timedelta(hours=3), now) == "3時間前"
    assert _relative_time(now - datetime.timedelta(days=3), now) == "3日前"
    text = _format_local_turns([(datetime.datetime(2026, 11, 22, 9, 41), "地図拡大して", "拡大したよ。")], now)
    assert text == "（前の会話：\n- 11月22日 9:41（19分前） 利用者「地図拡大して」 ARGOS「拡大したよ。」\n）\n"


def test_router_disabled_in_dry_run_and_created_when_enabled(monkeypatch):
    """dry_run や無効なら振り分けを作らず、有効なら作る。"""
    _patch_app(monkeypatch)
    assert ArgosApp(_settings())._fast_router is None
    assert ArgosApp(dataclasses.replace(_settings(), router_enabled=True, dry_run=True))._fast_router is None
    settings = dataclasses.replace(_settings(), router_enabled=True, dry_run=False, router_tool_dirs=("/tmp/none",))
    app = ArgosApp(settings)
    assert app._fast_router is not None
    assert app._tool_registry.tools() == {}


def test_load_router_settings(monkeypatch):
    """router.* の設定を読み、不正なモードはエラーにする。"""
    for name in ("ARGOS_ROUTER_ENABLED", "ARGOS_ROUTER_MODE", "ARGOS_ROUTER_URL", "ARGOS_ROUTER_MODEL",
                 "ARGOS_ROUTER_TIMEOUT_SECONDS", "ARGOS_ROUTER_TOOL_DIRS", "ARGOS_ROUTER_HISTORY_LIMIT"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings()
    assert (settings.router_enabled, settings.router_mode, settings.router_model) == (False, "shadow", "gemma4:e4b")
    assert settings.router_tool_dirs == () and settings.router_history_limit == 5

    monkeypatch.setenv("ARGOS_ROUTER_ENABLED", "true")
    monkeypatch.setenv("ARGOS_ROUTER_MODE", "Active")
    monkeypatch.setenv("ARGOS_ROUTER_URL", "http://clove:11434")
    monkeypatch.setenv("ARGOS_ROUTER_TIMEOUT_SECONDS", "99")
    monkeypatch.setenv("ARGOS_ROUTER_TOOL_DIRS", "~/a; /b")
    monkeypatch.setenv("ARGOS_ROUTER_HISTORY_LIMIT", "100")
    settings = load_settings()
    assert settings.router_enabled and settings.router_mode == "active"
    assert settings.router_url == "http://clove:11434"
    assert settings.router_timeout_seconds == 5.0
    assert settings.router_tool_dirs == ("~/a", "/b")
    assert settings.router_history_limit == 20

    monkeypatch.setenv("ARGOS_ROUTER_MODE", "maybe")
    try:
        load_settings()
    except ValueError as exc:
        assert "router.mode" in str(exc)
    else:
        raise AssertionError("不正なモードはエラーにする")
