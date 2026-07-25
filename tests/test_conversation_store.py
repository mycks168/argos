"""会話履歴と引き継ぎ要約の永続化テスト。"""

from argos.services.conversation_store import ConversationStore


def test_conversation_store_round_trip_and_limit(tmp_path):
    """スロット別履歴を上限付きで保存・復元する。"""
    path = tmp_path / "conversation.json"
    store = ConversationStore(path, True, max_messages=2)
    store.save_histories(
        {
            "codex\0作業": [
                {"role": "user", "text": "1"},
                {"role": "assistant", "text": "2"},
                {"role": "user", "text": "3"},
            ]
        }
    )

    assert [item["text"] for item in store.load_histories()["codex\0作業"]] == ["2", "3"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_conversation_store_memory_is_consumable(tmp_path):
    """引き継ぎ要約を保存して削除できる。"""
    store = ConversationStore(tmp_path / "conversation.json", True)
    store.save_memory("slot", "重要な要約")
    assert store.load_memory("slot") == "重要な要約"
    store.clear_memory("slot")
    assert store.load_memory("slot") == ""


def test_disabled_conversation_store_does_not_write(tmp_path):
    """無効時は履歴ファイルを作らない。"""
    path = tmp_path / "conversation.json"
    store = ConversationStore(path, False)
    store.save_histories({"slot": [{"role": "user", "text": "秘密"}]})
    store.save_memory("slot", "秘密")
    assert not path.exists()
