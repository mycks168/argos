from argos.config import AgentSlot, Settings
from argos.services.agent import create_agent_client
from argos.services.agent.client import RoutedAgentClient, SystemPromptAgentClient, SystemPromptStateStore, build_agent_prompt


def _settings() -> Settings:
    """テスト用の最小設定を返す。"""
    return Settings(
        agent_provider="codex",
        agent_state_path="~/.argos/agent-sessions.json",
        stt_gateway_url="http://stt",
        stt_language="ja",
        stt_gateway_token="",
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
        lcd_enabled=False,
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
        agent_slots=(AgentSlot("作業", "codex", "/tmp"),),
        codex_home="",
        codex_model="",
        codex_sandbox="workspace-write",
        codex_bypass_sandbox=False,
        codex_approval_policy="on-request",
        codex_extra_args=(),
        antigravity_command="agy",
        antigravity_home="~/.gemini/antigravity-cli",
        antigravity_extra_args=(),
    )


def test_create_agent_client_routes_codex_slot():
    """codexスロットへルーティングできるクライアントを作成する。"""
    client = create_agent_client(_settings())

    assert client.current_name == "作業"
    assert client.current_provider == "codex"


def test_create_agent_client_routes_hermes_slot():
    """hermesスロットへルーティングできるクライアントを作成する。"""
    settings = Settings(**{**_settings().__dict__, "agent_slots": (AgentSlot("Hermes", "hermes", "/tmp"),)})

    client = create_agent_client(settings)

    assert client.current_name == "Hermes"
    assert client.current_provider == "hermes"


def test_create_agent_client_uses_runner_when_url_is_set():
    """Runner URLが設定されている場合はRunnerクライアントを使う。"""
    settings = Settings(**{**_settings().__dict__, "agent_runner_url": "http://127.0.0.1:28765"})

    client = create_agent_client(settings)

    assert client.current_name == "作業"
    assert client.current_provider == "codex"


def test_unknown_agent_provider_raises():
    """未対応プロバイダーは起動時に検出できる。"""
    settings = Settings(**{**_settings().__dict__, "agent_slots": (AgentSlot("謎", "unknown", "/tmp"),)})

    try:
        create_agent_client(settings)
    except ValueError as exc:
        assert "未対応" in str(exc)
    else:
        raise AssertionError("ValueError が発生しませんでした")


def test_routed_agent_client_delegates_to_current_slot(monkeypatch):
    """RoutedAgentClientが現在スロットのクライアントへ処理を委譲する。"""

    class FakeProvider:
        """provider別の呼び出しを記録する偽クライアント。"""

        def __init__(self, slot):
            """スロットと記録領域を保持する。"""
            self.slot = slot
            self.calls = []
            self.reset_count = 0

        @property
        def current_name(self):
            """スロット名を返す。"""
            return self.slot.name

        @property
        def current_provider(self):
            """providerを返す。"""
            return self.slot.provider

        def next_slot(self):
            """自分自身では切り替えない。"""
            return self.current_name

        def reset_current(self):
            """リセット回数を記録する。"""
            self.reset_count += 1

        def ask(self, prompt):
            """プロンプトを記録して応答する。"""
            self.calls.append(("ask", prompt))
            return f"{self.slot.name}:{prompt}"

        def ask_stream(self, prompt):
            """プロンプトを記録して応答差分を返す。"""
            self.calls.append(("stream", prompt))
            yield f"{self.slot.name}:{prompt}"

    providers = []

    def fake_create_provider(_settings, slot):
        """スロットごとに偽providerを返す。"""
        provider = FakeProvider(slot)
        providers.append(provider)
        return provider

    monkeypatch.setattr("argos.services.agent.client.create_provider_client", fake_create_provider)
    settings = Settings(
        **{
            **_settings().__dict__,
            "agent_slots": (
                AgentSlot("作業", "codex", "/tmp/a"),
                AgentSlot("調査", "hermes", "/tmp/b"),
            ),
        }
    )
    client = RoutedAgentClient(settings)

    assert client.ask("最初") == "作業:最初"
    assert client.next_slot() == "調査"
    assert list(client.ask_stream("次")) == ["調査:次"]
    client.reset_current()

    assert providers[0].calls == [("ask", "最初")]
    assert providers[1].calls == [("stream", "次")]
    assert providers[1].reset_count == 1


