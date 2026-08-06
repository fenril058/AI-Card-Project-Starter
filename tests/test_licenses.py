import json
import tempfile
import unittest
from pathlib import Path

from licenses.io import safetensors_weights_sha256
from licenses.records import inspect_local_file
from licenses.registry import Asset, Registry, Source


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


if __name__ == "__main__":
    unittest.main()
