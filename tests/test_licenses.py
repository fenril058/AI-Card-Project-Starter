import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from licenses.io import resolve_model_path, safetensors_weights_sha256
from licenses.providers import configured_sha256
from licenses.records import inspect_local_file
from licenses.registry import Asset, Registry, Source
from licenses.report import render_readme
from project_env import load_project_env


class LicenseHashTest(unittest.TestCase):
    def _write_safetensors(self, path: Path, *, spacer: str) -> None:
        header = {
            "__metadata__": {
                "modelspec.hash_sha256": "0x" + "ab" * 32,
                "__spacer": spacer,
            }
        }
        encoded = json.dumps(header).encode("utf-8")
        path.write_bytes(len(encoded).to_bytes(8, "little") + encoded + b"weights")

    def test_weight_identity_ignores_header_padding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.safetensors"
            second = root / "second.safetensors"
            self._write_safetensors(first, spacer="")
            self._write_safetensors(second, spacer="padding")

            self.assertNotEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(
                safetensors_weights_sha256(first),
                safetensors_weights_sha256(second),
            )

    def test_provider_weight_hash_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.safetensors"
            self._write_safetensors(model, spacer="local padding")
            asset = Asset(
                id="model",
                type="controlnet",
                name="Model",
                version_name="1",
                local_path=str(model),
                source=Source(
                    provider="huggingface",
                    repo_id="owner/model",
                    filename="model.safetensors",
                    revision="main",
                ),
            )
            result = inspect_local_file(
                root,
                asset,
                {
                    "provider_sha256": "cd" * 32,
                    "provider_weights_sha256": "ab" * 6,
                },
                skip_hash=False,
            )

            self.assertEqual(result["verification"], "weights_match")
            self.assertEqual(result["configured_path"], str(model).replace("\\", "/"))


class LicenseCoverageTest(unittest.TestCase):
    def test_every_approved_model_has_a_license_record(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = Registry.load(root / "licenses" / "registry.toml")
        registered = {
            Path(asset.local_path).name
            for asset in registry.assets
            if asset.local_path
        }
        approved: set[str] = set()
        for profile_path in (root / "config" / "profiles").glob("*.json"):
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            for filenames in profile["approved_models"].values():
                approved.update(filenames)

        self.assertEqual(approved - registered, set())

    def test_report_falls_back_to_reviewed_source_hashes(self) -> None:
        record = {
            "asset_id": "model",
            "asset_type": "checkpoint",
            "identity": {"name": "Model", "version_name": "1"},
            "source": {"provider": "civitai"},
            "file": {
                "filename": "model.safetensors",
                "sha256": None,
                "weights_sha256": None,
                "provider_sha256": "ab" * 32,
                "provider_weights_sha256": "cd" * 6,
                "verification": "local_file_missing",
            },
            "review": {
                "status": "approved",
                "permissions": {"generated_output_commercial_use": "allowed"},
            },
        }

        report = render_readme([record])
        self.assertIn("`ABABABABABAB…` (source)", report)
        self.assertIn("`CDCDCDCDCDCD` (source)", report)

    def test_configured_hash_can_pin_a_source_without_api_digest(self) -> None:
        asset = Asset(
            id="model",
            type="upscale_model",
            source=Source(
                provider="github_release",
                expected_sha256="ef" * 32,
            ),
        )
        self.assertEqual(configured_sha256(asset), ("EF" * 32))


class ProjectEnvironmentTest(unittest.TestCase):
    def test_dotenv_loads_models_dir_without_overriding_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                'CARDGEN_COMFYUI_MODELS_DIR="C:\\\\ComfyUI\\\\models"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                load_project_env(env_file)
                self.assertEqual(
                    os.environ["CARDGEN_COMFYUI_MODELS_DIR"],
                    "C:\\\\ComfyUI\\\\models",
                )
                os.environ["CARDGEN_COMFYUI_MODELS_DIR"] = "D:\\existing"
                load_project_env(env_file)
                self.assertEqual(
                    os.environ["CARDGEN_COMFYUI_MODELS_DIR"],
                    "D:\\existing",
                )

    def test_registry_model_path_uses_comfyui_models_root(self) -> None:
        models_dir = Path("C:/ComfyUI/models")
        with patch.dict(
            os.environ,
            {"CARDGEN_COMFYUI_MODELS_DIR": str(models_dir)},
        ):
            resolved = resolve_model_path(
                Path("C:/project"),
                "models/checkpoints/model.safetensors",
            )
        self.assertEqual(
            resolved,
            models_dir / "checkpoints" / "model.safetensors",
        )


if __name__ == "__main__":
    unittest.main()
