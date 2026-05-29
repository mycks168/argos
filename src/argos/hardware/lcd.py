"""ST7789 LCD へのテキスト表示。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageDraw, ImageFont

from argos.config import Settings


log = logging.getLogger(__name__)

IPA_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf",
)
ST7789_INVERSION_OFF = 0x20


class Display(Protocol):
    """画像を表示できるLCDオブジェクト。"""

    def image(self, image: Image.Image, rotation: int = 0) -> None:
        """画像をLCDへ転送する。"""


class St7789TextDisplay:
    """ST7789 LCD に横向き日本語テキストを表示する。"""

    def __init__(self, display: Display, settings: Settings, font: ImageFont.FreeTypeFont) -> None:
        """表示器、設定、IPAフォントを保持する。"""
        self._display = display
        self._settings = settings
        self._font = font
        self._lock = threading.Lock()
        self._lines: list[str] = []

    @classmethod
    def create(cls, settings: Settings) -> "St7789TextDisplay":
        """設定からLCD表示器を初期化する。"""
        font = load_ipa_font(settings.lcd_font_path, settings.lcd_font_size)
        display = create_st7789_display(settings)
        return cls(display, settings, font)

    def show_text(self, text: str) -> None:
        """日本語テキストをLCDへ表示する。"""
        if not text:
            return
        with self._lock:
            logical_size = (self._settings.lcd_height, self._settings.lcd_width)
            max_lines = max_display_lines(logical_size, self._font)
            self._lines.extend(wrap_text(text, self._font, logical_size[0] - 12))
            self._lines = self._lines[-max_lines:]
            image = render_lines_image(self._lines, self._settings, self._font)
            self._display.image(image, rotation=0)


def load_ipa_font(font_path: str, font_size: int) -> ImageFont.FreeTypeFont:
    """IPA系フォントを読み込む。"""
    candidates = (font_path, *IPA_FONT_CANDIDATES) if font_path else IPA_FONT_CANDIDATES
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            log.info("LCD IPAフォントを使用します: %s", path)
            return ImageFont.truetype(str(path), font_size)
    raise RuntimeError("IPA系フォントが見つかりません。ARGOS_LCD_FONT_PATH を設定してください。")


def create_st7789_display(settings: Settings) -> Display:
    """Raspberry Pi のSPIから ST7789 表示オブジェクトを作成する。"""
    try:
        import board
        import digitalio
        from adafruit_rgb_display import st7789
    except ModuleNotFoundError as exc:
        raise RuntimeError("LCD用ライブラリが見つかりません。`uv sync` を実行してください。") from exc

    spi = board.SPI()
    cs = digitalio.DigitalInOut(_board_pin(board, settings.lcd_cs_pin))
    dc = digitalio.DigitalInOut(_board_pin(board, settings.lcd_dc_pin))
    reset = digitalio.DigitalInOut(_board_pin(board, settings.lcd_reset_pin))
    display = st7789.ST7789(
        spi,
        cs=cs,
        dc=dc,
        rst=reset,
        width=settings.lcd_width,
        height=settings.lcd_height,
        x_offset=settings.lcd_x_offset,
        y_offset=settings.lcd_y_offset,
        baudrate=settings.lcd_baudrate,
    )
    disable_color_inversion(display)
    return display


def disable_color_inversion(display: object) -> None:
    """ST7789 の色反転を無効にして黒背景を黒く表示する。"""
    display.write(ST7789_INVERSION_OFF, None)


def render_text_image(text: str, settings: Settings, font: ImageFont.FreeTypeFont) -> Image.Image:
    """LCDへ送る横向きテキスト画像を作成する。"""
    logical_size = (settings.lcd_height, settings.lcd_width)
    lines = wrap_text(text, font, logical_size[0] - 12)
    return render_lines_image(lines[-max_display_lines(logical_size, font):], settings, font)


def render_lines_image(lines: list[str], settings: Settings, font: ImageFont.FreeTypeFont) -> Image.Image:
    """複数行の履歴をLCDへ送る横向き画像として作成する。"""
    logical_size = (settings.lcd_height, settings.lcd_width)
    image = Image.new("RGB", logical_size, "#101820")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, logical_size[0] - 1, logical_size[1] - 1), outline="#f4d35e")
    line_height = font.size + 3
    for index, line in enumerate(lines):
        draw.text((6, 5 + index * line_height), line, fill="#f8f9fa", font=font)
    return image.rotate(90, expand=True)


def max_display_lines(logical_size: tuple[int, int], font: ImageFont.FreeTypeFont) -> int:
    """LCDに表示できる最大行数を返す。"""
    return max(1, (logical_size[1] - 10) // (font.size + 3))


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """日本語を含む文字列を表示幅で折り返す。"""
    lines: list[str] = []
    current = ""
    for char in text.replace("\n", " "):
        candidate = current + char
        if current and font.getlength(candidate) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _board_pin(board_module: object, name: str) -> object:
    """board モジュールから指定名のピンを取得する。"""
    try:
        return getattr(board_module, name)
    except AttributeError as exc:
        raise RuntimeError(f"board.{name} が見つかりません。LCDピン設定を確認してください。") from exc
