"""ffmpeg を用いたブラウザ音声・Opusコーデック。

stt-gateway への送信サイズ削減と、Opus 対応 VOICEVOX ラッパーからの
応答デコードのために、WAV と Ogg Opus を相互変換する。
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class OpusCodecError(RuntimeError):
    """Opus のエンコード/デコードに失敗したときに送出する例外。"""


def encode_wav_to_opus(wav_data: bytes, bitrate: str = "24k") -> bytes:
    """WAV バイト列を Ogg Opus にエンコードして返す。"""
    return _run_ffmpeg(
        wav_data,
        ["-c:a", "libopus", "-b:a", bitrate, "-f", "ogg"],
        suffix=".opus",
    )


def decode_opus_to_wav(opus_data: bytes) -> bytes:
    """Ogg Opus バイト列を 16bit PCM WAV にデコードして返す。"""
    return decode_audio_to_wav(opus_data)


def decode_audio_to_wav(audio_data: bytes) -> bytes:
    """ffmpegが対応する音声バイト列を16bit PCM WAVにデコードして返す。"""
    return _run_ffmpeg(
        audio_data,
        ["-c:a", "pcm_s16le", "-f", "wav"],
        suffix=".wav",
    )


def _run_ffmpeg(input_data: bytes, output_args: list[str], suffix: str) -> bytes:
    """ffmpeg に stdin から入力を渡し、一時ファイル出力を読み取って返す。

    WAV 出力はヘッダのサイズ確定にシークが必要なため、pipe 出力ではなく
    一時ファイルへ書き出してから読み戻す。
    """
    with tempfile.NamedTemporaryFile(prefix="argos-opus-", suffix=suffix) as out_file:
        out_path = out_file.name
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            "pipe:0",
            *output_args,
            out_path,
        ]
        try:
            proc = subprocess.run(command, input=input_data, capture_output=True)
        except FileNotFoundError as exc:  # ffmpeg 未インストール
            raise OpusCodecError("ffmpeg が見つかりません") from exc
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace")[:300]
            raise OpusCodecError(f"ffmpeg 失敗 (code={proc.returncode}): {detail}")
        return Path(out_path).read_bytes()
