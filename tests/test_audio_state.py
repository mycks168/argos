import json

from argos.services.audio_state import AudioStateStore


def test_audio_state_store_saves_and_loads(tmp_path):
    """音量とミュート状態をJSONへ保存し、次回読み込める。"""
    path = tmp_path / "audio-state.json"
    store = AudioStateStore(str(path))

    store.save(42, True)
    state = store.load()

    assert state.volume == 42
    assert state.muted is True
    assert json.loads(path.read_text(encoding="utf-8")) == {"volume": 42, "muted": True}


def test_audio_state_store_ignores_broken_file(tmp_path):
    """壊れた保存値は起動を止めずに無視する。"""
    path = tmp_path / "audio-state.json"
    path.write_text("{broken", encoding="utf-8")
    store = AudioStateStore(str(path))

    state = store.load()

    assert state.volume is None
    assert state.muted is None


def test_audio_state_store_clamps_volume(tmp_path):
    """範囲外の音量は0から100に補正する。"""
    path = tmp_path / "audio-state.json"
    path.write_text('{"volume": 180, "muted": false}', encoding="utf-8")
    store = AudioStateStore(str(path))

    state = store.load()

    assert state.volume == 100
    assert state.muted is False
