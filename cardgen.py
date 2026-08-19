#!/usr/bin/env python3
"""Run approved ComfyUI API workflows through named generation profiles.

The project is intentionally standard-library only. Configuration is split into:

- config/app.json: settings shared by every profile
- config/profiles/<profile>.json: workflow and approved models for one pipeline

Only localhost ComfyUI endpoints and project-local files are accepted.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from project_env import ProjectEnvError, load_project_env


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_APP_CONFIG = PROJECT_DIR / "config" / "app.json"
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
APP_CONFIG_SCHEMA_VERSION = 3
PROFILE_SCHEMA_VERSION = 4
SAMPLER_TYPES = {"KSampler", "KSamplerAdvanced", "SamplerCustomAdvanced"}
LATENT_TYPES = {
    "EmptyLatentImage",
    "EmptySD3LatentImage",
    "EmptyFlux2LatentImage",
}

# Sampler inputs that --steps/--cfg/--sampler/--scheduler may overwrite.
SAMPLER_OVERRIDE_FIELDS = ("steps", "cfg", "sampler_name", "scheduler")

# bindingsで指名できるキー。読む側が知らないキーを黙って無視すると、綴りを1文字
# 間違えたプロファイルがvalidateを通り、generateで「bindings.stepsを追加してくだ
# さい」と言われる。追加したつもりの本人には何が違うのか見えない。
BINDING_KEYS = frozenset(
    {
        "positive_prompt",
        "negative_prompt",
        "seed",
        "input_image",
        "denoise",
        "resolution_nodes",
        *SAMPLER_OVERRIDE_FIELDS,
    }
)

# Sampler inputs recorded in run metadata so a result can be traced to settings.
SAMPLER_RECORD_FIELDS = (
    "steps",
    "cfg",
    "sampler_name",
    "scheduler",
    "denoise",
    "add_noise",
    "start_at_step",
    "end_at_step",
    "return_with_leftover_noise",
)

# class_type -> input field -> approval category
MODEL_INPUTS: dict[str, dict[str, str]] = {
    "CheckpointLoaderSimple": {"ckpt_name": "checkpoints"},
    "CheckpointLoader": {"ckpt_name": "checkpoints"},
    "UNETLoader": {"unet_name": "unet"},
    "CLIPLoader": {"clip_name": "clip"},
    "DualCLIPLoader": {"clip_name1": "clip", "clip_name2": "clip"},
    "TripleCLIPLoader": {
        "clip_name1": "clip",
        "clip_name2": "clip",
        "clip_name3": "clip",
    },
    "VAELoader": {"vae_name": "vae"},
    "LoraLoader": {"lora_name": "loras"},
    "LoraLoaderModelOnly": {"lora_name": "loras"},
    "UpscaleModelLoader": {"model_name": "upscale_models"},
    "ControlNetLoader": {"control_net_name": "controlnet"},
    "DiffControlNetLoader": {"control_net_name": "controlnet"},
}

# Prompt-bearing node types supported by the mutator.
PROMPT_FIELDS: dict[str, tuple[str, ...]] = {
    "CLIPTextEncode": ("text",),
    "CLIPTextEncodeSDXL": ("text_g", "text_l"),
    "CLIPTextEncodeSDXLRefiner": ("text",),
}


class CardGenError(RuntimeError):
    """Expected user-facing error."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise CardGenError(f"ファイルがありません: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CardGenError(f"JSONが不正です: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CardGenError(f"JSONの最上位はオブジェクトである必要があります: {path}")
    return value


def resolve_project_path(raw_path: str | Path) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    path = path.resolve()
    try:
        path.relative_to(PROJECT_DIR)
    except ValueError as exc:
        raise CardGenError(f"プロジェクト外のパスは使用できません: {path}") from exc
    return path


def resolve_external_input_path(raw_path: str | Path) -> Path:
    """Resolve one explicitly supplied local input without taking ownership of it."""
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise CardGenError(
            "--input-imageは入力を所有するプロジェクト内の絶対パスで指定してください。"
        )
    path = path.resolve()
    if not path.is_file():
        raise CardGenError(f"入力画像がありません: {path}")
    return path


def project_relative(path: Path) -> str:
    return str(path.resolve().relative_to(PROJECT_DIR))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    """Hash a graph by content, independent of key order and whitespace."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# A safetensors header is a little-endian u64 length followed by that many bytes
# of JSON. Anything larger than this is not a header we should try to parse.
SAFETENSORS_HEADER_LIMIT = 64 * 1024 * 1024


def safetensors_weight_identity(path: Path) -> dict[str, Any]:
    """Read the weight identity a safetensors file declares about itself.

    Whole-file SHA-256 does not identify a model. WAI v17 downloaded from
    Civitai hashes differently from the file Civitai lists, purely because the
    header carries an extra padding field — the weights are identical. Comparing
    whole-file hashes against a distributor therefore produces false alarms.

    ``modelspec.hash_sha256`` covers the tensor data only, and matches Civitai's
    AutoV3, so it is the field that can be checked against a distributor.

    Returns empty values rather than raising: a missing or unusual header must
    not stop a generation run.
    """
    identity: dict[str, Any] = {"weights_sha256": None, "modelspec_title": None}
    if path.suffix.lower() != ".safetensors":
        return identity
    try:
        with path.open("rb") as handle:
            raw_length = handle.read(8)
            if len(raw_length) < 8:
                return identity
            length = int.from_bytes(raw_length, "little")
            if not 0 < length <= SAFETENSORS_HEADER_LIMIT:
                return identity
            header = json.loads(handle.read(length))
    except (OSError, ValueError):
        return identity

    metadata = header.get("__metadata__")
    if not isinstance(metadata, dict):
        return identity

    declared = metadata.get("modelspec.hash_sha256")
    if isinstance(declared, str) and declared:
        # Distributors write it with and without the 0x prefix; normalise so the
        # recorded value can be compared directly.
        identity["weights_sha256"] = declared.removeprefix("0x").lower()
    title = metadata.get("modelspec.title")
    if isinstance(title, str) and title:
        identity["modelspec_title"] = title
    return identity


# Approval category -> ComfyUI model folders, in search order. ComfyUI accepts
# several names per category (unet/diffusion_models, clip/text_encoders) and a
# portable install usually has both directories present but only one populated.
MODEL_DIR_CANDIDATES: dict[str, tuple[str, ...]] = {
    "checkpoints": ("checkpoints", "Stable-Diffusion"),
    "unet": ("diffusion_models", "unet"),
    "clip": ("text_encoders", "clip"),
    "vae": ("vae",),
    "loras": ("loras", "Lora"),
    "upscale_models": ("upscale_models",),
    "controlnet": ("controlnet",),
}


def hash_model_files(
    models_dir: Path | None, model_uses: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Hash the weight files a run actually loaded.

    A filename does not pin a model: replacing the file under the same name
    silently changes every later result. Hashing is cached on (size, mtime)
    because checkpoints are multi-gigabyte and a batch would otherwise re-read
    them on every run.
    """
    records: list[dict[str, Any]] = []
    if models_dir is None:
        return records

    cache_path = PROJECT_DIR / ".cache" / "model-hashes.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    dirty = False

    for use in model_uses:
        category, filename = use.get("category"), use.get("filename")
        if not isinstance(category, str) or not isinstance(filename, str):
            continue
        entry: dict[str, Any] = {"category": category, "filename": filename}
        path = None
        for folder in MODEL_DIR_CANDIDATES.get(category, (category,)):
            candidate = models_dir / folder / filename
            if candidate.is_file():
                path = candidate
                break
        if path is None:
            entry["sha256"] = None
            entry["note"] = "file not found under comfyui_models_dir"
            records.append(entry)
            continue
        entry["folder"] = path.parent.name
        stat = path.stat()
        key = f"{path.parent.name}/{filename}"
        cached = cache.get(key)
        if (
            isinstance(cached, dict)
            and cached.get("size") == stat.st_size
            and cached.get("mtime_ns") == stat.st_mtime_ns
            and "weights_sha256" in cached
        ):
            entry["sha256"] = cached.get("sha256")
            entry["weights_sha256"] = cached.get("weights_sha256")
            entry["modelspec_title"] = cached.get("modelspec_title")
        else:
            entry["sha256"] = sha256_file(path)
            entry.update(safetensors_weight_identity(path))
            cache[key] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": entry["sha256"],
                "weights_sha256": entry["weights_sha256"],
                "modelspec_title": entry["modelspec_title"],
            }
            dirty = True
        entry["size"] = stat.st_size
        records.append(entry)

    if dirty:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return records


