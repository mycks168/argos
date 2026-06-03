from argos.services.security_alert import SecurityAlertDispatcher


def test_dispatch_without_command_is_noop():
    """コマンド未設定なら何もしない。"""
    result = SecurityAlertDispatcher("").dispatch("本人確認", "失敗")

    assert result.executed is False
    assert result.succeeded is True


def test_dispatch_runs_configured_command(tmp_path):
    """設定済みコマンドを実行する。"""
    output_path = tmp_path / "alert.txt"
    dispatcher = SecurityAlertDispatcher(f"printf '%s' '{{source}}:{{message}}' > {output_path}")

    result = dispatcher.dispatch("本人確認", "失敗")

    assert result.executed is True
    assert result.succeeded is True
    assert output_path.read_text() == "本人確認:失敗"


def test_dispatch_reports_command_failure():
    """コマンド失敗を結果で返す。"""
    result = SecurityAlertDispatcher("false").dispatch("本人確認", "失敗")

    assert result.executed is True
    assert result.succeeded is False
