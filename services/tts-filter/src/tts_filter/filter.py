from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml


DEFAULT_CONFIG = {
    "acronyms": {
        "AI": "エーアイ",
        "API": "エーピーアイ",
        "AUTOSSH": "オートエスエスエイチ",
        "CLI": "シーエルアイ",
        "CPU": "シーピーユー",
        "CSS": "シーエスエス",
        "GPU": "ジーピーユー",
        "GPSD": "ジーピーエスディー",
        "GITEA": "ギテア",
        "GITIGNORE": "ギットイグノア",
        "HTML": "エイチティーエムエル",
        "HTTP": "エイチティーティーピー",
        "HTTPS": "エイチティーティーピーエス",
        "IMAGE": "イメージ",
        "JPEG": "ジェイペグ",
        "JSON": "ジェイソン",
        "LLM": "エルエルエム",
        "MARKDOWN": "マークダウン",
        "MCP": "エムシーピー",
        "OFF": "オフ",
        "OPENCLAW": "オープンクロー",
        "PNG": "ピング",
        "PR": "ピーアール",
        "PYTEST": "パイテスト",
        "PYTHON": "パイソン",
        "README": "リードミー",
        "REMINDER": "リマインダー",
        "SERVICE": "サービス",
        "SSH": "エスエスエイチ",
        "TEMP": "テンプ",
        "TMP": "テンプ",
        "TMPFS": "テンプエフエス",
        "TTS": "ティーティーエス",
        "URL": "ユーアールエル",
        "UV": "ユーブイ",
        "UI": "ユーアイ",
        "VOICEVOX": "ボイスボックス",
        "YAML": "ヤムル",
    },
    "terms": {
        "誤変換": "ごへんかん",
    },
    "extensions": {
        "conf": "コンフ",
        "ini": "イニ",
        "js": "ジェイエス",
        "jpeg": "ジェイペグ",
        "jpg": "ジェイペグ",
        "json": "ジェイソン",
        "md": "エムディー",
        "png": "ピング",
        "py": "パイ",
        "sh": "シェル",
        "service": "サービス",
        "toml": "トムル",
        "ts": "ティーエス",
        "txt": "テキスト",
        "yaml": "ヤムル",
        "yml": "ヤムル",
    },
    "phrase_rules": [
        {
            "pattern": r"((?:テスト|検証|pytest|パイテスト)[^。、\n]{0,12}?)通(っている|ってる|った|る|ります|りました)",
            "replacement": r"\1とお\2",
        },
    ],
}

CODE_BLOCK_RE = re.compile(r"```(?P<lang>[^\n`]*)\n(?P<body>.*?)```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
URL_RE = re.compile(r"https?://[^\s)]+")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PATHISH_RE = re.compile(r"\b(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+\b|(?:(?:[A-Za-z]:)?[~/\\/]|\./|\.\./)[^\s、。,.!?()\[\]{}<>]+", re.ASCII)
FILENAME_RE = re.compile(r"\b(?=[A-Za-z0-9_-]*[A-Za-z_][A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\b)[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+\b", re.ASCII)
DOTFILE_RE = re.compile(r"(?<![\w/])\.([A-Za-z][A-Za-z0-9_-]+)(?![A-Za-z0-9_-])", re.ASCII)
VERSION_RE = re.compile(r"\bv(\d+(?:\.\d+)+)\b", re.IGNORECASE)
DATE_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
SNAKE_KEBAB_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)+\b")
CAMEL_RE = re.compile(r"\b[a-z]+(?:[A-Z][a-z0-9]+)+\b")
UPPER_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}(?:s)?\b", re.ASCII)

MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
MARKDOWN_LIST_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
MARKDOWN_ORDERED_LIST_RE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
MARKDOWN_EMPHASIS_RE = re.compile(r"\*{1,3}([^*\n]*)\*{1,3}")
MARKDOWN_HR_RE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)


def _default_config_path() -> Path:
    return Path(__file__).with_name("dictionary.yml")


def load_config(path: str | Path | None = None) -> dict[str, object]:
    config_path = Path(path) if path else _default_config_path()
    if not config_path.exists():
        save_config(DEFAULT_CONFIG, config_path)
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return {
        "acronyms": _normalize_config_bucket(data.get("acronyms", {})),
        "terms": _normalize_config_bucket(data.get("terms", {})),
        "extensions": _normalize_config_bucket(data.get("extensions", {})),
        "phrase_rules": _normalize_phrase_rules(data.get("phrase_rules", [])),
    }


