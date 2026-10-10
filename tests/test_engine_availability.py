"""ローカルの音声合成・文字起こしが、導入済みかを試さずに判定できることのテスト。"""

import importlib.util

import pytest

from argos.services.stt.whisper import FasterWhisperClient
from argos.services.tts.kokoro import KokoroClient


def _find_spec(installed):
    """指定した名前だけ導入済みとして答える、find_specの代役。"""
    return lambda name, *args, **kwargs: object() if name in installed else None


@pytest.mark.parametrize("installed, expected", [({"kokoro"}, True), (set(), False)])
def test_kokoro_availability(monkeypatch, installed, expected):
    """Kokoroの導入有無を、読み込まずに判定する。"""
    monkeypatch.setattr(importlib.util, "find_spec", _find_spec(installed))
    assert KokoroClient("v", 1.0, "repo", 24000).available is expected


@pytest.mark.parametrize("installed, expected", [({"faster_whisper"}, True), (set(), False)])
def test_whisper_availability(monkeypatch, installed, expected):
    """faster-whisperの導入有無を、読み込まずに判定する。"""
    monkeypatch.setattr(importlib.util, "find_spec", _find_spec(installed))
    assert FasterWhisperClient("small", "ja", "cpu", "int8").available is expected


def test_loaded_engines_are_available_even_if_spec_lookup_fails(monkeypatch):
    """すでに読み込み済みなら、導入有無の判定によらず、使えると答える。"""
    monkeypatch.setattr(importlib.util, "find_spec", _find_spec(set()))
    kokoro = KokoroClient("v", 1.0, "repo", 24000)
    kokoro._pipeline = object()
    whisper = FasterWhisperClient("small", "ja", "cpu", "int8")
    whisper._model = object()
    assert kokoro.available and whisper.available