def validate_local_url(base_url: str) -> None:
    parsed = urllib.parse.urlparse(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise CardGenError(
            "安全のためcomfy_urlはhttp://127.0.0.1:<port>または"
            "http://localhost:<port>のみ許可します。"
        )


def require_schema_version(
    document: dict[str, Any], label: str, expected: int
) -> None:
    """App config and profiles version independently; pass the one you mean."""
    if document.get("schema_version") != expected:
        raise CardGenError(
            f"{label}のschema_versionは{expected}である必要があります: "
            f"{document.get('schema_version')!r}"
        )


def load_app_config(path: Path) -> dict[str, Any]:
    app = load_json(path)
    require_schema_version(app, "app config", APP_CONFIG_SCHEMA_VERSION)

    base_url = str(app.get("comfy_url", "http://127.0.0.1:8188"))
    validate_local_url(base_url)
    app["comfy_url"] = base_url
    app["app_config_path"] = path

    default_profile = app.get("default_profile")
    if not isinstance(default_profile, str) or not PROFILE_ID_PATTERN.fullmatch(
        default_profile
    ):
        raise CardGenError("default_profileが不正です。")

    profiles_dir = app.get("profiles_dir", "config/profiles")
    if not isinstance(profiles_dir, str) or not profiles_dir:
        raise CardGenError("profiles_dirは文字列で指定してください。")
    app["profiles_dir_path"] = resolve_project_path(profiles_dir)

    output_dir = app.get("output_dir", "outputs")
    if not isinstance(output_dir, str) or not output_dir:
        raise CardGenError("output_dirは文字列で指定してください。")
    app["output_dir_path"] = resolve_project_path(output_dir)

    for key, default in (
        ("request_timeout_seconds", 60),
        ("generation_timeout_seconds", 900),
    ):
        value = app.get(key, default)
        if not isinstance(value, int) or value < 1:
            raise CardGenError(f"{key}は1以上の整数で指定してください。")
        app[key] = value

    # Optional. Without it a run records model filenames but cannot prove which
    # bytes were loaded. ComfyUI lives outside this project, so the value is an
    # absolute machine-specific path and belongs in the environment rather than
    # in the committed config.
    models_dir = os.environ.get("CARDGEN_COMFYUI_MODELS_DIR") or app.get(
        "comfyui_models_dir"
    )
    app["comfyui_models_dir_path"] = None
    if models_dir is not None:
        if not isinstance(models_dir, str) or not models_dir.strip():
            raise CardGenError("comfyui_models_dirは非空の文字列で指定してください。")
        resolved = Path(models_dir).expanduser()
        if not resolved.is_absolute():
            raise CardGenError("comfyui_models_dirは絶対パスで指定してください。")
        if not resolved.is_dir():
            raise CardGenError(f"comfyui_models_dirが存在しません: {resolved}")
        app["comfyui_models_dir_path"] = resolved

    return app


def validate_profile_id(profile_id: str) -> None:
    if not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise CardGenError(
            "profile IDは英数字で始まり、英数字・ピリオド・ハイフン・"
            "アンダースコアだけを使用してください。"
        )


def profile_path(app: dict[str, Any], profile_id: str) -> Path:
    validate_profile_id(profile_id)
    profiles_dir = app["profiles_dir_path"]
    if not isinstance(profiles_dir, Path):
        raise CardGenError("profiles_dir_pathが不正です。")
    return resolve_project_path(profiles_dir / f"{profile_id}.json")


def normalize_approved_models(profile: dict[str, Any]) -> dict[str, set[str]]:
    raw = profile.get("approved_models")
    if not isinstance(raw, dict):
        raise CardGenError("approved_modelsはオブジェクトで指定してください。")

    result: dict[str, set[str]] = {}
    allowed_categories = {
        "checkpoints", "unet", "clip", "vae", "loras", "upscale_models",
        "controlnet",
    }
    unknown = sorted(set(raw) - allowed_categories)
    if unknown:
        raise CardGenError(
            "approved_modelsに未対応カテゴリがあります: " + ", ".join(unknown)
        )

    for category in allowed_categories:
        values = raw.get(category, [])
        if not isinstance(values, list) or not all(
            isinstance(item, str) and item for item in values
        ):
            raise CardGenError(
                f"approved_models.{category}は空でない文字列の配列で指定してください。"
            )
        result[category] = set(values)

    if not any(result.values()):
        raise CardGenError("approved_modelsがすべて空です。")
    return result


def is_link(value: Any) -> bool:
    """True when a node input carries an edge rather than a setting.

    ComfyUI's API format puts both in one inputs dict; an edge is [node_id, slot].
    Writing a setting over one deletes the edge and silently changes the graph,
    so every write path has to ask this before it writes.
    """
    return isinstance(value, (list, dict))


def check_binding_values(bindings: dict[str, Any]) -> None:
    """Refuse a binding key whose value is null.

    Absent and null read the same at every use site, so a null lands as "no
    binding" and the option it names is dropped without a word: bindings.seed
    null swallows --seed, and null prompt bindings make --prompt required and
    then discard it while the metadata still records what the user typed.
    """
    empty = sorted(key for key, value in bindings.items() if value is None)
    if empty:
        raise CardGenError(
            f"bindingsの値がnullです: {'、'.join(empty)}。"
            "nullは指名の省略とは違い、そのオプションが黙って捨てられます。"
            "指名しないならキーごと消してください。"
        )


def check_binding_keys(bindings: dict[str, Any]) -> None:
    """Refuse a bindings key nothing reads, so a typo is not a silent no-op."""
    unknown = sorted(set(bindings) - BINDING_KEYS)
    if unknown:
        raise CardGenError(
            f"bindingsに未知のキーがあります: {'、'.join(unknown)}。"
            f"使えるのは{'、'.join(sorted(BINDING_KEYS))}です。"
        )


def check_sampler_binding_fields(bindings: dict[str, Any]) -> None:
    """Refuse a sampler binding that names an input other than the option itself.

    seed and input_image legitimately name a different input (noise_seed, image),
    so the rule cannot be general. ComfyUI fixes the names of these four, and
    every shipped profile already matches. Left unchecked, bindings.steps naming
    "width" passes validate and then writes --steps over the width --width just
    set on that same node, which is the mismatch resolution_nodes exists to stop.
    """
    for field in SAMPLER_OVERRIDE_FIELDS:
        binding = bindings.get(field)
        if not isinstance(binding, dict):
            continue
        named = binding.get("field")
        if named != field:
            raise CardGenError(
                f"bindings.{field}が指す入力は{field}でなければなりません: "
                f"field={named!r}。ComfyUIの入力名は固定で、別の入力を指すと"
                f"--{field}が無関係な設定を書き換えます。"
            )


def load_profile(app: dict[str, Any], requested_id: str | None) -> dict[str, Any]:
    profile_id = requested_id or str(app["default_profile"])
    path = profile_path(app, profile_id)
    profile = load_json(path)
    require_schema_version(profile, f"profile {profile_id}", PROFILE_SCHEMA_VERSION)

    actual_id = profile.get("id")
    if actual_id != profile_id:
        raise CardGenError(
            f"プロファイルIDがファイル名と一致しません: {actual_id!r} != {profile_id!r}"
        )

    display_name = profile.get("display_name")
    if not isinstance(display_name, str) or not display_name:
        raise CardGenError("display_nameは空でない文字列で指定してください。")

    workflow = profile.get("workflow")
    if not isinstance(workflow, str) or not workflow:
        raise CardGenError("workflowは空でない文字列で指定してください。")
    profile["workflow_path"] = resolve_project_path(workflow)
    profile["profile_path"] = path
    profile["approved_model_sets"] = normalize_approved_models(profile)

    capabilities = profile.get("capabilities", {})
    if not isinstance(capabilities, dict):
        raise CardGenError("capabilitiesはオブジェクトで指定してください。")
    expected_negative = capabilities.get("negative_prompt_mode")
    if expected_negative not in {None, "text", "zeroed", "mixed"}:
        raise CardGenError(
            "capabilities.negative_prompt_modeはtext、zeroed、mixedのいずれかです。"
        )
    if "refiner" in capabilities:
        raise CardGenError(
            "capabilities.refinerは廃止されました。Sampler数が2以上であることしか"
            "表しておらず、refinerとhires fixを区別できません。"
            "capabilities.multi_passへ改名してください。"
        )
    expected_multi_pass = capabilities.get("multi_pass")
    if expected_multi_pass is not None and not isinstance(expected_multi_pass, bool):
        raise CardGenError("capabilities.multi_passは真偽値で指定してください。")

    defaults = profile.get("defaults", {})
    if not isinstance(defaults, dict):
        raise CardGenError("defaultsはオブジェクトで指定してください。")
    negative_default = defaults.get("negative_prompt", "")
    if not isinstance(negative_default, str):
        raise CardGenError("defaults.negative_promptは文字列で指定してください。")

    bindings = profile.get("bindings")
    if bindings is not None and not isinstance(bindings, dict):
        raise CardGenError("bindingsはオブジェクトで指定してください。")
    if isinstance(bindings, dict):
        check_binding_keys(bindings)
        check_binding_values(bindings)
        check_sampler_binding_fields(bindings)

    return profile


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: int,
) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.URLError as exc:
        raise CardGenError(f"ComfyUIへ接続できません: {exc}") from exc

    if not body:
        return {}
    try:
        value = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CardGenError(f"ComfyUIの応答がJSONではありません: {path}") from exc
    if not isinstance(value, dict):
        raise CardGenError(f"ComfyUIの応答形式が想定外です: {path}")
    return value


