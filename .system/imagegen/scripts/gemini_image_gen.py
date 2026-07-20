#!/usr/bin/env python3
"""Image generation CLI for Google Gemini image APIs."""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable, Optional


DEFAULT_MODEL = "gemini-3.1-flash-image"
DEFAULT_OUTPUT_PATH = "output/imagegen/output.png"
DEFAULT_CONCURRENCY = 5
DEFAULT_DOWNSCALE_SUFFIX = "-web"
MAX_BATCH_JOBS = 500
MAX_IMAGE_BYTES = 50 * 1024 * 1024
SUPPORTED_MODELS = {
    "gemini-3.1-flash-lite-image",
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
    "gemini-2.5-flash-image",
    "nano-banana-2-lite",
    "nano-banana-2",
    "nano-banana-pro",
    "nano-banana",
}
SUPPORTED_API_MODES = {"generate-content", "interactions"}
LITE_MODELS = {
    "gemini-3.1-flash-lite-image",
    "nano-banana-2-lite",
}
HALF_K_MODELS = {"gemini-3.1-flash-image", "nano-banana-2"}
SUPPORTED_RATIOS = {
    "1:1": 1.0,
    "2:3": 2 / 3,
    "3:2": 3 / 2,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
    "4:5": 4 / 5,
    "5:4": 5 / 4,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
    "21:9": 21 / 9,
}


class GeminiCliError(RuntimeError):
    pass


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def _read_prompt(prompt: Optional[str], prompt_file: Optional[str]) -> str:
    if prompt and prompt_file:
        raise GeminiCliError("Use --prompt or --prompt-file, not both.")
    if prompt_file:
        path = Path(prompt_file)
        if not path.is_file():
            raise GeminiCliError(f"Prompt file not found: {path}")
        value = path.read_text(encoding="utf-8").strip()
    else:
        value = (prompt or "").strip()
    if not value:
        raise GeminiCliError("Missing prompt. Use --prompt or --prompt-file.")
    return value


def _fields_from_args(args: argparse.Namespace) -> dict[str, Optional[str]]:
    return {
        key: getattr(args, key, None)
        for key in (
            "use_case",
            "scene",
            "subject",
            "style",
            "composition",
            "lighting",
            "palette",
            "materials",
            "text",
            "constraints",
            "negative",
        )
    }


def _augment_prompt(prompt: str, fields: dict[str, Optional[str]], enabled: bool) -> str:
    if not enabled:
        return prompt
    labels = {
        "use_case": "Use case",
        "scene": "Scene/background",
        "subject": "Subject",
        "style": "Style/medium",
        "composition": "Composition/framing",
        "lighting": "Lighting/mood",
        "palette": "Color palette",
        "materials": "Materials/textures",
        "text": "Text (verbatim)",
        "constraints": "Constraints",
        "negative": "Avoid",
    }
    lines = []
    if fields.get("use_case"):
        lines.append(f"Use case: {fields['use_case']}")
    lines.append(f"Primary request: {prompt}")
    for key, label in labels.items():
        if key == "use_case" or not fields.get(key):
            continue
        value = fields[key]
        lines.append(f'{label}: "{value}"' if key == "text" else f"{label}: {value}")
    return "\n".join(lines)


def _validate_model(model: str) -> None:
    if model not in SUPPORTED_MODELS:
        raise GeminiCliError(
            "Unsupported Gemini image model. Supported models: "
            + ", ".join(sorted(SUPPORTED_MODELS))
        )


def _parse_size(size: str, model: str) -> tuple[Optional[str], Optional[str]]:
    if size == "auto":
        return None, None
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", size)
    if not match:
        raise GeminiCliError("size must be auto or WIDTHxHEIGHT, for example 1024x1024.")
    width, height = int(match.group(1)), int(match.group(2))
    ratio = width / height
    matching = [name for name, value in SUPPORTED_RATIOS.items() if math.isclose(ratio, value, rel_tol=0.002)]
    if not matching:
        raise GeminiCliError(
            f"Gemini does not support the {width}:{height} aspect ratio. "
            f"Supported ratios: {', '.join(SUPPORTED_RATIOS)}."
        )
    max_edge = max(width, height)
    image_size = (
        "0.5K"
        if max_edge <= 512 and model in HALF_K_MODELS
        else "1K"
        if max_edge <= 1024
        else "2K"
        if max_edge <= 2048
        else "4K"
    )
    if model in LITE_MODELS and image_size != "1K":
        raise GeminiCliError(f"{model} supports only 1K output; requested size maps to {image_size}.")
    return matching[0], image_size


