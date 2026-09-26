"""他アプリの音声再生を監視し、ARGOSの発話を保留するための部品のテスト。"""

import json
import os
import subprocess
import time

import pytest

from argos.hardware import audio_priority
from argos.hardware.audio_priority import PriorityStreamWatcher, StreamActivityTracker, iter_json_elements


def node(node_id, app="Waydroid", media_class="Stream/Output/Audio", state=None, with_props=True):
    """pw-dumpのNode更新を模した要素を作る。"""
    info = {}
    if with_props:
        info["props"] = {"media.class": media_class, "application.name": app}
    if state:
        info["state"] = state
    return {"id": node_id, "type": "PipeWire:Interface:Node", "info": info}


def removed(node_id):
    """削除は info が null で届く。"""
    return {"id": node_id, "info": None}


class TestTracker:
    """再生ストリームの動作判定。"""

    def test_running_playback_of_target_is_active(self):
        """対象アプリの再生ストリームがrunningなら動作中になる。"""
        tracker = StreamActivityTracker(["Waydroid"])
        assert tracker.feed(node(1, state="suspended")) is False
        assert tracker.feed(node(1, state="running", with_props=False)) is True
        assert tracker.active is True

    def test_removal_and_idle_end_activity(self):
        """削除やidleで動作中でなくなる。"""
        tracker = StreamActivityTracker(["waydroid"])
        tracker.feed(node(1, state="running"))
        assert tracker.feed(node(1, state="idle", with_props=False)) is True
        assert tracker.active is False
        tracker.feed(node(1, state="running", with_props=False))
        assert tracker.feed(removed(1)) is True
        assert tracker.active is False

    @pytest.mark.parametrize(
        "element",
        [
            node(1, app="Chromium", state="running"),
            node(1, media_class="Stream/Input/Audio", state="running"),
            node(1, media_class="Audio/Sink", state="running"),
        ],
    )
    def test_other_apps_and_classes_are_ignored(self, element):
        """別のアプリ・マイク入力・シンクの動作は無視する。"""
        tracker = StreamActivityTracker(["Waydroid"])
        assert tracker.feed(element) is False
        assert tracker.active is False

    def test_names_are_case_insensitive_and_blank_ignored(self):
        """アプリ名の大文字小文字は区別せず、空の名前は無視する。"""
        tracker = StreamActivityTracker([" WAYDROID ", "", "  "])
        tracker.feed(node(2, app="waydroid", state="running"))
        assert tracker.active is True

    def test_multiple_streams_need_all_to_stop(self):
        """複数のストリームは、全てが止まるまで動作中のまま。"""
        tracker = StreamActivityTracker(["Waydroid"])
        tracker.feed(node(1, state="running"))
        tracker.feed(node(2, state="running"))
        tracker.feed(removed(1))
        assert tracker.active is True
        tracker.feed(removed(2))
        assert tracker.active is False

    def test_invalid_elements_and_reset(self):
        """idのない要素は無視し、resetで状態を捨てる。"""
        tracker = StreamActivityTracker(["Waydroid"])
        assert tracker.feed({"info": {}}) is False
        assert tracker.feed(removed(99)) is False
        tracker.feed(node(1, state="running"))
        tracker.reset()
        assert tracker.active is False


class TestJsonElements:
    """閉じていないJSON配列の要素抽出。"""

    def test_yields_completed_elements_and_keeps_partial(self):
        """完成した要素だけ取り出し、途中の要素は残す。"""
        text = '[\n{"id": 1},\n{"id": 2},\n{"id": 3, "info": {"sta'
        elements = list(iter_json_elements(text))
        assert [element["id"] for element, _ in elements] == [1, 2]
        assert elements[-1][1].lstrip(" ,\n").startswith('{"id": 3')

    def test_ignores_non_objects(self):
        """オブジェクト以外は読み飛ばす。"""
        assert [e["id"] for e, _ in iter_json_elements('[1, {"id": 5}, "x"]')] == [5]

    def test_empty_input(self):
        """空や区切りだけなら何も返さない。"""
        assert list(iter_json_elements("")) == []
        assert list(iter_json_elements(" [ , ")) == []