def test_build_agent_prompt_adds_argos_defaults():
    """ARGOS共通の車載向け指示とスキル場所をユーザー発話へ付与する。"""
    prompt = build_agent_prompt("地図を出して", _settings())

    assert "<ARGOS_SYSTEM_CONTEXT>" in prompt
    assert "車載音声アシスタントARGOS" in prompt
    assert "回答は日本語で短く" in prompt
    assert "/opt/argos/skills" in prompt
    assert "ユーザー発話:\n地図を出して" in prompt


def test_build_agent_prompt_adds_env_and_file_prompt(tmp_path):
    """環境変数相当の追加指示と外部ファイルの指示を付与できる。"""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("外部ファイル指示", encoding="utf-8")
    settings = Settings(
        **{
            **_settings().__dict__,
            "agent_system_prompt": "追加指示",
            "agent_system_prompt_file": str(prompt_file),
            "agent_skills_dir": "/tmp/skills",
        }
    )

    prompt = build_agent_prompt("お願い", settings)

    assert "追加指示" in prompt
    assert "外部ファイル指示" in prompt
    assert "/tmp/skills" in prompt


def test_system_prompt_agent_client_wraps_ask_once(tmp_path):
    """共通プロンプトラッパーは同一スロットの初回だけ指示を付与する。"""

    class FakeAgent:
        """送られたプロンプトを記録する偽エージェント。"""

        current_name = "作業"
        current_provider = "codex"

        def __init__(self):
            """記録領域を初期化する。"""
            self.prompts = []

        def next_slot(self):
            """スロット名を返す。"""
            return self.current_name

        def reset_current(self):
            """何もしない。"""
            return None

        def ask(self, prompt):
            """プロンプトを記録して応答する。"""
            self.prompts.append(prompt)
            return "応答"

        def ask_stream(self, prompt):
            """プロンプトを記録して応答差分を返す。"""
            self.prompts.append(prompt)
            yield "応答"

    fake = FakeAgent()
    settings = Settings(
        **{
            **_settings().__dict__,
            "agent_system_prompt_state_path": str(tmp_path / "prompt-state.json"),
        }
    )
    client = SystemPromptAgentClient(fake, settings)

    assert client.ask("こんにちは") == "応答"
    assert "車載音声アシスタントARGOS" in fake.prompts[0]
    assert "ユーザー発話:\nこんにちは" in fake.prompts[0]
    assert client.ask("次の質問") == "応答"
    assert fake.prompts[1] == "次の質問"


def test_system_prompt_agent_client_reinjects_after_reset(tmp_path):
    """会話リセット後は同一スロットへ再度システム指示を付与する。"""

    class FakeAgent:
        """送られたプロンプトとリセットを記録する偽エージェント。"""

        current_name = "作業"
        current_provider = "codex"

        def __init__(self):
            """記録領域を初期化する。"""
            self.prompts = []
            self.reset_count = 0

        def next_slot(self):
            """スロット名を返す。"""
            return self.current_name

        def reset_current(self):
            """リセット回数を記録する。"""
            self.reset_count += 1

        def ask(self, prompt):
            """プロンプトを記録して応答する。"""
            self.prompts.append(prompt)
            return "応答"

        def ask_stream(self, prompt):
            """プロンプトを記録して応答差分を返す。"""
            self.prompts.append(prompt)
            yield "応答"

    fake = FakeAgent()
    settings = Settings(
        **{
            **_settings().__dict__,
            "agent_system_prompt_state_path": str(tmp_path / "prompt-state.json"),
        }
    )
    client = SystemPromptAgentClient(fake, settings)

    assert client.ask("最初") == "応答"
    assert client.ask("次") == "応答"
    client.reset_current()
    assert client.ask("リセット後") == "応答"

    assert fake.reset_count == 1
    assert "ユーザー発話:\n最初" in fake.prompts[0]
    assert fake.prompts[1] == "次"
    assert "ユーザー発話:\nリセット後" in fake.prompts[2]


