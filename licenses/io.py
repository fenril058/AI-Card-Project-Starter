from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from .constants import CHUNK_SIZE
from .errors import LicenseManagerError


def read_toml(path: Path, *, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise LicenseManagerError(f"File not found: {path}")
        return {}
    try:
        with path.open("rb") as file:
            value = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise LicenseManagerError(f"Invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise LicenseManagerError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LicenseManagerError(f"Expected a TOML table in {path}")
    return value


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def normalize_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if re.fullmatch(r"[0-9A-F]{64}", normalized) else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(CHUNK_SIZE), b""):
                digest.update(chunk)
    except OSError as exc:
        raise LicenseManagerError(f"Cannot hash {path}: {exc}") from exc
    return digest.hexdigest().upper()


def resolve_path(repository_root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else repository_root / path


def display_path(repository_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repository_root).as_posix()
    except ValueError:
        return str(path)