class FakeClock:
    """進められる時計。"""

    def __init__(self):
        """0秒から始める。"""
        self.now = 0.0

    def __call__(self):
        """現在の時刻を返す。"""
        return self.now


class TestWatcher:
    """保留と再開の通知。"""

    def make(self, delay=0.6):
        """通知を記録するウォッチャーと時計を作る。"""
        events = []
        clock = FakeClock()
        watcher = PriorityStreamWatcher(
            ["Waydroid"], lambda: events.append("hold"), lambda: events.append("release"), release_delay=delay, clock=clock
        )
        return watcher, events, clock

    def test_hold_on_start_and_release_after_delay(self):
        """動き始めたら保留し、止まって待ち時間がたってから再開する。"""
        watcher, events, clock = self.make()
        watcher.handle(node(1, state="running"))
        assert events == ["hold"] and watcher.holding
        watcher.handle(removed(1))
        assert events == ["hold"]
        clock.now = 0.5
        watcher.evaluate()
        assert events == ["hold"]
        clock.now = 0.7
        watcher.evaluate()
        assert events == ["hold", "release"] and not watcher.holding

    def test_gap_between_announcements_does_not_release(self):
        """案内が続けて鳴るときの短いすき間では再開しない。"""
        watcher, events, clock = self.make()
        watcher.handle(node(1, state="running"))
        watcher.handle(removed(1))
        clock.now = 0.3
        watcher.handle(node(2, state="running"))
        clock.now = 5.0
        watcher.evaluate()
        assert events == ["hold"]
        watcher.handle(removed(2))
        clock.now = 6.0
        watcher.evaluate()
        assert events == ["hold", "release"]

    def test_no_duplicate_notifications(self):
        """同じ状態では二重に通知しない。"""
        watcher, events, _ = self.make()
        watcher.handle(node(1, state="running"))
        watcher.handle(node(2, state="running"))
        assert events == ["hold"]
        watcher.evaluate()
        assert events == ["hold"]

    def test_evaluate_when_idle_does_nothing(self):
        """保留していなければ何も通知しない。"""
        watcher, events, _ = self.make()
        watcher.evaluate()
        assert events == []

    def test_callback_error_does_not_break_watcher(self):
        """通知先が例外を出しても、監視は続く。"""
        calls = []

        def broken():
            calls.append("hold")
            raise RuntimeError("失敗")

        watcher = PriorityStreamWatcher(["Waydroid"], broken, lambda: calls.append("release"), release_delay=0.0, clock=FakeClock())
        watcher.handle(node(1, state="running"))
        watcher.handle(removed(1))
        assert calls == ["hold", "release"]

    def test_stop_releases_hold(self):
        """停止時に保留していれば解除する。"""
        watcher, events, _ = self.make()
        watcher.handle(node(1, state="running"))
        watcher.stop()
        assert events == ["hold", "release"]


class FakeProcess:
    """pw-dumpの出力を、実際のパイプ経由で流すプロセスの代役。"""

    def __init__(self, chunks):
        """流す出力を保持する。"""
        self.read_fd, self.write_fd = os.pipe()
        self.stdout = os.fdopen(self.read_fd, "rb", buffering=0)
        self.terminated = False
        self._chunks = chunks
        self._closed = False

    def start(self):
        """出力を書き込んで閉じる。"""
        for chunk in self._chunks:
            os.write(self.write_fd, chunk)
        os.close(self.write_fd)
        self._closed = True

    def poll(self):
        """出力を閉じたあとは終了扱いにする。"""
        return 0 if self._closed else None

    def terminate(self):
        """終了要求を記録する。"""
        self.terminated = True
        if not self._closed:
            os.close(self.write_fd)
            self._closed = True