def _normalize_config_bucket(raw: object) -> dict[str, str]:
    """YAMLでON/OFFなどが真偽値キーとして読まれても辞書キーへ戻す"""
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        if key is True:
            normalized_key = "ON"
        elif key is False:
            normalized_key = "OFF"
        else:
            normalized_key = str(key)
        normalized[normalized_key] = str(value)
    return normalized


def _normalize_phrase_rules(raw: object) -> list[dict[str, str]]:
    """YAMLから読み込んだ正規表現ルールを安全な形にそろえる"""
    if not isinstance(raw, list):
        return []
    rules: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pattern = item.get("pattern")
        replacement = item.get("replacement")
        if isinstance(pattern, str) and isinstance(replacement, str):
            rules.append({"pattern": pattern, "replacement": replacement})
    return rules


def save_config(config: dict[str, object], path: str | Path | None = None) -> Path:
    config_path = Path(path) if path else _default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, allow_unicode=True, sort_keys=True)
    return config_path


def upsert_entry(category: str, key: str, value: str, path: str | Path | None = None) -> dict[str, object]:
    config = load_config(path)
    bucket = config.setdefault(category, {})
    if not isinstance(bucket, dict):
        bucket = {}
        config[category] = bucket
    bucket[key] = value
    save_config(config, path)
    return config


def delete_entry(category: str, key: str, path: str | Path | None = None) -> dict[str, object]:
    config = load_config(path)
    bucket = config.setdefault(category, {})
    if isinstance(bucket, dict):
        bucket.pop(key, None)
    save_config(config, path)
    return config


