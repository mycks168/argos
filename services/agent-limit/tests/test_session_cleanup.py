"""利用枠を調べたときの記録の片付けのテスト。"""

import json
from pathlib import Path

from session_cleanup import list_entries, remove_history_lines, remove_new_entries

WORKSPACE = "/opt/argos/services/agent-limit"
COMMANDS = {"/usage", "/exit"}


def history_line(display, timestamp, workspace=WORKSPACE):
    """履歴の1行を作る。"""
    return json.dumps({"display": display, "timestamp": timestamp, "workspace": workspace, "type": "slash_command"}) + "\n"


def test_remove_new_entries_keeps_existing(tmp_path):
    """起動前からあった記録は残し、今回できた記録（ファイルとフォルダ）だけを消す。"""
    (tmp_path / "old.jsonl").write_text("{}", encoding="utf-8")
    before = list_entries(tmp_path)
    (tmp_path / "new.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "tool.txt").write_text("x", encoding="utf-8")

    assert remove_new_entries(tmp_path, before) == ["new", "new.jsonl"]
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["old.jsonl"]


def test_remove_new_entries_without_directory(tmp_path):
    """記録の場所がまだないときは、何もしない。"""
    missing = tmp_path / "missing"
    assert list_entries(missing) == set()
    assert remove_new_entries(missing, set()) == []


def test_remove_new_entries_skips_undeletable(tmp_path, monkeypatch):
    """消せないものは残し、ほかは消す。"""
    (tmp_path / "a.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "b.jsonl").write_text("{}", encoding="utf-8")
    original = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == "a.jsonl":
            raise OSError("busy")
        return original(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert remove_new_entries(tmp_path, set()) == ["b.jsonl"]
    assert (tmp_path / "a.jsonl").exists()


def test_remove_history_lines_only_own_commands(tmp_path):
    """ディレクトリ・コマンド・時刻がすべて当てはまる行だけを取り除く。"""
    path = tmp_path / "history.jsonl"
    lines = [
        history_line("/usage", 100),  # 実行より前の利用者の操作
        history_line("/usage", 1000),  # 今回の実行
        history_line("/exit", 1500),  # 今回の実行
        history_line("/usage", 1200, workspace="/home/argos/work"),  # ほかのディレクトリ
        history_line("こんにちは", 1300),  # 同じ時間の、ほかのコマンド
        "壊れた行\n",
        "[1, 2]\n",
        history_line("/exit", 2500),  # 実行より後
    ]
    path.write_text("".join(lines), encoding="utf-8")
    path.chmod(0o600)

    assert remove_history_lines(path, workspace=WORKSPACE, commands=COMMANDS, start_ms=1000, end_ms=2000) == 2
    assert path.read_text(encoding="utf-8") == "".join(lines[0:1] + lines[3:])
    assert path.stat().st_mode & 0o777 == 0o600


def test_remove_history_lines_without_matches_or_file(tmp_path):
    """取り除く行がないときは書き換えず、ファイルがないときは何もしない。"""
    path = tmp_path / "history.jsonl"
    assert remove_history_lines(path, workspace=WORKSPACE, commands=COMMANDS, start_ms=0, end_ms=1) == 0
    path.write_text(history_line("/usage", 5), encoding="utf-8")
    before = path.stat().st_mtime_ns
    assert remove_history_lines(path, workspace=WORKSPACE, commands=COMMANDS, start_ms=10, end_ms=20) == 0
    assert path.stat().st_mtime_ns == before


def test_remove_history_lines_retries_when_file_changes(tmp_path, monkeypatch):
    """書き戻す前に別のCLIが書き込んだら、その行を消さないよう、読み直してやり直す。"""
    path = tmp_path / "history.jsonl"
    path.write_text(history_line("/usage", 1000), encoding="utf-8")
    waits = []

    def sleep(seconds):
        waits.append(seconds)

    original_stat = Path.stat
    calls = {"count": 0}

    def stat(self, *args, **kwargs):
        # 1回目の書き戻しの直前（2回目のstat）に、別のCLIが1行書き込んだ状態にする。
        calls["count"] += 1
        if self == path and calls["count"] == 2:
            with open(path, "a", encoding="utf-8") as other:
                other.write(history_line("利用者の入力", 1100, workspace="/home/argos/work"))
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert remove_history_lines(path, workspace=WORKSPACE, commands=COMMANDS, start_ms=0, end_ms=2000, sleep=sleep) == 1
    assert waits == [0.2]
    assert path.read_text(encoding="utf-8") == history_line("利用者の入力", 1100, workspace="/home/argos/work")
    assert not [name for name in list_entries(tmp_path) if name.startswith(".history")]


def test_remove_history_lines_gives_up_when_file_keeps_changing(tmp_path, monkeypatch):
    """ファイルが変わり続けるなら、書き換えずに諦める。"""
    path = tmp_path / "history.jsonl"
    path.write_text(history_line("/usage", 1000), encoding="utf-8")
    original_stat = Path.stat
    calls = {"count": 0}

    def stat(self, *args, **kwargs):
        calls["count"] += 1
        result = original_stat(self, *args, **kwargs)
        if self == path and calls["count"] % 2 == 0:
            with open(path, "a", encoding="utf-8") as other:
                other.write("x\n")
            result = original_stat(self, *args, **kwargs)
        return result

    monkeypatch.setattr(Path, "stat", stat)
    assert remove_history_lines(path, workspace=WORKSPACE, commands=COMMANDS, start_ms=0, end_ms=2000, sleep=lambda s: None) == 0
    assert history_line("/usage", 1000) in path.read_text(encoding="utf-8")