def wait_for(condition, timeout=3.0):
    """条件が満たされるまで待つ。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class TestWatcherThread:
    """pw-dumpの出力を読むスレッドの動作。"""

    def test_reads_stream_and_notifies(self, monkeypatch):
        """パイプで流れてきた要素を読み、保留と再開を通知する。"""
        monkeypatch.setattr(audio_priority.shutil, "which", lambda name: f"/usr/bin/{name}")
        events = []
        stream = json.dumps([node(7, state="running")])[:-1].encode() + b", "
        # 要素の途中でUTF-8が切れても読めることを確かめるため、日本語を含めて途中で分割する。
        tail = json.dumps(removed(7)).encode()
        proc = FakeProcess([stream, tail[:5], tail[5:]])
        launched = []

        def popen(command, **kwargs):
            """起動コマンドを記録して、用意したプロセスを返す。"""
            launched.append(command)
            proc.start()
            return proc

        watcher = PriorityStreamWatcher(
            ["Waydroid"], lambda: events.append("hold"), lambda: events.append("release"), release_delay=0.0, restart_delay=0.05, popen=popen
        )
        watcher.start()
        assert wait_for(lambda: events == ["hold", "release"])
        watcher.stop()
        assert launched[0][:3] == ["stdbuf", "-oL", "pw-dump"] and "-m" in launched[0]

    def test_reconnects_and_releases_when_process_exits(self, monkeypatch):
        """pw-dumpが終了したら保留を解除し、再接続する。"""
        monkeypatch.setattr(audio_priority.shutil, "which", lambda name: f"/usr/bin/{name}")
        events = []
        launches = []
        payloads = [[json.dumps([node(3, state="running")])[:-1].encode() + b", "], []]

        def popen(command, **kwargs):
            """1回目は動作中のまま終了、2回目は何も流さない。"""
            proc = FakeProcess(payloads[min(len(launches), 1)])
            launches.append(command)
            proc.start()
            return proc

        watcher = PriorityStreamWatcher(
            ["Waydroid"], lambda: events.append("hold"), lambda: events.append("release"), release_delay=5.0, restart_delay=0.05, popen=popen
        )
        watcher.start()
        assert wait_for(lambda: len(launches) >= 2)
        watcher.stop()
        assert events[:2] == ["hold", "release"]

    def test_popen_failure_retries_without_crashing(self, monkeypatch):
        """pw-dumpを起動できなくても、記録して再試行する。"""
        monkeypatch.setattr(audio_priority.shutil, "which", lambda name: f"/usr/bin/{name}")
        attempts = []

        def popen(command, **kwargs):
            """起動失敗を模擬する。"""
            attempts.append(command)
            raise OSError("起動できません")

        watcher = PriorityStreamWatcher(["Waydroid"], lambda: None, lambda: None, restart_delay=0.02, popen=popen)
        watcher.start()
        assert wait_for(lambda: len(attempts) >= 2)
        watcher.stop()

    def test_start_is_skipped_without_pw_dump(self, monkeypatch):
        """pw-dumpがない環境では、スレッドを作らない。"""
        monkeypatch.setattr(audio_priority.shutil, "which", lambda name: None)
        watcher = PriorityStreamWatcher(["Waydroid"], lambda: None, lambda: None, popen=lambda *a, **k: pytest.fail("起動しないはず"))
        watcher.start()
        assert watcher._thread is None

    def test_command_without_stdbuf(self, monkeypatch):
        """stdbufがなければ、pw-dumpだけで起動する。"""
        monkeypatch.setattr(audio_priority.shutil, "which", lambda name: None if name == "stdbuf" else f"/usr/bin/{name}")
        watcher = PriorityStreamWatcher(["Waydroid"], lambda: None, lambda: None)
        assert watcher._command() == ["pw-dump", "-m"]

    def test_second_start_does_not_duplicate_thread(self, monkeypatch):
        """すでに動いているときのstartは、スレッドを増やさない。"""
        monkeypatch.setattr(audio_priority.shutil, "which", lambda name: f"/usr/bin/{name}")
        watcher = PriorityStreamWatcher(["Waydroid"], lambda: None, lambda: None, restart_delay=0.05, popen=lambda *a, **k: FakeProcess([]))
        watcher.start()
        first = watcher._thread
        watcher.start()
        assert watcher._thread is first
        watcher.stop()


def test_real_pipe_with_subprocess_is_readable():
    """実際のサブプロセスの標準出力も、同じ読み方で読める（fdの扱いの確認）。"""
    proc = subprocess.Popen(["printf", '[{"id": 1}, '], stdout=subprocess.PIPE)
    data = os.read(proc.stdout.fileno(), 100)
    proc.wait()
    assert [e["id"] for e, _ in iter_json_elements(data.decode())] == [1]
