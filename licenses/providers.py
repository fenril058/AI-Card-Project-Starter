from __future__ import annotations

import dataclasses
import os
from datetime import datetime
from typing import Any, Mapping, Sequence

import requests
from huggingface_hub import HfApi

from .registry import Asset
from .constants import USER_AGENT
from .errors import LicenseManagerError
from .io import normalize_sha256


def request_json(
    url: str,
    *,
    timeout: float,
    headers: Mapping[str, str] | None = None,
) -> Any:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if headers:
        request_headers.update(headers)

    try:
        response = requests.get(
            url,
            headers=request_headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise LicenseManagerError(
            f"HTTP request failed for {url}: {exc}"
        ) from exc
    except ValueError as exc:
        raise LicenseManagerError(
            f"Server returned invalid JSON for {url}"
        ) from exc


def choose_civitai_file(
    files: Sequence[Mapping[str, Any]],
    configured_filename: str | None,
) -> Mapping[str, Any]:
    if configured_filename:
        matches = [
            item for item in files
            if item.get("name") == configured_filename
        ]
        if len(matches) != 1:
            raise LicenseManagerError(
                f"Civitai file {configured_filename!r} "
                "was not found exactly once"
            )
        return matches[0]

    primary = [item for item in files if item.get("primary") is True]
    if len(primary) == 1:
        return primary[0]

    if len(files) == 1:
        return files[0]

    names = ", ".join(str(item.get("name")) for item in files)
    raise LicenseManagerError(
        "Civitai version contains multiple files. "
        "Set source.filename in registry.toml. "
        f"Available: {names}"
    )

def fetch_civitai(
    asset: Asset,
    *,
    timeout: float,
) -> dict[str, Any]:
    version_id = asset.source.model_version_id
    assert version_id is not None

    api_url = (
        "https://civitai.com/api/v1/model-versions/"
        f"{version_id}"
    )
    raw = request_json(api_url, timeout=timeout)

    if not isinstance(raw, dict):
        raise LicenseManagerError(
            f"Unexpected Civitai response for {asset.id}"
        )

    raw_files = raw.get("files") or []
    if not isinstance(raw_files, list) or not raw_files:
        raise LicenseManagerError(
            f"Civitai returned no files for {asset.id}"
        )

    files: list[dict[str, Any]] = []

    for index, item in enumerate(raw_files):
        if not isinstance(item, dict):
            raise LicenseManagerError(
                f"{asset.id}: Civitai files[{index}] must be an object"
            )
        files.append(item)

    selected = choose_civitai_file(
        files,
        asset.source.filename,
    )

    hashes = (
        selected.get("hashes")
        if isinstance(selected.get("hashes"), dict)
        else {}
    )
    provider_sha256 = normalize_sha256(
        hashes.get("SHA256") or hashes.get("sha256")
    )

    model_id = raw.get("modelId")
    canonical_url = (
        f"https://civitai.com/models/{model_id}"
        f"?modelVersionId={version_id}"
        if model_id
        else f"https://civitai.com/models?modelVersionId={version_id}"
    )

    model = raw.get("model")
    model_name = (
        model.get("name")
        if isinstance(model, dict)
        else None
    )

    size_kb = selected.get("sizeKB")
    provider_size_bytes = (
        int(float(size_kb) * 1000)
        if isinstance(size_kb, (int, float))
        else None
    )

    return {
        "provider": "civitai",
        "provider_model_id": model_id,
        "provider_version_id": raw.get("id", version_id),
        "name": asset.source.name or model_name,
        "version_name": raw.get("name"),
        "base_model": raw.get("baseModel"),
        "canonical_url": asset.source.url or canonical_url,
        "api_url": api_url,
        "revision": None,
        "license_declared": None,
        "selected_file": {
            "filename": selected.get("name"),
            "size_bytes": provider_size_bytes,
            "download_url": selected.get("downloadUrl"),
            "provider_sha256": provider_sha256,
        },
        "provider_metadata": {
            "created_at": raw.get("createdAt"),
            "published_at": raw.get("publishedAt"),
            "updated_at": raw.get("updatedAt"),
            "status": raw.get("status"),
        },
    }

def object_to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: object_to_jsonable(
                getattr(value, field.name)
            )
            for field in dataclasses.fields(value)
        }

    if isinstance(value, Mapping):
        return {
            str(key): object_to_jsonable(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [object_to_jsonable(item) for item in value]

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    if hasattr(value, "__dict__"):
        return {
            key: object_to_jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }

    return str(value)


def hf_lfs_sha256(sibling: Any) -> str | None:
    lfs = getattr(sibling, "lfs", None)

    if lfs is None:
        return None

    if isinstance(lfs, Mapping):
        return normalize_sha256(
            lfs.get("sha256") or lfs.get("oid")
        )

    return normalize_sha256(
        getattr(lfs, "sha256", None)
        or getattr(lfs, "oid", None)
    )


def fetch_huggingface(
    asset: Asset,
    *,
    timeout: float,
) -> dict[str, Any]:
    asset_id = asset.id
    repo_id = asset.source.repo_id
    filename = asset.source.filename
    revision = asset.source.revision or "main"
    assert repo_id is not None
    assert filename is not None

    api = HfApi(
        token=os.getenv("HF_TOKEN") or None,
        user_agent={"license_manager": "1.0"},
    )

    try:
        info = api.model_info(
            repo_id=repo_id,
            revision=revision,
            files_metadata=True,
            timeout=timeout,
        )
    except Exception as exc:
        raise LicenseManagerError(
            f"Hugging Face lookup failed for "
            f"{repo_id}@{revision}: {exc}"
        ) from exc

    matches = [
        item
        for item in list(info.siblings or [])
        if item.rfilename == filename
    ]
    if len(matches) != 1:
        raise LicenseManagerError(
            f"Hugging Face file {filename!r} "
            f"was not found exactly once in {repo_id}"
        )

    selected = matches[0]
    card_data = (
        object_to_jsonable(info.card_data)
        if info.card_data
        else {}
    )
    if not isinstance(card_data, dict):
        card_data = {}

    resolved_revision = info.sha or revision

    return {
        "provider": "huggingface",
        "provider_model_id": repo_id,
        "provider_version_id": None,
        "name": asset.source.name or repo_id.split("/")[-1],
        "version_name": revision,
        "base_model": card_data.get("base_model"),
        "canonical_url": (
            asset.source.url
            or f"https://huggingface.co/{repo_id}/blob/"
               f"{resolved_revision}/{filename}"
        ),
        "api_url": f"https://huggingface.co/api/models/{repo_id}",
        "revision": resolved_revision,
        "license_declared": card_data.get("license"),
        "selected_file": {
            "filename": filename,
            "size_bytes": getattr(selected, "size", None),
            "download_url": (
                f"https://huggingface.co/{repo_id}/resolve/"
                f"{resolved_revision}/{filename}"
            ),
            "provider_sha256": hf_lfs_sha256(selected),
        },
        "provider_metadata": {
            "last_modified": object_to_jsonable(
                getattr(info, "last_modified", None)
            ),
            "private": getattr(info, "private", None),
            "gated": object_to_jsonable(
                getattr(info, "gated", None)
            ),
            "pipeline_tag": getattr(info, "pipeline_tag", None),
        },
    }


def fetch_github_release(
    asset: Asset,
    *,
    timeout: float,
) -> dict[str, Any]:
    """Read one asset out of a tagged GitHub release.

    The release API reports asset sizes but no checksums, so provider_sha256
    stays None and verification falls back to hashing the local file.
    """
    repo_id = asset.source.repo_id
    filename = asset.source.filename
    tag = asset.source.revision
    if not repo_id or not filename or not tag:
        raise LicenseManagerError(
            f"{asset.id}: github_release needs repo_id, filename and revision"
        )

    api_url = f"https://api.github.com/repos/{repo_id}/releases/tags/{tag}"
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    release = request_json(api_url, timeout=timeout, headers=headers)

    matches = [
        item
        for item in release.get("assets", [])
        if item.get("name") == filename
    ]
    if len(matches) != 1:
        raise LicenseManagerError(
            f"GitHub release asset {filename!r} was not found exactly once "
            f"in {repo_id}@{tag}"
        )
    selected = matches[0]

    license_info = request_json(
        f"https://api.github.com/repos/{repo_id}/license",
        timeout=timeout,
        headers=headers,
    )
    declared = (license_info.get("license") or {}).get("spdx_id")

    return {
        "provider": "github_release",
        "provider_model_id": repo_id,
        "provider_version_id": release.get("id"),
        "name": asset.source.name or repo_id.split("/")[-1],
        "version_name": tag,
        "base_model": None,
        "canonical_url": (
            asset.source.url or f"https://github.com/{repo_id}"
        ),
        "api_url": api_url,
        "revision": tag,
        "license_declared": declared,
        "selected_file": {
            "filename": filename,
            "size_bytes": selected.get("size"),
            "download_url": selected.get("browser_download_url"),
            "provider_sha256": None,
        },
        "provider_metadata": {
            "published_at": release.get("published_at"),
            "license_url": license_info.get("html_url"),
            "prerelease": release.get("prerelease"),
        },
    }


def fetch_provider(
    asset: Asset,
    *,
    timeout: float,
) -> dict[str, Any]:
    provider = asset.source.provider

    if provider == "civitai":
        return fetch_civitai(asset, timeout=timeout)

    if provider == "huggingface":
        return fetch_huggingface(asset, timeout=timeout)

    if provider == "github_release":
        return fetch_github_release(asset, timeout=timeout)

    raise LicenseManagerError(f"Unsupported provider: {provider}")
