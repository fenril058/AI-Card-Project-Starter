from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import LicenseManagerError
from .io import read_toml


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise LicenseManagerError(f"{context} must be a TOML table")
    return value


def _required_string(value: Mapping[str, Any], key: str, context: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise LicenseManagerError(f"{context}.{key} must be a non-empty string")
    return item.strip()


def _optional_string(value: Mapping[str, Any], key: str, context: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise LicenseManagerError(f"{context}.{key} must be a non-empty string when present")
    return item.strip()


@dataclass(frozen=True, slots=True)
class Source:
    provider: str
    model_version_id: int | None = None
    repo_id: str | None = None
    filename: str | None = None
    revision: str | None = None
    url: str | None = None
    name: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], context: str) -> "Source":
        provider = _required_string(value, "provider", context).lower()
        if provider == "civitai":
            version_id = value.get("model_version_id")
            if not isinstance(version_id, int) or version_id <= 0:
                raise LicenseManagerError(f"{context}.model_version_id must be a positive integer")
            return cls(provider=provider, model_version_id=version_id,
                       filename=_optional_string(value, "filename", context),
                       url=_optional_string(value, "url", context),
                       name=_optional_string(value, "name", context))
        if provider == "huggingface":
            return cls(provider=provider,
                       repo_id=_required_string(value, "repo_id", context),
                       filename=_required_string(value, "filename", context),
                       revision=_optional_string(value, "revision", context) or "main",
                       url=_optional_string(value, "url", context),
                       name=_optional_string(value, "name", context))
        if provider == "github_release":
            return cls(provider=provider,
                       repo_id=_required_string(value, "repo_id", context),
                       filename=_required_string(value, "filename", context),
                       revision=_required_string(value, "revision", context),
                       url=_optional_string(value, "url", context),
                       name=_optional_string(value, "name", context))
        raise LicenseManagerError(
            f"{context}.provider must be civitai, huggingface or github_release"
        )


@dataclass(frozen=True, slots=True)
class Asset:
    id: str
    type: str
    source: Source
    name: str | None = None
    version_name: str | None = None
    local_path: str | None = None
    license_url: str | None = None
    license_notes: tuple[str, ...] = ()
    provenance_notes: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], context: str) -> "Asset":
        asset_id = _required_string(value, "id", context)
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", asset_id):
            raise LicenseManagerError(f"{context}.id has invalid characters")
        def string_array(key: str) -> tuple[str, ...]:
            raw = value.get(key, [])
            if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
                raise LicenseManagerError(f"{context}.{key} must be an array of strings")
            return tuple(raw)
        return cls(
            id=asset_id,
            type=_required_string(value, "type", context),
            name=_optional_string(value, "name", context),
            version_name=_optional_string(value, "version_name", context),
            local_path=_optional_string(value, "local_path", context),
            license_url=_optional_string(value, "license_url", context),
            license_notes=string_array("license_notes"),
            provenance_notes=string_array("provenance_notes"),
            source=Source.from_mapping(_mapping(value.get("source"), f"{context}.source"), f"{context}.source"),
        )


@dataclass(frozen=True, slots=True)
class Registry:
    schema_version: int
    assets: tuple[Asset, ...]

    @classmethod
    def load(cls, path: Path) -> "Registry":
        document = read_toml(path)
        if document.get("schema_version") != 1:
            raise LicenseManagerError(f"{path}: schema_version must be 1")
        raw_assets = document.get("assets")
        if not isinstance(raw_assets, list):
            raise LicenseManagerError(f"{path}: [[assets]] entries are required")
        assets=[]; seen=set()
        for index, raw in enumerate(raw_assets):
            asset=Asset.from_mapping(_mapping(raw, f"{path}: assets[{index}]"), f"{path}: assets[{index}]")
            if asset.id in seen:
                raise LicenseManagerError(f"{path}: duplicate asset id {asset.id!r}")
            seen.add(asset.id); assets.append(asset)
        return cls(1, tuple(assets))

    def select(self, requested_ids: Sequence[str]) -> tuple[Asset, ...]:
        if not requested_ids:
            return self.assets
        by_id={asset.id: asset for asset in self.assets}
        unknown=sorted(set(requested_ids)-set(by_id))
        if unknown:
            raise LicenseManagerError(f"Unknown asset id(s): {', '.join(unknown)}")
        return tuple(by_id[x] for x in requested_ids)
