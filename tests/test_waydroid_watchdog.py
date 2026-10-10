"""Waydroidの音声の部品の見張りのテスト。"""

import json

import pytest

from argos.tools import waydroid_watchdog as wd
from argos.tools.waydroid_watchdog import Watchdog, parse_status

HEALTHY = "226 235\nService media.audio_flinger: found\n"
NOT_REGISTERED = "72306 72303\nService media.audio_flinger: not found\n"
RUNNING = "Session:\tRUNNING\nContainer:\tRUNNING\nVendor type:\tMAINLINE\n"
FROZEN = "Session:\tRUNNING\nContainer:\tFROZEN\n"
STOPPED = "Session:\tSTOPPED\n"


class Env:
    """外部コマンドと時計を偽物にした環境。"""

    def __init__(self, tmp_path, **overrides):
        """既定は、起動から十分たった、正常なWaydroid。"""
        self.status = RUNNING
        self.pidof = HEALTHY
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
    env.pidof = "235\nService media.audio_flinger: found\n"
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
    env.pidof = HEALTHY
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

        def __init__(self, restart, **kwargs):
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

        def __init__(self, restart, **kwargs):
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


class WindowEnv(Env):
    """地図のウィンドウの有無を指定できる環境。"""

    def __init__(self, tmp_path, **overrides):
        """既定は、ウィンドウが無い状態。"""
        self.missing = True
        self.restored = 0
        super().__init__(tmp_path, window_missing=lambda: self.missing, restore_window=self.restore, **overrides)

    def restore(self):
        """出し直しの呼び出しを数える。"""
        self.restored += 1


def test_window_is_restored_after_consecutive_misses(tmp_path):
    """ウィンドウが続けて見つからなければ、出し直す。1〜2回見つからないだけでは何もしない。"""
    env = WindowEnv(tmp_path)
    assert [env.dog.tick() for _ in range(3)] == ["window-suspect", "window-suspect", "window-restored"]
    assert env.restored == 1 and env.restarts == 0


def test_short_disappearance_is_ignored(tmp_path):
    """ナビ終了で一時的に閉じて、すぐ開き直した場合は、出し直さない（数え直し）。"""
    env = WindowEnv(tmp_path)
    env.dog.tick()
    env.dog.tick()
    env.missing = False
    assert env.dog.tick() == "ok"
    env.missing = True
    assert env.dog.tick() == "window-suspect" and env.restored == 0


def test_window_restore_has_cooldown_and_hourly_limit(tmp_path):
    """出し直しても直らないとき、間隔を置き、時間あたりの回数に上限がある。"""
    env = WindowEnv(tmp_path, window_cooldown=120.0, max_window_restores=2, window_threshold=1)
    assert env.dog.tick() == "window-restored"
    env.now += 60
    assert env.dog.tick() == "window-wait"
    env.now += 100
    assert env.dog.tick() == "window-restored"
    env.now += 200
    assert env.dog.tick() == "window-limited" and env.restored == 2
    env.now += 4000
    assert env.dog.tick() == "window-restored"


def test_window_not_checked_when_not_applicable(tmp_path):
    """判断できない状態（地図を出さない配置など）や、機能を渡していないときは、何もしない。"""
    env = WindowEnv(tmp_path, window_threshold=1)
    env.dog._window_missing = lambda: None
    assert env.dog.tick() == "ok" and env.restored == 0
    plain = Env(tmp_path / "plain")
    assert plain.dog.tick() == "ok"
    no_restore = WindowEnv(tmp_path / "nr", window_threshold=1)
    no_restore.dog._restore_window = None
    assert no_restore.dog.tick() == "window-suspect"


def test_window_check_waits_for_audio_health(tmp_path):
    """音声の部品が壊れているときは、ウィンドウより先に再起動の判断を行い、ウィンドウは見ない。"""
    env = WindowEnv(tmp_path, fail_threshold=1)
    env.pidof = None
    assert env.dog.tick() == "restarted" and env.restored == 0


