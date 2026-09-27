"""Waydroidの音声の部品の見張りのテスト。"""

import json

import pytest

from argos.tools import waydroid_watchdog as wd
from argos.tools.waydroid_watchdog import Watchdog, parse_status

RUNNING = "Session:\tRUNNING\nContainer:\tRUNNING\nVendor type:\tMAINLINE\n"
FROZEN = "Session:\tRUNNING\nContainer:\tFROZEN\n"
STOPPED = "Session:\tSTOPPED\n"


class Env:
    """外部コマンドと時計を偽物にした環境。"""

    def __init__(self, tmp_path, **overrides):
        """既定は、起動から十分たった、正常なWaydroid。"""
        self.status = RUNNING
        self.pidof = "226 235\n"
        self.reachable = True
        self.now = 1000.0
        self.restarts = 0
        self.commands = []
        arguments = dict(
            run=self.run,
            restart=self.restart,
            clock=lambda: self.now,
            wall=lambda: 1_700_000_000.0 + self.now,
            incident_dir=tmp_path / "incidents",
            grace_seconds=0.0,
            sample_host=lambda: {"loadavg": [0.5, 0.5, 0.5]},
        )
        arguments.update(overrides)
        self.dog = Watchdog(**arguments)

    def run(self, command, timeout=8.0):
        """コマンドに応じた出力を返す。"""
        self.commands.append(command)
        if command[:2] == ["waydroid", "status"]:
            return self.status
        if command[:len(wd.LXC)] == wd.LXC:
            rest = command[len(wd.LXC):]
            if rest[:2] == ["sh", "-c"] and rest[2].startswith("pidof"):
                return self.pidof
            if rest == ["true"]:
                return "" if self.reachable else None
            return "記録の中身\n"
        if command[:3] == ["sudo", "-n", "journalctl"]:
            return "kernel log\n"
        return None

    def restart(self):
        """再起動の呼び出しを数える。"""
        self.restarts += 1


def test_parse_status():
    """waydroid statusの出力を、辞書にする。空やNoneでも落ちない。"""
    assert parse_status(RUNNING)["Container"] == "RUNNING"
    assert parse_status(None) == {} and parse_status("") == {}


def test_healthy_when_both_audio_processes_exist(tmp_path):
    """音声サーバーと音声HALが両方あれば、正常。"""
    assert Env(tmp_path).dog.tick() == "ok"


@pytest.mark.parametrize("status", [FROZEN, STOPPED])
def test_skips_when_frozen_or_stopped(tmp_path, status):
    """凍結中や停止中は、判断しない（コンテナに入れない・音声が動かないのは正常）。"""
    env = Env(tmp_path)
    env.status = status
    env.pidof = None
    assert env.dog.tick() == "skip" and env.restarts == 0


def test_grace_period_after_session_start(tmp_path):
    """起動して間もない間は、音声の部品が揃っていなくても、判断しない。"""
    env = Env(tmp_path, grace_seconds=120.0)
    env.pidof = None
    assert env.dog.tick() == "skip"
    env.now += 119
    assert env.dog.tick() == "skip"
    env.now += 2
    assert env.dog.tick() == "suspect"


def test_one_missing_process_is_broken(tmp_path):
    """片方だけ消えていても、壊れていると見なす（音声HALだけ戻らない状態がある）。"""
    env = Env(tmp_path)
    env.pidof = "235\n"
    assert env.dog.tick() == "suspect"


def test_restarts_after_consecutive_failures_and_saves_incident(tmp_path):
    """連続で壊れていたら、記録を残して再起動する。1回だけの失敗では再起動しない。"""
    env = Env(tmp_path)
    env.pidof = None
    assert env.dog.tick() == "suspect" and env.restarts == 0
    assert env.dog.tick() == "restarted" and env.restarts == 1
    folders = list((tmp_path / "incidents").iterdir())
    assert len(folders) == 1
    files = {path.name for path in folders[0].iterdir()}
    assert {"host-history.json", "summary.txt", "logcat-audio.txt", "tombstone.txt", "processes.txt", "kernel.txt"} <= files
    assert "action=restart" in (folders[0] / "summary.txt").read_text()
    history = json.loads((folders[0] / "host-history.json").read_text())
    assert len(history) == 2 and history[0]["loadavg"] == [0.5, 0.5, 0.5]


def test_recovery_resets_failure_count(tmp_path):
    """途中で正常に戻ったら、失敗の数え直し。"""
    env = Env(tmp_path)
    env.pidof = None
    env.dog.tick()
    env.pidof = "1 2\n"
    assert env.dog.tick() == "ok"
    env.pidof = None
    assert env.dog.tick() == "suspect" and env.restarts == 0


def test_unreachable_container_is_not_treated_as_broken(tmp_path):
    """コンテナに入れないだけ（音声の部品が消えたか不明）なら、再起動せずに、判断しない。"""
    env = Env(tmp_path)
    env.pidof = None
    env.reachable = False
    assert env.dog.tick() == "skip" and env.restarts == 0


def test_restart_is_rate_limited(tmp_path):
    """繰り返し壊れても、時間あたりの再起動の回数に上限があり、暴走しない。上限のときも記録は残す。"""
    env = Env(tmp_path, max_restarts=2, fail_threshold=1, window_seconds=3600.0)
    env.pidof = None
    results = []
    for _ in range(4):
        results.append(env.dog.tick())
        env.now += 200
    assert results == ["restarted", "restarted", "limited", "limited"] and env.restarts == 2
    summaries = [(path / "summary.txt").read_text() for path in (tmp_path / "incidents").iterdir()]
    assert any("limited" in text for text in summaries)
    env.now += 4000
    assert env.dog.tick() == "restarted"


