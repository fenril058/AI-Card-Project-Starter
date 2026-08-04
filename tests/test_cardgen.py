from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cardgen", ROOT / "cardgen.py")
assert SPEC and SPEC.loader
cardgen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cardgen)


class CardGenTests(unittest.TestCase):
    def test_public_repository_tracks_no_input_or_image_assets(self) -> None:
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        tracked = [
            Path(raw.decode("utf-8"))
            for raw in completed.stdout.split(b"\0")
            if raw
        ]
        image_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".psd"}
        forbidden = [
            str(path)
            for path in tracked
            if path.parts[:1] == ("inputs",)
            or path.suffix.lower() in image_suffixes
        ]
        self.assertEqual(forbidden, [])

    def test_external_input_path_requires_absolute_existing_file(self) -> None:
        with self.assertRaises(cardgen.CardGenError):
            cardgen.resolve_external_input_path("relative-reference.png")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reference.png"
            path.write_bytes(b"fixture")
            self.assertEqual(
                cardgen.resolve_external_input_path(path), path.resolve()
            )

    def test_flux2_klein_edit_profile_validates(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "flux2-klein-edit")
        summary = cardgen.validate_profile_workflow(profile)
        self.assertEqual(summary["negative_prompt_mode"], "text")
        self.assertFalse(summary["multi_pass_detected"])
        self.assertEqual(summary["primary_sampler"], "73")
        self.assertEqual(summary["seed_field"], "noise_seed")

    def test_zimage_profile_validates(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "zimage")
        summary = cardgen.validate_profile_workflow(profile)
        self.assertEqual(summary["negative_prompt_mode"], "zeroed")
        self.assertFalse(summary["multi_pass_detected"])
        self.assertEqual(summary["sampler_count"], 1)

    def test_checkpoint_override_requires_approval(self) -> None:
        workflow = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "a.safetensors"},
            }
        }
        approved = {
            "checkpoints": {"a.safetensors", "b.safetensors"},
            "unet": set(),
            "clip": set(),
            "vae": set(),
            "loras": set(),
        }
        changed = cardgen.apply_checkpoint_override(
            workflow, approved, "b.safetensors"
        )
        self.assertEqual(changed, ["1"])
        self.assertEqual(workflow["1"]["inputs"]["ckpt_name"], "b.safetensors")

    def test_sdxl_prompt_fields_are_replaced(self) -> None:
        node = {
            "class_type": "CLIPTextEncodeSDXL",
            "inputs": {"text_g": "old", "text_l": "old"},
        }
        cardgen.set_prompt_node_text("10", node, "new")
        self.assertEqual(node["inputs"]["text_g"], "new")
        self.assertEqual(node["inputs"]["text_l"], "new")

    def test_bound_prompt_and_seed_are_replaced(self) -> None:
        workflow = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
            "3": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
        }
        bindings = {
            "positive_prompt": {"node_id": "1", "field": "text"},
            "negative_prompt": {"node_id": "2", "field": "text"},
            "seed": {"node_id": "3", "field": "noise_seed"},
        }
        cardgen.set_bound_prompts(workflow, bindings, "positive", "negative")
        cardgen.set_bound_seed(workflow, bindings, 42)
        self.assertEqual(workflow["1"]["inputs"]["text"], "positive")
        self.assertEqual(workflow["2"]["inputs"]["text"], "negative")
        self.assertEqual(workflow["3"]["inputs"]["noise_seed"], 42)


if __name__ == "__main__":
    unittest.main()
