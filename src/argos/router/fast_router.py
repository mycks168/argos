"""発話を、ローカルの軽量 LLM（ollama の tools API）でツールに振り分ける。

ツールで片付く発話（地図の拡大、画面配置など）は、エージェントを呼ばずに ARGOS がその場で実行する。
どのツールにも当たらない、時間切れ、エラーのときは None を返し、呼び出し側は今までどおり
エージェントへ渡す。エージェントは振り分けがあることを知らない。

検証（stt-gateway 側 2026-11-22、206件）：gemma4:e4b の tools 方式で p50 50ms・p95 225ms、
エージェントへ回すべき発話をツールで実行した誤爆は 3.8%、ツールで片付く発話は 45件中44件を当てた。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from argos.router.registry import ToolError, ToolRegistry, ToolSpec

log = logging.getLogger(__name__)

ROUTER_SYSTEM_PROMPT = (
    "あなたは車載アシスタントの振り分け係です。利用者の発話が、渡したツールのどれか1つで"
    "そのまま片付くときだけ、そのツールを呼びます。"
    "質問・相談・雑談・開発の指示・ツールの仕組みについての話・ツールで片付かない依頼では、"
    "ツールを呼ばず、何も書かずに終えます。"
    "「そこ」「さっきの」のように前の会話が分からないと決まらない依頼でも、ツールを呼びません。"
    "引数は発話に出てきた言葉だけを使い、推測で補いません。"
)


@dataclass(frozen=True)
class RouteDecision:
    """振り分けの結果。tool が None ならエージェントへ渡す。"""

    tool: str | None
    args: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    reason: str = ""


class FastRouter:
    """ollama の /api/chat に tools を渡して、使うツールを1つ選ばせる。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        url: str,
        model: str,
        timeout_seconds: float = 0.8,
        keep_alive: str = "30m",
        post: Callable[..., Any] = requests.post,
    ) -> None:
        """ツール置き場、ollama の URL・モデル・待ち時間を受け取る。"""
        self._registry = registry
        self._url = url.rstrip("/") + "/api/chat"
        self._model = model
        self._timeout = max(0.05, float(timeout_seconds))
        self._keep_alive = keep_alive
        self._post = post

    def decide(self, text: str) -> RouteDecision:
        """発話を振り分ける。ツールが決まらなければ tool=None を返す（例外は投げない）。"""
        started = time.monotonic()

        def done(tool: str | None, args: dict[str, Any] | None = None, reason: str = "") -> RouteDecision:
            """経過時間を付けて結果を作る。"""
            return RouteDecision(tool, dict(args or {}), (time.monotonic() - started) * 1000, reason)

        tools = self._registry.tools()
        if not tools or not text.strip():
            return done(None, reason="no_tools" if not tools else "empty")
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": text.strip()},
            ],
            "tools": [spec.tool_schema() for spec in tools.values()],
            "stream": False,
            "think": False,
            "keep_alive": self._keep_alive,
            "options": {"temperature": 0},
        }
        try:
            response = self._post(self._url, json=body, timeout=self._timeout)
            response.raise_for_status()
            data = response.json()
        except requests.Timeout:
            return done(None, reason="timeout")
        except (requests.RequestException, ValueError) as exc:
            log.warning("振り分けの問い合わせに失敗しました: %s", exc)
            return done(None, reason="error")
        calls = (data.get("message") or {}).get("tool_calls") or []
        if not calls:
            return done(None, reason="agent")
        function = (calls[0] or {}).get("function") or {}
        name = function.get("name")
        args = function.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                return done(None, reason="bad_args")
        if not isinstance(name, str) or name not in tools or not isinstance(args, dict):
            return done(None, reason="unknown_tool")
        try:
            tools[name].validate_args(args)
        except ToolError as exc:
            # 引数が足りない・おかしいときは実行せず、エージェントへ渡す。
            return done(None, reason=f"invalid_args:{name}:{exc}")
        return done(name, args, reason="tool")

    def spec(self, name: str) -> ToolSpec:
        """ツールの定義を返す。"""
        return self._registry.get(name)
