"""ツール置き場（router.yaml）の読み込み・引数の検証・実行。

高速振り分け（argos.router.fast_router）と、エージェント向けの `argos-tool` コマンドの両方が
このモジュールを通してツールを動かす。引数の検証、実行するコマンド、ログを1か所にそろえるため。

ツールは、スキルのフォルダの `router.yaml` と、追加の置き場（既定 `~/.config/argos/router-tools/*.yaml`）
に書く。ファイルの更新時刻を見て読み直すので、ツールを足しても ARGOS の再起動は要らない。
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

# ツール名は LLM の tools API にそのまま渡すため、英数字と _ だけにする。
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
SAFETY_LEVELS = ("display", "confirm")
PARAM_TYPES = ("string", "integer", "enum")
MAX_STRING_LENGTH = 200
MAX_EXAMPLES = 5
DEFAULT_TIMEOUT_SECONDS = 15.0
# コマンドの中で使える置き換え。引数の名前と重ならないよう予約する。
RESERVED_PLACEHOLDERS = ("python", "skill_dir", "argos_root")
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class ToolError(Exception):
    """ツールの定義・引数・実行のいずれかが正しくないときの例外。"""


@dataclass(frozen=True)
class ToolParam:
    """ツールの引数1つ分の定義。"""

    name: str
    type: str
    description: str = ""
    required: bool = True
    choices: tuple[str, ...] = ()
    max_length: int = 40
    minimum: int | None = None
    maximum: int | None = None

    def validate(self, value: Any) -> str:
        """値を検証し、コマンドへ埋め込む文字列にして返す。"""
        if self.type == "enum":
            if not isinstance(value, str) or value not in self.choices:
                raise ToolError(f"{self.name} は {', '.join(self.choices)} のどれかで指定してください")
            return value
        if self.type == "integer":
            if isinstance(value, bool):
                raise ToolError(f"{self.name} は整数で指定してください")
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ToolError(f"{self.name} は整数で指定してください") from exc
            if (self.minimum is not None and number < self.minimum) or (
                self.maximum is not None and number > self.maximum
            ):
                raise ToolError(f"{self.name} は {self.minimum}〜{self.maximum} で指定してください")
            return str(number)
        if not isinstance(value, str) or not value.strip():
            raise ToolError(f"{self.name} は空でない文字列で指定してください")
        text = value.strip()
        if len(text) > self.max_length:
            raise ToolError(f"{self.name} は {self.max_length} 文字以内で指定してください")
        # 改行や制御文字は、引数として受け取らない（コマンドはシェルを通さないが、念のため）。
        if any(ord(ch) < 32 for ch in text):
            raise ToolError(f"{self.name} に制御文字は使えません")
        return text

    def json_schema(self) -> dict[str, Any]:
        """LLM の tools API に渡す JSON Schema を返す。"""
        if self.type == "enum":
            schema: dict[str, Any] = {"type": "string", "enum": list(self.choices)}
        elif self.type == "integer":
            schema = {"type": "integer"}
            if self.minimum is not None:
                schema["minimum"] = self.minimum
            if self.maximum is not None:
                schema["maximum"] = self.maximum
        else:
            schema = {"type": "string", "maxLength": self.max_length}
        if self.description:
            schema["description"] = self.description
        return schema


@dataclass(frozen=True)
class ToolSpec:
    """ツール1つ分の定義。"""

    name: str
    description: str
    command: tuple[str, ...]
    reply: str
    source: Path
    params: tuple[ToolParam, ...] = ()
    safety: str = "display"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    undo: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    cwd: str = ""
    examples: tuple[str, ...] = ()
    # 引数の値ごとの返事（reply の by で指定した enum 引数の値 → 文）。なければ reply を使う。
    reply_by: str = ""
    reply_map: tuple[tuple[str, str], ...] = ()

    @property
    def skill_dir(self) -> Path:
        """定義ファイルのあるフォルダ。コマンドの {skill_dir} に入る。"""
        return self.source.parent

    def tool_schema(self) -> dict[str, Any]:
        """ollama の tools API に渡す1ツール分の定義を返す。"""
        description = self.description
        if self.examples:
            description += "（例：" + "／".join(self.examples) + "）"
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {param.name: param.json_schema() for param in self.params},
                    "required": [param.name for param in self.params if param.required],
                },
            },
        }

    def validate_args(self, args: dict[str, Any] | None) -> dict[str, str]:
        """引数を検証し、コマンドへ埋め込む文字列の辞書を返す。知らない引数はエラーにする。"""
        args = dict(args or {})
        known = {param.name for param in self.params}
        unknown = set(args) - known
        if unknown:
            raise ToolError(f"未対応の引数です: {', '.join(sorted(unknown))}")
        values: dict[str, str] = {}
        for param in self.params:
            if param.name not in args or args[param.name] in (None, ""):
                if param.required:
                    raise ToolError(f"{param.name} が指定されていません")
                continue
            values[param.name] = param.validate(args[param.name])
        return values

    def build_argv(self, values: dict[str, str], command: tuple[str, ...] | None = None) -> list[str]:
        """コマンドの置き換えを埋めた引数リストを作る。値がない任意引数を含む要素は省く。"""
        fixed = {"python": sys.executable, "skill_dir": str(self.skill_dir), "argos_root": str(_argos_root())}
        argv: list[str] = []
        for part in command or self.command:
            names = _PLACEHOLDER.findall(part)
            if any(name not in fixed and name not in values for name in names):
                continue  # 任意引数が空なら、その要素ごと渡さない
            argv.append(_PLACEHOLDER.sub(lambda m: fixed.get(m.group(1), values.get(m.group(1), "")), part))
        return argv

    def format_reply(self, values: dict[str, str], stdout: str = "") -> str:
        """読み上げる結果の文に引数を埋めて返す。値ごとの返事があればそれを使う。

        {stdout} は、コマンドの標準出力の最後の空でない行に置き換える（残り時間など、毎回変わる返事のため）。
        """
        text = dict(self.reply_map).get(values.get(self.reply_by, ""), self.reply) if self.reply_by else self.reply
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        filled = {**values, "stdout": lines[-1] if lines else ""}
        return _PLACEHOLDER.sub(lambda m: filled.get(m.group(1), ""), text)


@dataclass
class ToolResult:
    """ツールを実行した結果。"""

    tool: str
    ok: bool
    reply: str
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: float = 0.0
    args: dict[str, str] = field(default_factory=dict)


def _argos_root() -> Path:
    """ARGOS のリポジトリの場所（src/argos の2つ上）を返す。"""
    return Path(__file__).resolve().parents[3]


def default_tool_dirs() -> tuple[Path, ...]:
    """ツール定義を探す既定の場所。スキルのフォルダと、利用者の追加の置き場。"""
    return (_argos_root() / "skills", Path("~/.config/argos/router-tools").expanduser())


def _trusted(path: Path) -> bool:
    """自分が持ち主で、他人が書き換えられないファイルだけを信頼する（コマンドを実行する設定のため）。"""
    try:
        info = path.stat()
    except OSError:
        return False
    if info.st_uid != os.getuid():
        log.warning("持ち主が違うため、ツール定義を読みません: %s", path)
        return False
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        log.warning("他人が書き換えられるため、ツール定義を読みません: %s", path)
        return False
    return True


def _definition_files(dirs: tuple[Path, ...]) -> list[Path]:
    """置き場からツール定義のファイルを集める。スキルは */router.yaml、追加の置き場は *.yaml。"""
    files: list[Path] = []
    for directory in dirs:
        if not directory.is_dir():
            continue
        files.extend(sorted(directory.glob("*/router.yaml")))
        files.extend(sorted(path for path in directory.glob("*.yaml") if path.name != "router.yaml"))
    return files


def _str_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    """文字列の配列を検証してタプルにする。"""
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ToolError(f"{field_name} は文字列の配列で指定してください")
    return tuple(value)


def _parse_param(name: str, raw: Any) -> ToolParam:
    """引数1つ分の定義を検証する。"""
    if not TOOL_NAME_PATTERN.match(name) or name in RESERVED_PLACEHOLDERS:
        raise ToolError(f"引数名が使えません: {name}")
    if not isinstance(raw, dict):
        raise ToolError(f"引数 {name} の定義はテーブルで指定してください")
    kind = raw.get("type", "string")
    if kind not in PARAM_TYPES:
        raise ToolError(f"引数 {name} の type は {', '.join(PARAM_TYPES)} のどれかです")
    choices = _str_tuple(raw.get("choices"), f"引数 {name} の choices")
    if kind == "enum" and not choices:
        raise ToolError(f"引数 {name} は enum なので choices が必要です")
    max_length = raw.get("max_length", 40)
    if type(max_length) is not int or not 1 <= max_length <= MAX_STRING_LENGTH:
        raise ToolError(f"引数 {name} の max_length は1〜{MAX_STRING_LENGTH}です")
    minimum, maximum = raw.get("minimum"), raw.get("maximum")
    for bound in (minimum, maximum):
        if bound is not None and type(bound) is not int:
            raise ToolError(f"引数 {name} の minimum・maximum は整数です")
    return ToolParam(
        name=name,
        type=kind,
        description=str(raw.get("description", "")),
        required=bool(raw.get("required", True)),
        choices=choices,
        max_length=max_length,
        minimum=minimum,
        maximum=maximum,
    )


def parse_tool(raw: Any, source: Path) -> ToolSpec:
    """ツール1つ分の定義を検証して ToolSpec にする。"""
    if not isinstance(raw, dict):
        raise ToolError("ツールの定義はテーブルで指定してください")
    allowed = {"name", "description", "params", "command", "reply", "safety", "timeout_seconds",
               "undo", "env", "cwd", "examples"}
    extra = set(raw) - allowed
    if extra:
        raise ToolError(f"未対応の項目があります: {', '.join(sorted(extra))}")
    name = raw.get("name", "")
    if not isinstance(name, str) or not TOOL_NAME_PATTERN.match(name):
        raise ToolError(f"ツール名は英小文字・数字・_ で、英小文字から始めてください: {name!r}")
    description = raw.get("description", "")
    reply = raw.get("reply", "")
    if not isinstance(description, str) or not description.strip():
        raise ToolError(f"{name}: description が必要です")
    params_raw = raw.get("params") or {}
    if not isinstance(params_raw, dict):
        raise ToolError(f"{name}: params はテーブルで指定してください")
    params = tuple(_parse_param(str(key), value) for key, value in params_raw.items())
    reply_by, reply_map = "", ()
    if isinstance(reply, dict):
        # reply: {by: <enum引数>, <値>: <文>, ..., default: <文>} の形。値ごとに返事を変える。
        reply = dict(reply)
        reply_by = reply.pop("by", "")
        target = next((param for param in params if param.name == reply_by and param.type == "enum"), None)
        if target is None:
            raise ToolError(f"{name}: reply の by には enum の引数名を書いてください")
        if not all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in reply.items()):
            raise ToolError(f"{name}: reply の値は文字列で書いてください")
        missing = [choice for choice in target.choices if choice not in reply] if "default" not in reply else []
        if missing:
            raise ToolError(f"{name}: reply に {', '.join(missing)} の文か default が必要です")
        reply_map = tuple((key, value.strip()) for key, value in reply.items() if key != "default")
        reply = reply.get("default", reply_map[0][1] if reply_map else "")
    if not isinstance(reply, str) or not reply.strip():
        raise ToolError(f"{name}: reply が必要です")
    command = _str_tuple(raw.get("command"), f"{name}: command")
    if not command:
        raise ToolError(f"{name}: command が必要です")
    undo = _str_tuple(raw.get("undo"), f"{name}: undo")
    known = set(RESERVED_PLACEHOLDERS) | {param.name for param in params}
    for part in (*command, *undo, reply, *(text for _, text in reply_map)):
        # {stdout} は返事の中だけで使える。
        allowed = known | ({"stdout"} if part == reply or part in dict(reply_map).values() else set())
        for placeholder in _PLACEHOLDER.findall(part):
            if placeholder not in allowed:
                raise ToolError(f"{name}: 知らない置き換え {{{placeholder}}} があります")
    safety = raw.get("safety", "display")
    if safety not in SAFETY_LEVELS:
        raise ToolError(f"{name}: safety は {', '.join(SAFETY_LEVELS)} のどれかです")
    timeout = raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < float(timeout) <= 120:
        raise ToolError(f"{name}: timeout_seconds は0より大きく120以下です")
    env_raw = raw.get("env") or {}
    if not isinstance(env_raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env_raw.items()):
        raise ToolError(f"{name}: env は文字列のテーブルで指定してください")
    cwd = raw.get("cwd", "")
    if not isinstance(cwd, str):
        raise ToolError(f"{name}: cwd は文字列です")
    examples = _str_tuple(raw.get("examples"), f"{name}: examples")
    if len(examples) > MAX_EXAMPLES:
        raise ToolError(f"{name}: examples は{MAX_EXAMPLES}個までです")
    return ToolSpec(
        name=name,
        description=description.strip(),
        command=command,
        reply=reply.strip(),
        source=source,
        params=params,
        safety=safety,
        timeout_seconds=float(timeout),
        undo=undo,
        env=tuple(sorted(env_raw.items())),
        cwd=cwd,
        examples=examples,
        reply_by=reply_by,
        reply_map=reply_map,
    )


def load_file(path: Path) -> list[ToolSpec]:
    """1つの定義ファイルを読み、ツールの一覧を返す。正しくなければ ToolError。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ToolError(f"読み込めません: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tools"), list):
        raise ToolError("tools: にツールの配列を書いてください")
    return [parse_tool(item, path) for item in data["tools"]]