def test_session_restart_resets_grace(tmp_path):
    """再起動のあとは、また猶予期間から数え直す。"""
    env = Env(tmp_path, grace_seconds=100.0, fail_threshold=1)
    env.now = 0.0
    env.dog.tick()
    env.now = 200.0
    env.pidof = None
    assert env.dog.tick() == "restarted"
    assert env.dog.tick() == "skip"


def test_incident_survives_unreadable_directory_and_missing_probes(tmp_path):
    """記録の保存に失敗しても、再起動は止めない。Android内の情報を取れなくても、記録は残る。"""
    blocked = tmp_path / "blocked"
    blocked.write_text("ファイルがあるので、ディレクトリを作れない")
    env = Env(tmp_path, incident_dir=blocked / "incidents", fail_threshold=1)
    env.pidof = None
    assert env.dog.tick() == "restarted" and env.restarts == 1

    env2 = Env(tmp_path / "second", fail_threshold=1)
    env2.pidof = None
    original = env2.run
    env2.dog._run = lambda command, timeout=8.0: None if command[-1].startswith(("logcat", "T=", "ps")) else original(command, timeout)
    env2.dog.tick()
    folder = next((tmp_path / "second" / "incidents").iterdir())
    assert "取得できませんでした" in (folder / "logcat-audio.txt").read_text()


def test_history_is_bounded(tmp_path):
    """ホストの様子の履歴は、決まった件数だけ残す。"""
    env = Env(tmp_path, history_size=3)
    for _ in range(10):
        env.dog.tick()
    assert len(env.dog._history) == 3


def test_default_host_sample_reads_system_state(monkeypatch):
    """ホストの様子（負荷・メモリ・音の流れ）を集める。pw-dumpが使えなくても落ちない。"""
    dump = json.dumps([
        {"type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {"media.class": "Stream/Output/Audio", "application.name": "Waydroid"}}},
        {"type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Sink"}}},
    ])
    monkeypatch.setattr(wd, "run_text", lambda command, timeout=8.0: dump)
    sample = Watchdog.default_host_sample()
    assert sample["streams"] == [["Waydroid", "Stream/Output/Audio", "running"]]
    assert {"loadavg", "mem_available_kb", "swap_free_kb"} <= set(sample)
    monkeypatch.setattr(wd, "run_text", lambda command, timeout=8.0: None)
    assert Watchdog.default_host_sample()["streams"] == "pw-dumpが応答しません"
    monkeypatch.setattr(wd, "run_text", lambda command, timeout=8.0: "not json")
    assert Watchdog.default_host_sample()["streams"] == "解析できません"


def test_run_text_handles_failures(monkeypatch):
    """コマンドが失敗・見つからない・タイムアウトしたら、Noneを返す。"""
    assert wd.run_text(["true"]) == ""
    assert wd.run_text(["false"]) is None
    assert wd.run_text(["command-that-does-not-exist-xyz"]) is None
    assert wd.run_text(["sleep", "5"], timeout=0.1) is None


def test_recover_restarts_with_current_size_then_shows_layout(monkeypatch):
    """復旧は、今の描画サイズのままWaydroidを再起動し、画面配置を戻す。サイズが分からなければ再起動しない。"""
    from argos.tools import window_layout

    calls = []
    monkeypatch.setattr(window_layout, "android_size", lambda: [1004, 416])
    monkeypatch.setattr(window_layout, "restart_android", lambda size: calls.append(("restart", size)))
    monkeypatch.setattr(wd.subprocess, "run", lambda command, **kwargs: calls.append(("run", command[-1])))
    wd.recover()
    assert calls == [("restart", [1004, 416]), ("run", "show")]
    monkeypatch.setattr(window_layout, "android_size", lambda: None)
    with pytest.raises(RuntimeError):
        wd.recover()


def test_main_once_reports_result(monkeypatch, capsys):
    """--onceは、1回だけ確認して、結果を出力し、異常なら終了コードを1にする。"""
    class FakeDog:
        """結果を指定できる見張り。"""

        result = "ok"

        def __init__(self, restart):
            """再起動関数を受け取る。"""

        def tick(self):
            """指定の結果を返す。"""
            return FakeDog.result

    monkeypatch.setattr(wd, "Watchdog", FakeDog)
    assert wd.main(["--once"]) == 0 and capsys.readouterr().out.strip() == "ok"
    FakeDog.result = "limited"
    assert wd.main(["--once"]) == 1


def test_main_loop_survives_errors(monkeypatch):
    """見張りの途中で例外が出ても、続ける（1回目は例外、2回目で停止のための例外）。"""
    class FlakyDog:
        """1回目は失敗し、2回目でループを抜けさせる見張り。"""

        calls = 0

        def __init__(self, restart):
            """再起動関数を受け取る。"""

        def tick(self):
            """1回目は例外、2回目は正常を返す。"""
            FlakyDog.calls += 1
            if FlakyDog.calls == 1:
                raise RuntimeError("失敗")
            return "ok"

    sleeps = []

    def stop_after_two(seconds):
        """眠りの回数を数え、3回目で抜ける。"""
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(wd, "Watchdog", FlakyDog)
    monkeypatch.setattr(wd.time, "sleep", stop_after_two)
    with pytest.raises(KeyboardInterrupt):
        wd.main(["--interval", "1"])
    assert FlakyDog.calls == 2 and sleeps == [1.0, 1.0]