@dataclass
class TTSFilter:
    acronyms: dict[str, str] = field(default_factory=dict)
    terms: dict[str, str] = field(default_factory=dict)
    extensions: dict[str, str] = field(default_factory=dict)
    phrase_rules: list[dict[str, str]] = field(default_factory=list)
    code_block_mode: str = "ollama-summary"
    ollama_model: str = "qwen2.5:0.5b"
    ollama_timeout_seconds: int = 20

    def __post_init__(self) -> None:
        # 辞書キーを大文字小文字問わず・ASCII境界で一括マッチするパターンを構築
        # re.ASCII により日本語直前の略語（LLM応答 など）も正しく検出できる
        if self.acronyms:
            keys = sorted(self.acronyms.keys(), key=len, reverse=True)
            pattern = "|".join(re.escape(k) for k in keys)
            self._dict_re: re.Pattern[str] | None = re.compile(
                rf"\b({pattern})\b", re.IGNORECASE | re.ASCII
            )
        else:
            self._dict_re = None
        self._phrase_res: list[tuple[re.Pattern[str], str]] = []
        for rule in self.phrase_rules:
            try:
                self._phrase_res.append((re.compile(rule["pattern"]), rule["replacement"]))
            except re.error:
                continue

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "TTSFilter":
        config = load_config(path)
        return cls(
            acronyms=config["acronyms"],  # type: ignore[arg-type]
            terms=config["terms"],  # type: ignore[arg-type]
            extensions=config["extensions"],  # type: ignore[arg-type]
            phrase_rules=config["phrase_rules"],  # type: ignore[arg-type]
        )

    def normalize(self, text: str) -> str:
        text = CODE_BLOCK_RE.sub(self._replace_code_block, text)
        text = INLINE_CODE_RE.sub(lambda m: self._normalize_plain_text(m.group(1)), text)
        text = self._strip_markdown_syntax(text)
        return self._normalize_plain_text(text)

    def _normalize_plain_text(self, text: str) -> str:
        protected: list[str] = []
        text = self._protect_pattern(text, protected, URL_RE)
        text = self._protect_pattern(text, protected, EMAIL_RE)
        text = self._apply_phrase_rules(text)
        text = self._apply_terms(text)
        text = DATE_RE.sub(lambda m: f"{int(m.group(1))}年{int(m.group(2))}月{int(m.group(3))}日", text)
        text = TIME_RE.sub(lambda m: f"{int(m.group(1))}時{int(m.group(2))}分", text)
        text = VERSION_RE.sub(lambda m: f"バージョン {m.group(1)}", text)
        text = PATHISH_RE.sub(self._replace_pathish, text)
        text = FILENAME_RE.sub(self._replace_filename, text)
        text = DOTFILE_RE.sub(self._replace_dotfile, text)
        text = SNAKE_KEBAB_RE.sub(self._replace_identifier, text)
        text = CAMEL_RE.sub(self._replace_identifier, text)
        text = UPPER_ACRONYM_RE.sub(self._replace_acronym, text)
        # 上記パターンに引っかからなかった辞書語（PascalCase・小文字略語・日本語直前など）を変換
        if self._dict_re:
            text = self._dict_re.sub(lambda m: self.acronyms[m.group(1).upper()], text)
        text = self._cleanup(text)
        text = self._unprotect(text, protected)
        text = URL_RE.sub(self._replace_url, text)
        text = EMAIL_RE.sub(self._replace_email, text)
        return self._cleanup(text)

    def _strip_markdown_syntax(self, text: str) -> str:
        # コードブロック・インラインコード処理後に呼ぶ前提（中身はすでに変換済み）
        text = MARKDOWN_HEADING_RE.sub("", text)
        text = MARKDOWN_LIST_RE.sub("", text)
        text = MARKDOWN_ORDERED_LIST_RE.sub("", text)
        text = MARKDOWN_EMPHASIS_RE.sub(r"\1", text)
        text = MARKDOWN_HR_RE.sub("", text)
        return text

    def _replace_code_block(self, match: re.Match[str]) -> str:
        lang = (match.group("lang") or "").strip()
        body = (match.group("body") or "").strip()
        mode = self.code_block_mode
        if mode == "skip":
            return f"{lang or 'コード'} のコードブロックがあります"
        if mode == "literal":
            return self._normalize_plain_text(body)
        if mode == "meta":
            return self._summarize_meta(lang, body)
        if mode == "first-line":
            return self._summarize_first_line(lang, body)
        if mode == "rule":
            return self._summarize_rule(lang, body)
        # ollama-summary
        return self._summarize_ollama(lang, body)

    def _apply_phrase_rules(self, text: str) -> str:
        """外部定義した正規表現ルールを順番に適用する"""
        for pattern, replacement in self._phrase_res:
            text = pattern.sub(replacement, text)
        return text

    def _apply_terms(self, text: str) -> str:
        """日本語など単語境界を使いにくい語を辞書で置換する"""
        for key, value in sorted(self.terms.items(), key=lambda item: len(item[0]), reverse=True):
            text = text.replace(key, value)
        return text

    def _summarize_meta(self, lang: str, body: str) -> str:
        """言語名と行数だけ読む（B案）"""
        lines = [l for l in body.splitlines() if l.strip()]
        label = self._normalize_plain_text(lang) if lang else "コード"
        return f"{label}、{len(lines)}行のコードブロックです"

    def _summarize_first_line(self, lang: str, body: str) -> str:
        """コメント記号を除いた最初の行を読む（C案）。長いコマンドは最大3トークンに短縮"""
        lines = [l.strip() for l in body.splitlines() if l.strip()]
        label = self._normalize_plain_text(lang) if lang else "コード"
        if not lines:
            return f"{label} のコードブロックがあります"
        first = re.sub(r"^[#/*!\-]+\s*", "", lines[0]).strip()
        # スペース区切りで最大3トークン（長いコマンドラインを短縮）
        tokens = first.split()
        if len(tokens) > 3:
            first = " ".join(tokens[:3])
        spoken = self._normalize_plain_text(first) if first else ""
        if spoken:
            return f"{label}：{spoken}"
        return f"{label} のコードブロックがあります"

    def _summarize_rule(self, lang: str, body: str) -> str:
        """コメント行優先、なければ最初のコマンド名＋行数（A案）"""
        lines = [l.strip() for l in body.splitlines() if l.strip()]
        label = self._normalize_plain_text(lang) if lang else "コード"
        if not lines:
            return f"{label} のコードブロックがあります"
        # コメント行を優先して読む
        for line in lines:
            if re.match(r"^[#/*!\-]+\s*\S", line):
                comment = re.sub(r"^[#/*!\-]+\s*", "", line).strip()
                if comment:
                    spoken = self._normalize_plain_text(comment)
                    return f"{label}、{len(lines)}行。{spoken}"
        # コメントなし：最初のコマンド名＋行数
        cmd = lines[0].split()[0] if lines[0].split() else lines[0]
        spoken_cmd = self._normalize_plain_text(cmd)
        suffix = f"など{len(lines)}行" if len(lines) > 1 else "1行"
        return f"{label}、{spoken_cmd} {suffix}のコードです"

    def _summarize_ollama(self, lang: str, body: str) -> str:
        """Ollama で一文に要約する"""
        language = lang or "text"
        # 出力を短く抑えるためシステム的な制約を明示する
        prompt = (
            f"次の{language}コードが何をするか、日本語で15字以内の一文で答えてください。"
            "出力はその一文だけ。余分な説明不要。\n"
            f"{body}\n"
        )
        try:
            result = subprocess.run(
                ["ollama", "run", self.ollama_model, prompt],
                capture_output=True,
                text=True,
                timeout=self.ollama_timeout_seconds,
                check=True,
            )
            summary = result.stdout.strip()
            if summary:
                return summary
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
        return f"{language} のコードブロックです。処理内容を要約できませんでした。"

    def _protect_pattern(self, text: str, protected: list[str], pattern: re.Pattern[str]) -> str:
        while True:
            match = pattern.search(text)
            if not match:
                break
            idx = len(protected)
            protected.append(match.group(0))
            text = text[: match.start()] + f"__PROTECTED_{idx}__" + text[match.end() :]
        return text

    def _unprotect(self, text: str, protected: list[str]) -> str:
        for idx, raw in enumerate(protected):
            text = text.replace(f"__PROTECTED_{idx}__", raw)
        return text

    def _replace_url(self, match: re.Match[str]) -> str:
        url = match.group(0)
        body = re.sub(r"^https?://", "", url)
        body = body.rstrip("/)")
        return f"URL {body}"

    def _replace_email(self, match: re.Match[str]) -> str:
        local, domain = match.group(0).split("@", 1)
        return f"メールアドレス {local} アット {domain}"

    def _replace_pathish(self, match: re.Match[str]) -> str:
        raw = match.group(0)
        stripped = raw.rstrip(".,)")
        suffix = raw[len(stripped):]
        prefix = ""
        body = stripped
        if raw.startswith("~/"):
            prefix = "ホーム スラッシュ "
            body = stripped[2:]
        elif raw.startswith("../"):
            prefix = "親ディレクトリ スラッシュ "
            body = stripped[3:]
        elif raw.startswith("./"):
            prefix = "カレントディレクトリ スラッシュ "
            body = stripped[2:]
        elif raw.startswith("/"):
            prefix = "ルート スラッシュ "
            body = stripped[1:]
        parts = [p for p in re.split(r"[\\/]", body) if p and p != "."]
        if not parts:
            return raw
        spoken = prefix + " スラッシュ ".join(self._speak_path_part(p) for p in parts)
        return spoken + suffix

    def _speak_path_part(self, part: str) -> str:
        if "." in part:
            return self._speak_filename(part)
        return self._split_identifier(part)

    def _replace_filename(self, match: re.Match[str]) -> str:
        return self._speak_filename(match.group(0))

    def _replace_dotfile(self, match: re.Match[str]) -> str:
        """単体のドットファイル名を読み上げ向けに変換する"""
        name = match.group(1)
        if name.upper() in self.acronyms:
            return self.acronyms[name.upper()]
        return f"ドット {self._split_identifier(name)}"

    def _speak_filename(self, token: str) -> str:
        name = PurePosixPath(token).name
        bits = name.split(".")
        if len(bits) < 2:
            return self._split_identifier(name)
        base = bits[0]
        exts = bits[1:]
        spoken = [self._split_identifier(base)]
        for ext in exts:
            spoken.append(self.extensions.get(ext.lower(), self._spell_letters(ext)))
        return " ".join(filter(None, spoken))

    def _replace_identifier(self, match: re.Match[str]) -> str:
        return self._split_identifier(match.group(0))

    def _split_identifier(self, token: str) -> str:
        token = token.replace("_", " ").replace("-", " ")
        token = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", token)
        parts = token.split()
        spoken_parts = []
        for part in parts:
            if part.upper() in self.acronyms:
                spoken_parts.append(self.acronyms[part.upper()])
            elif part.isupper() and len(part) >= 2:
                spoken_parts.append(self._spell_letters(part))
            else:
                spoken_parts.append(part)
        return " ".join(spoken_parts)

    def _replace_acronym(self, match: re.Match[str]) -> str:
        token = match.group(0)
        singular = token[:-1] if token.endswith("s") and token[:-1] in self.acronyms else token
        if singular in self.acronyms:
            suffix = "ズ" if singular != token else ""
            return self.acronyms[singular] + suffix
        return self._spell_letters(token)

    def _spell_letters(self, token: str) -> str:
        letter_map = {
            "a": "エー", "b": "ビー", "c": "シー", "d": "ディー", "e": "イー", "f": "エフ",
            "g": "ジー", "h": "エイチ", "i": "アイ", "j": "ジェイ", "k": "ケー", "l": "エル",
            "m": "エム", "n": "エヌ", "o": "オー", "p": "ピー", "q": "キュー", "r": "アール",
            "s": "エス", "t": "ティー", "u": "ユー", "v": "ブイ", "w": "ダブリュー", "x": "エックス",
            "y": "ワイ", "z": "ズィー",
        }
        # スペースを入れずに連結することで TTS が間を開けずに読む
        return "".join(letter_map.get(ch.lower(), ch) for ch in token)

    def _cleanup(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s+([、。,.!?])", r"\1", text)
        return text.strip()


def normalize_for_tts(text: str, config_path: str | Path | None = None) -> str:
    return TTSFilter.from_yaml(config_path).normalize(text)