def test_system_prompt_agent_client_marks_injected_after_stream(tmp_path):
    """ストリーム応答が完了した場合だけ注入済みとして保存する。"""

    class FakeAgent:
        """ストリーム呼び出しを記録する偽エージェント。"""

        current_name = "作業"
        current_provider = "codex"

        def __init__(self):
            """記録領域を初期化する。"""
            self.prompts = []

        def next_slot(self):
            """スロット名を返す。"""
            return self.current_name

        def reset_current(self):
            """何もしない。"""
            return None

        def ask(self, prompt):
            """プロンプトを記録して応答する。"""
            self.prompts.append(prompt)
            return "応答"

        def ask_stream(self, prompt):
            """プロンプトを記録して応答差分を返す。"""
            self.prompts.append(prompt)
            yield "応"
            yield "答"

    fake = FakeAgent()
    settings = Settings(
        **{
            **_settings().__dict__,
            "agent_system_prompt_state_path": str(tmp_path / "prompt-state.json"),
        }
    )
    client = SystemPromptAgentClient(fake, settings)

    assert "".join(client.ask_stream("最初")) == "応答"
    assert "".join(client.ask_stream("次")) == "応答"

    assert "ユーザー発話:\n最初" in fake.prompts[0]
    assert fake.prompts[1] == "次"


def test_system_prompt_agent_client_injects_per_slot(tmp_path):
    """システム指示はスロットごとに初回注入する。"""

    class FakeAgent:
        """スロット切替を持つ偽エージェント。"""

        def __init__(self):
            """スロットと記録領域を初期化する。"""
            self._slots = [("作業", "codex"), ("調査", "antigravity")]
            self._index = 0
            self.prompts = []

        @property
        def current_name(self):
            """現在のスロット名を返す。"""
            return self._slots[self._index][0]

        @property
        def current_provider(self):
            """現在のproviderを返す。"""
            return self._slots[self._index][1]

        def next_slot(self):
            """次のスロットへ切り替える。"""
            self._index = (self._index + 1) % len(self._slots)
            return self.current_name

        def reset_current(self):
            """何もしない。"""
            return None

        def ask(self, prompt):
            """プロンプトを記録して応答する。"""
            self.prompts.append(prompt)
            return "応答"

        def ask_stream(self, prompt):
            """プロンプトを記録して応答差分を返す。"""
            self.prompts.append(prompt)
            yield "応答"

    settings = Settings(
        **{
            **_settings().__dict__,
            "agent_system_prompt_state_path": str(tmp_path / "prompt-state.json"),
            "agent_slots": (
                AgentSlot("作業", "codex", "/tmp/a"),
                AgentSlot("調査", "antigravity", "/tmp/b"),
            ),
        }
    )
    fake = FakeAgent()
    client = SystemPromptAgentClient(fake, settings)

    assert client.ask("作業1") == "応答"
    assert client.next_slot() == "調査"
    assert client.ask("調査1") == "応答"
    assert client.next_slot() == "作業"
    assert client.ask("作業2") == "応答"

    assert "ユーザー発話:\n作業1" in fake.prompts[0]
    assert "ユーザー発話:\n調査1" in fake.prompts[1]
    assert fake.prompts[2] == "作業2"


def test_system_prompt_state_store_ignores_broken_files(tmp_path):
    """状態ファイルが壊れていても未注入として扱い、再保存できる。"""
    path = tmp_path / "prompt-state.json"
    path.write_text("{broken", encoding="utf-8")
    store = SystemPromptStateStore(path)

    assert store.is_injected("slot") is False
    store.clear("missing")
    store.mark_injected("slot")

    assert store.is_injected("slot") is True