def _validate_requested_ratio(size: str, value: Optional[str]) -> None:
    if not value:
        return
    ratio_match = re.fullmatch(r"([1-9][0-9]*):([1-9][0-9]*)", value)
    size_match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", size)
    if not ratio_match:
        raise GeminiCliError("aspect-ratio must use W:H, for example 3:4.")
    if not size_match:
        raise GeminiCliError("--aspect-ratio requires an explicit --size; auto is not allowed.")
    rw, rh = int(ratio_match.group(1)), int(ratio_match.group(2))
    width, height = int(size_match.group(1)), int(size_match.group(2))
    if width * rh != height * rw:
        raise GeminiCliError(f"--size {size} does not exactly match aspect ratio {value}.")


def _normalize_output_format(value: Optional[str]) -> str:
    fmt = (value or "png").lower()
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt not in {"png", "jpeg", "webp"}:
        raise GeminiCliError("output-format must be png, jpeg, jpg, or webp.")
    return fmt


def _api_mode() -> str:
    mode = os.getenv("GEMINI_API_MODE", "").strip().lower() or "generate-content"
    if mode not in SUPPORTED_API_MODES:
        raise GeminiCliError(
            "GEMINI_API_MODE must be generate-content or interactions."
        )
    return mode


def _validate_options(args: argparse.Namespace) -> None:
    _validate_model(args.model)
    _validate_requested_ratio(args.size, getattr(args, "aspect_ratio", None))
    _parse_size(args.size, args.model)
    if args.n < 1 or args.n > 10:
        raise GeminiCliError("--n must be between 1 and 10.")
    if args.background == "transparent":
        raise GeminiCliError(
            "Gemini does not provide native background=transparent output. "
            "Generate a flat chroma-key background and remove it locally instead."
        )
    if args.background not in {None, "auto", "opaque", "transparent"}:
        raise GeminiCliError("background must be one of transparent, opaque, or auto.")
    if getattr(args, "mask", None):
        raise GeminiCliError("Gemini edit does not support --mask in this provider implementation.")
    if args.output_compression is not None and not 0 <= args.output_compression <= 100:
        raise GeminiCliError("--output-compression must be between 0 and 100.")
    if args.downscale_max_dim is not None and args.downscale_max_dim < 1:
        raise GeminiCliError("--downscale-max-dim must be >= 1.")
    if args.quality is not None:
        _warn("--quality is not mapped by the Gemini provider and will be ignored.")
    if getattr(args, "input_fidelity", None) is not None:
        _warn("--input-fidelity is not mapped by the Gemini provider and will be ignored.")
    if args.moderation is not None:
        _warn("--moderation is not configurable by the Gemini provider and will be ignored.")


def _response_format(args: argparse.Namespace) -> dict[str, str]:
    output_format = _normalize_output_format(args.output_format)
    mime_type = "image/jpeg" if output_format == "jpeg" else "image/png"
    response = {"type": "image", "mime_type": mime_type}
    aspect_ratio, image_size = _parse_size(args.size, args.model)
    if aspect_ratio:
        response["aspect_ratio"] = aspect_ratio
    if image_size:
        response["image_size"] = image_size
    return response


def _check_images(raw_paths: Iterable[str]) -> list[Path]:
    paths = []
    for raw in raw_paths:
        path = Path(raw)
        if not path.is_file():
            raise GeminiCliError(f"Image file not found: {path}")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            _warn(f"Image exceeds 50MB: {path}")
        paths.append(path)
    return paths


