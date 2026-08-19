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

SAMPLER_FIELDS = ("steps", "cfg", "sampler_name", "scheduler")


def every_sampler_field(nodes: list[str] | None) -> dict[str, list[str] | None]:
    """One expectation shared by all four sampler options."""
    return {field: nodes for field in SAMPLER_FIELDS}


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
        """Pin where each override lands, per profile.

        docs/profiles-explained.md prints this matrix and tells readers that an
        option with nowhere to land is an error rather than a silent no-op.
        Moving any cell must fail here so the document is corrected in the same
        change. The sampler fields are resolved with the profile's bindings, the
        way command_generate does; without them this only exercises the blanket
        path and says nothing about what --steps actually does.
        """
        sample = {
            "steps": 40,
            "cfg": 3.5,
            "sampler_name": "dpmpp_2m",
            "scheduler": "karras",
        }
        # profile -> (denoise node, latent nodes, sampler field -> nodes).
        # None means the option is refused for that profile.
        expected: dict[
            str, tuple[str | None, list[str] | None, dict[str, list[str] | None]]
        ] = {
            "wai-hires": ("12", ["23", "5"], every_sampler_field(["10", "12"])),
            "wai-hires-latent": ("12", ["5"], every_sampler_field(["10", "12"])),
            "wai-single": (None, ["5"], every_sampler_field(["10"])),
            "zimage": (None, ["57:13"], every_sampler_field(["57:3"])),
            "wai-refine": ("12", None, every_sampler_field(["12"])),
            # FLUX.2 keeps each setting in its own node, so the profile binds
            # them one by one. Nothing carries a scheduler name. The resolution
            # is stated twice and both statements have to move together.
            "flux2-klein-edit": (
                None,
                ["62", "66"],
                {
                    "steps": ["62"],
                    "cfg": ["63"],
                    "sampler_name": ["61"],
                    "scheduler": None,
                },
            ),
            "esrgan-upscale": (None, None, every_sampler_field(None)),
            "wai-controlnet": (None, ["23", "31", "5"], every_sampler_field(["10", "12"])),
        }

        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profiles_dir = app["profiles_dir_path"]
        self.assertEqual(
            sorted(expected),
            sorted(path.stem for path in profiles_dir.glob("*.json")),
            "a profile was added or removed; update the matrix and the document",
        )
        self.assertEqual(
            sorted(SAMPLER_FIELDS),
            sorted(cardgen.SAMPLER_OVERRIDE_FIELDS),
            "an override field was added; update the matrix and the document",
        )

        for profile_id, (denoise, latents, sampler_fields) in expected.items():
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
                        cardgen.apply_latent_size(workflow, 832, 1216, bindings)
                else:
                    changed = cardgen.apply_latent_size(workflow, 832, 1216, bindings)
                    self.assertEqual(changed, latents)
                    # A listed node may state the size at its own scale, so read
                    # back what the profile declares rather than one value.
                    scales = {
                        entry["node_id"]: entry["scale"]
                        for entry in (cardgen.resolution_entries(bindings) or [])
                    }
                    for node_id in changed:
                        inputs = workflow[node_id]["inputs"]
                        scale = scales.get(node_id, 1.0)
                        self.assertEqual(
                            (inputs["width"], inputs["height"]),
                            (int(832 * scale), int(1216 * scale)),
                        )

            self.assertEqual(sorted(sampler_fields), sorted(SAMPLER_FIELDS))
            for field, nodes in sampler_fields.items():
                with self.subTest(profile=profile_id, option=field):
                    workflow = cardgen.load_json(profile["workflow_path"])
                    params = {field: sample[field]}
                    if nodes is None:
                        with self.assertRaises(cardgen.CardGenError):
                            cardgen.apply_sampler_params(workflow, params, bindings)
                    else:
                        changed = cardgen.apply_sampler_params(
                            workflow, params, bindings
                        )
                        self.assertEqual(changed[field], nodes)
                        # The returned node id says where the write was aimed,
                        # not that it happened. Read it back.
                        binding = bindings.get(field)
                        written = (
                            binding["field"] if isinstance(binding, dict) else field
                        )
                        for node_id in nodes:
                            self.assertEqual(
                                workflow[node_id]["inputs"][written], sample[field]
                            )

    def test_a_bound_sampler_param_moves_only_that_node(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "flux2-klein-edit")
        workflow = cardgen.load_json(profile["workflow_path"])
        changed = cardgen.apply_sampler_params(
            workflow, {"steps": 40}, profile["bindings"]
        )
        self.assertEqual(changed["steps"], ["62"])
        self.assertEqual(workflow["62"]["inputs"]["steps"], 40)
        # The sampler itself only holds links, which is why the blanket path had
        # nowhere to write and refused the option before the binding existed.
        self.assertEqual(workflow["64"]["class_type"], "SamplerCustomAdvanced")
        self.assertNotIn("steps", workflow["64"]["inputs"])

    def test_flux2_resolution_moves_in_both_places_at_once(self) -> None:
        """A run at 832x1216 measurably differs from one that left the scheduler
        at 1024x1344, while two identical runs are pixel-identical. So the two
        statements of the size have to move together or the graph means two
        things at once."""
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "flux2-klein-edit")
        workflow = cardgen.load_json(profile["workflow_path"])
        self.assertEqual(workflow["62"]["class_type"], "Flux2Scheduler")
        self.assertEqual(workflow["66"]["class_type"], "EmptyFlux2LatentImage")

        changed = cardgen.apply_latent_size(workflow, 832, 1216, profile["bindings"])
        self.assertEqual(changed, ["62", "66"])
        for node_id in ("62", "66"):
            inputs = workflow[node_id]["inputs"]
            self.assertEqual((inputs["width"], inputs["height"]), (832, 1216))

    def test_a_stale_resolution_node_is_refused(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "flux2-klein-edit")
        workflow = cardgen.load_json(profile["workflow_path"])
        for broken in (["62", "999"], ["62", "64"], "62", []):
            with self.subTest(resolution_nodes=broken):
                with self.assertRaises(cardgen.CardGenError):
                    cardgen.apply_latent_size(
                        workflow, 832, 1216, {"resolution_nodes": broken}
                    )

    def test_a_resolution_list_missing_the_latent_is_refused(self) -> None:
        """Listing nodes turns the empty-latent scan off, so an incomplete list
        would move the scheduler while the output size stayed put -- the very
        mismatch the listing exists to prevent, inverted and silent."""
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "flux2-klein-edit")
        workflow = cardgen.load_json(profile["workflow_path"])
        self.assertEqual(workflow["66"]["class_type"], "EmptyFlux2LatentImage")

        # 62 is Flux2Scheduler: real, resolution-bearing, and not the latent.
        with self.assertRaises(cardgen.CardGenError):
            cardgen.apply_latent_size(
                workflow, 832, 1216, {"resolution_nodes": ["62"]}
            )
        self.assertEqual(workflow["62"]["inputs"]["width"], 1024)
        self.assertEqual(workflow["66"]["inputs"]["width"], 1024)

        # The profile's own list covers both, so it still resolves.
        self.assertEqual(
            cardgen.resolve_resolution_nodes(workflow, profile["bindings"]),
            ["62", "66"],
        )

    def test_a_scaled_resolution_node_follows_the_base(self) -> None:
        """wai-hires states the size twice at different sizes: EmptyLatentImage
        holds the base pass and ImageScale holds the 1.5x hires target, and it
        is ImageScale that decides the saved image. Writing one value into both
        would be wrong, and writing only the latent left --width unable to
        change the output at all."""
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "wai-hires")
        workflow = cardgen.load_json(profile["workflow_path"])
        self.assertEqual(workflow["5"]["class_type"], "EmptyLatentImage")
        self.assertEqual(workflow["23"]["class_type"], "ImageScale")

        changed = cardgen.apply_latent_size(workflow, 832, 1216, profile["bindings"])
        self.assertEqual(changed, ["23", "5"])
        self.assertEqual((workflow["5"]["inputs"]["width"],
                          workflow["5"]["inputs"]["height"]), (832, 1216))
        self.assertEqual((workflow["23"]["inputs"]["width"],
                          workflow["23"]["inputs"]["height"]), (1248, 1824))

    def test_a_declared_scale_must_match_the_workflow(self) -> None:
        """The scale duplicates a ratio the workflow already states, so it can
        drift. Editing either side without the other has to fail here."""
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        workflow = cardgen.load_json(
            cardgen.load_profile(app, "wai-hires")["workflow_path"]
        )
        for entries in (
            [{"node_id": "5", "scale": 1}, {"node_id": "23", "scale": 2}],
            [{"node_id": "5", "scale": 1}, {"node_id": "23", "scale": 1}],
            # No scale 1 entry: nothing says what --width refers to.
            [{"node_id": "5", "scale": 2}, {"node_id": "23", "scale": 3}],
            [{"node_id": "5", "scale": 1}, {"node_id": "5", "scale": 1.5}],
            [{"node_id": "5", "scale": 0}],
            [{"node_id": "5", "scale": "1.5"}],
        ):
            with self.subTest(entries=entries):
                with self.assertRaises(cardgen.CardGenError):
                    cardgen.resolve_resolution_nodes(
                        workflow, {"resolution_nodes": entries}
                    )

    def test_a_bare_node_id_still_means_scale_one(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "flux2-klein-edit")
        workflow = cardgen.load_json(profile["workflow_path"])
        entries = cardgen.resolution_entries({"resolution_nodes": ["62", "66"]})
        self.assertEqual([e["scale"] for e in entries], [1.0, 1.0])
        cardgen.apply_latent_size(
            workflow, 832, 1216, {"resolution_nodes": ["62", "66"]}
        )
        for node_id in ("62", "66"):
            inputs = workflow[node_id]["inputs"]
            self.assertEqual((inputs["width"], inputs["height"]), (832, 1216))

    def test_a_scaled_latent_must_stay_on_the_eight_grid(self) -> None:
        """Latents are stored at 1/8 resolution, so their width and height must
        divide by 8. Pixel-space nodes carry no such rule, which is why the
        check is by node kind and not applied to every scaled value."""
        workflow = {
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 64, "height": 64, "batch_size": 1},
            },
            "9": {
                "class_type": "ImageScale",
                "inputs": {"width": 96, "height": 96, "crop": "disabled"},
            },
        }
        bindings = {
            "resolution_nodes": [
                {"node_id": "9", "scale": 1.5},
                {"node_id": "5", "scale": 1},
            ]
        }
        # 64*1.5 = 96 on the pixel node: fine, and not a multiple of 8 is fine.
        self.assertEqual(
            cardgen.apply_latent_size(workflow, 72, 72, bindings), ["5", "9"]
        )
        self.assertEqual(workflow["9"]["inputs"]["width"], 108)

        latent_scaled = {
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 64, "height": 64, "batch_size": 1},
            },
            "6": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 96, "height": 96, "batch_size": 1},
            },
        }
        with self.assertRaises(cardgen.CardGenError):
            cardgen.apply_latent_size(
                latent_scaled,
                72,
                72,
                {
                    "resolution_nodes": [
                        {"node_id": "5", "scale": 1},
                        {"node_id": "6", "scale": 1.5},
                    ]
                },
            )

    def test_a_resolution_node_naming_a_link_is_refused(self) -> None:
        """resolution_nodes is the one binding that never reaches bound_node.

        A listed node whose width is wired from somewhere else is the same slip
        bound_node refuses, and overwriting it would drop the edge.
        """
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "flux2-klein-edit")
        workflow = cardgen.load_json(profile["workflow_path"])
        workflow["901"] = {
            "class_type": "Stand-in",
            "inputs": {"width": ["66", 0], "height": 1216},
        }
        with self.assertRaises(cardgen.CardGenError):
            cardgen.apply_latent_size(
                workflow, 832, 1216, {"resolution_nodes": ["66", "901"]}
            )
        self.assertEqual(workflow["901"]["inputs"]["width"], ["66", 0])
        # The good node in the same list must not have been written either.
        self.assertEqual(workflow["66"]["inputs"]["width"], 1024)

    def test_a_stale_sampler_binding_fails_validation(self) -> None:
        """validate is the gate that keeps a stale node_id out of a run.

        The bindings are only resolved when an override is passed, so nothing
        else would notice a renumbered graph until --steps had already uploaded
        an image and queued work.
        """
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        summary = cardgen.validate_profile_workflow(
            cardgen.load_profile(app, "flux2-klein-edit")
        )
        self.assertEqual(
            summary["sampler_nodes"],
            {"steps": ["62"], "cfg": ["63"], "sampler_name": ["61"]},
        )

        broken_cases = [
            {"node_id": "999", "field": "steps"},
            {"node_id": "62", "field": "renamed"},
            # 63 is CFGGuider: positive is a link sitting next to cfg.
            {"node_id": "63", "field": "positive"},
            "62",
            ["62"],
        ]
        for broken in broken_cases:
            with self.subTest(steps=broken):
                profile = cardgen.load_profile(app, "flux2-klein-edit")
                profile["bindings"] = dict(profile["bindings"])
                profile["bindings"]["steps"] = broken
                with self.assertRaises(cardgen.CardGenError):
                    cardgen.validate_profile_workflow(profile)

    def test_a_null_binding_is_refused_when_the_profile_loads(self) -> None:
        """null and absent read alike at every use site, so a null binding drops
        the option without a word: seed swallows --seed, and null prompt
        bindings make --prompt required and then discard it while the metadata
        still records what the user typed. Catch it where the profile is read."""
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        real = json.loads(
            (ROOT / "config" / "profiles" / "flux2-klein-edit.json").read_text(
                encoding="utf-8"
            )
        )

        # Go through load_profile, not the checker: a check nothing calls is
        # worth nothing, and testing the function alone would not notice.
        for key in ("seed", "positive_prompt", "denoise", "steps",
                    "input_image", "resolution_nodes"):
            with self.subTest(key=key):
                broken = copy.deepcopy(real)
                broken["bindings"][key] = None
                with unittest.mock.patch.object(
                    cardgen, "load_json", return_value=broken
                ):
                    with self.assertRaises(cardgen.CardGenError):
                        cardgen.load_profile(app, "flux2-klein-edit")

        with unittest.mock.patch.object(
            cardgen, "load_json", return_value=copy.deepcopy(real)
        ):
            cardgen.load_profile(app, "flux2-klein-edit")
        for profile_id in ("flux2-klein-edit", "wai-hires", "esrgan-upscale"):
            cardgen.load_profile(app, profile_id)

    def test_a_null_seed_binding_would_have_swallowed_the_seed(self) -> None:
        """The consequence the load-time check exists to prevent. Kept as the
        record of why null cannot mean "unbound": the runtime cannot tell."""
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "flux2-klein-edit")
        workflow = cardgen.load_json(profile["workflow_path"])
        bindings = dict(profile["bindings"])
        bindings["seed"] = None
        self.assertEqual(cardgen.set_bound_seed(workflow, bindings, 1000), (None, None))
        self.assertEqual(workflow["73"]["inputs"]["noise_seed"], 0)

    def test_a_binding_that_names_a_link_is_refused(self) -> None:
        """Settings and links share one inputs dict, so a slip is writable."""
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "flux2-klein-edit")
        workflow = cardgen.load_json(profile["workflow_path"])
        self.assertEqual(workflow["63"]["inputs"]["positive"], ["77", 0])
        with self.assertRaises(cardgen.CardGenError):
            cardgen.apply_sampler_params(
                workflow,
                {"cfg": 3.5},
                {"cfg": {"node_id": "63", "field": "positive"}},
            )
        self.assertEqual(workflow["63"]["inputs"]["positive"], ["77", 0])

    def test_a_malformed_binding_is_a_cardgen_error(self) -> None:
        """main() prints CardGenError and lets anything else traceback."""
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "flux2-klein-edit")
        for broken in ("62", 62, ["62"]):
            with self.subTest(steps=broken):
                workflow = cardgen.load_json(profile["workflow_path"])
                with self.assertRaises(cardgen.CardGenError):
                    cardgen.apply_sampler_params(
                        workflow, {"steps": 40}, {"steps": broken}
                    )

    def test_an_unknown_bindings_key_is_refused(self) -> None:
        """A key nothing reads is a typo, and a silent no-op hides it."""
        with self.assertRaises(cardgen.CardGenError):
            cardgen.check_binding_keys({"step": {"node_id": "62", "field": "steps"}})
        cardgen.check_binding_keys(
            {field: {"node_id": "1", "field": field} for field in SAMPLER_FIELDS}
        )

    def test_a_sampler_binding_must_name_its_own_input(self) -> None:
        """A sampler binding naming a neighbouring input is writable and wrong.

        --steps then lands on that neighbour. On flux2-klein-edit, pointing
        bindings.steps at "width" makes --steps overwrite the width --width just
        set on node 62, leaving it disagreeing with the latent -- the mismatch
        resolution_nodes exists to prevent, with no error raised.
        """
        for named in ("width", "height", "cfg", "denoise", None):
            with self.subTest(field=named):
                with self.assertRaises(cardgen.CardGenError):
                    cardgen.check_sampler_binding_fields(
                        {"steps": {"node_id": "62", "field": named}}
                    )
        # seed and input_image legitimately name a different input, so the rule
        # is scoped to the four whose ComfyUI input names are fixed.
        cardgen.check_sampler_binding_fields(
            {"seed": {"node_id": "73", "field": "noise_seed"}}
        )
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        for profile_id in ("flux2-klein-edit", "wai-hires"):
            cardgen.load_profile(app, profile_id)

    def test_the_run_record_does_not_call_them_latent_nodes(self) -> None:
        """A listed resolution node need not be a latent -- flux2-klein-edit
        records Flux2Scheduler among them -- so the key cannot claim otherwise.
        Same name and shape as validate's resolution_nodes."""
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
                    self._generate_args(width=832, height=1216), app, "wai-hires"
                )
            self.assertEqual(code, 0)
            meta = json.loads(
                next(out.glob("*_metadata.json")).read_text(encoding="utf-8")
            )

        overrides = meta["setting_overrides"]
        self.assertNotIn("latent_nodes", overrides)
        self.assertEqual(overrides["resolution_nodes"], ["23", "5"])
        self.assertEqual(meta["schema_version"], 7)

    def test_the_scan_paths_refuse_to_write_over_an_edge(self) -> None:
        """bound_node has refused this since it was written; the scan paths that
        run when a profile names nothing had the same hazard and no guard.
        Overwriting an edge deletes it and the graph silently means something
        else, so neither path may write a setting over one."""
        sampler = {
            "1": {"class_type": "Elsewhere", "inputs": {}},
            "9": {
                "class_type": "KSampler",
                "inputs": {
                    "steps": ["1", 0],
                    "cfg": 6.0,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "seed": 1,
                },
            },
        }
        with self.assertRaises(cardgen.CardGenError):
            cardgen.apply_sampler_params(sampler, {"steps": 40}, None)
        self.assertEqual(sampler["9"]["inputs"]["steps"], ["1", 0])
        # A field that is a literal on the same node is still writable.
        self.assertEqual(
            cardgen.apply_sampler_params(sampler, {"cfg": 3.5}, None), {"cfg": ["9"]}
        )

        latent = {
            "1": {"class_type": "Elsewhere", "inputs": {}},
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": ["1", 0], "height": 1344, "batch_size": 1},
            },
        }
        with self.assertRaises(cardgen.CardGenError):
            cardgen.apply_latent_size(latent, 832, 1216)
        self.assertEqual(latent["5"]["inputs"]["width"], ["1", 0])

    def test_is_link_separates_a_setting_from_an_edge(self) -> None:
        self.assertTrue(cardgen.is_link(["12", 0]))
        self.assertTrue(cardgen.is_link({"node": "12"}))
        for setting in (28, 6.0, "euler", "", True, None):
            self.assertFalse(cardgen.is_link(setting))

    def test_an_unbound_sampler_param_still_moves_every_sampler(self) -> None:
        app = cardgen.load_app_config(ROOT / "config" / "app.json")
        profile = cardgen.load_profile(app, "wai-hires")
        workflow = cardgen.load_json(profile["workflow_path"])
        changed = cardgen.apply_sampler_params(
            workflow, {"steps": 40}, profile["bindings"]
        )
        self.assertEqual(changed["steps"], ["10", "12"])
        for node_id in ("10", "12"):
            self.assertEqual(workflow[node_id]["inputs"]["steps"], 40)

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

        self.assertEqual(meta["schema_version"], 7)
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
