from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
import unittest.mock
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

    def test_esrgan_upscale_profile_validates_without_a_sampler(self) -> None:
        """拡大だけのワークフローは prompt も seed も Sampler も持たない。

        cardgen は元々どれも必須にしていたので、3箇所を緩めてある。ここが通らなく
        なったら、決定的な工程を ComfyUI へ寄せる経路が閉じたということ。
        """
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "esrgan-upscale")
        summary = cardgen.validate_profile_workflow(profile)
        self.assertEqual(summary["sampler_count"], 0)
        self.assertIsNone(summary["primary_sampler"])
        self.assertIsNone(summary["seed_field"])
        self.assertEqual(summary["positive_prompt_nodes"], [])
        self.assertTrue(summary["input_image_required"])

    def test_seed_binding_is_optional_but_a_broken_one_is_not(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "esrgan-upscale")
        workflow = cardgen.load_json(profile["workflow_path"])
        self.assertEqual(cardgen.set_bound_seed(workflow, {}, 1), (None, None))
        with self.assertRaises(cardgen.CardGenError):
            cardgen.set_bound_seed(workflow, {"seed": "30"}, 1)

    def test_prompt_bindings_are_all_or_nothing(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "esrgan-upscale")
        workflow = cardgen.load_json(profile["workflow_path"])
        self.assertEqual(cardgen.set_bound_prompts(workflow, {}, "p", "n"), ([], [], []))
        with self.assertRaises(cardgen.CardGenError):
            cardgen.set_bound_prompts(
                workflow, {"positive_prompt": {"node_id": "90", "field": "filename_prefix"}},
                "p", "n",
            )

    def test_zimage_profile_validates(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "zimage")
        summary = cardgen.validate_profile_workflow(profile)
        self.assertEqual(summary["negative_prompt_mode"], "zeroed")
        self.assertFalse(summary["multi_pass_detected"])
        self.assertEqual(summary["sampler_count"], 1)

    def test_input_image_binding_is_resolved_before_a_run(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "wai-refine")
        summary = cardgen.validate_profile_workflow(profile)
        self.assertTrue(summary["input_image_required"])
        self.assertIsNotNone(summary["input_image_node"])

    def test_stale_input_image_binding_fails_validation(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "wai-refine")
        profile["bindings"] = copy.deepcopy(profile["bindings"])
        profile["bindings"]["input_image"]["node_id"] = "9999"
        with self.assertRaises(cardgen.CardGenError):
            cardgen.validate_profile_workflow(profile)

    def test_profiles_without_input_image_report_it(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "wai-hires")
        summary = cardgen.validate_profile_workflow(profile)
        self.assertFalse(summary["input_image_required"])
        self.assertIsNone(summary["input_image_node"])

    def test_denoise_moves_only_the_bound_node(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "wai-hires")
        workflow = cardgen.load_json(profile["workflow_path"])
        base_before = workflow["10"]["inputs"]["denoise"]
        node_id = cardgen.set_bound_denoise(workflow, profile["bindings"], 0.6)
        self.assertEqual(node_id, "12")
        self.assertEqual(workflow["12"]["inputs"]["denoise"], 0.6)
        # The base pass must keep denoise 1.0; a fractional value there would
        # turn generation from empty latent into img2img over noise.
        self.assertEqual(workflow["10"]["inputs"]["denoise"], base_before)
        self.assertEqual(base_before, 1.0)

    def test_denoise_is_rejected_outside_zero_to_one(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "wai-hires")
        workflow = cardgen.load_json(profile["workflow_path"])
        for value in (-0.1, 1.5):
            with self.assertRaises(cardgen.CardGenError):
                cardgen.set_bound_denoise(workflow, profile["bindings"], value)

    def test_denoise_is_refused_without_a_binding(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "wai-single")
        workflow = cardgen.load_json(profile["workflow_path"])
        with self.assertRaises(cardgen.CardGenError):
            cardgen.set_bound_denoise(workflow, {}, 0.45)
        self.assertEqual(workflow["10"]["inputs"]["denoise"], 1.0)

    def test_hires_pair_differs_only_in_the_upscale_path(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        pixel = cardgen.load_json(
            cardgen.load_profile(app, "wai-hires")["workflow_path"]
        )
        latent = cardgen.load_json(
            cardgen.load_profile(app, "wai-hires-latent")["workflow_path"]
        )
        # The comparison is only meaningful while every sampler setting matches.
        for node_id in ("10", "12"):
            for field in ("steps", "cfg", "sampler_name", "scheduler", "denoise"):
                self.assertEqual(
                    pixel[node_id]["inputs"][field],
                    latent[node_id]["inputs"][field],
                    f"node {node_id} {field} differs between the hires profiles",
                )

    def test_documented_override_matrix_holds(self) -> None:
        """Pin which profiles accept --denoise, --width/--height and --steps.

        docs/profiles-explained.md prints this matrix and tells readers that an
        option with nowhere to land is an error rather than a silent no-op.
        Widening any cell (teaching --steps to reach Flux2Scheduler, say) must
        fail here so the document is corrected in the same change.
        """
        # profile -> (denoise node, latent nodes, steps nodes). None means the
        # option is refused for that profile.
        expected: dict[str, tuple[str | None, list[str] | None, list[str] | None]] = {
            "wai-hires": ("12", ["5"], ["10", "12"]),
            "wai-hires-latent": ("12", ["5"], ["10", "12"]),
            "wai-single": (None, ["5"], ["10"]),
            "zimage": (None, ["57:13"], ["57:3"]),
            "wai-refine": ("12", None, ["12"]),
            # steps and cfg live outside the sampler node, so neither override
            # has anywhere to land. --width moves the latent but not the
            # width/height Flux2Scheduler carries of its own.
            "flux2-klein-edit": (None, ["66"], None),
            "esrgan-upscale": (None, None, None),
            "wai-controlnet": (None, ["5"], ["10", "12"]),
        }

        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profiles_dir = app["profiles_dir_path"]
        self.assertEqual(
            sorted(expected),
            sorted(path.stem for path in profiles_dir.glob("*.json")),
            "a profile was added or removed; update the matrix and the document",
        )

        for profile_id, (denoise, latents, steps) in expected.items():
            profile = cardgen.load_profile(app, profile_id)
            raw_bindings = profile.get("bindings")
            bindings = raw_bindings if isinstance(raw_bindings, dict) else {}

            with self.subTest(profile=profile_id, option="--denoise"):
                workflow = cardgen.load_json(profile["workflow_path"])
                if denoise is None:
                    with self.assertRaises(cardgen.CardGenError):
                        cardgen.set_bound_denoise(workflow, bindings, 0.5)
                else:
                    node_id = cardgen.set_bound_denoise(workflow, bindings, 0.5)
                    self.assertEqual(node_id, denoise)

            with self.subTest(profile=profile_id, option="--width/--height"):
                workflow = cardgen.load_json(profile["workflow_path"])
                if latents is None:
                    with self.assertRaises(cardgen.CardGenError):
                        cardgen.apply_latent_size(workflow, 832, 1216)
                else:
                    changed = cardgen.apply_latent_size(workflow, 832, 1216)
                    self.assertEqual(changed, latents)

            with self.subTest(profile=profile_id, option="--steps"):
                workflow = cardgen.load_json(profile["workflow_path"])
                if steps is None:
                    with self.assertRaises(cardgen.CardGenError):
                        cardgen.apply_sampler_params(workflow, {"steps": 40})
                else:
                    changed = cardgen.apply_sampler_params(workflow, {"steps": 40})
                    self.assertEqual(changed["steps"], steps)

    def _generate_args(self, **overrides: object) -> argparse.Namespace:
        base = dict(
            prompt="test prompt",
            negative=None,
            checkpoint=None,
            input_image=None,
            count=1,
            seed=1000,
            timeout=5,
            width=None,
            height=None,
            steps=None,
            cfg=None,
            sampler_name=None,
            scheduler=None,
            denoise=None,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_a_failed_run_still_writes_metadata(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        # Inside the project: project_relative() refuses paths outside it, and
        # outputs/ is gitignored so nothing leaks into the tree.
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temp_dir:
            app["output_dir_path"] = Path(temp_dir)
            boom = cardgen.CardGenError("ComfyUI execution error")
            with unittest.mock.patch.object(
                cardgen, "queue_prompt", side_effect=boom
            ), unittest.mock.patch.object(cardgen, "comfy_versions", return_value={}):
                with self.assertRaises(cardgen.CardGenError):
                    cardgen.command_generate(
                        self._generate_args(), app, "wai-hires"
                    )

            written = list(Path(temp_dir).glob("*_metadata.json"))
            self.assertEqual(len(written), 1)
            meta = json.loads(written[0].read_text(encoding="utf-8"))

        self.assertEqual(meta["schema_version"], 6)
        self.assertEqual(meta["status"], "error")
        self.assertEqual(meta["results"], [])
        self.assertEqual(meta["failure"]["error_type"], "CardGenError")
        self.assertEqual(meta["failure"]["failed_on_image"], 1)
        # The point of writing on failure is that the run stays traceable.
        self.assertEqual(meta["profile"], "wai-hires")
        self.assertTrue(meta["workflow_sha256"])

    def test_a_successful_run_is_marked_ok(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temp_dir:
            out = Path(temp_dir)
            app["output_dir_path"] = out
            image = out / "fake.png"
            image.write_bytes(b"not really a png")
            with unittest.mock.patch.object(
                cardgen, "queue_prompt", return_value="pid-1"
            ), unittest.mock.patch.object(
                cardgen, "wait_for_history", return_value={}
            ), unittest.mock.patch.object(
                cardgen, "download_outputs", return_value=[image]
            ), unittest.mock.patch.object(
                cardgen, "comfy_versions", return_value={}
            ):
                code = cardgen.command_generate(
                    self._generate_args(), app, "wai-hires"
                )
            self.assertEqual(code, 0)
            meta = json.loads(
                next(out.glob("*_metadata.json")).read_text(encoding="utf-8")
            )

        self.assertEqual(meta["status"], "ok")
        self.assertIsNone(meta["failure"])
        self.assertEqual(len(meta["results"]), 1)
        self.assertEqual(meta["results"][0]["seed"], 1000)

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


class WeightIdentityTests(unittest.TestCase):
    """Whole-file SHA-256 changes with header padding; the weights hash does not."""

    @staticmethod
    def write_safetensors(path: Path, metadata: dict | None, padding: int = 0) -> None:
        header: dict = {"a": {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}}
        if metadata is not None:
            header["__metadata__"] = metadata
        blob = json.dumps(header).encode("utf-8") + b" " * padding
        path.write_bytes(len(blob).to_bytes(8, "little") + blob + b"\x00\x00")

    def test_reads_the_declared_weight_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.safetensors"
            self.write_safetensors(
                path,
                {
                    "modelspec.hash_sha256": "0xABC123",
                    "modelspec.title": "example_v1",
                },
            )
            identity = cardgen.safetensors_weight_identity(path)
        # Normalised: distributors write it both with and without the prefix.
        self.assertEqual(identity["weights_sha256"], "abc123")
        self.assertEqual(identity["modelspec_title"], "example_v1")

    def test_padding_changes_the_file_hash_but_not_the_weight_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain.safetensors"
            padded = Path(tmp) / "padded.safetensors"
            metadata = {"modelspec.hash_sha256": "0xDEADBEEF"}
            self.write_safetensors(plain, metadata)
            self.write_safetensors(padded, metadata, padding=4096)

            self.assertNotEqual(cardgen.sha256_file(plain), cardgen.sha256_file(padded))
            self.assertEqual(
                cardgen.safetensors_weight_identity(plain)["weights_sha256"],
                cardgen.safetensors_weight_identity(padded)["weights_sha256"],
            )

    def test_missing_metadata_and_other_formats_are_not_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / "bare.safetensors"
            self.write_safetensors(bare, None)
            self.assertIsNone(
                cardgen.safetensors_weight_identity(bare)["weights_sha256"]
            )

            checkpoint = Path(tmp) / "upscaler.pth"
            checkpoint.write_bytes(b"not safetensors")
            self.assertIsNone(
                cardgen.safetensors_weight_identity(checkpoint)["weights_sha256"]
            )

            truncated = Path(tmp) / "truncated.safetensors"
            truncated.write_bytes(b"\x04")
            self.assertIsNone(
                cardgen.safetensors_weight_identity(truncated)["weights_sha256"]
            )


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
