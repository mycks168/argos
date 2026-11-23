"""タイマーのツール（skills/timer/scripts/argos_timer.py）と一覧ページのテスト。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

from argos.router.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def timer(tmp_path, monkeypatch):
    """systemd とダッシュボードを偽物にした argos_timer モジュールを返す。"""
    path = ROOT / "skills/timer/scripts/argos_timer.py"
    spec = importlib.util.spec_from_file_location("argos_timer_for_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["argos_timer_for_test"] = module
    spec.loader.exec_module(module)
    module.STATE_PATH = tmp_path / "timers.json"
    module.calls = {"schedule": [], "unschedule": [], "events": []}
    monkeypatch.setattr(module, "schedule", lambda timer_id, seconds: module.calls["schedule"].append((timer_id, seconds)))
    monkeypatch.setattr(module, "unschedule", lambda timer_id: module.calls["unschedule"].append(timer_id))
    monkeypatch.setattr(module, "post_event", lambda payload: module.calls["events"].append(payload))
    original = module.locked_state
    monkeypatch.setattr(module, "locked_state", lambda path=None: original(module.STATE_PATH))
    return module


def _start(module, capsys, *argv) -> str:
    """start を実行して読み上げる文を返す。"""
    assert module.main(["start", *argv]) == 0
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_format_and_labels(timer):
    """時間の読み方、時間の範囲、名前の付け方。"""
    assert timer.format_duration(3725) == "1時間2分5秒"
    assert timer.format_duration(180) == "3分"
    assert timer.format_duration(0) == "0秒"
    assert timer.total_seconds(1, 30, 0) == 5400
    for bad in ((0, 0, 0), (25, 0, 0), (0, -1, 0)):
        with pytest.raises(ValueError):
            timer.total_seconds(*bad)
    assert timer.unique_label("", set()) == "タイマー1"
    assert timer.unique_label("", {"タイマー1"}) == "タイマー2"
    assert timer.unique_label("パスタ", {"パスタ"}) == "パスタ2"
    assert timer.unique_label("パスタ", {"パスタ", "パスタ2"}) == "パスタ3"
    assert timer.unique_label("あ" * 30, set()) == "あ" * 20


def test_start_list_cancel_and_screen(timer, capsys, monkeypatch):
    """複数始めて一覧に出し、名前で止め、画面を出し直し、最後に閉じる。"""
    clock = [1000.0]
    monkeypatch.setattr(timer.time, "time", lambda: clock[0])
    assert _start(timer, capsys, "--minutes", "3", "--label", "パスタ") == "パスタ、3分のタイマーを始めたよ。"
    assert _start(timer, capsys, "--seconds", "30") == "タイマー1、30秒のタイマーを始めたよ。"
    assert _start(timer, capsys, "--minutes", "1", "--label", "パスタ") == "パスタ2、1分のタイマーを始めたよ。"
    assert [seconds for _, seconds in timer.calls["schedule"]] == [180, 30, 60]

    # 画面は右側に1回出し、2回目からは差し替える。終わる順に並ぶ。
    overlays = [e for e in timer.calls["events"] if e["type"] == "overlay"]
    assert [o["replace_top"] for o in overlays] == [False, True, True]
    assert overlays[-1]["target_slot"] == "right"
    query = urllib.parse.parse_qs(urllib.parse.urlparse(overlays[-1]["url"]).query)
    assert [item["l"] for item in json.loads(query["t"][0])] == ["タイマー1", "パスタ2", "パスタ"]
    assert overlays[-1]["url"].startswith("/static/timers.html?")

    clock[0] = 1010.0
    assert timer.main(["list"]) == 0
    assert capsys.readouterr().out.strip() == "タイマー1があと20秒、パスタ2があと50秒、パスタがあと2分50秒だよ。"

    # 「パスタ」は完全一致の「パスタ」だけ止める。
    assert timer.main(["cancel", "--label", "パスタ"]) == 0
    assert capsys.readouterr().out.strip() == "パスタのタイマーを止めたよ。"
    # 完全一致がなければ、前方一致の1つだけ。
    assert timer.main(["cancel", "--label", "タイマー"]) == 0
    assert capsys.readouterr().out.strip() == "タイマー1のタイマーを止めたよ。"
    assert timer.main(["cancel", "--label", "洗濯"]) == 0
    assert capsys.readouterr().out.strip() == "洗濯のタイマーは見つからなかったよ。"
    assert len(timer.calls["unschedule"]) == 2

    # 全部止めたら画面を閉じる。
    assert timer.main(["cancel"]) == 0
    assert capsys.readouterr().out.strip() == "タイマーを全部止めたよ。"
    assert timer.calls["events"][-1] == {"type": "clear_overlay", "target_slot": "right"}
    assert timer.main(["cancel"]) == 0
    assert capsys.readouterr().out.strip() == "動いているタイマーはないよ。"
    assert timer.main(["list"]) == 0
    assert capsys.readouterr().out.strip() == "動いているタイマーはないよ。"


def test_fire_notifies_and_updates_screen(timer, capsys, monkeypatch):
    """時間になったら通知（音と読み上げ）を送り、そのタイマーを画面から消す。"""
    clock = [1000.0]
    monkeypatch.setattr(timer.time, "time", lambda: clock[0])
    _start(timer, capsys, "--seconds", "20", "--label", "テスト")
    _start(timer, capsys, "--minutes", "5")
    first_id = timer.calls["schedule"][0][0]

    clock[0] = 1020.5
    assert timer.main(["fire", "--id", first_id]) == 0
    assert capsys.readouterr().out.strip() == "テストのタイマーが終わったよ。"
    notice = timer.calls["events"][-1]
    assert notice["type"] == "notification" and notice["title"] == "テストのタイマーが終わったよ"
    assert notice["text"] == "20秒たちました。" and notice["sound"] and notice["speak"]
    overlay = timer.calls["events"][-2]
    assert overlay["type"] == "overlay" and "テスト" not in urllib.parse.unquote(overlay["url"])

    # 止めた後に systemd が呼んでも、知らせない。
    assert timer.main(["fire", "--id", first_id]) == 0
    assert capsys.readouterr().out.strip() == "そのタイマーはもう止まっているよ。"


def test_limits_and_errors(timer, capsys, monkeypatch):
    """同時に10個まで。範囲外の時間や、画面の更新の失敗は分かるように扱う。"""
    for _ in range(timer.MAX_TIMERS):
        _start(timer, capsys, "--minutes", "5")
    assert timer.main(["start", "--minutes", "5"]) == 1
    assert "10個まで" in capsys.readouterr().err
    assert timer.main(["start"]) == 1
    capsys.readouterr()

    def broken(payload):
        """ダッシュボードにつながらない場合。"""
        raise OSError("connection refused")

    monkeypatch.setattr(timer, "post_event", broken)
    assert timer.main(["cancel"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "タイマーを全部止めたよ。"
    assert "画面を更新できませんでした" in captured.err


def test_broken_state_file_is_reset(timer, capsys):
    """壊れた状態ファイルは、空として扱う。"""
    timer.STATE_PATH.write_text("{bad", encoding="utf-8")
    assert timer.main(["list"]) == 0
    assert capsys.readouterr().out.strip() == "動いているタイマーはないよ。"


def test_systemd_and_dashboard_calls(tmp_path, monkeypatch):
    """systemd-run の引数と、ダッシュボードへの送信先・トークン。"""
    path = ROOT / "skills/timer/scripts/argos_timer.py"
    spec = importlib.util.spec_from_file_location("argos_timer_raw", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runs = []
    monkeypatch.setattr(module.subprocess, "run", lambda argv, **kw: runs.append((argv, kw)))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    module.schedule("abc", 90)
    module.unschedule("abc")
    argv, kwargs = runs[0]
    assert argv[:2] == ["systemd-run", "--user"]
    assert "--unit=argos-timer-abc" in argv and "--on-active=90s" in argv
    assert argv[-3:] == ["fire", "--id", "abc"]
    assert kwargs["env"]["XDG_RUNTIME_DIR"].startswith("/run/user/")
    assert runs[1][0] == ["systemctl", "--user", "stop", "argos-timer-abc.timer"]

    monkeypatch.setenv("ARGOS_DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("ARGOS_DASHBOARD_PORT", "9999")
    monkeypatch.setenv("ARGOS_DASHBOARD_TOKEN", "tok")
    assert module._dashboard() == ("http://127.0.0.1:9999/api/events", "tok")
    sent = []

    class Response:
        """urlopen の戻り値の代わり。"""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"{}"

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda request, timeout: sent.append(request) or Response())
    module.post_event({"type": "clear_overlay"})
    assert sent[0].get_header("Authorization") == "Bearer tok"
    assert sent[0].full_url == "http://127.0.0.1:9999/api/events"


def test_router_definitions_use_stdout_reply():
    """同梱のタイマー定義が読め、返事にはスクリプトの出力を使う。省いた引数は渡さない。"""
    tools = ToolRegistry((ROOT / "skills",)).tools()
    start = tools["timer_start"]
    argv = start.build_argv(start.validate_args({"minutes": 3}))
    assert argv[-2:] == ["start", "--minutes=3"]
    assert start.format_reply({}, "途中の行\nパスタ、3分のタイマーを始めたよ。\n") == "パスタ、3分のタイマーを始めたよ。"
    assert tools["timer_list"].format_reply({}, "") == ""
    assert {"timer_list", "timer_cancel"} <= set(tools)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.jsがない環境では実行しない")
def test_timers_page_javascript():
    """一覧ページの残り時間の書き方と並び順を、Node.js上で確かめる。"""
    result = subprocess.run(["node", "--test", "tests/js/test_timers_page.js"], cwd=ROOT,
                            capture_output=True, check=False, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
