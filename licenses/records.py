from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .constants import (
    PERMISSION_KEYS,
    SCHEMA_VERSION,
    VALID_PERMISSION_STATUSES,
    VALID_REVIEW_STATUSES,
)
from .errors import LicenseManagerError
from .registry import Asset
from .review import Review
from .io import (
    display_path,
    normalize_sha256,
    normalize_weights_sha256,
    resolve_model_path,
    resolve_path,
    safetensors_weights_sha256,
    sha256_file,
    weights_hash_matches,
)


def local_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def inspect_local_file(
    repository_root: Path,
    asset: Asset,
    selected_file: Mapping[str, Any],
    *,
    skip_hash: bool,
) -> dict[str, Any]:
    configured_path = (
        Path(asset.local_path).as_posix()
        if asset.local_path
        else None
    )
    local_path = resolve_model_path(
        repository_root,
        asset.local_path,
    )
    provider_sha256 = normalize_sha256(
        selected_file.get("provider_sha256")
    )
    provider_weights_sha256 = normalize_weights_sha256(
        selected_file.get("provider_weights_sha256")
    )
    expected_sha256 = normalize_sha256(
        selected_file.get("expected_sha256")
    )
    comparison_sha256 = provider_sha256 or expected_sha256

    if local_path is None:
        return {
            "configured_path": configured_path,
            "exists": False,
            "size_bytes": None,
            "sha256": None,
            "weights_sha256": None,
            "provider_sha256": provider_sha256,
            "provider_weights_sha256": provider_weights_sha256,
            "expected_sha256": expected_sha256,
            "verification": (
                "provider_hash_available"
                if comparison_sha256 or provider_weights_sha256
                else "not_verifiable"
            ),
        }

    if not local_path.exists():
        return {
            "configured_path": configured_path,
            "exists": False,
            "size_bytes": None,
            "sha256": None,
            "weights_sha256": None,
            "provider_sha256": provider_sha256,
            "provider_weights_sha256": provider_weights_sha256,
            "expected_sha256": expected_sha256,
            "verification": "local_file_missing",
        }

    if not local_path.is_file():
        raise LicenseManagerError(
            f"Local path is not a file: {local_path}"
        )

    local_sha256 = None if skip_hash else sha256_file(local_path)
    local_weights_sha256 = (
        None if skip_hash else safetensors_weights_sha256(local_path)
    )

    if skip_hash:
        verification = "hash_skipped"
    elif provider_weights_sha256 and local_weights_sha256:
        verification = (
            "weights_match"
            if weights_hash_matches(
                local_weights_sha256, provider_weights_sha256
            )
            else "weights_mismatch"
        )
    elif comparison_sha256 is None:
        verification = "provider_hash_unavailable"
    elif local_sha256 == comparison_sha256:
        verification = "exact_file_match"
    else:
        verification = "exact_file_mismatch"

    return {
        "configured_path": configured_path,
        "exists": True,
        "size_bytes": local_path.stat().st_size,
        "sha256": local_sha256,
        "weights_sha256": local_weights_sha256,
        "provider_sha256": provider_sha256,
        "provider_weights_sha256": provider_weights_sha256,
        "expected_sha256": expected_sha256,
        "verification": verification,
    }


def normalize_evidence(repository_root: Path, review: Review) -> list[dict[str, Any]]:
    result=[]
    for item in review.evidence:
        path=resolve_path(repository_root, item.path)
        assert path is not None
        result.append({
            "type": item.type,
            "path": display_path(repository_root, path),
            "exists": path.is_file(),
            "sha256": sha256_file(path) if path.is_file() else None,
            "note": item.note,
        })
    return result


def build_record(
    repository_root: Path,
    reviews_dir: Path,
    asset: Asset,
    provider_data: Mapping[str, Any],
    *,
    skip_hash: bool,
) -> dict[str, Any]:
    asset_id = asset.id
    review = Review.load_optional(reviews_dir / f"{asset_id}.toml")

    evidence = normalize_evidence(repository_root, review)

    selected_file = provider_data.get("selected_file")
    if not isinstance(selected_file, dict):
        raise LicenseManagerError(
            f"{asset_id}: provider returned invalid selected_file"
        )

    local = inspect_local_file(
        repository_root,
        asset,
        selected_file,
        skip_hash=skip_hash,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "asset_id": asset_id,
        "asset_type": asset.type,
        "generated_at": utc_now_iso(),
        "identity": {
            "name": (
                asset.name
                or provider_data.get("name")
                or asset_id
            ),
            "version_name": (
                asset.version_name
                or provider_data.get("version_name")
            ),
        },
        "source": {
            "provider": provider_data["provider"],
            "canonical_url": provider_data.get("canonical_url"),
            "api_url": provider_data.get("api_url"),
            "provider_model_id": (
                provider_data.get("provider_model_id")
            ),
            "provider_version_id": (
                provider_data.get("provider_version_id")
            ),
            "revision": provider_data.get("revision"),
            "retrieved_at": local_now_iso(),
        },
        "provenance": {
            "base_model": provider_data.get("base_model"),
            "notes": list(asset.provenance_notes),
        },
        "file": {
            "filename": selected_file.get("filename"),
            "download_url": selected_file.get("download_url"),
            "provider_size_bytes": selected_file.get("size_bytes"),
            **local,
        },
        "license": {
            "declared": provider_data.get("license_declared"),
            "declared_url": asset.license_url,
            "notes": list(asset.license_notes),
        },
        "review": {
            "status": review.status,
            "reviewed_at": review.reviewed_at,
            "reviewed_by": review.reviewed_by,
            "permissions": dict(review.permissions),
            "todo": list(review.todo),
            "notes": list(review.notes),
        },
        "evidence": evidence,
        "provider_metadata": (
            provider_data.get("provider_metadata") or {}
        ),
    }


def read_generated_records(
    models_dir: Path,
) -> list[dict[str, Any]]:
    if not models_dir.exists():
        return []

    records: list[dict[str, Any]] = []

    for path in sorted(models_dir.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LicenseManagerError(
                f"Invalid generated JSON {path}: {exc}"
            ) from exc

        if not isinstance(value, dict):
            raise LicenseManagerError(
                f"Expected object in {path}"
            )

        value["_record_path"] = path
        records.append(value)

    return records
