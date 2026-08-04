from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .constants import PERMISSION_KEYS, VALID_PERMISSION_STATUSES, VALID_REVIEW_STATUSES
from .errors import LicenseManagerError
from .io import read_toml


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    type: str
    path: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Review:
    status: str
    reviewed_at: str | None
    reviewed_by: str | None
    permissions: Mapping[str, str]
    todo: tuple[str, ...]
    notes: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]

    @classmethod
    def pending(cls) -> "Review":
        return cls("pending", None, None, {k: "unknown" for k in PERMISSION_KEYS}, (), (), ())

    @classmethod
    def load_optional(cls, path: Path) -> "Review":
        document=read_toml(path, required=False)
        if not document:
            return cls.pending()
        status=document.get("status", "pending")
        if status not in VALID_REVIEW_STATUSES:
            raise LicenseManagerError(f"{path}: invalid status {status!r}")
        reviewed_at=document.get("reviewed_at"); reviewed_by=document.get("reviewed_by")
        if reviewed_at is not None and not isinstance(reviewed_at, str):
            raise LicenseManagerError(f"{path}: reviewed_at must be a string")
        if reviewed_by is not None and not isinstance(reviewed_by, str):
            raise LicenseManagerError(f"{path}: reviewed_by must be a string")
        raw_permissions=document.get("permissions", {})
        if not isinstance(raw_permissions, dict):
            raise LicenseManagerError(f"{path}: [permissions] must be a table")
        permissions={}
        for key in PERMISSION_KEYS:
            value=raw_permissions.get(key, "unknown")
            if value not in VALID_PERMISSION_STATUSES:
                raise LicenseManagerError(f"{path}: invalid permissions.{key} value {value!r}")
            permissions[key]=value
        def string_array(key: str) -> tuple[str, ...]:
            raw=document.get(key, [])
            if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
                raise LicenseManagerError(f"{path}: {key} must be an array of strings")
            return tuple(raw)
        raw_evidence=document.get("evidence", [])
        if not isinstance(raw_evidence, list):
            raise LicenseManagerError(f"{path}: [[evidence]] must be an array of tables")
        evidence=[]
        for i, item in enumerate(raw_evidence):
            if not isinstance(item, dict):
                raise LicenseManagerError(f"{path}: evidence[{i}] must be a table")
            typ=item.get("type", "other"); p=item.get("path"); note=item.get("note")
            if not isinstance(typ, str) or not typ.strip() or not isinstance(p, str) or not p.strip():
                raise LicenseManagerError(f"{path}: evidence[{i}] needs type/path strings")
            if note is not None and not isinstance(note, str):
                raise LicenseManagerError(f"{path}: evidence[{i}].note must be a string")
            evidence.append(EvidenceRef(typ.strip(), p.strip(), note))
        return cls(status, reviewed_at, reviewed_by, permissions,
                   string_array("todo"), string_array("notes"), tuple(evidence))
