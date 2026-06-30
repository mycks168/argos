from argos_reminder.cli import main


def test_cli_add_list_remove(monkeypatch, tmp_path, capsys):
    """CLIで追加、一覧、削除ができる。"""
    monkeypatch.setenv("ARGOS_REMINDER_STATE_PATH", str(tmp_path / "reminders.json"))

    assert main(["add", "2026-06-19 18:30", "旅費申請"]) == 0
    add_output = capsys.readouterr().out
    reminder_id = add_output.split()[1]

    assert main(["list"]) == 0
    list_output = capsys.readouterr().out
    assert "旅費申請" in list_output

    assert main(["remove", reminder_id]) == 0
    assert "削除しました" in capsys.readouterr().out


def test_cli_add_location(monkeypatch, tmp_path, capsys):
    """CLIで位置リマインダーを追加できる。"""
    monkeypatch.setenv("ARGOS_REMINDER_STATE_PATH", str(tmp_path / "reminders.json"))

    assert main(["add-location", "到着", "--lat", "35.0", "--lon", "139.0"]) == 0
    add_output = capsys.readouterr().out
    assert "半径100m" in add_output

    assert main(["list"]) == 0
    list_output = capsys.readouterr().out
    assert "到着" in list_output
    assert "35.0,139.0 半径100m" in list_output