def _image_input(path: Path) -> dict[str, str]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if not mime_type.startswith("image/"):
        raise GeminiCliError(f"Unsupported reference image type: {path}")
    return {
        "type": "image",
        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        "mime_type": mime_type,
    }


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _decode_image_data(data: Any) -> bytes:
    try:
        return data if isinstance(data, bytes) else base64.b64decode(data, validate=True)
    except Exception as exc:
        raise GeminiCliError(f"Gemini returned invalid base64 image data: {exc}") from exc


def _extract_interaction_images(interaction: Any) -> list[tuple[bytes, str]]:
    images: list[tuple[bytes, str]] = []
    for step in _get(interaction, "steps", []) or []:
        if _get(step, "type") != "model_output":
            continue
        for block in _get(step, "content", []) or []:
            if _get(block, "type") != "image":
                continue
            data = _get(block, "data")
            if not data:
                continue
            images.append((_decode_image_data(data), _get(block, "mime_type", "image/png") or "image/png"))
    if not images:
        raise GeminiCliError("Gemini response contained no image blocks.")
    return images


def _extract_generate_content_images(response: Any) -> list[tuple[bytes, str]]:
    images: list[tuple[bytes, str]] = []
    for candidate in _get(response, "candidates", []) or []:
        content = _get(candidate, "content")
        for part in _get(content, "parts", []) or []:
            inline_data = _get(part, "inline_data")
            if inline_data is None:
                inline_data = _get(part, "inlineData")
            data = _get(inline_data, "data") if inline_data is not None else None
            if not data:
                continue
            mime_type = (
                _get(inline_data, "mime_type")
                or _get(inline_data, "mimeType")
                or "image/png"
            )
            images.append((_decode_image_data(data), mime_type))
    if not images:
        raise GeminiCliError("Gemini response contained no image blocks.")
    return images


def _extract_images(response: Any, api_mode: str) -> list[tuple[bytes, str]]:
    if api_mode == "interactions":
        return _extract_interaction_images(response)
    return _extract_generate_content_images(response)


def _create_client() -> Any:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise GeminiCliError("GEMINI_API_KEY is not set.")
    try:
        from google import genai
    except ImportError as exc:
        raise GeminiCliError(
            "google-genai is not installed. Install it with `py -m pip install --upgrade google-genai`."
        ) from exc
    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("GEMINI_BASE_URL", "").strip()
    if base_url:
        kwargs["http_options"] = {"base_url": base_url.rstrip("/")}
    return genai.Client(**kwargs)


def _is_transient(exc: Exception) -> bool:
    message = str(exc).lower()
    name = exc.__class__.__name__.lower()
    return any(
        marker in message or marker in name
        for marker in ("429", "rate limit", "timeout", "timed out", "tempor", "connection reset", "unavailable")
    )


def _retry_after(exc: Exception, attempt: int) -> float:
    for attr in ("retry_after", "retry_after_seconds"):
        value = getattr(exc, attr, None)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    match = re.search(r"retry[- ]after[:= ]+([0-9]+(?:\.[0-9]+)?)", str(exc), re.I)
    return float(match.group(1)) if match else min(60.0, 2.0**attempt)