def upload_input_image(app: dict[str, Any], image_path: Path) -> str:
    if not image_path.is_file():
        raise CardGenError(f"入力画像がありません: {image_path}")
    boundary = "----CardGen" + secrets.token_hex(16)
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        (
            'Content-Disposition: form-data; name="image"; '
            f'filename="{image_path.name}"\r\n'
        ).encode("utf-8")
    )
    body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
    body.extend(image_path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"{app['comfy_url'].rstrip('/')}/upload/image",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=app["request_timeout_seconds"]
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise CardGenError(f"入力画像をComfyUIへ送信できません: {exc}") from exc
    name = result.get("name") if isinstance(result, dict) else None
    subfolder = result.get("subfolder", "") if isinstance(result, dict) else ""
    if not isinstance(name, str) or not name:
        raise CardGenError(f"入力画像のアップロード応答が不正です: {result}")
    return str(Path(subfolder) / name) if subfolder else name


def sampler_nodes(
    workflow: dict[str, Any], *, required: bool = True
) -> list[tuple[str, dict[str, Any]]]:
    """required=False は拡大だけのワークフロー用。サンプラーが無くても空で返す。"""
    result: list[tuple[str, dict[str, Any]]] = []
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") in SAMPLER_TYPES:
            result.append((str(node_id), node))
    if not result and required:
        raise CardGenError("対応済みSamplerが見つかりません。")
    return result


def linked_node_id(value: Any, label: str) -> str:
    if isinstance(value, list) and value and isinstance(value[0], (str, int)):
        return str(value[0])
    raise CardGenError(f"{label}のノード接続を解釈できません。")


def set_prompt_node_text(node_id: str, node: dict[str, Any], text: str) -> None:
    class_type = str(node.get("class_type"))
    fields = PROMPT_FIELDS.get(class_type)
    inputs = node.get("inputs")
    if not fields or not isinstance(inputs, dict):
        raise CardGenError(
            f"未対応のプロンプトノードです: node {node_id} ({class_type})。"
            "対応済みはCLIPTextEncode、CLIPTextEncodeSDXL、"
            "CLIPTextEncodeSDXLRefinerです。"
        )

    present_fields = [field for field in fields if field in inputs]
    if not present_fields:
        raise CardGenError(
            f"プロンプト入力フィールドがありません: node {node_id} ({class_type})"
        )
    for field in present_fields:
        inputs[field] = text


def set_condition_text(
    workflow: dict[str, Any],
    sampler: dict[str, Any],
    sampler_input: str,
    text: str,
) -> tuple[str, bool]:
    """Apply prompt text to a sampler conditioning input.

    Returns ``(conditioning_node_id, text_applied)``. A negative input routed
    through ConditioningZeroOut is valid but has no independent negative text.
    """
    inputs = sampler.get("inputs")
    if not isinstance(inputs, dict) or sampler_input not in inputs:
        raise CardGenError(f"Samplerに{sampler_input}入力がありません。")

    node_id = linked_node_id(inputs[sampler_input], sampler_input)
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        raise CardGenError(f"条件付けノードがありません: {node_id}")

    class_type = str(node.get("class_type"))
    if class_type in PROMPT_FIELDS:
        set_prompt_node_text(node_id, node, text)
        return node_id, True

    if sampler_input == "negative" and class_type == "ConditioningZeroOut":
        node_inputs = node.get("inputs")
        if not isinstance(node_inputs, dict) or "conditioning" not in node_inputs:
            raise CardGenError(
                f"ConditioningZeroOutのconditioning入力がありません: node {node_id}"
            )
        source_id = linked_node_id(
            node_inputs["conditioning"], f"ConditioningZeroOut node {node_id}"
        )
        source = workflow.get(source_id)
        if not isinstance(source, dict):
            raise CardGenError(
                f"ConditioningZeroOutの接続元ノードがありません: node {source_id}"
            )
        return node_id, False

    raise CardGenError(
        f"{sampler_input}が対応済み条件付けへ直接接続されていません: "
        f"node {node_id} ({class_type})。ControlNet、Conditioning Combine、"
        "地域プロンプト等は個別対応が必要です。"
    )


def set_all_sampler_prompts(
    workflow: dict[str, Any], positive: str, negative: str
) -> tuple[list[str], list[str], list[str]]:
    positive_nodes: set[str] = set()
    negative_nodes: set[str] = set()
    zeroed_negative_nodes: set[str] = set()

    for _, sampler in sampler_nodes(workflow):
        positive_id, positive_applied = set_condition_text(
            workflow, sampler, "positive", positive
        )
        if not positive_applied:
            raise CardGenError(
                f"positiveプロンプトを適用できませんでした: node {positive_id}"
            )
        positive_nodes.add(positive_id)

        negative_id, negative_applied = set_condition_text(
            workflow, sampler, "negative", negative
        )
        negative_nodes.add(negative_id)
        if not negative_applied:
            zeroed_negative_nodes.add(negative_id)

    return (
        sorted(positive_nodes),
        sorted(negative_nodes),
        sorted(zeroed_negative_nodes),
    )


def bound_node(
    workflow: dict[str, Any], binding: dict[str, Any], label: str
) -> tuple[str, dict[str, Any], str]:
    node_id = str(binding.get("node_id", ""))
    field = binding.get("field")
    node = workflow.get(node_id)
    if not node_id or not isinstance(node, dict) or not isinstance(field, str):
        raise CardGenError(f"bindings.{label}が不正です。")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict) or field not in inputs:
        raise CardGenError(f"bindings.{label}の入力がありません: node {node_id}")
    if is_link(inputs[field]):
        # 他ノードからの接続。設定と接続は同じinputsに並んでいるので、隣を指した
        # bindingは書き込めてしまう。上書きすると辺が消え、グラフの意味が変わる。
        raise CardGenError(
            f"bindings.{label}が指しているのは設定ではなく他ノードからの接続です: "
            f"node {node_id} {field}"
        )
    return node_id, node, field


