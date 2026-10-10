"""利用枠を調べたときに、各CLIが残す記録を片付ける。

`/usage` を調べるたびに、CLIは会話の記録・ログ・コマンド履歴を残す。5分おきに調べると
大量にたまり、どこからも使わないため、今回の実行でできたものだけを消す。
前からあったもの、ほかのディレクトリやほかの時間に利用者が使った記録には触らない。
"""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path


def list_entries(directory: Path) -> set[str]:
    """ディレクトリの中の名前の一覧を返す。ディレクトリがなければ空にする。"""
    try:
        return {entry.name for entry in directory.iterdir()}
    except OSError:
        return set()


def remove_new_entries(directory: Path, before: set[str]) -> list[str]:
    """起動前になかったもの（今回の実行でできた記録）だけを消し、消した名前を返す。

    前からあったものには触らない。消せなかったものは残す。
    """
    removed: list[str] = []
    for name in sorted(list_entries(directory) - before):
        path = directory / name
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError:
            continue
        removed.append(name)
    return removed


def _is_own_history(line: str, workspace: str, commands: set[str], start_ms: int, end_ms: int) -> bool:
    """履歴の1行が、今回の実行で打ったコマンドか判定する。読めない行は、利用者のものとして残す。"""
    try:
        entry = json.loads(line)
    except ValueError:
        return False
    if not isinstance(entry, dict):
        return False
    timestamp = entry.get("timestamp")
    return (
        entry.get("workspace") == workspace
        and entry.get("display") in commands
        and isinstance(timestamp, (int, float))
        and start_ms <= timestamp <= end_ms
    )


def remove_history_lines(
    path: Path,
    *,
    workspace: str,
    commands: set[str],
    start_ms: int,
    end_ms: int,
    retries: int = 3,
    sleep=time.sleep,
) -> int:
    """コマンド履歴から、今回の実行で打ったコマンドの行だけを取り除き、取り除いた行数を返す。

    ディレクトリ・コマンド・時刻（今回の実行の間）がすべて当てはまる行だけを取り除く。
    書き戻す直前にファイルが変わっていたら（別のCLIが書き込んだら）、その行を消さないよう
    書き戻しをやめて、少し待ってからやり直す。retries回やり直しても変わり続けるなら、諦めて0を返す。
    """
    for attempt in range(retries):
        try:
            before = path.stat()
            text = path.read_text(encoding="utf-8")
        except OSError:
            return 0
        lines = text.splitlines(keepends=True)
        kept = [line for line in lines if not _is_own_history(line, workspace, commands, start_ms, end_ms)]
        removed = len(lines) - len(kept)
        if removed == 0:
            return 0
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                temp_file.writelines(kept)
            os.chmod(temp_name, before.st_mode & 0o777)
            current = path.stat()
            if (current.st_size, current.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                # 読んでから書き戻すまでに、別のCLIが書き込んだ。その行を失わないよう、やり直す。
                if attempt + 1 < retries:
                    sleep(0.2)
                continue
            os.replace(temp_name, path)
            return removed
        except OSError:
            return 0
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return 0
