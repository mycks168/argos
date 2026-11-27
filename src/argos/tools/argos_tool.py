"""エージェント向けのツール実行コマンド `argos-tool`。

高速振り分けと同じツール置き場（router.yaml）を読み、同じ検証で実行する。
呼ばれるたびに定義を読むので、ツールを足したらすぐ使える。

    argos-tool list            使えるツールの一覧
    argos-tool list --json     一覧をJSONで
    argos-tool run <名前> --args '{"destination": "博多駅"}'
"""

from __future__ import annotations

import argparse
import json
import sys

from argos.router.registry import ToolError, ToolRegistry


def _describe(registry: ToolRegistry) -> str:
    """人が読める一覧を作る。"""
    tools = registry.tools()
    lines = []
    for spec in tools.values():
        params = ", ".join(
            f"{p.name}{'' if p.required else '?'}:{('|'.join(p.choices)) if p.type == 'enum' else p.type}"
            for p in spec.params
        )
        mark = "（実行前に利用者へ確認）" if spec.safety == "confirm" else ""
        lines.append(f"- {spec.name}({params}): {spec.description}{mark}")
    for path, error in registry.errors.items():
        lines.append(f"! 読み飛ばし {path}: {error}")
    return "\n".join(lines) if lines else "使えるツールはありません。"


def main(argv: list[str] | None = None) -> int:
    """コマンドの入口。終了コードを返す。"""
    parser = argparse.ArgumentParser(prog="argos-tool", description="ARGOSのツール置き場のツールを使う")
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list", help="使えるツールの一覧")
    list_parser.add_argument("--json", action="store_true", help="JSONで出す")
    run_parser = sub.add_parser("run", help="ツールを実行する")
    run_parser.add_argument("name")
    run_parser.add_argument("--args", default="{}", help="引数のJSON（例: '{\"level\": 14}'）")
    run_parser.add_argument("--undo", action="store_true", help="元に戻すコマンドを実行する")
    options = parser.parse_args(argv)
    registry = ToolRegistry()

    if options.command == "list":
        if options.json:
            data = [spec.tool_schema()["function"] | {"safety": spec.safety} for spec in registry.tools().values()]
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(_describe(registry))
        return 0

    try:
        args = json.loads(options.args)
        if not isinstance(args, dict):
            raise ToolError("--args はJSONのオブジェクトで指定してください")
        result = registry.run(options.name, args, undo=options.undo)
    except (ToolError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(
        {"ok": result.ok, "reply": result.reply, "returncode": result.returncode,
         "stdout": result.stdout, "stderr": result.stderr},
        ensure_ascii=False,
    ))
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