def resolve_bound_node(
    workflow: dict[str, Any], bindings: dict[str, Any], label: str
) -> tuple[str, dict[str, Any], str]:
    """Guard and resolve one binding without writing it. validate's entry point."""
    binding = bindings.get(label)
    if not isinstance(binding, dict):
        raise CardGenError(f"bindings.{label}が不正です。")
    return bound_node(workflow, binding, label)


def set_bound_value(
    workflow: dict[str, Any], bindings: dict[str, Any], label: str, value: Any
) -> tuple[str, str]:
    """Resolve one binding and write value into the node it names.

    Every caller used to repeat guard, resolve, write. The copies drifted: the
    sampler overrides arrived without the isinstance guard the others carried,
    so a binding written as a string surfaced as AttributeError instead of a
    CardGenError main() knows how to print.
    """
    node_id, node, field = resolve_bound_node(workflow, bindings, label)
    node["inputs"][field] = value
    return node_id, field


def set_bound_prompts(
    workflow: dict[str, Any], bindings: dict[str, Any], positive: str, negative: str
) -> tuple[list[str], list[str], list[str]]:
    positive_binding = bindings.get("positive_prompt")
    negative_binding = bindings.get("negative_prompt")
    if positive_binding is None and negative_binding is None:
        # 条件付けを持たないワークフロー（拡大だけの工程など）。書き込む先が無い。
        return [], [], []
    if not isinstance(positive_binding, dict) or not isinstance(negative_binding, dict):
        raise CardGenError(
            "bindingsのpositive_prompt/negative_promptは両方書くか、両方省くかのどちらかです。"
        )
    positive_id, positive_node, positive_field = bound_node(
        workflow, positive_binding, "positive_prompt"
    )
    negative_id, negative_node, negative_field = bound_node(
        workflow, negative_binding, "negative_prompt"
    )
    positive_node["inputs"][positive_field] = positive
    negative_node["inputs"][negative_field] = negative
    return [positive_id], [negative_id], []


def set_bound_seed(
    workflow: dict[str, Any], bindings: dict[str, Any], seed: int
) -> tuple[str | None, str | None]:
    """seed の binding が無いプロファイルでは何もしない。

    拡大だけの工程は決定的で、同じ入力からは常に同じ出力が出る。seed を書く先が
    無いのは設定の誤りではない。
    """
    if bindings.get("seed") is None:
        return None, None
    return set_bound_value(workflow, bindings, "seed", seed)


def set_bound_denoise(
    workflow: dict[str, Any], bindings: dict[str, Any], denoise: float
) -> str:
    """Overwrite denoise on the one node a profile names.

    Deliberately not part of SAMPLER_OVERRIDE_FIELDS. Those overrides hit every
    sampler exposing the field, which is right for steps/cfg but wrong here: a
    hires graph wants denoise 1.0 on the base pass and a fraction on the second.
    Applying one value to both turns the base pass into an img2img over empty
    latent. So denoise moves only where the profile points it.
    """
    if not 0.0 <= denoise <= 1.0:
        raise CardGenError("--denoiseは0.0以上1.0以下で指定してください。")
    if not isinstance(bindings.get("denoise"), dict):
        raise CardGenError(
            "このプロファイルは--denoiseに対応していません。"
            "対応させるにはプロファイルへbindings.denoiseを追加してください。"
        )
    node_id, _ = set_bound_value(workflow, bindings, "denoise", denoise)
    return node_id


def set_bound_input_image(
    workflow: dict[str, Any], bindings: dict[str, Any], uploaded_name: str
) -> str:
    if not isinstance(bindings.get("input_image"), dict):
        raise CardGenError("このプロファイルは入力画像に対応していません。")
    node_id, _ = set_bound_value(workflow, bindings, "input_image", uploaded_name)
    return node_id


def detect_negative_mode(
    negative_nodes: list[str], zeroed_negative_nodes: list[str]
) -> str:
    if not zeroed_negative_nodes:
        return "text"
    if len(zeroed_negative_nodes) == len(set(negative_nodes)):
        return "zeroed"
    return "mixed"


