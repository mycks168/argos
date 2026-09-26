"""通知の読み上げ文の組み立てと、音声合成が使えないときの保存音声・警告音。

運転中は画面の通知に気づけないため、通知を声でも知らせる。ただし、音声合成そのものが
壊れているときは、音声合成では知らせられないので、あらかじめ作って保存した短い音声と、
生成した警告音で知らせる。
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# エラー通知の発信元ごとの、読み上げる決まった言葉。
# ここにない発信元のエラーは読み上げない。代替手段で動き続けたVOICEVOXやstt-gatewayの失敗や、
# エージェントの失敗（別に読み上げる）を、二重に知らせないため。
ERROR_PHRASES: dict[str, str] = {
    "音声合成": "声を作れなかったよ。",
    "音声認識": "音声を認識できなかったよ。",
    "文字起こし": "音声を認識できなかったよ。",
    "音声再生": "音声を再生できなかったよ。",
}

ERROR_TITLE_SUFFIX = "エラー"
_URL_PATTERN = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class SpeechPlan:
    """通知1件の読み上げ方。keyは連続して読み上げないための識別子。"""

    key: str
    text: str
    is_error: bool


def should_speak(notice: dict[str, Any]) -> bool:
    """読み上げる対象の通知か判定する。

    ダッシュボードで通知欄を自動で開く対象と同じ基準にする。ARGOS自身の通常の通知
    （音声入力の開始、ミュートなど）は頻繁に出るので読まない。外部の通知と、優先度が
    高い通知は対象。
    """
    source = str(notice.get("source", "")).strip().upper()
    priority = str(notice.get("priority", "normal")).strip().lower()
    return not (source == "ARGOS" and priority != "high")


def _is_error(notice: dict[str, Any]) -> bool:
    """内部エラーの通知（優先度が高く、題名が「〜 エラー」）か判定する。"""
    return str(notice.get("priority", "")).lower() == "high" and str(notice.get("title", "")).endswith(ERROR_TITLE_SUFFIX)


def _shorten(text: str, max_chars: int) -> str:
    """読み上げ向けにURLと改行を除き、指定文字数までに切り詰める。"""
    plain = re.sub(r"\s+", " ", _URL_PATTERN.sub("", text)).strip()
    if len(plain) <= max_chars:
        return plain
    return plain[:max_chars].rstrip() + "、以下省略。"


def build_plan(notice: dict[str, Any], max_chars: int = 60) -> SpeechPlan | None:
    """通知から読み上げ方を作る。読み上げない通知ならNoneを返す。"""
    if not should_speak(notice):
        return None
    source = str(notice.get("source", "")).strip()
    title = str(notice.get("title", "")).strip()
    if _is_error(notice):
        # 題名は「<発信元> エラー」。決まった言葉がある発信元だけ読み上げる。
        phrase = ERROR_PHRASES.get(source)
        return SpeechPlan(key=f"error:{source}", text=phrase, is_error=True) if phrase else None
    body = _shorten(str(notice.get("text", "")), max_chars)
    heading = _shorten(title, max_chars)
    if not heading and not body:
        # 読み上げる中身がない通知は、「通知だよ」だけにならないよう、読み上げない。
        return None
    parts = [f"{source}から通知だよ。" if source else "通知だよ。", f"{heading}。" if heading else ""]
    if body:
        parts.append(body if body.endswith(("。", "！", "？")) else f"{body}。")
    return SpeechPlan(key=f"notice:{source}:{title}", text="".join(parts), is_error=False)


def is_valid_wav(data: bytes | None) -> bool:
    """音声として再生できるWAV（PCMで、1フレーム以上ある）か判定する。"""
    if not data:
        return False
    try:
        with wave.open(io.BytesIO(data), "rb") as wav_file:
            return wav_file.getnframes() > 0 and wav_file.getframerate() > 0
    except (wave.Error, EOFError):
        return False


class PhraseStore:
    """決まった言葉の音声(WAV)を、話者ごとにファイルとして保存する。

    音声合成が使えないときも、保存した音声なら再生できる。この保存先には、上限や
    有効期限による自動削除がなく、利用者がディレクトリを消さない限り残る（一般の
    音声キャッシュのように、しばらく使わないと消えることはない）。壊れたファイルは
    保存も再生もしない。
    """

    def __init__(self, directory: Path) -> None:
        """保存先のディレクトリを保持する。作成は最初の保存時に行う。"""
        self._directory = directory

    def _path(self, text: str, speaker: int) -> Path:
        """言葉と話者から保存先のファイルを決める。"""
        digest = hashlib.sha1(f"{speaker}:{text}".encode()).hexdigest()[:20]
        return self._directory / f"{digest}.wav"

    def get(self, text: str, speaker: int) -> bytes | None:
        """保存済みの音声を返す。ないとき、壊れているときは、Noneを返す（作り直す対象になる）。"""
        try:
            data = self._path(text, speaker).read_bytes()
        except OSError:
            return None
        return data if is_valid_wav(data) else None

    def put(self, text: str, speaker: int, wav_data: bytes) -> bool:
        """音声を保存する。再生できない音声は保存せずFalseを返す。

        途中で切れたファイルを残さないよう、一時ファイル経由で置き換える。
        """
        if not is_valid_wav(wav_data):
            return False
        path = self._path(text, speaker)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(wav_data)
        temporary.replace(path)
        return True

    def has(self, text: str, speaker: int) -> bool:
        """保存済みかを返す。"""
        return self.get(text, speaker) is not None


def build_error_tone(sample_rate: int = 48000) -> bytes:
    """音声合成が使えないときに鳴らす、下がる2音の警告音を16bit PCM WAVとして生成する。"""
    frames = bytearray()
    for start, length, frequency in ((0.0, 0.16, 880.0), (0.22, 0.26, 660.0)):
        pad = int(sample_rate * start) - len(frames) // 2
        frames.extend(b"\x00\x00" * max(0, pad))
        count = int(sample_rate * length)
        for index in range(count):
            t = index / sample_rate
            envelope = min(t / 0.01, 1.0) * min((length - t) / 0.05, 1.0)
            sample = int(9000 * envelope * math.sin(2 * math.pi * frequency * t))
            frames.extend(struct.pack("<h", max(-32768, min(32767, sample))))
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(frames))
        return buffer.getvalue()