def _create_response(
    client: Any,
    payload: dict[str, Any],
    attempts: int,
    label: str,
    api_mode: str,
) -> Any:
    for attempt in range(1, attempts + 1):
        try:
            if api_mode == "interactions":
                return client.interactions.create(**payload)
            return client.models.generate_content(**payload)
        except Exception as exc:
            if not _is_transient(exc) or attempt == attempts:
                raise
            delay = _retry_after(exc, attempt)
            print(
                f"{label} attempt {attempt}/{attempts} failed ({exc.__class__.__name__}); "
                f"retrying in {delay:.1f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise GeminiCliError("Gemini request failed without an exception.")


def _output_paths(out: str, out_dir: Optional[str], output_format: str, count: int) -> list[Path]:
    extension = ".jpg" if output_format == "jpeg" else f".{output_format}"

    def normalized(path: Path) -> Path:
        if not path.suffix:
            return path.with_suffix(extension)
        accepted = {".jpg", ".jpeg"} if output_format == "jpeg" else {extension}
        if path.suffix.lower() not in accepted:
            _warn(
                f"Output extension {path.suffix} does not match output-format {output_format}; "
                f"using {extension}."
            )
            return path.with_suffix(extension)
        return path

    if out_dir:
        directory = Path(out_dir)
        if out != DEFAULT_OUTPUT_PATH:
            base = normalized(directory / Path(out).name)
            if count == 1:
                return [base]
            return [
                base.with_name(f"{base.stem}-{index}{base.suffix}")
                for index in range(1, count + 1)
            ]
        return [directory / f"image_{index}{extension}" for index in range(1, count + 1)]
    base = normalized(Path(out))
    if count == 1:
        return [base]
    return [base.with_name(f"{base.stem}-{index}{base.suffix}") for index in range(1, count + 1)]


def _derive_downscale_path(path: Path, suffix: str) -> Path:
    if suffix and not suffix.startswith(("-", "_")):
        suffix = "-" + suffix
    return path.with_name(f"{path.stem}{suffix}{path.suffix}")


def _convert_image(
    raw: bytes,
    output_format: str,
    compression: Optional[int],
    max_dim: Optional[int] = None,
) -> bytes:
    try:
        from PIL import Image
    except ImportError as exc:
        raise GeminiCliError(
            "Gemini image validation and conversion require Pillow. "
            "Install it with `py -m pip install --upgrade pillow`."
        ) from exc
    with Image.open(BytesIO(raw)) as image:
        image.load()
        if max_dim is not None and max(image.size) > max_dim:
            scale = max_dim / max(image.size)
            target = tuple(max(1, round(value * scale)) for value in image.size)
            image = image.resize(target, Image.Resampling.LANCZOS)
        if output_format == "jpeg":
            if image.mode in {"RGBA", "LA"}:
                background = Image.new("RGB", image.size, "white")
                background.paste(image.convert("RGBA"), mask=image.convert("RGBA").getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
        buffer = BytesIO()
        kwargs: dict[str, Any] = {}
        if compression is not None:
            if output_format == "png":
                kwargs["compress_level"] = round((100 - compression) * 9 / 100)
            else:
                kwargs["quality"] = compression
        image.save(buffer, format=output_format.upper(), **kwargs)
        return buffer.getvalue()


def _write_images(images: list[tuple[bytes, str]], args: argparse.Namespace) -> list[Path]:
    output_format = _normalize_output_format(args.output_format)
    paths = _output_paths(args.out, args.out_dir, output_format, len(images))
    for path, (raw, _mime_type) in zip(paths, images):
        if path.exists() and not args.force:
            raise GeminiCliError(f"Output already exists: {path} (use --force to overwrite)")
        path.parent.mkdir(parents=True, exist_ok=True)
        converted = _convert_image(raw, output_format, args.output_compression)
        path.write_bytes(converted)
        print(f"Wrote {path}")
        if args.downscale_max_dim is not None:
            derived = _derive_downscale_path(path, args.downscale_suffix)
            if derived.exists() and not args.force:
                raise GeminiCliError(f"Output already exists: {derived} (use --force to overwrite)")
            derived.write_bytes(
                _convert_image(raw, output_format, args.output_compression, args.downscale_max_dim)
            )
            print(f"Wrote {derived}")
    return paths


def _payload_preview(
    payload: dict[str, Any], outputs: list[Path], api_mode: str
) -> dict[str, Any]:
    preview = dict(payload)
    inputs = preview.get("input")
    if isinstance(inputs, list):
        preview["input"] = [
            {**item, "data": f"<base64:{len(item.get('data', ''))} chars>"}
            if isinstance(item, dict) and item.get("type") == "image"
            else item
            for item in inputs
        ]
    contents = preview.get("contents")
    if isinstance(contents, list):
        preview["contents"] = [
            {
                **item,
                "inline_data": {
                    **item["inline_data"],
                    "data": f"<base64:{len(item['inline_data'].get('data', ''))} chars>",
                },
            }
            if isinstance(item, dict) and isinstance(item.get("inline_data"), dict)
            else item
            for item in contents
        ]
    preview["api_mode"] = api_mode
    preview["outputs"] = [str(path) for path in outputs]
    return preview


def _base_payload(args: argparse.Namespace, prompt: str, api_mode: str) -> dict[str, Any]:
    if api_mode == "interactions":
        return {"model": args.model, "input": prompt, "response_format": _response_format(args)}
    aspect_ratio, image_size = _parse_size(args.size, args.model)
    image_config = {}
    if aspect_ratio:
        image_config["aspect_ratio"] = aspect_ratio
    if image_size:
        image_config["image_size"] = image_size
    config: dict[str, Any] = {"response_modalities": ["TEXT", "IMAGE"]}
    if image_config:
        config["image_config"] = image_config
    return {"model": args.model, "contents": [{"text": prompt}], "config": config}


def _run_single(args: argparse.Namespace) -> None:
    _validate_options(args)
    api_mode = _api_mode()
    prompt = _augment_prompt(_read_prompt(args.prompt, args.prompt_file), _fields_from_args(args), args.augment)
    payload = _base_payload(args, prompt, api_mode)
    if args.command == "edit":
        paths = _check_images(args.image)
        if api_mode == "interactions":
            payload["input"] = [{"type": "text", "text": prompt}, *[_image_input(path) for path in paths]]
        else:
            payload["contents"] = [
                {"text": prompt},
                *[
                    {
                        "inline_data": {
                            "data": item["data"],
                            "mime_type": item["mime_type"],
                        }
                    }
                    for item in (_image_input(path) for path in paths)
                ],
            ]
    requested_paths = _output_paths(args.out, args.out_dir, _normalize_output_format(args.output_format), args.n)
    if args.dry_run:
        print(json.dumps(_payload_preview(payload, requested_paths, api_mode), indent=2, sort_keys=True))
        return
    client = _create_client()
    images: list[tuple[bytes, str]] = []
    for index in range(1, args.n + 1):
        api_label = "Interactions" if api_mode == "interactions" else "Generate Content"
        print(f"[request {index}/{args.n}] calling Gemini {api_label} API", file=sys.stderr)
        response = _create_response(
            client, payload, args.max_attempts, f"[request {index}/{args.n}]", api_mode
        )
        images.extend(_extract_images(response, api_mode))
    _write_images(images, args)


def _read_jobs(path: str) -> list[dict[str, Any]]:
    input_path = Path(path)
    if not input_path.is_file():
        raise GeminiCliError(f"Input file not found: {input_path}")
    jobs = []
    for line_number, raw in enumerate(input_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            item = json.loads(line) if line.startswith("{") else {"prompt": line}
        except json.JSONDecodeError as exc:
            raise GeminiCliError(f"Invalid JSON on line {line_number}: {exc}") from exc
        if not isinstance(item, dict) or not str(item.get("prompt", "")).strip():
            raise GeminiCliError(f"Job {line_number} must contain a non-empty prompt.")
        jobs.append(item)
    if not jobs:
        raise GeminiCliError("No jobs found in input file.")
    if len(jobs) > MAX_BATCH_JOBS:
        raise GeminiCliError(f"Too many jobs ({len(jobs)}); maximum is {MAX_BATCH_JOBS}.")
    return jobs


def _job_args(args: argparse.Namespace, job: dict[str, Any], index: int) -> argparse.Namespace:
    values = vars(args).copy()
    for key in ("model", "n", "size", "quality", "background", "output_format", "output_compression", "moderation"):
        if job.get(key) is not None:
            values[key] = job[key]
    values["prompt"] = str(job["prompt"])
    values["prompt_file"] = None
    values["out"] = str(job.get("out") or f"{index:03d}-image.{_normalize_output_format(values['output_format'])}")
    values["out_dir"] = args.out_dir
    fields = job.get("fields", {}) if isinstance(job.get("fields"), dict) else {}
    for key in _fields_from_args(args):
        values[key] = job.get(key, fields.get(key, values.get(key)))
    return argparse.Namespace(**values)


def _run_batch(args: argparse.Namespace) -> None:
    if not args.out_dir:
        raise GeminiCliError("generate-batch requires --out-dir.")
    if not 1 <= args.concurrency <= 25:
        raise GeminiCliError("--concurrency must be between 1 and 25.")
    jobs = _read_jobs(args.input)
    api_mode = _api_mode()
    job_args = [_job_args(args, job, index) for index, job in enumerate(jobs, start=1)]
    for item in job_args:
        _validate_options(item)
    if args.dry_run:
        for index, item in enumerate(job_args, start=1):
            prompt = _augment_prompt(item.prompt, _fields_from_args(item), item.augment)
            payload = _base_payload(item, prompt, api_mode)
            outputs = _output_paths(item.out, item.out_dir, _normalize_output_format(item.output_format), item.n)
            print(json.dumps({"job": index, **_payload_preview(payload, outputs, api_mode)}, indent=2, sort_keys=True))
        return

    client = _create_client()

    def run_job(index: int, item: argparse.Namespace) -> None:
        prompt = _augment_prompt(item.prompt, _fields_from_args(item), item.augment)
        payload = _base_payload(item, prompt, api_mode)
        images: list[tuple[bytes, str]] = []
        for request_index in range(1, item.n + 1):
            label = f"[job {index}/{len(job_args)} request {request_index}/{item.n}]"
            response = _create_response(client, payload, item.max_attempts, label, api_mode)
            images.extend(_extract_images(response, api_mode))
        _write_images(images, item)

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {executor.submit(run_job, index, item): index for index, item in enumerate(job_args, start=1)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                future.result()
            except Exception as exc:
                failures.append(f"job {index}: {exc}")
                print(f"[job {index}/{len(job_args)}] failed: {exc}", file=sys.stderr)
                if args.fail_fast:
                    for pending in futures:
                        pending.cancel()
                    break
    if failures:
        raise GeminiCliError(f"{len(failures)} batch job(s) failed: {'; '.join(failures)}")


def _add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--size", default="auto")
    parser.add_argument("--aspect-ratio")
    parser.add_argument("--quality")
    parser.add_argument("--background")
    parser.add_argument("--output-format")
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--moderation")
    parser.add_argument("--out", default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--out-dir")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--augment", dest="augment", action="store_true")
    parser.add_argument("--no-augment", dest="augment", action="store_false")
    parser.set_defaults(augment=True)
    parser.add_argument("--use-case")
    parser.add_argument("--scene")
    parser.add_argument("--subject")
    parser.add_argument("--style")
    parser.add_argument("--composition")
    parser.add_argument("--lighting")
    parser.add_argument("--palette")
    parser.add_argument("--materials")
    parser.add_argument("--text")
    parser.add_argument("--constraints")
    parser.add_argument("--negative")
    parser.add_argument("--downscale-max-dim", type=int)
    parser.add_argument("--downscale-suffix", default=DEFAULT_DOWNSCALE_SUFFIX)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or edit images with Google Gemini")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="Create a new image")
    _add_shared_args(generate)
    generate.set_defaults(func=_run_single)
    edit = subparsers.add_parser("edit", help="Edit an image using one or more references")
    _add_shared_args(edit)
    edit.add_argument("--image", action="append", required=True)
    edit.add_argument("--mask")
    edit.add_argument("--input-fidelity")
    edit.set_defaults(func=_run_single)
    batch = subparsers.add_parser("generate-batch", help="Generate JSONL jobs concurrently")
    _add_shared_args(batch)
    batch.add_argument("--input", required=True)
    batch.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    batch.add_argument("--fail-fast", action="store_true")
    batch.set_defaults(func=_run_batch)
    args = parser.parse_args()
    if not 1 <= args.max_attempts <= 10:
        _die("--max-attempts must be between 1 and 10.")
    try:
        args.func(args)
    except GeminiCliError as exc:
        _die(str(exc))
    except KeyboardInterrupt:
        _die("Interrupted.", 130)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
