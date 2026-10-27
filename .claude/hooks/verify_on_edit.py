"""AGENTS.mdの「変更したら検査を走らせる」を、散文ではなく実行で守らせる。

PostToolUse フックとして呼ばれる。標準入力のイベントJSONを読み、編集先のパスで
走らせる検査を選ぶ。

- `config/**.json`、`workflows/approved/**.json` → `cardgen.py validate --all`
- `cardgen.py`、`tests/**.py`                    → `pytest`

どちらでもないパスなら何も走らせない。検査が失敗したら終了コード2で、標準エラーの
内容がそのままモデルへ差し戻される。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
FEEDBACK_LINES = 20

VALIDATE = (
    ["uv", "run", "python", "cardgen.py", "validate", "--all"],
    "validate --all が失敗した。生成を試す前に、いま編集した設定を直すこと。",
)
PYTEST = (
    ["uv", "run", "python", "-m", "pytest", "-q"],
    "pytest が失敗した。いま編集した cardgen.py かテストを直すこと。",
)
CONFIG_PREFIXES = ("config/", "workflows/approved/")


def select_check(raw_path: str) -> tuple[list[str], str] | None:
    """編集先に対応する検査。プロジェクト外や無関係なファイルならNone。"""
    if not raw_path:
        return None
    try:
        relative = Path(raw_path).resolve().relative_to(PROJECT_DIR).as_posix()
    except (OSError, ValueError):
        # プロジェクト外のパス、または解決できないパス。
        return None
    if relative.endswith(".json") and relative.startswith(CONFIG_PREFIXES):
        return VALIDATE
    if relative == "cardgen.py" or (
        relative.startswith("tests/") and relative.endswith(".py")
    ):
        return PYTEST
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    tool_input = event.get("tool_input") or {}
    check = select_check(str(tool_input.get("file_path") or ""))
    if check is None:
        return 0
    command, note = check

    result = subprocess.run(
        command,
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
    print(note, file=sys.stderr)
    print("\n".join(detail[-FEEDBACK_LINES:]), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
