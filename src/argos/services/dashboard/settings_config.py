"""初心者向け設定画面で扱うconfig.yamlの読み書き。"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


SETTING_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "key": "audio.output_volume",
        "section": "音声",
        "label": "読み上げ音量",
        "description": "ARGOSが返事を読み上げる音量です。0から100で指定します。",
        "type": "integer",
        "min": 0,
        "max": 100,
        "default": 70,
        "unit": "%",
    },
    {
        "key": "audio.listen_mode",
        "section": "音声",
        "label": "音声入力の開始方法",
        "description": "呼びかけで開始するか、マイクの音声検知で開始するかを選びます。",
        "type": "select",
        "options": [
            {"value": "wakeword", "label": "「アルゴス」の呼びかけ"},
            {"value": "vad", "label": "音声を検知したら開始"},
        ],
        "default": "wakeword",
    },
    {
        "key": "wakeword.enabled",
        "section": "音声",
        "label": "ウェイクワードを使う",
        "description": "オンにすると「アルゴス」という呼びかけを待ち受けます。",
        "type": "boolean",
        "default": True,
    },
    {
        "key": "wakeword.threshold",
        "section": "音声",
        "label": "呼びかけの検出感度",
        "description": "小さいほど反応しやすくなります。通常は0.5がおすすめです。",
        "type": "number",
        "min": 0.1,
        "max": 1.0,
        "step": 0.05,
        "default": 0.5,
    },
    {
        "key": "agent.progress_voice",
        "section": "AI",
        "label": "処理中の声かけ",
        "description": "回答に時間がかかるとき「確認中だよ」などの進捗を読み上げます。",
        "type": "boolean",
        "default": True,
    },
    {
        "key": "agents.codex.bypass_sandbox",
        "section": "AI",
        "label": "Codexの権限確認を省略",
        "description": "端末や機器を直接操作できます。信頼できる環境だけで有効にしてください。",
        "type": "boolean",
        "default": False,
        "advanced": True,
        "warning": True,
    },
    {
        "key": "agents.antigravity.skip_permissions",
        "section": "AI",
        "label": "Agyの権限確認を省略",
        "description": "Agyへ --dangerously-skip-permissions を渡します。信頼できる環境だけで有効にしてください。",
        "type": "boolean",
        "default": False,
        "advanced": True,
        "warning": True,
    },
    {
        "key": "dashboard.screensaver_seconds",
        "section": "画面",
        "label": "画面保護までの時間",
        "description": "操作がない状態からスクリーンセーバーを表示するまでの秒数です。0で無効です。",
        "type": "integer",
        "min": 0,
        "max": 86400,
        "default": 1800,
        "unit": "秒",
    },
    {
        "key": "dashboard.default_font_size",
        "section": "画面",
        "label": "標準の文字サイズ",
        "description": "初めて画面を開いた端末で使う文字サイズです。",
        "type": "select",
        "options": [
            {"value": "small", "label": "小"},
            {"value": "medium", "label": "中（おすすめ）"},
            {"value": "large", "label": "大"},
        ],
        "default": "medium",
    },
    {
        "key": "location.provider",
        "section": "GPS",
        "label": "現在地の取得元",
        "description": "この端末につないだGPSを使うか、別の端末から取得するかを選びます。",
        "type": "select",
        "options": [
            {"value": "local", "label": "この端末のGPS（おすすめ）"},
            {"value": "remote", "label": "別の端末のGPS"},
        ],
        "default": "local",
    },
    {
        "key": "location.remote.url",
        "section": "GPS",
        "label": "リモートGPSのURL",
        "description": "取得元を「別の端末」にした場合だけ使います。",
        "type": "text",
        "default": "",
        "placeholder": "http://example.local:8080/gps",
        "advanced": True,
    },
)

FIELD_BY_KEY = {field["key"]: field for field in SETTING_FIELDS}

SECTION_LABELS = {
    "agent": "エージェント共通",
    "agents": "AIプロバイダー",
    "audio": "マイク・スピーカー",
    "ptt": "PTTボタン",
    "tts": "読み上げ前処理",
    "network": "ネットワーク",
    "dashboard": "ダッシュボード",
    "runtime": "動作モード",
    "location": "GPS・地図",
    "wakeword": "ウェイクワード",
    "acknowledgement": "相づち",
    "runner": "Agent Runner",
    "greeting": "あいさつ",
    "startup": "起動演出",
    "whisper": "ローカル音声認識",
    "kokoro": "Kokoro音声合成",
    "stt": "音声認識API",
    "auth": "本人確認",
    "lcd": "小型LCD",
    "voicevox": "VOICEVOX",
    "conversation_history": "会話履歴",
    "conversation_memory": "会話メモリー",
}

DESCRIPTION_BY_KEY = {
    "agent.slots": "ダッシュボードで切り替えるAIスロット一覧です。名前、種類、作業場所、音声話者などをJSONで指定します。",
    "agent.progress_start_phrases": "AI処理開始時に読み上げる短い案内文の候補です。空なら内蔵の文言を使います。",
    "agent.progress_wait_phrases": "AI処理が長引いたときに読み上げる案内文の候補です。",
    "agent.provider": "起動時に選ばれる標準AIプロバイダーです。",
    "agent.state_path": "AIの会話セッションIDを保存するファイルです。",
    "agent.system_prompt": "すべてのAIへ直接追加するシステム指示です。",
    "agent.system_prompt_file": "システム指示を読み込むファイルです。直接指定より長い指示に向きます。",
    "agent.system_prompt_state_path": "スロットごとに変更したシステム指示の保存先です。",
    "agent.default_system_prompt": "個別指定がないスロットで使う標準のシステム指示です。",
    "agent.skills_dir": "ARGOS用スキルを探すディレクトリです。",
    "agent.cwd": "AIコマンドを実行する標準の作業ディレクトリです。",
    "agent.usage_commands": "AIごとの利用枠を取得するコマンドです。JSONを標準出力へ返す必要があります。",
    "audio.state_path": "ミュート状態や音量を記憶するファイルです。",
    "audio.silence_rms_threshold": "録音を無音と判断する音量境界です。大きくすると小さな声も無音扱いになりやすくなります。",
    "audio.input_devices": "使用するマイク候補です。上から順に接続を確認し、利用可能なものを使います。",
    "audio.device": "旧設定との互換用の標準マイクです。通常は入力マイク候補を設定してください。",
    "audio.output_device": "読み上げ音声を出すALSA再生デバイスです。",
    "audio.output_card": "音量調整に使うALSAカード名です。空ならソフトウェア側だけで音量調整します。",
    "audio.sample_rate": "録音音声の1秒あたりのサンプル数です。通常は16000のまま使います。",
    "ptt.gpio": "PTTボタンを接続したGPIOのBCM番号です。使わない場合は空にします。",
    "tts.delimiters": "ストリーミング回答を読み上げ単位へ区切る文字です。",
    "tts.url": "読み上げ前の辞書変換サービスのURLです。",
    "tts.cache.enabled": "同じ読み上げ音声を再利用して応答を速くします。",
    "tts.cache.max_chars": "キャッシュ対象にする文章の最大文字数です。",
    "tts.cache.max_size_mb": "音声キャッシュ全体の最大容量です。",
    "tts.cache.dir": "音声キャッシュの保存場所です。",
    "network.wifi_status_refresh_seconds": "Wi-Fi状態をダッシュボードへ更新する間隔です。",
    "dashboard.camera_snapshot_path": "ダッシュボードに表示する最新カメラ画像の保存先です。",
    "dashboard.enabled": "ARGOSのダッシュボードWebサーバーを起動します。",
    "dashboard.host": "ダッシュボードが待ち受けるアドレスです。0.0.0.0はLANからも接続できます。",
    "dashboard.port": "ダッシュボードWebサーバーのポート番号です。",
    "dashboard.screensaver_seconds": "操作がないとき画面保護を始めるまでの秒数です。0で無効です。",
    "dashboard.default_font_size": "初めて開くブラウザで使う文字サイズです。",
    "runtime.dry_run": "オンにすると機器操作などを実行せず、確認用の動作になります。",
    "location.osrm_url": "道路に沿った経路を計算するOSRMサーバーのURLです。",
    "location.provider": "現在地をローカルGPSとリモートGPSのどちらから取得するか選びます。",
    "location.remote.url": "リモートGPSの現在地JSONを取得するURLです。",
    "location.remote.timeout_seconds": "リモートGPSの応答を待つ最大秒数です。",
    "wakeword.aliases": "音声認識で「アルゴス」と同じ呼びかけとして扱う表記です。",
    "wakeword.enabled": "「アルゴス」の呼びかけを常時待ち受けます。",
    "wakeword.model_dir": "ウェイクワード判定モデルを置くディレクトリです。",
    "wakeword.threshold": "呼びかけと判定する確信度です。小さいほど反応しやすく誤反応も増えます。",
    "wakeword.capture_sample_rate": "マイクから取得する音声のサンプルレートです。",
    "wakeword.window_seconds": "呼びかけ判定へ渡す音声窓の長さです。",
    "wakeword.interval_seconds": "呼びかけ判定を実行する間隔です。",
    "wakeword.chunk_ms": "マイクから一度に読み取る音声の長さです。",
    "wakeword.record_min_seconds": "呼びかけ後に必ず録音する最短時間です。",
    "wakeword.record_max_seconds": "呼びかけ後に録音する最長時間です。",
    "wakeword.record_silence_seconds": "発話終了と判断する無音継続時間です。",
    "wakeword.pre_roll_seconds": "呼びかけ検出前から録音へ含める時間です。発話先頭の欠落を防ぎます。",
    "wakeword.min_actual_seconds": "有効な発話として扱う最短音声時間です。",
    "wakeword.endpoint_mode": "発話終了の判定方法です。通常はVADを使います。",
    "wakeword.vad_model": "音声区間検出に使うONNXモデルです。",
    "wakeword.vad_threshold": "声があると判断するVAD確率の境界です。",
    "wakeword.vad_min_silence_seconds": "発話終了に必要な最短無音時間です。",
    "wakeword.vad_check_seconds": "VADが一度に確認する音声の長さです。",
    "wakeword.tts_cooldown_seconds": "読み上げ終了後、マイク待受を再開するまでの時間です。",
    "wakeword.bargein_enabled": "読み上げ中の呼びかけで読み上げを中断できるようにします。",
    "wakeword.score_log_path": "ウェイクワード判定スコアのログ保存先です。",
    "wakeword.require_stt_wakeword": "検出後の文字起こしにも呼びかけ語が含まれることを必須にします。",
    "acknowledgement.url": "処理中の相づち文を取得するサービスURLです。",
    "runner.url": "ARGOS本体がAgent Runnerへジョブを送るURLです。",
    "runner.host": "Agent Runnerが待ち受けるアドレスです。",
    "runner.port": "Agent Runnerが待ち受けるポートです。",
    "runner.state_dir": "Runnerのジョブ状態を保存する場所です。",
    "greeting.enabled": "時間帯などに応じた起動時のあいさつを有効にします。",
    "greeting.state_path": "最後にあいさつした状態の保存先です。",
    "startup.splash_enabled": "起動時にダッシュボードへARGOSロゴを表示します。",
    "startup.splash_seconds": "起動ロゴを表示する秒数です。",
    "startup.sound_enabled": "起動完了時の効果音を再生します。",
    "whisper.model_size": "ローカルWhisperモデルの大きさです。大きいほど高精度ですが重くなります。",
    "whisper.device": "Whisperを実行するCPUまたはGPUです。autoは自動選択です。",
    "whisper.compute_type": "Whisperの計算精度です。int8は軽量です。",
    "kokoro.voice": "Kokoro音声合成で使う声のIDです。",
    "kokoro.speed": "Kokoroの読み上げ速度倍率です。",
    "kokoro.repo_id": "Kokoroモデルを取得するリポジトリIDです。",
    "kokoro.sample_rate": "Kokoroが生成する音声のサンプルレートです。",
    "stt.url": "録音を文字へ変換するSTT GatewayのURLです。",
    "stt.language": "音声認識で優先する言語です。",
    "stt.use_opus": "録音をOpusへ圧縮してSTTへ送り、通信量を減らします。",
    "stt.opus_bitrate": "STTへ送るOpus音声のビットレートです。",
    "auth.enabled": "音声キーワードによる本人確認を有効にします。",
    "auth.keyword_hash": "本人確認キーワードのハッシュ値です。元の言葉は保存しません。",
    "auth.trust_seconds": "本人確認後に再確認なしで操作できる時間です。",
    "auth.failure_threshold": "警戒通知を出すまでの認証失敗回数です。",
    "auth.face_enabled": "カメラ画像を使った顔認証を有効にします。",
    "auth.face_samples_dir": "本人として登録した顔画像の保存場所です。",
    "auth.face_capture_command": "認証用の顔写真を撮影するコマンドです。{path}が保存先になります。",
    "auth.face_capture_path": "撮影した一時画像の保存先です。",
    "auth.face_image_rotation": "カメラ画像を時計回りに回転する角度です。",
    "auth.face_threshold": "従来方式の顔類似度しきい値です。",
    "auth.face_min_matches": "本人と判断するために一致が必要な登録画像数です。",
    "auth.face_detection_enabled": "顔の有無と人数の検出を有効にします。",
    "auth.face_min_detected_faces": "認証画像に必要な最少の顔人数です。",
    "auth.face_max_detected_faces": "認証画像に許可する最大の顔人数です。",
    "auth.face_detector_model_path": "顔検出モデルのファイルです。",
    "auth.face_recognizer_model_path": "顔特徴量モデルのファイルです。",
    "auth.face_sface_threshold": "SFace方式で本人と判定する類似度です。",
    "auth.alert_command": "警戒時に実行する外部通知コマンドです。",
    "auth.warning_sound_enabled": "本人確認に失敗したとき警告音を鳴らします。",
    "auth.warning_delay_seconds": "認証失敗から警告を始めるまでの秒数です。",
    "auth.alert_delay_seconds": "認証失敗から外部警戒通知までの秒数です。",
    "auth.warning_interval_seconds": "警告音を繰り返す間隔です。",
    "lcd.enabled": "SPI接続の小型LCDへ状態を表示します。",
    "lcd.width": "LCDコントローラーへ渡す描画幅です。",
    "lcd.height": "LCDコントローラーへ渡す描画高さです。",
    "lcd.x_offset": "LCDの横方向描画開始位置です。",
    "lcd.y_offset": "LCDの縦方向描画開始位置です。",
    "lcd.dc_pin": "LCDのデータ・コマンド切替GPIOです。",
    "lcd.cs_pin": "LCDのチップ選択GPIOです。",
    "lcd.reset_pin": "LCDのリセットGPIOです。",
    "lcd.baudrate": "LCDへ送るSPI通信速度です。",
    "lcd.font_path": "LCD表示に使うフォントファイルです。空なら内蔵候補を使います。",
    "lcd.font_size": "LCDへ表示する文字サイズです。",
    "voicevox.url": "VOICEVOX EngineのURLです。",
    "voicevox.speaker": "標準で使うVOICEVOX話者IDです。",
    "voicevox.sample_rate": "VOICEVOXへ要求する音声サンプルレートです。",
    "voicevox.speed_scale": "VOICEVOXの話速倍率です。",
    "voicevox.volume_scale": "VOICEVOXが生成する音声の音量倍率です。",
    "conversation_history.enabled": "スロットごとの会話表示履歴を再起動後も復元します。",
    "conversation_history.path": "会話表示履歴の保存先です。",
    "conversation_history.max_messages": "スロットごとに保持する最大メッセージ数です。",
    "conversation_memory.enabled": "過去会話の要約を新しいAIセッションへ引き継ぎます。",
    "conversation_memory.path": "会話要約の保存先です。",
}

SECRET_SUFFIXES = ("token", "bearer_token", "view_key", "keyword_hash")


def load_settings_form(config_path: Path) -> dict[str, Any]:
    """設定画面用の項目定義と現在値を返す。"""
    data = _load_yaml_mapping(config_path)
    fields: list[dict[str, Any]] = []
    for key, value in _flatten_config(data):
        field = _make_field(key, value)
        fields.append(field)
    return {
        "fields": fields,
        "sections": SECTION_LABELS,
        "restart_required": True,
    }


def save_settings_form(config_path: Path, values: object) -> Path:
    """検証済みの設定値だけをYAMLへ保存し、退避ファイルのパスを返す。"""
    if not isinstance(values, dict) or not values:
        raise ValueError("保存する設定がありません")
    data = _load_yaml_mapping(config_path)
    current_values = dict(_flatten_config(data))
    unknown = sorted(set(values) - set(current_values))
    if unknown:
        raise ValueError(f"未対応の設定項目です: {', '.join(unknown)}")

    normalized = {
        key: _validate_dynamic_value(key, current_values[key], value)
        for key, value in values.items()
        if value != "__ARGOS_SECRET_UNCHANGED__"
    }
    for key, value in normalized.items():
        _nested_set(data, key, value)

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = config_path.with_name(f"{config_path.name}.backup-{timestamp}")
    original = config_path.read_bytes()
    backup_path.write_bytes(original)
    os.chmod(backup_path, config_path.stat().st_mode & 0o777)

    rendered = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=f".{config_path.name}.", dir=config_path.parent)
    try:
        with os.fdopen(fd, "wb") as temp_file:
            temp_file.write(rendered)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.chmod(temp_name, config_path.stat().st_mode & 0o777)
        os.replace(temp_name, config_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return backup_path


def _flatten_config(data: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    """設定辞書を画面編集可能なドット区切り項目へ展開する。"""
    result: list[tuple[str, Any]] = []
    for name, value in data.items():
        key = f"{prefix}.{name}" if prefix else str(name)
        if isinstance(value, dict):
            result.extend(_flatten_config(value, key))
        else:
            result.append((key, value))
    return result


def _make_field(key: str, value: Any) -> dict[str, Any]:
    """現在値から画面項目の表示情報と入力形式を組み立てる。"""
    override = FIELD_BY_KEY.get(key, {})
    section = key.split(".", 1)[0]
    leaf = key.rsplit(".", 1)[-1]
    field: dict[str, Any] = {
        "key": key,
        "section": section,
        "section_label": SECTION_LABELS.get(section, section),
        "label": override.get("label", _humanize_key(leaf)),
        "description": DESCRIPTION_BY_KEY.get(key, override.get("description", _fallback_description(key))),
        "advanced": True,
    }
    field.update({name: value for name, value in override.items() if name not in {"key", "section", "value"}})

    if key == "audio.input_devices":
        field["type"] = "audio-input-list"
    elif key == "audio.device":
        field["type"] = "audio-input"
    elif key == "audio.output_device":
        field["type"] = "audio-output"
    elif isinstance(value, bool):
        field["type"] = "boolean"
    elif isinstance(value, int):
        field["type"] = "integer"
    elif isinstance(value, float):
        field["type"] = "number"
        field.setdefault("step", "any")
    elif isinstance(value, (list, dict)):
        field["type"] = "json"
    else:
        field["type"] = "text"

    secret = leaf.endswith(SECRET_SUFFIXES)
    field["secret"] = secret
    field["configured"] = bool(value) if secret else False
    field["value"] = "" if secret else value
    return field


def _humanize_key(key: str) -> str:
    """英語キーを最低限読みやすい表示名へ変換する。"""
    words = {
        "enabled": "有効",
        "url": "接続URL",
        "path": "保存先",
        "model": "モデル",
        "command": "実行コマンド",
        "home": "ホームディレクトリ",
        "token": "認証トークン",
        "port": "ポート番号",
        "host": "待受アドレス",
        "speaker": "話者ID",
        "provider": "取得元・プロバイダー",
    }
    return words.get(key, key.replace("_", " "))


def _fallback_description(key: str) -> str:
    """個別説明がない拡張項目にも用途の手掛かりを表示する。"""
    parent, _, leaf = key.rpartition(".")
    suffixes = {
        "enabled": "この機能を使用するかどうかを切り替えます。",
        "timeout_seconds": "応答や処理を待つ最大秒数です。",
        "interval_seconds": "処理を繰り返す間隔です。",
        "model": "この機能で使用するモデル名です。空なら標準値を使います。",
        "command": "この機能を起動するコマンドです。",
        "home": "この機能が設定や履歴を保存するホームディレクトリです。",
        "extra_args": "コマンド起動時に追加で渡す引数です。",
        "sandbox": "外部操作を隔離するサンドボックスを使用します。",
    }
    return suffixes.get(leaf, f"{parent or 'ARGOS'}機能の「{leaf}」を調整する詳細設定です。")


def _validate_dynamic_value(key: str, current: Any, value: Any) -> Any:
    """既存値の型に合わせて全設定エディターの入力を検証する。"""
    if key in FIELD_BY_KEY and FIELD_BY_KEY[key]["type"] not in {"audio-input", "audio-output", "audio-input-list"}:
        return _validate_value(FIELD_BY_KEY[key], value)
    if isinstance(current, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{key}はオンまたはオフで指定してください")
        return value
    if isinstance(current, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key}は整数で指定してください")
        return value
    if isinstance(current, float):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{key}は数値で指定してください")
        return float(value)
    if isinstance(current, list):
        if not isinstance(value, list):
            raise ValueError(f"{key}は配列で指定してください")
        return value
    if isinstance(current, dict):
        if not isinstance(value, dict):
            raise ValueError(f"{key}はJSONオブジェクトで指定してください")
        return value
    if not isinstance(value, str) or len(value) > 10000:
        raise ValueError(f"{key}は10000文字以内で指定してください")
    return value


def _load_yaml_mapping(config_path: Path) -> dict[str, Any]:
    """YAMLをルート辞書として読み込む。"""
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("config.yamlが見つかりません") from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"config.yamlを読み込めません: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("config.yamlの形式が正しくありません")
    return data


def _nested_get(data: dict[str, Any], dotted_key: str, default: Any) -> Any:
    """ドット区切りのキーから値を取得する。"""
    current: Any = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _nested_set(data: dict[str, Any], dotted_key: str, value: Any) -> None:
    """ドット区切りのキーへ値を設定する。"""
    parts = dotted_key.split(".")
    current = data
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _validate_value(field: dict[str, Any], value: Any) -> Any:
    """項目定義に従って値を検証し、保存用の型へ変換する。"""
    field_type = field["type"]
    label = field["label"]
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{label}はオンまたはオフで指定してください")
        return value
    if field_type == "text":
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError(f"{label}は500文字以内で指定してください")
        return value.strip()
    if field_type == "select":
        allowed = {option["value"] for option in field["options"]}
        if value not in allowed:
            raise ValueError(f"{label}の選択肢が正しくありません")
        return value
    if field_type in {"integer", "number"}:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{label}は数値で指定してください")
        if field_type == "integer" and not isinstance(value, int):
            raise ValueError(f"{label}は整数で指定してください")
        if value < field["min"] or value > field["max"]:
            raise ValueError(f"{label}は{field['min']}から{field['max']}で指定してください")
        return value
    raise ValueError(f"{label}の入力形式が未対応です")
