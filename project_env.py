"""Load the small, explicitly supported project-local .env surface."""

from __future__ import annotations

import os
import re
from pathlib import Path


SUPPORTED_KEYS = {"CARDGEN_COMFYUI_MODELS_DIR"}
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ProjectEnvError(ValueError):
    pass


def load_project_env(path: Path) -> None:
    """Load supported keys without overriding the process environment."""
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ProjectEnvError(f"Cannot read {path}: {exc}") from exc

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not KEY_PATTERN.fullmatch(key) or key not in SUPPORTED_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if not value:
            raise ProjectEnvError(
                f"{path}:{line_number}: {key} must not be empty"
            )
        os.environ.setdefault(key, value)