class ToolRegistry:
    """ツール定義を置き場から読み、更新されたら読み直す。"""

    def __init__(self, dirs: tuple[Path, ...] | None = None) -> None:
        """探す場所を受け取る。省略時は既定の置き場。"""
        self._dirs = tuple(dirs) if dirs is not None else default_tool_dirs()
        self._lock = threading.Lock()
        self._stamp: tuple[tuple[str, float, int], ...] | None = None
        self._tools: dict[str, ToolSpec] = {}
        self.errors: dict[str, str] = {}

    def _current_stamp(self, files: list[Path]) -> tuple[tuple[str, float, int], ...]:
        """ファイルの一覧と更新時刻・大きさ。変わったら読み直す。"""
        stamp = []
        for path in files:
            try:
                info = path.stat()
            except OSError:
                continue
            stamp.append((str(path), info.st_mtime, info.st_size))
        return tuple(stamp)

    def tools(self) -> dict[str, ToolSpec]:
        """いま使えるツールを名前で引ける辞書で返す。必要なら読み直す。"""
        files = _definition_files(self._dirs)
        stamp = self._current_stamp(files)
        with self._lock:
            if stamp == self._stamp:
                return dict(self._tools)
            tools: dict[str, ToolSpec] = {}
            errors: dict[str, str] = {}
            for path in files:
                if not _trusted(path):
                    errors[str(path)] = "持ち主または書き込み権限が安全でないため読みません"
                    continue
                try:
                    specs = load_file(path)
                except ToolError as exc:
                    errors[str(path)] = str(exc)
                    log.warning("ツール定義を読み飛ばします: %s: %s", path, exc)
                    continue
                for spec in specs:
                    if spec.name in tools:
                        errors[str(path)] = f"ツール名 {spec.name} が {tools[spec.name].source} と重複しています"
                        log.warning("ツール名が重複しています: %s (%s)", spec.name, path)
                        continue
                    tools[spec.name] = spec
            self._tools, self.errors, self._stamp = tools, errors, stamp
            log.info("ツール定義を読み込みました: %d個（読み飛ばし %d件）", len(tools), len(errors))
            return dict(tools)

    def get(self, name: str) -> ToolSpec:
        """名前でツールを返す。なければ ToolError。"""
        spec = self.tools().get(name)
        if spec is None:
            raise ToolError(f"ツールがありません: {name}")
        return spec

    def run(self, name: str, args: dict[str, Any] | None = None, *, undo: bool = False) -> ToolResult:
        """ツールを検証して実行する。undo=True なら元に戻すコマンドを実行する。"""
        spec = self.get(name)
        values = spec.validate_args(args)
        command = spec.undo if undo else spec.command
        if not command:
            raise ToolError(f"{name} には元に戻すコマンドがありません")
        argv = spec.build_argv(values, command)
        env = {**os.environ, **dict(spec.env)}
        cwd = spec.build_argv(values, (spec.cwd,))[0] if spec.cwd else str(spec.skill_dir)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=spec.timeout_seconds, check=False
            )
        except subprocess.TimeoutExpired:
            elapsed = (time.monotonic() - started) * 1000
            log.warning("ツールが時間内に終わりませんでした: %s (%.0fms)", name, elapsed)
            return ToolResult(name, False, "", None, elapsed_ms=elapsed, args=values)
        except OSError as exc:
            log.warning("ツールを起動できませんでした: %s: %s", name, exc)
            return ToolResult(name, False, "", None, stderr=str(exc), args=values)
        elapsed = (time.monotonic() - started) * 1000
        ok = proc.returncode == 0
        log.info("ツール実行: %s %s 結果=%s (%.0fms)", name, json.dumps(values, ensure_ascii=False),
                 "成功" if ok else f"失敗({proc.returncode})", elapsed)
        return ToolResult(
            name, ok, spec.format_reply(values, proc.stdout) if ok and not undo else "", proc.returncode,
            stdout=proc.stdout[-2000:], stderr=proc.stderr[-2000:], elapsed_ms=elapsed, args=values,
        )
