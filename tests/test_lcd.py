from pathlib import Path

from argos.config import Settings, CodexSlot
from argos.hardware.lcd import St7789TextDisplay, disable_color_inversion, load_ipa_font, render_text_image, wrap_text


def _settings():
    return Settings(
        stt_gateway_url="http://stt",
        stt_language="ja",
        tts_filter_url="",
        tts_filter_token="",
        tts_delimiters="。！？!?",
        voicevox_url="http://voicevox",
        voicevox_speaker=2,
        voicevox_sample_rate=48000,
        voicevox_speed_scale=1.0,
        audio_input_device="in",
        audio_output_device="out",
        audio_output_card="",
        audio_output_volume=90,
        audio_sample_rate=16000,
        lcd_enabled=True,
        lcd_width=76,
        lcd_height=284,
        lcd_x_offset=82,
        lcd_y_offset=18,
        lcd_dc_pin="D25",
        lcd_cs_pin="D5",
        lcd_reset_pin="D24",
        lcd_baudrate=4_000_000,
        lcd_font_path="",
        lcd_font_size=16,
        dashboard_enabled=False,
        dashboard_host="127.0.0.1",
        dashboard_port=8765,
        dashboard_token="",
        ptt_gpio=17,
        silence_rms_threshold=200,
        dry_run=True,
        codex_slots=(CodexSlot("作業", "/tmp", "", ""),),
        codex_sandbox="workspace-write",
        codex_bypass_sandbox=False,
        codex_approval_policy="on-request",
        codex_extra_args=(),
    )


def test_load_ipa_font_finds_system_font():
    """IPA系フォントを読み込める。"""
    font = load_ipa_font("", 16)

    assert Path(font.path).exists()


def test_wrap_text_splits_long_japanese_text():
    """日本語テキストを表示幅で折り返せる。"""
    font = load_ipa_font("", 16)

    lines = wrap_text("これはLCDに表示する長い日本語テキストです", font, 80)

    assert len(lines) > 1


def test_render_text_image_returns_physical_lcd_size():
    """横向き内容を物理解像度76x284へ回転して返す。"""
    settings = _settings()
    font = load_ipa_font("", settings.lcd_font_size)

    image = render_text_image("こんにちは、ARGOSです", settings, font)

    assert image.size == (76, 284)


class FakeDisplay:
    """LCDへ送られた画像を記録する。"""

    def __init__(self):
        """送信履歴を初期化する。"""
        self.images = []

    def image(self, image, rotation=0):
        """画像と回転指定を保存する。"""
        self.images.append((image, rotation))


def test_text_display_keeps_recent_history():
    """複数回の表示で直近行だけを履歴として保持する。"""
    settings = _settings()
    font = load_ipa_font("", settings.lcd_font_size)
    display = St7789TextDisplay(FakeDisplay(), settings, font)

    display.show_text("一行目です")
    display.show_text("二行目です")
    display.show_text("三行目です")
    display.show_text("四行目です")
    display.show_text("五行目です")

    assert display._lines[-1] == "五行目です"
    assert len(display._lines) <= 3


def test_disable_color_inversion_sends_st7789_command():
    """黒背景を正しく出すため色反転OFFコマンドを送る。"""
    calls = []
    display = type("FakeSt7789", (), {"write": lambda self, command, data: calls.append((command, data))})()

    disable_color_inversion(display)

    assert calls == [(0x20, None)]
