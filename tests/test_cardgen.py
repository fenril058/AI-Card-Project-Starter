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


class NodeInputSnapshotTests(unittest.TestCase):
    """The FLUX.2 graph keeps every sampler setting on a separate node."""

    WORKFLOW = {
        "61": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "62": {
            "class_type": "Flux2Scheduler",
            "inputs": {"steps": 28, "width": 1024, "height": 1344},
        },
        "63": {
            "class_type": "CFGGuider",
            "inputs": {"model": ["70", 0], "positive": ["77", 0], "cfg": 5.0},
        },
        "64": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {"noise": ["73", 0], "guider": ["63", 0]},
        },
        "73": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}},
        "74": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["71", 0], "text": "secret"},
        },
        "82": {"class_type": "LoadImage", "inputs": {"image": "private-reference.jpg"}},
    }

    def snapshot(self) -> dict:
        return cardgen.snapshot_node_inputs(copy.deepcopy(self.WORKFLOW))

    def test_settings_on_helper_nodes_are_recorded(self) -> None:
        snapshot = self.snapshot()
        self.assertEqual(snapshot["63"]["inputs"]["cfg"], 5.0)
        self.assertEqual(snapshot["62"]["inputs"]["steps"], 28)
        self.assertEqual(snapshot["61"]["inputs"]["sampler_name"], "euler")

    def test_links_are_not_recorded_as_settings(self) -> None:
        snapshot = self.snapshot()
        self.assertNotIn("64", snapshot)
        self.assertNotIn("model", snapshot["63"]["inputs"])

    def test_prompt_seed_and_input_image_are_withheld(self) -> None:
        snapshot = self.snapshot()
        serialised = json.dumps(snapshot)
        self.assertNotIn("74", snapshot)
        self.assertNotIn("82", snapshot)
        self.assertNotIn("73", snapshot)
        self.assertNotIn("secret", serialised)
        self.assertNotIn("private-reference.jpg", serialised)

    def test_graph_hash_is_order_independent(self) -> None:
        reordered = dict(reversed(list(self.WORKFLOW.items())))
        self.assertEqual(
            cardgen.sha256_json(self.WORKFLOW), cardgen.sha256_json(reordered)
        )

    def test_graph_hash_changes_when_a_setting_changes(self) -> None:
        edited = copy.deepcopy(self.WORKFLOW)
        edited["63"]["inputs"]["cfg"] = 3.5
        self.assertNotEqual(
            cardgen.sha256_json(self.WORKFLOW), cardgen.sha256_json(edited)
        )


if __name__ == "__main__":
    unittest.main()
