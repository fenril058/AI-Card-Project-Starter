from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .registry import Asset, Registry
from .constants import (
    PERMISSION_KEYS,
    SCHEMA_VERSION,
    VALID_PERMISSION_STATUSES,
    VALID_REVIEW_STATUSES,
)
from .errors import LicenseManagerError
from .io import (
    normalize_sha256,
    normalize_weights_sha256,
    resolve_model_path,
    resolve_path,
    safetensors_weights_sha256,
    sha256_file,
    write_json,
    weights_hash_matches,
)
from .providers import fetch_provider
from .records import build_record, read_generated_records
from .report import render_readme, write_report


class LicenseService:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.licenses_dir = self.repository_root / "licenses"
        self.registry_path = self.licenses_dir / "registry.toml"
        self.models_dir = self.licenses_dir / "models"
        self.reviews_dir = self.licenses_dir / "reviews"
        self.readme_path = self.licenses_dir / "README.md"

    def report(self) -> None:
        records = read_generated_records(self.models_dir)
        cleaned = [
            {
                key: value
                for key, value in record.items()
                if key != "_record_path"
            }
            for record in records
        ]
        write_report(self.readme_path, cleaned)
        print(f"Wrote {self.readme_path}")

    def sync(
        self,
        *,
        asset_ids: Sequence[str],
        timeout: float,
        skip_hash: bool,
        allow_hash_mismatch: bool,
        no_report: bool,
        fail_fast: bool,
    ) -> None:
        registry = Registry.load(self.registry_path)
        assets = registry.select(asset_ids)
        self.models_dir.mkdir(parents=True, exist_ok=True)

        failures: list[str] = []

        for asset in assets:
            asset_id = asset.id

            try:
                print(f"[sync] {asset_id}")
                provider_data = fetch_provider(
                    asset,
                    timeout=timeout,
                )
                record = build_record(
                    self.repository_root,
                    self.reviews_dir,
                    asset,
                    provider_data,
                    skip_hash=skip_hash,
                )

                if (
                    record["file"]["verification"] in {
                        "weights_mismatch",
                        "exact_file_mismatch",
                    }
                    and not allow_hash_mismatch
                ):
                    raise LicenseManagerError(
                        f"{asset_id}: local model identity does not "
                        "match the provider record"
                    )

                write_json(
                    self.models_dir / f"{asset_id}.json",
                    record,
                )

            except LicenseManagerError as exc:
                failures.append(str(exc))
                print(f"ERROR: {exc}")

                if fail_fast:
                    break

        if not no_report:
            self.report()

        if failures:
            raise LicenseManagerError(
                f"sync completed with {len(failures)} failure(s)"
            )

    def verify(
        self,
        *,
        asset_ids: Sequence[str],
        require_approved: bool,
        require_local_files: bool,
        require_evidence: bool,
        skip_hash: bool,
    ) -> None:
        registry = Registry.load(self.registry_path)
        assets = registry.select(asset_ids)

        records = {
            str(record.get("asset_id")): record
            for record in read_generated_records(self.models_dir)
        }

        errors: list[str] = []

        for asset in assets:
            asset_id = asset.id
            record = records.get(asset_id)

            if record is None:
                errors.append(
                    f"{asset_id}: generated record is missing; run sync"
                )
                continue

            errors.extend(
                self._validate_record(
                    asset,
                    record,
                    require_approved=require_approved,
                    require_local_files=require_local_files,
                    require_evidence=require_evidence,
                    skip_hash=skip_hash,
                )
            )

        expected_ids = {
            asset.id
            for asset in registry.assets
        }
        
        stale = sorted(set(records) - expected_ids)

        for asset_id in stale:
            errors.append(
                f"{asset_id}: generated record has no "
                "matching registry.toml entry"
            )

        expected_readme = render_readme([
            {
                key: value
                for key, value in record.items()
                if key != "_record_path"
            }
            for record in records.values()
        ])
        actual_readme = (
            self.readme_path.read_text(encoding="utf-8")
            if self.readme_path.exists()
            else ""
        )

        if actual_readme != expected_readme:
            errors.append(
                f"{self.readme_path}: out of date; run report"
            )

        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            raise LicenseManagerError(
                f"verification failed with {len(errors)} error(s)"
            )

        print(f"Verified {len(assets)} asset(s)")

    def _validate_record(
        self,
        asset: Asset,
        record: Mapping[str, Any],
        *,
        require_approved: bool,
        require_local_files: bool,
        require_evidence: bool,
        skip_hash: bool,
    ) -> list[str]:
        errors: list[str] = []
        asset_id = asset.id

        if record.get("schema_version") != SCHEMA_VERSION:
            errors.append(
                f"{asset_id}: unsupported generated schema_version"
            )

        if record.get("asset_id") != asset_id:
            errors.append(
                f"{asset_id}: generated asset_id mismatch"
            )

        review = record.get("review")
        if not isinstance(review, dict):
            errors.append(
                f"{asset_id}: missing review object"
            )
            review = {}

        status = review.get("status")
        if status not in VALID_REVIEW_STATUSES:
            errors.append(
                f"{asset_id}: invalid review status {status!r}"
            )

        if require_approved and status != "approved":
            errors.append(
                f"{asset_id}: review is not approved"
            )

        permissions = review.get("permissions")
        if not isinstance(permissions, dict):
            errors.append(
                f"{asset_id}: missing review permissions"
            )
        else:
            for key in PERMISSION_KEYS:
                if permissions.get(key) not in VALID_PERMISSION_STATUSES:
                    errors.append(
                        f"{asset_id}: invalid permission value for {key}"
                    )

        evidence = record.get("evidence")
        if not isinstance(evidence, list):
            errors.append(
                f"{asset_id}: evidence must be a list"
            )
            evidence = []

        if require_evidence and not evidence:
            errors.append(
                f"{asset_id}: evidence is required "
                "but no evidence is listed"
            )

        for item in evidence:
            if (
                isinstance(item, dict)
                and not item.get("exists")
            ):
                errors.append(
                    f"{asset_id}: missing evidence file "
                    f"{item.get('path')}"
                )

        local_path = resolve_model_path(
            self.repository_root,
            asset.local_path,
        )
        file_info = record.get("file")

        if not isinstance(file_info, dict):
            errors.append(
                f"{asset_id}: missing file object"
            )
            return errors

        if local_path is None:
            if require_local_files:
                errors.append(
                    f"{asset_id}: local_path is not configured"
                )
            return errors

        if not local_path.is_file():
            if require_local_files:
                errors.append(
                    f"{asset_id}: local file missing: {local_path}"
                )
            return errors

        if not skip_hash:
            actual = sha256_file(local_path)
            recorded = normalize_sha256(
                file_info.get("sha256")
            )

            if recorded is None:
                errors.append(
                    f"{asset_id}: generated record has "
                    "no local SHA-256"
                )
            elif actual != recorded:
                errors.append(
                    f"{asset_id}: local file changed since sync"
                )

            provider_sha256 = normalize_sha256(
                file_info.get("provider_sha256")
            )
            expected_sha256 = normalize_sha256(
                file_info.get("expected_sha256")
            )
            actual_weights = safetensors_weights_sha256(local_path)
            recorded_weights = normalize_sha256(
                file_info.get("weights_sha256")
            )
            if actual_weights != recorded_weights:
                errors.append(
                    f"{asset_id}: local weight identity changed since sync"
                )

            provider_weights = normalize_weights_sha256(
                file_info.get("provider_weights_sha256")
            )
            if provider_weights and actual_weights:
                if not weights_hash_matches(actual_weights, provider_weights):
                    errors.append(
                        f"{asset_id}: local weights differ from provider"
                    )
            elif (
                provider_sha256 or expected_sha256
            ) and actual != (provider_sha256 or expected_sha256):
                errors.append(
                    f"{asset_id}: local file differs from source record; "
                    "no comparable weight hash is available"
                )

        return errors