def _layout_env(monkeypatch, tmp_path, *, mode="split", package="pkg", found=0, argos=0, preflight_ok=True):
    """配置ツールと外部コマンドを偽物にして、ウィンドウ判定の環境を作る。"""
    from argos.tools import window_layout

    monkeypatch.setattr(wd, "STATE_DIR", tmp_path)
    monkeypatch.setattr(window_layout, "configured_package", lambda: package)
    monkeypatch.setattr(window_layout, "load_state", lambda path: {"mode": mode})
    monkeypatch.setattr(window_layout, "android_id", lambda pkg: f"waydroid.{pkg}")

    def fake_preflight():
        """labwcのセッションが無い状態を模擬する。"""
        if not preflight_ok:
            raise RuntimeError("labwcなし")
        return {}

    monkeypatch.setattr(window_layout, "preflight", fake_preflight)

    class Result:
        """コマンドの結果を模した代役。"""

        def __init__(self, code):
            """終了コードを保持する。"""
            self.returncode = code

    def run(command, **kwargs):
        """ARGOSの画面か、地図のウィンドウかで結果を分ける。"""
        return Result(argos if command[-1] == window_layout.ARGOS_MATCH else found)

    monkeypatch.setattr(wd.subprocess, "run", run)


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"found": 1}, True),
        ({"found": 0}, False),
        ({"found": 1, "argos": 1}, None),
        ({"mode": "argos", "found": 1}, None),
        ({"package": None, "found": 1}, None),
        ({"preflight_ok": False, "found": 1}, None),
    ],
)
def test_window_missing_detection(monkeypatch, tmp_path, kwargs, expected):
    """地図を出すはずの配置で、ウィンドウが無いときだけTrue。ARGOSだけの配置・未設定・ARGOSも無い・labwcなしは判断しない。"""
    _layout_env(monkeypatch, tmp_path, **kwargs)
    assert wd.window_missing() is expected


def test_window_missing_returns_none_on_config_errors(monkeypatch, tmp_path):
    """設定が不正・状態ファイルを読めないときは、判断しない。"""
    from argos.tools import window_layout

    monkeypatch.setattr(window_layout, "configured_package", lambda: (_ for _ in ()).throw(RuntimeError("未対応")))
    assert wd.window_missing() is None


def test_restore_window_shows_layout_and_restarts_only_stopped_services(monkeypatch):
    """出し直しは、配置を戻し、止まっている関連サービス（GPS中継など）だけ再開する。動いているものには触れない。"""
    from argos.tools import window_layout

    monkeypatch.setattr(window_layout, "setting", lambda name: "gps.service, running.service")
    calls = []

    class Result:
        """コマンドの結果を模した代役。"""

        def __init__(self, code):
            """終了コードを保持する。"""
            self.returncode = code

    def run(command, **kwargs):
        """gps.serviceだけ止まっている状態にする。"""
        calls.append(command)
        if "is-active" in command:
            return Result(3 if command[-1] == "gps.service" else 0)
        return Result(0)

    monkeypatch.setattr(wd.subprocess, "run", run)
    wd.restore_window()
    assert calls[0][-1] == "show" and calls[0][1:3] == ["-m", "argos.tools.window_layout"]
    started = [command[-1] for command in calls if "start" in command]
    assert started == ["gps.service"]


def test_processes_alive_but_audio_service_not_registered_is_broken(tmp_path):
    """プロセスが両方いても、音声の中心機能(audio_flinger)が登録されていなければ、壊れていると見なす。

    音声サーバーが落ちて再起動されても、音声HALを起動できず、機能が登録されない状態がある。
    """
    env = Env(tmp_path, fail_threshold=1)
    env.pidof = NOT_REGISTERED
    assert env.dog.tick() == "restarted" and env.restarts == 1


@pytest.mark.parametrize("output", ["226 235\n", "\n", "Service media.audio_flinger: found\n", "226\nService media.audio_flinger: found\n"])
def test_health_requires_both_processes_and_service(tmp_path, output):
    """プロセスが足りない、機能の確認結果がない、のいずれも、正常とは見なさない。"""
    env = Env(tmp_path)
    env.pidof = output
    assert env.dog.check_audio() is False