def collect_model_uses(workflow: dict[str, Any]) -> list[dict[str, str]]:
    used: list[dict[str, str]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type"))
        fields = MODEL_INPUTS.get(class_type)
        if not fields:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for field, category in fields.items():
            value = inputs.get(field)
            if isinstance(value, str) and value:
                used.append(
                    {
                        "node_id": str(node_id),
                        "class_type": class_type,
                        "field": field,
                        "category": category,
                        "filename": value,
                    }
                )
    if not used:
        raise CardGenError(
            "対応するモデルLoaderが見つかりません。CheckpointLoader、UNETLoader、"
            "CLIPLoader、VAELoader等を確認してください。"
        )
    return used


def verify_approved_models(
    workflow: dict[str, Any], approved: dict[str, set[str]]
) -> list[dict[str, str]]:
    used = collect_model_uses(workflow)
    unapproved = [
        item
        for item in used
        if item["filename"] not in approved.get(item["category"], set())
    ]
    if unapproved:
        details = ", ".join(
            f"{item['category']}:{item['filename']} (node {item['node_id']})"
            for item in unapproved
        )
        raise CardGenError("未承認モデルがワークフローに含まれています: " + details)
    return used


def apply_checkpoint_override(
    workflow: dict[str, Any], approved: dict[str, set[str]], checkpoint: str
) -> list[str]:
    if checkpoint not in approved.get("checkpoints", set()):
        raise CardGenError(
            "--checkpointで指定したファイルはこのプロファイルで承認されていません: "
            + checkpoint
        )

    changed: list[str] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or node.get("class_type") not in {
            "CheckpointLoaderSimple",
            "CheckpointLoader",
        }:
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and "ckpt_name" in inputs:
            inputs["ckpt_name"] = checkpoint
            changed.append(str(node_id))

    if not changed:
        raise CardGenError(
            "このワークフローには--checkpointで変更できるCheckpointLoaderがありません。"
        )
    return sorted(changed)


def resolve_resolution_nodes(
    workflow: dict[str, Any], bindings: dict[str, Any] | None
) -> list[str] | None:
    """Check every node a profile lists as carrying the output resolution.

    Returns None when the profile lists none, which leaves the empty-latent scan
    in charge. Resolving without writing lets validate catch a stale node_id.
    """
    binding = bindings.get("resolution_nodes") if isinstance(bindings, dict) else None
    if binding is None:
        return None
    if (
        not isinstance(binding, list)
        or not binding
        or not all(isinstance(node_id, str) and node_id for node_id in binding)
    ):
        raise CardGenError(
            "bindings.resolution_nodesは空でない文字列の配列で指定してください。"
        )

    for node_id in binding:
        node = workflow.get(node_id)
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            raise CardGenError(
                f"bindings.resolution_nodesのノードがありません: node {node_id}"
            )
        missing = [field for field in ("width", "height") if field not in inputs]
        if missing:
            raise CardGenError(
                "bindings.resolution_nodesの"
                f"{'と'.join(missing)}入力がありません: node {node_id}"
            )
        # bound_nodeと同じ理由。この経路だけがbound_nodeを通らないので、接続を
        # 指した列挙が素通りしていた。上書きすると辺が消え、グラフの意味が変わる。
        linked = [
            field for field in ("width", "height") if is_link(inputs[field])
        ]
        if linked:
            raise CardGenError(
                "bindings.resolution_nodesが指しているのは設定ではなく"
                f"他ノードからの接続です: node {node_id} {'と'.join(linked)}"
            )

    # 列挙を書くと空Latent走査は行われない。取りこぼすと、出力サイズは既定のまま
    # 列挙した側だけが動く。この列挙が防ぐはずの食い違いが、逆向きに、しかも
    # エラーを出さずに起きる。
    uncovered = sorted(
        node_id
        for node_id, node in workflow.items()
        if isinstance(node, dict)
        and node.get("class_type") in LATENT_TYPES
        and isinstance(node.get("inputs"), dict)
        and "width" in node["inputs"]
        and "height" in node["inputs"]
        and str(node_id) not in binding
    )
    if uncovered:
        raise CardGenError(
            "bindings.resolution_nodesが空Latentノードを列挙していません: "
            f"node {'、'.join(uncovered)}。"
            "列挙を書くと空Latentの走査は行われないので、解像度を述べている"
            "ノードをすべて挙げてください。"
        )
    return sorted(binding)


def apply_latent_size(
    workflow: dict[str, Any],
    width: int,
    height: int,
    bindings: dict[str, Any] | None = None,
) -> list[str]:
    """Write the output resolution everywhere the graph states it.

    Scanning for empty-latent nodes is enough when the latent is the only place
    the size appears. FLUX.2 states it twice: EmptyFlux2LatentImage sizes the
    latent and Flux2Scheduler takes width and height of its own, so writing only
    the latent leaves the two disagreeing. Which nodes carry the resolution is
    not derivable from the graph shape, so a profile may list them instead.
    """
    if width < 64 or height < 64:
        raise CardGenError("--widthと--heightは64以上で指定してください。")
    if width % 8 or height % 8:
        raise CardGenError("--widthと--heightは8の倍数で指定してください。")

    listed = resolve_resolution_nodes(workflow, bindings)
    if listed is not None:
        for node_id in listed:
            inputs = workflow[node_id]["inputs"]
            inputs["width"] = width
            inputs["height"] = height
        return listed

    changed: list[str] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or node.get("class_type") not in LATENT_TYPES:
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and "width" in inputs and "height" in inputs:
            linked = [f for f in ("width", "height") if is_link(inputs[f])]
            if linked:
                # The size is computed elsewhere in the graph. Writing here would
                # drop that edge, so say so rather than quietly rewiring.
                raise CardGenError(
                    f"空Latentの{'と'.join(linked)}が他ノードからの接続です: "
                    f"node {node_id}。--widthで上書きすると辺が消えます。"
                    "解像度を持つノードをbindings.resolution_nodesで指名してください。"
                )
            inputs["width"] = width
            inputs["height"] = height
            changed.append(str(node_id))

    if not changed:
        raise CardGenError(
            "このワークフローには解像度を変更できる空Latentノードがありません。"
        )
    return sorted(changed)


def literal_field_nodes(workflow: dict[str, Any], field: str) -> list[str]:
    """Node ids stating field as a literal. Links are settings of another node."""
    found: list[str] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or field not in inputs:
            continue
        if is_link(inputs[field]):
            continue
        found.append(str(node_id))
    return sorted(found)


def sampler_param_option(field: str) -> str:
    return "--" + field.replace("sampler_name", "sampler").replace("_", "-")


def apply_sampler_params(
    workflow: dict[str, Any],
    params: dict[str, Any],
    bindings: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Overwrite sampler inputs, preferring the node a profile names.

    Writing every sampler that exposes the field is right for a graph whose
    samplers carry their own settings: a hires pair wants both passes moved
    together. It has nowhere to land when the setting lives outside the sampler.
    FLUX.2 keeps steps in Flux2Scheduler, cfg in CFGGuider and sampler_name in
    KSamplerSelect, leaving SamplerCustomAdvanced with links alone, so --steps
    used to fail on a graph that plainly has steps. A profile may therefore name
    one node per field, the way bindings.denoise already does.
    """
    bound = bindings if isinstance(bindings, dict) else {}
    changed: dict[str, list[str]] = {}
    for field in SAMPLER_OVERRIDE_FIELDS:
        value = params.get(field)
        if value is None:
            continue

        if bound.get(field) is not None:
            node_id, _ = set_bound_value(workflow, bound, field, value)
            changed[field] = [node_id]
            continue

        nodes: list[str] = []
        for node_id, node in sampler_nodes(workflow):
            inputs = node.get("inputs")
            if not isinstance(inputs, dict) or field not in inputs:
                continue
            if is_link(inputs[field]):
                # Another node supplies this value. Overwriting it deletes the
                # edge, and skipping it would apply the option to some samplers
                # and not others without saying which.
                raise CardGenError(
                    f"{sampler_param_option(field)}の書き込み先が他ノードからの"
                    f"接続です: node {node_id} {field}。上書きすると辺が消えます。"
                    f"プロファイルへbindings.{field}を書いて指名してください。"
                )
            inputs[field] = value
            nodes.append(node_id)

        if not nodes:
            # bindingを勧めてよいのは、そのフィールドを持つノードが実際にある
            # ときだけ。FLUX.2のschedulerのように書く先が無い項目まで「1行足せ
            # ば直る」と言うと、足した先でvalidateが落ちる。
            candidates = literal_field_nodes(workflow, field)
            hint = (
                f"{field}を持つのはnode {'、'.join(candidates)}なので、"
                f"プロファイルへbindings.{field}を追加すれば対応できます。"
                if candidates
                else f"{field}を持つノードがこのグラフに無いので、bindingでも"
                "対応できません。"
            )
            raise CardGenError(
                f"{sampler_param_option(field)}を適用できるSampler入力が"
                f"このワークフローにありません。{hint}"
            )
        changed[field] = sorted(nodes)
    return changed


def describe_generation_settings(workflow: dict[str, Any]) -> dict[str, Any]:
    """Snapshot the settings actually queued, including workflow defaults.

    Recording overrides alone would leave a run untraceable whenever a value
    came from the workflow file instead of the command line.
    """
    latents: list[dict[str, Any]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or node.get("class_type") not in LATENT_TYPES:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        latents.append(
            {
                "node_id": str(node_id),
                "class_type": node.get("class_type"),
                "width": inputs.get("width"),
                "height": inputs.get("height"),
                "batch_size": inputs.get("batch_size"),
            }
        )

    samplers: list[dict[str, Any]] = []
    for node_id, node in sampler_nodes(workflow, required=False):
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        entry: dict[str, Any] = {
            "node_id": node_id,
            "class_type": node.get("class_type"),
        }
        for field in SAMPLER_RECORD_FIELDS:
            if field in inputs:
                entry[field] = inputs[field]
        samplers.append(entry)

    return {
        "latents": sorted(latents, key=lambda item: item["node_id"]),
        "samplers": sorted(samplers, key=lambda item: item["node_id"]),
        "node_inputs": snapshot_node_inputs(workflow),
    }


def snapshot_node_inputs(workflow: dict[str, Any]) -> dict[str, Any]:
    """Record every literal input of every node in the queued graph.

    Enumerating "interesting" node types is what made this record useless for
    the FLUX.2 graph: SamplerCustomAdvanced holds no settings of its own, so
    cfg (CFGGuider), steps (Flux2Scheduler) and sampler_name (KSamplerSelect)
    were all absent from metadata while appearing nowhere else. Any node type
    added later would have hit the same gap.

    Links are ``[node_id, slot]`` lists and are skipped; the graph structure is
    already pinned by the workflow hash.

    Three kinds of field are withheld:

    - prompt text, recorded separately and would double the file
    - seed, which varies per result and is authoritative in ``results``; the
      template value here would be a stale default
    - the input image name, which belongs to the caller's private project
    """
    snapshot: dict[str, Any] = {}
    for node_id, node in sorted(workflow.items(), key=lambda item: str(item[0])):
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = str(node.get("class_type"))
        withheld = set(PROMPT_FIELDS.get(class_type, ()))
        withheld.update({"seed", "noise_seed"})
        if class_type in {"LoadImage", "LoadImageMask"}:
            withheld.add("image")
        literals = {
            field: value
            for field, value in sorted(inputs.items())
            if not isinstance(value, (list, dict)) and field not in withheld
        }
        if literals:
            snapshot[str(node_id)] = {
                "class_type": node.get("class_type"),
                "inputs": literals,
            }
    return snapshot


def get_primary_sampler(workflow: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    samplers = sampler_nodes(workflow)

    # Base sampler in a Base + Refiner graph usually adds the initial noise.
    for node_id, node in samplers:
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and inputs.get("add_noise") == "enable":
            return node_id, node

    for node_id, node in samplers:
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and ("seed" in inputs or "noise_seed" in inputs):
            return node_id, node

    raise CardGenError("seedまたはnoise_seedを持つSamplerが見つかりません。")


def set_sampler_seed(sampler: dict[str, Any], seed: int) -> str:
    inputs = sampler.get("inputs")
    if not isinstance(inputs, dict):
        raise CardGenError("Samplerのinputsが不正です。")
    if "seed" in inputs:
        inputs["seed"] = seed
        return "seed"
    if "noise_seed" in inputs:
        inputs["noise_seed"] = seed
        return "noise_seed"
    raise CardGenError("Samplerにseedまたはnoise_seed入力がありません。")


def set_save_prefix(workflow: dict[str, Any], prefix: str) -> None:
    found = False
    for node in workflow.values():
        if not isinstance(node, dict) or node.get("class_type") != "SaveImage":
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict):
            inputs["filename_prefix"] = prefix
            found = True
    if not found:
        raise CardGenError("SaveImageノードが見つかりません。")


def validate_profile_workflow(profile: dict[str, Any]) -> dict[str, Any]:
    workflow_path = profile["workflow_path"]
    if not isinstance(workflow_path, Path):
        raise CardGenError("workflow_pathが不正です。")
    workflow = load_json(workflow_path)
    approved = profile["approved_model_sets"]
    if not isinstance(approved, dict):
        raise CardGenError("approved_model_setsが不正です。")
    model_uses = verify_approved_models(workflow, approved)

    probe = copy.deepcopy(workflow)
    bindings = profile.get("bindings")
    if isinstance(bindings, dict):
        positive_nodes, negative_nodes, zeroed_negative_nodes = set_bound_prompts(
            probe, bindings, "validation positive prompt", "validation negative prompt"
        )
    else:
        positive_nodes, negative_nodes, zeroed_negative_nodes = set_all_sampler_prompts(
            probe, "validation positive prompt", "validation negative prompt"
        )
    negative_mode = detect_negative_mode(negative_nodes, zeroed_negative_nodes)
    sampler_count = len(sampler_nodes(workflow, required=False))
    # Sampler count alone cannot tell a refiner from a hires pass; it only says
    # the graph denoises more than once.
    multi_pass_detected = sampler_count > 1

    capabilities = profile.get("capabilities", {})
    expected_negative = capabilities.get("negative_prompt_mode")
    if expected_negative is not None and expected_negative != negative_mode:
        raise CardGenError(
            "プロファイルのnegative_prompt_modeとワークフローが一致しません: "
            f"expected={expected_negative}, detected={negative_mode}"
        )
    expected_multi_pass = capabilities.get("multi_pass")
    if expected_multi_pass is not None and expected_multi_pass != multi_pass_detected:
        raise CardGenError(
            "プロファイルのmulti_pass指定とSampler数が一致しません: "
            f"expected={expected_multi_pass}, detected={multi_pass_detected}"
        )

    input_image_node: str | None = None
    denoise_node: str | None = None
    sampler_param_nodes: dict[str, list[str]] = {}
    resolution_nodes: list[str] = []
    if isinstance(bindings, dict):
        primary_id, seed_field = set_bound_seed(probe, bindings, 1)
        # Resolve only, so a stale node_id fails here and not on the --width of
        # a run that has already uploaded an image and queued work.
        resolution_nodes = resolve_resolution_nodes(probe, bindings) or []
        for field in SAMPLER_OVERRIDE_FIELDS:
            # generate reads a null binding as "no binding" and falls back to
            # the blanket path. validate has to read it the same way, or it
            # refuses a profile the run would have accepted.
            if bindings.get(field) is None:
                continue
            # Resolve only. validate has no value to write, and the node need
            # not be a sampler: FLUX.2 keeps steps and cfg outside it.
            node_id, _, _ = resolve_bound_node(probe, bindings, field)
            sampler_param_nodes[field] = [node_id]
        if bindings.get("denoise") is not None:
            # Resolve only: bound_node raises on a stale node_id or field, and
            # validate has no denoise to write. The workflow's own value stands.
            denoise_node, _, _ = resolve_bound_node(probe, bindings, "denoise")
        if "input_image" in bindings:
            # generate uploads first and binds the returned name, so the binding
            # itself is never resolved until a run is already underway. Resolve
            # it here against a placeholder name to catch a stale node_id/field
            # before the upload, not after.
            input_image_node = set_bound_input_image(
                probe, bindings, "validation-input.png"
            )
    else:
        primary_id, primary_sampler = get_primary_sampler(probe)
        seed_field = set_sampler_seed(primary_sampler, 1)
    set_save_prefix(probe, "CardGen/validation")

    return {
        "profile": profile["id"],
        "display_name": profile["display_name"],
        "profile_file": project_relative(profile["profile_path"]),
        "workflow": project_relative(workflow_path),
        "sampler_count": sampler_count,
        "multi_pass_detected": multi_pass_detected,
        "primary_sampler": primary_id,
        "seed_field": seed_field,
        "input_image_required": input_image_node is not None,
        "input_image_node": input_image_node,
        "denoise_node": denoise_node,
        # Same name and same shape as setting_overrides.sampler_nodes in the
        # run metadata, so the two records can be read side by side.
        "sampler_nodes": sampler_param_nodes,
        "resolution_nodes": resolution_nodes,
        "positive_prompt_nodes": positive_nodes,
        "negative_conditioning_nodes": negative_nodes,
        "zeroed_negative_nodes": zeroed_negative_nodes,
        "negative_prompt_mode": negative_mode,
        "model_uses": model_uses,
    }


def queue_prompt(
    app: dict[str, Any], workflow: dict[str, Any]
) -> str:
    result = request_json(
        app["comfy_url"],
        "POST",
        "/prompt",
        payload={"prompt": workflow},
        timeout=app["request_timeout_seconds"],
    )
    prompt_id = result.get("prompt_id")
    if not isinstance(prompt_id, str) or not prompt_id:
        raise CardGenError(f"prompt_idを取得できませんでした: {result}")
    return prompt_id


def wait_for_history(
    app: dict[str, Any], prompt_id: str, timeout_seconds: int
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    quoted = urllib.parse.quote(prompt_id, safe="")
    while time.monotonic() < deadline:
        history = request_json(
            app["comfy_url"],
            "GET",
            f"/history/{quoted}",
            timeout=app["request_timeout_seconds"],
        )
        entry = history.get(prompt_id)
        if isinstance(entry, dict):
            status = entry.get("status")
            if isinstance(status, dict) and status.get("status_str") == "error":
                raise CardGenError(
                    "ComfyUI実行エラー:\n"
                    + json.dumps(status, ensure_ascii=False, indent=2)
                )
            outputs = entry.get("outputs")
            if isinstance(outputs, dict) and outputs:
                return entry
        time.sleep(1.0)
    raise CardGenError(f"生成がタイムアウトしました: {timeout_seconds}秒")


def download_outputs(
    app: dict[str, Any],
    history_entry: dict[str, Any],
    output_dir: Path,
    stem: str,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    outputs = history_entry.get("outputs", {})
    if not isinstance(outputs, dict):
        return saved

    image_index = 1
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        images = node_output.get("images", [])
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict) or "filename" not in image:
                continue
            query = urllib.parse.urlencode(
                {
                    "filename": image["filename"],
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                }
            )
            url = f"{app['comfy_url'].rstrip('/')}/view?{query}"
            try:
                with urllib.request.urlopen(
                    url, timeout=app["request_timeout_seconds"]
                ) as response:
                    content = response.read()
            except urllib.error.URLError as exc:
                raise CardGenError(f"画像を取得できません: {exc}") from exc

            suffix = Path(str(image["filename"])).suffix or ".png"
            destination = output_dir / f"{stem}_{image_index:02d}{suffix}"
            destination.write_bytes(content)
            saved.append(destination)
            image_index += 1
    return saved


def command_profiles(app: dict[str, Any]) -> int:
    profiles_dir = app["profiles_dir_path"]
    if not isinstance(profiles_dir, Path):
        raise CardGenError("profiles_dir_pathが不正です。")
    if not profiles_dir.is_dir():
        raise CardGenError(f"profiles_dirがありません: {profiles_dir}")

    found = 0
    for path in sorted(profiles_dir.glob("*.json")):
        raw = load_json(path)
        profile_id = raw.get("id", path.stem)
        display_name = raw.get("display_name", "(display_nameなし)")
        marker = "*" if profile_id == app["default_profile"] else " "
        workflow_raw = raw.get("workflow")
        workflow_exists = False
        if isinstance(workflow_raw, str):
            try:
                workflow_exists = resolve_project_path(workflow_raw).is_file()
            except CardGenError:
                workflow_exists = False
        status = "ready" if workflow_exists else "workflow missing"
        print(f"{marker} {profile_id}: {display_name} [{status}]")
        found += 1
    if not found:
        raise CardGenError("プロファイルがありません。")
    print("* = default profile")
    return 0


def comfy_versions(app: dict[str, Any]) -> dict[str, Any]:
    """Record the engine version. A ComfyUI update can change node behaviour."""
    try:
        stats = request_json(
            app["comfy_url"],
            "GET",
            "/system_stats",
            timeout=app["request_timeout_seconds"],
        )
    except CardGenError:
        return {"comfyui_version": None, "note": "/system_stats unavailable"}
    system = stats.get("system", {}) if isinstance(stats, dict) else {}
    return {
        "comfyui_version": system.get("comfyui_version"),
        "python_version": system.get("python_version"),
        "pytorch_version": system.get("pytorch_version"),
        "comfy_package_versions": system.get("comfy_package_versions"),
    }


def command_check(app: dict[str, Any]) -> int:
    stats = request_json(
        app["comfy_url"],
        "GET",
        "/system_stats",
        timeout=app["request_timeout_seconds"],
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_validate(
    app: dict[str, Any], requested_profile: str | None, validate_all: bool
) -> int:
    if validate_all:
        profiles_dir = app["profiles_dir_path"]
        if not isinstance(profiles_dir, Path):
            raise CardGenError("profiles_dir_pathが不正です。")
        ids = [path.stem for path in sorted(profiles_dir.glob("*.json"))]
        if not ids:
            raise CardGenError("検証対象プロファイルがありません。")
    else:
        ids = [requested_profile or str(app["default_profile"])]

    summaries: list[dict[str, Any]] = []
    for profile_id in ids:
        profile = load_profile(app, profile_id)
        summary = validate_profile_workflow(profile)
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"VALID: profile={profile_id}")
    if len(summaries) > 1:
        print(f"VALID ALL: {len(summaries)} profiles")
    return 0


def command_generate(
    args: argparse.Namespace, app: dict[str, Any], requested_profile: str | None
) -> int:
    profile = load_profile(app, requested_profile)
    workflow_path = profile["workflow_path"]
    if not isinstance(workflow_path, Path):
        raise CardGenError("workflow_pathが不正です。")
    workflow = load_json(workflow_path)
    bindings = profile.get("bindings")

    approved = profile["approved_model_sets"]
    if not isinstance(approved, dict):
        raise CardGenError("approved_model_setsが不正です。")

    checkpoint_nodes: list[str] = []
    if args.checkpoint:
        checkpoint_nodes = apply_checkpoint_override(
            workflow, approved, args.checkpoint
        )

    model_uses = verify_approved_models(workflow, approved)

    resolution_nodes: list[str] = []
    if args.width is not None or args.height is not None:
        if args.width is None or args.height is None:
            raise CardGenError("--widthと--heightは同時に指定してください。")
        resolution_nodes = apply_latent_size(
            workflow,
            args.width,
            args.height,
            bindings if isinstance(bindings, dict) else None,
        )

    sampler_overrides = apply_sampler_params(
        workflow,
        {
            "steps": args.steps,
            "cfg": args.cfg,
            "sampler_name": args.sampler_name,
            "scheduler": args.scheduler,
        },
        bindings if isinstance(bindings, dict) else None,
    )
    # 分割点が危ういかどうかを決めるのはSampler数で、書き込んだノード数ではない。
    # bindingで1ノードだけ動かしたときこそ、もう片方のstart_at_step/end_at_stepが
    # 古いstepsのまま取り残される。
    if args.steps is not None and len(sampler_nodes(workflow, required=False)) > 1:
        written = "、".join(sampler_overrides.get("steps", []))
        print(
            "NOTE: このワークフローには複数のSamplerがあります。--stepsを書き込んだ"
            f"のはnode {written}で、start_at_step/end_at_stepの分割点は"
            "変更しません。"
        )

    defaults = profile.get("defaults", {})
    default_negative = defaults.get("negative_prompt", "")
    negative = args.negative if args.negative is not None else default_negative
    if not isinstance(negative, str):
        raise CardGenError("negative promptが不正です。")

    needs_prompt = not isinstance(bindings, dict) or "positive_prompt" in bindings
    if needs_prompt and args.prompt is None:
        raise CardGenError("このプロファイルには--promptが必要です。")
    positive = args.prompt or ""

    if isinstance(bindings, dict):
        _, negative_nodes, zeroed_negative_nodes = set_bound_prompts(
            workflow, bindings, positive, negative
        )
    else:
        _, negative_nodes, zeroed_negative_nodes = set_all_sampler_prompts(
            workflow, positive, negative
        )
    negative_mode = detect_negative_mode(negative_nodes, zeroed_negative_nodes)
    if negative_mode in {"zeroed", "mixed"} and negative.strip():
        print(
            "NOTE: このワークフローのnegative条件付けにはConditioningZeroOutが"
            "含まれます。該当経路では--negativeの文字列は適用されません。"
        )

    output_dir = app["output_dir_path"]
    if not isinstance(output_dir, Path):
        raise CardGenError("output_dir_pathが不正です。")

    denoise_node: str | None = None
    if args.denoise is not None:
        if not isinstance(bindings, dict):
            raise CardGenError(
                "このプロファイルは--denoiseに対応していません。"
                "対応させるにはプロファイルへbindings.denoiseを追加してください。"
            )
        denoise_node = set_bound_denoise(workflow, bindings, args.denoise)

    if isinstance(bindings, dict):
        primary_id, _ = set_bound_seed(workflow, bindings, 1)
        if primary_id is None and args.count != 1:
            raise CardGenError(
                "このプロファイルはseedを持たないので、--countは1だけです。"
                "同じ入力からは常に同じ出力が出ます。"
            )
        if args.input_image is not None:
            image_path = resolve_external_input_path(args.input_image)
            uploaded_name = upload_input_image(app, image_path)
            set_bound_input_image(workflow, bindings, uploaded_name)
        elif "input_image" in bindings:
            raise CardGenError("このプロファイルでは--input-imageが必要です。")
    else:
        if args.input_image is not None:
            raise CardGenError("このプロファイルは--input-imageに対応していません。")
        primary_id, primary_sampler = get_primary_sampler(workflow)
        set_sampler_seed(primary_sampler, 1)  # Validate before entering the loop.

    timeout = (
        args.timeout
        if args.timeout is not None
        else int(app["generation_timeout_seconds"])
    )
    if timeout < 1:
        raise CardGenError("--timeoutは1以上の整数で指定してください。")

    results: list[dict[str, Any]] = []
    session_stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    # A run that dies partway still leaves downloaded images in output_dir. Without
    # a record they cannot be traced to a seed, a workflow or a set of weights, so
    # the failure is written to the same metadata file a success would have used.
    failure: dict[str, Any] | None = None
    failure_exc: BaseException | None = None
    try:
        for index in range(args.count):
            current = copy.deepcopy(workflow)
            seed = (
                args.seed + index
                if args.seed is not None
                else secrets.randbelow(2**63 - 1)
            )
            if isinstance(bindings, dict):
                bound_seed_node, _ = set_bound_seed(current, bindings, seed)
                if bound_seed_node is None:
                    # seed を書く先が無い工程。乱数を名前や記録へ残すと、
                    # 再現に要らない値が要るように見える。
                    seed = None
            else:
                current_primary = current.get(primary_id)
                if not isinstance(current_primary, dict):
                    raise CardGenError(f"Primary samplerが不正です: {primary_id}")
                set_sampler_seed(current_primary, seed)

            suffix = f"_seed{seed}" if seed is not None else ""
            stem = f"{profile['id']}_{session_stamp}_{index + 1:02d}{suffix}"
            set_save_prefix(current, f"CardGen/{profile['id']}/{session_stamp}/{stem}")
            prompt_id = queue_prompt(app, current)
            print(
                f"[{index + 1}/{args.count}] profile={profile['id']} "
                f"queued={prompt_id}" + (f" seed={seed}" if seed is not None else "")
            )
            history = wait_for_history(app, prompt_id, timeout)
            files = download_outputs(app, history, output_dir, stem)
            if not files:
                raise CardGenError("ComfyUI履歴にダウンロード可能な画像がありません。")
            results.append(
                {
                    "prompt_id": prompt_id,
                    "seed": seed,
                    "files": [project_relative(path) for path in files],
                    "file_sha256": [sha256_file(path) for path in files],
                    # The graph as queued, per seed. Two runs that differ only in
                    # a workflow edit are otherwise indistinguishable in the
                    # record.
                    "queued_workflow_sha256": sha256_json(current),
                }
            )
            for path in files:
                print(f"RESULT: {path}")
    except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised as-is
        # Deliberately BaseException: a Ctrl-C or a socket error mid-run strands
        # images just as surely as a CardGenError does. The exception is re-raised
        # unchanged below, so exit codes and tracebacks are unaffected.
        failure_exc = exc
        failure = {
            "error_type": type(exc).__name__,
            "message": str(exc),
            "failed_on_image": len(results) + 1,
            "requested_count": args.count,
        }

    metadata = {
        "schema_version": 7,
        "status": "error" if failure is not None else "ok",
        "failure": failure,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "app_config": project_relative(app["app_config_path"]),
        "app_config_sha256": sha256_file(app["app_config_path"]),
        "profile": profile["id"],
        "profile_file": project_relative(profile["profile_path"]),
        "profile_sha256": sha256_file(profile["profile_path"]),
        "workflow": project_relative(workflow_path),
        "workflow_sha256": sha256_file(workflow_path),
        "comfyui": comfy_versions(app),
        "model_uses": model_uses,
        "model_files": hash_model_files(app["comfyui_models_dir_path"], model_uses),
        "checkpoint_override": args.checkpoint,
        "checkpoint_nodes": checkpoint_nodes,
        "generation_settings": describe_generation_settings(workflow),
        "setting_overrides": {
            "width": args.width,
            "height": args.height,
            # Not "latent_nodes": a profile may list a node that states the
            # resolution without being a latent. flux2-klein-edit records
            # Flux2Scheduler here. Same name and shape as validate's key.
            "resolution_nodes": resolution_nodes,
            "sampler_nodes": sampler_overrides,
            "denoise": args.denoise,
            "denoise_node": denoise_node,
        },
        "prompt": args.prompt,
        "negative": negative,
        "negative_prompt_mode": negative_mode,
        "input_image_supplied": args.input_image is not None,
        # Hash only. The path belongs to the caller's private project and must
        # not be written into this repository's metadata.
        "input_image_sha256": (
            sha256_file(Path(args.input_image)) if args.input_image else None
        ),
        "results": results,
    }
    metadata_path = output_dir / f"{profile['id']}_{session_stamp}_metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"METADATA: {metadata_path}")
    if failure_exc is not None:
        raise failure_exc
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Named-profile ComfyUI card illustration runner"
    )
    parser.add_argument(
        "--app-config",
        default=str(DEFAULT_APP_CONFIG),
        help="共通設定JSON（既定: config/app.json）",
    )
    parser.add_argument(
        "--profile",
        help="config/profiles/<ID>.jsonのプロファイルID（省略時は既定値）",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("profiles", help="利用可能なプロファイルを一覧表示")
    subparsers.add_parser("check", help="ComfyUI接続とGPU情報を確認")

    validate = subparsers.add_parser("validate", help="プロファイルとAPIワークフローを検証")
    validate.add_argument(
        "--profile",
        dest="profile",
        default=argparse.SUPPRESS,
        help="プロファイルID（サブコマンド後にも指定可能）",
    )
    validate.add_argument(
        "--all", action="store_true", help="すべてのプロファイルを検証"
    )

    generate = subparsers.add_parser("generate", help="選択したプロファイルで画像生成")
    generate.add_argument(
        "--profile",
        dest="profile",
        default=argparse.SUPPRESS,
        help="プロファイルID（サブコマンド後にも指定可能）",
    )
    generate.add_argument(
        "--prompt",
        help="ポジティブプロンプト。prompt の binding を持つプロファイルでは必須",
    )
    generate.add_argument(
        "--negative",
        default=None,
        help="ネガティブプロンプト（省略時はプロファイル既定値）",
    )
    generate.add_argument(
        "--checkpoint",
        help="承認済みCheckpointへ全CheckpointLoaderを切り替える",
    )
    generate.add_argument(
        "--input-image",
        help="参照・編集入力画像（所有元プロジェクト内の絶対パス）",
    )
    generate.add_argument("--count", type=int, default=1, choices=range(1, 9))
    generate.add_argument("--seed", type=int)
    generate.add_argument("--timeout", type=int)
    generate.add_argument(
        "--width", type=int, help="出力幅（8の倍数、--heightと同時指定）"
    )
    generate.add_argument(
        "--height", type=int, help="出力高さ（8の倍数、--widthと同時指定）"
    )
    generate.add_argument("--steps", type=int, help="サンプリングステップ数")
    generate.add_argument("--cfg", type=float, help="CFG scale")
    generate.add_argument(
        "--sampler", dest="sampler_name", help="sampler_name（例: euler_ancestral）"
    )
    generate.add_argument("--scheduler", help="scheduler（例: normal, karras）")
    generate.add_argument(
        "--denoise",
        type=float,
        help="bindings.denoiseを持つプロファイルで、その1ノードのdenoiseを上書き（0.0〜1.0）",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        load_project_env(PROJECT_DIR / ".env")
        app_path = resolve_project_path(args.app_config)
        app = load_app_config(app_path)
        if args.profile is not None:
            validate_profile_id(args.profile)

        if args.command == "profiles":
            return command_profiles(app)
        if args.command == "check":
            return command_check(app)
        if args.command == "validate":
            return command_validate(app, args.profile, args.all)
        if args.command == "generate":
            return command_generate(args, app, args.profile)
        parser.error("unknown command")
    except (CardGenError, ProjectEnvError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
