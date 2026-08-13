"""AGENTS.mdの「変更後は validate を実行する」を、散文ではなく実行で守らせる。

PostToolUse フックとして呼ばれる。標準入力のイベントJSONを読み、編集先が
`config/**.json` または `workflows/approved/**.json` のときだけ
`cardgen.py validate --all` を走らせる。失敗したら終了コード2で、標準エラーの
内容がそのままモデルへ差し戻される。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
WATCHED_PREFIXES = ("config/", "workflows/approved/")
FEEDBACK_LINES = 20


def watched(raw_path: str) -> bool:
    """このプロジェクト内の、検証対象JSONを指しているか。"""
    if not raw_path:
        return False
    try:
        relative = Path(raw_path).resolve().relative_to(PROJECT_DIR).as_posix()
    except (OSError, ValueError):
        # プロジェクト外のパス、または解決できないパス。
        return False
    return relative.endswith(".json") and relative.startswith(WATCHED_PREFIXES)


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    tool_input = event.get("tool_input") or {}
    if not watched(str(tool_input.get("file_path") or "")):
        return 0

    result = subprocess.run(
        ["uv", "run", "python", "cardgen.py", "validate", "--all"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # cardgenの日本語エラーはcp932で出るとここで化ける。子側もUTF-8に揃える。
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if result.returncode == 0:
        return 0

    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
    print(
        "validate --all が失敗した。生成を試す前に、いま編集した設定を直すこと。",
        file=sys.stderr,
    )
    print("\n".join(detail[-FEEDBACK_LINES:]), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
