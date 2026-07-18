#!/usr/bin/env python3
"""Generate and edit images through the xAI Grok Imagine JSON API."""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from fractions import Fraction
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path
import random
import re
import sys
import time
from typing import Any, Iterable, Optional
from urllib.parse import urlparse


DEFAULT_MODEL = "grok-imagine-image-quality"
DEFAULT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_OUTPUT_PATH = "output/imagegen/output.png"
DEFAULT_CONCURRENCY = 5
DEFAULT_DOWNSCALE_SUFFIX = "-web"
MAX_BATCH_JOBS = 500
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_REFERENCE_IMAGES = 3
SUPPORTED_MODELS = {
    "grok-imagine-image",
    "grok-imagine-image-2026-03-02",
    "grok-imagine-image-quality",
    "grok-imagine-image-quality-20260403",
    "grok-imagine-image-quality-latest",
    "grok-imagine-image-pro",
}
SUPPORTED_RATIOS = {
    "1:1": Fraction(1, 1),
    "3:4": Fraction(3, 4),
    "4:3": Fraction(4, 3),
    "9:16": Fraction(9, 16),
    "16:9": Fraction(16, 9),
    "2:3": Fraction(2, 3),
    "3:2": Fraction(3, 2),
    "9:19.5": Fraction(6, 13),
    "19.5:9": Fraction(13, 6),
    "9:20": Fraction(9, 20),
    "20:9": Fraction(20, 9),
    "1:2": Fraction(1, 2),
    "2:1": Fraction(2, 1),
}
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class GrokCliError(RuntimeError):
    pass


class GrokHttpError(GrokCliError):
    def __init__(self, status_code: int, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def _read_prompt(prompt: Optional[str], prompt_file: Optional[str]) -> str:
    if prompt and prompt_file:
        raise GrokCliError("Use --prompt or --prompt-file, not both.")
    if prompt_file:
        path = Path(prompt_file)
        if not path.is_file():
            raise GrokCliError(f"Prompt file not found: {path}")
        value = path.read_text(encoding="utf-8").strip()
    else:
        value = (prompt or "").strip()
    if not value:
        raise GrokCliError("Missing prompt. Use --prompt or --prompt-file.")
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
        raise GrokCliError(
            "Unsupported Grok image model. Supported models: " + ", ".join(sorted(SUPPORTED_MODELS))
        )


def _parse_size(size: str) -> tuple[Optional[str], Optional[str], bool]:
    if size == "auto":
        return None, None, False
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", size)
    if not match:
        raise GrokCliError("size must be auto or WIDTHxHEIGHT, for example 1024x1024.")
    width, height = int(match.group(1)), int(match.group(2))
    ratio = Fraction(width, height)
    matching = [name for name, value in SUPPORTED_RATIOS.items() if ratio == value]
    if not matching:
        raise GrokCliError(
            f"Grok Imagine does not support the {width}:{height} aspect ratio. "
            f"Supported ratios: {', '.join(SUPPORTED_RATIOS)}, auto."
        )
    resolution = "1k" if max(width, height) <= 1024 else "2k"
    return matching[0], resolution, max(width, height) > 2048


def _normalize_output_format(value: Optional[str]) -> str:
    fmt = (value or "png").lower()
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt not in {"png", "jpeg", "webp"}:
        raise GrokCliError("output-format must be png, jpeg, jpg, or webp.")
    return fmt


def _validate_options(args: argparse.Namespace) -> None:
    _validate_model(args.model)
    _aspect_ratio, _resolution, downgraded = _parse_size(args.size)
    if downgraded:
        _warn(f"--size {args.size} exceeds Grok's 2K tier and will map to 2k.")
    if args.n < 1 or args.n > 10:
        raise GrokCliError("--n must be between 1 and 10.")
    if args.background == "transparent":
        raise GrokCliError(
            "Grok Imagine does not provide native background=transparent output. "
            "Generate a flat chroma-key background and remove it locally instead."
        )
    if args.background not in {None, "auto", "opaque", "transparent"}:
        raise GrokCliError("background must be one of transparent, opaque, or auto.")
    if getattr(args, "mask", None):
        raise GrokCliError("Grok Imagine edit does not support --mask in this Provider.")
    if args.output_compression is not None and not 0 <= args.output_compression <= 100:
        raise GrokCliError("--output-compression must be between 0 and 100.")
    if args.downscale_max_dim is not None and args.downscale_max_dim < 1:
        raise GrokCliError("--downscale-max-dim must be >= 1.")
    if args.quality is not None:
        _warn("--quality is not mapped by the Grok Provider and will be ignored.")
    if getattr(args, "input_fidelity", None) is not None:
        _warn("--input-fidelity is not mapped by the Grok Provider and will be ignored.")
    if args.moderation is not None:
        _warn("--moderation is not configurable by the Grok Provider and will be ignored.")


def _check_images(raw_paths: Iterable[str]) -> list[Path]:
    paths = []
    for raw in raw_paths:
        path = Path(raw)
        if not path.is_file():
            raise GrokCliError(f"Image file not found: {path}")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise GrokCliError(f"Reference image exceeds 50MB: {path}")
        mime_type = mimetypes.guess_type(path.name)[0] or ""
        if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise GrokCliError(f"Reference image must be JPEG, PNG, or WebP: {path}")
        paths.append(path)
    if len(paths) > MAX_REFERENCE_IMAGES:
        raise GrokCliError(
            f"Grok Imagine supports at most {MAX_REFERENCE_IMAGES} reference images; got {len(paths)}."
        )
    return paths


def _image_object(path: Path) -> dict[str, str]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"url": f"data:{mime_type};base64,{encoded}"}


def _reference_object(raw: str) -> dict[str, str]:
    """Build the xAI image reference object from a path, URL, or file ID."""
    value = raw.strip()
    if not value:
        raise GrokCliError("Image reference cannot be empty.")
    if value.startswith("file_id:"):
        file_id = value[len("file_id:") :].strip()
        if not file_id:
            raise GrokCliError("file_id: reference cannot be empty.")
        return {"file_id": file_id}
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return {"url": value}
    paths = _check_images([value])
    return _image_object(paths[0])


def _label_multi_image_prompt(prompt: str, count: int) -> str:
    if count < 2:
        return prompt
    labels = "\n".join(
        f"<IMAGE_{index}> refers to reference image {index + 1} in the supplied order."
        for index in range(count)
    )
    return f"{prompt}\n\nInput image order:\n{labels}"


def _base_payload(args: argparse.Namespace, prompt: str, *, is_edit: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "n": args.n,
        "response_format": "b64_json",
    }
    aspect_ratio, resolution, _downgraded = _parse_size(args.size)
    if aspect_ratio and not (is_edit and len(getattr(args, "image", [])) == 1 and args.size == "auto"):
        payload["aspect_ratio"] = aspect_ratio
    if resolution:
        payload["resolution"] = resolution
    return payload


def _redacted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    preview = json.loads(json.dumps(payload))
    image_values = []
    if "image" in preview:
        image_values = [preview["image"]]
    elif "images" in preview:
        image_values = preview["images"]
    for item in image_values:
        if isinstance(item, dict) and item.get("file_id"):
            item["file_id"] = "<file_id>"
        url = item.get("url", "") if isinstance(item, dict) else ""
        if url.startswith("data:"):
            header, _, data = url.partition(",")
            item["url"] = f"{header},<base64:{len(data)} chars>"
    return preview


def _create_http_client() -> Any:
    try:
        import httpx
    except ImportError as exc:
        raise GrokCliError(
            "httpx is not installed. Install it with `py -m pip install --upgrade httpx`."
        ) from exc
    return httpx.Client(timeout=120.0, follow_redirects=True, max_redirects=3)


def _api_key() -> str:
    value = os.getenv("XAI_API_KEY", "").strip()
    if not value:
        raise GrokCliError("XAI_API_KEY is not set.")
    return value


def _base_url() -> str:
    return (os.getenv("XAI_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/")


def _retry_after(headers: Any) -> Optional[float]:
    value = headers.get("retry-after") if headers is not None else None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _error_message(response: Any) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict):
                return str(error.get("message") or error.get("code") or f"HTTP {response.status_code}")
            return str(error)
    except Exception:
        pass
    return f"HTTP {response.status_code}"


def _post_with_retries(
    client: Any,
    endpoint: str,
    payload: dict[str, Any],
    *,
    attempts: int,
    label: str,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}
    url = f"{_base_url()}/{endpoint.lstrip('/')}"
    for attempt in range(1, attempts + 1):
        try:
            response = client.post(url, headers=headers, json=payload)
            if response.status_code >= 400:
                raise GrokHttpError(
                    response.status_code,
                    _error_message(response),
                    _retry_after(response.headers),
                )
            body = response.json()
            if not isinstance(body, dict):
                raise GrokCliError("Grok API returned a non-object JSON response.")
            return body
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            retryable = status in RETRYABLE_STATUS_CODES if status is not None else not isinstance(exc, GrokCliError)
            if not retryable or attempt == attempts:
                raise
            delay = getattr(exc, "retry_after", None)
            if delay is None:
                delay = min(60.0, 2.0**attempt) + random.uniform(0.0, 0.5)
            print(
                f"{label} attempt {attempt}/{attempts} failed ({exc.__class__.__name__}); "
                f"retrying in {delay:.1f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise GrokCliError("Grok request failed without an exception.")


def _request_json(client: Any, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {_api_key()}"}
    if "json" in kwargs:
        headers["Content-Type"] = "application/json"
    url = f"{_base_url()}/{endpoint.lstrip('/')}"
    response = client.request(method, url, headers=headers, **kwargs)
    if response.status_code >= 400:
        raise GrokHttpError(response.status_code, _error_message(response), _retry_after(response.headers))
    body = response.json()
    if not isinstance(body, dict):
        raise GrokCliError("Grok Files API returned a non-object JSON response.")
    return body


def _run_files(args: argparse.Namespace) -> None:
    client = _create_http_client()
    try:
        if args.files_command == "upload":
            path = Path(args.file)
            if not path.is_file():
                raise GrokCliError(f"File not found: {path}")
            if path.stat().st_size > MAX_IMAGE_BYTES:
                raise GrokCliError(f"File exceeds 50MB: {path}")
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            with path.open("rb") as stream:
                body = _request_json(
                    client,
                    "POST",
                    "files",
                    data={"purpose": args.purpose},
                    files={"file": (path.name, stream, mime_type)},
                )
        elif args.files_command == "get":
            body = _request_json(client, "GET", f"files/{args.file_id}")
        elif args.files_command == "delete":
            body = _request_json(client, "DELETE", f"files/{args.file_id}")
        else:
            body = _request_json(client, "GET", "files")
        print(json.dumps(body, indent=2, ensure_ascii=False))
    finally:
        client.close()


def _download_image(client: Any, url: str) -> tuple[bytes, str]:
    response = client.get(url, headers={"Accept": "image/*"})
    if response.status_code >= 400:
        raise GrokCliError(f"Failed to download generated image: HTTP {response.status_code}")
    length = response.headers.get("content-length")
    if length and int(length) > MAX_IMAGE_BYTES:
        raise GrokCliError("Generated image download exceeds 50MB.")
    raw = bytes(response.content)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise GrokCliError("Generated image download is empty or exceeds 50MB.")
    mime_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
    return raw, mime_type


def _extract_images(body: dict[str, Any], client: Any) -> list[tuple[bytes, str]]:
    images = []
    for item in body.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        mime_type = str(item.get("mime_type") or "image/jpeg")
        encoded = item.get("b64_json")
        if encoded:
            try:
                raw = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise GrokCliError(f"Grok returned invalid base64 image data: {exc}") from exc
            images.append((raw, mime_type))
        elif item.get("url"):
            images.append(_download_image(client, str(item["url"])))
    if not images:
        raise GrokCliError("Grok response contained no image data.")
    return images


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
            return [base.with_name(f"{base.stem}-{index}{base.suffix}") for index in range(1, count + 1)]
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
        raise GrokCliError(
            "Grok image validation and conversion require Pillow. "
            "Install it with `py -m pip install --upgrade pillow`."
        ) from exc
    try:
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
    except GrokCliError:
        raise
    except Exception as exc:
        raise GrokCliError(f"Generated image is invalid or unsupported: {exc}") from exc


def _write_images(images: list[tuple[bytes, str]], args: argparse.Namespace) -> list[Path]:
    output_format = _normalize_output_format(args.output_format)
    paths = _output_paths(args.out, args.out_dir, output_format, len(images))
    derived_paths = (
        [_derive_downscale_path(path, args.downscale_suffix) for path in paths]
        if args.downscale_max_dim is not None
        else []
    )
    for path in [*paths, *derived_paths]:
        if path.exists() and not args.force:
            raise GrokCliError(f"Output already exists: {path} (use --force to overwrite)")
    for path, (raw, _mime_type) in zip(paths, images):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_convert_image(raw, output_format, args.output_compression))
        print(f"Wrote {path}")
        if args.downscale_max_dim is not None:
            derived = _derive_downscale_path(path, args.downscale_suffix)
            derived.write_bytes(
                _convert_image(raw, output_format, args.output_compression, args.downscale_max_dim)
            )
            print(f"Wrote {derived}")
    return paths


def _run_single(args: argparse.Namespace) -> None:
    _validate_options(args)
    prompt = _augment_prompt(_read_prompt(args.prompt, args.prompt_file), _fields_from_args(args), args.augment)
    is_edit = args.command == "edit"
    references = [_reference_object(raw) for raw in args.image] if is_edit else []
    if len(references) > MAX_REFERENCE_IMAGES:
        raise GrokCliError(
            f"Grok Imagine supports at most {MAX_REFERENCE_IMAGES} reference images; got {len(references)}."
        )
    prompt = _label_multi_image_prompt(prompt, len(references))
    payload = _base_payload(args, prompt, is_edit=is_edit)
    if len(references) == 1:
        payload["image"] = references[0]
    elif len(references) > 1:
        payload["images"] = references
    endpoint = "images/edits" if is_edit else "images/generations"
    if args.dry_run:
        preview = _redacted_payload(payload)
        preview["endpoint"] = f"/{endpoint}"
        preview["outputs"] = [
            str(path)
            for path in _output_paths(args.out, args.out_dir, _normalize_output_format(args.output_format), args.n)
        ]
        print(json.dumps(preview, indent=2, sort_keys=True))
        return
    client = _create_http_client()
    try:
        body = _post_with_retries(client, endpoint, payload, attempts=args.max_attempts, label="[request 1/1]")
        images = _extract_images(body, client)
        _write_images(images, args)
        usage = body.get("usage") if isinstance(body, dict) else None
        if isinstance(usage, dict) and isinstance(usage.get("cost_in_usd_ticks"), int):
            print(f"Cost: {usage['cost_in_usd_ticks']} USD ticks", file=sys.stderr)
    finally:
        client.close()


def _read_jobs(path: str) -> list[dict[str, Any]]:
    input_path = Path(path)
    if not input_path.is_file():
        raise GrokCliError(f"Input file not found: {input_path}")
    jobs = []
    for line_number, raw in enumerate(input_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            item = json.loads(line) if line.startswith("{") else {"prompt": line}
        except json.JSONDecodeError as exc:
            raise GrokCliError(f"Invalid JSON on line {line_number}: {exc}") from exc
        if not isinstance(item, dict) or not str(item.get("prompt", "")).strip():
            raise GrokCliError(f"Job {line_number} must contain a non-empty prompt.")
        jobs.append(item)
    if not jobs:
        raise GrokCliError("No jobs found in input file.")
    if len(jobs) > MAX_BATCH_JOBS:
        raise GrokCliError(f"Too many jobs ({len(jobs)}); maximum is {MAX_BATCH_JOBS}.")
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
        raise GrokCliError("generate-batch requires --out-dir.")
    if not 1 <= args.concurrency <= 25:
        raise GrokCliError("--concurrency must be between 1 and 25.")
    jobs = _read_jobs(args.input)
    job_args = [_job_args(args, job, index) for index, job in enumerate(jobs, start=1)]
    for item in job_args:
        _validate_options(item)
    if args.dry_run:
        for index, item in enumerate(job_args, start=1):
            prompt = _augment_prompt(item.prompt, _fields_from_args(item), item.augment)
            payload = _base_payload(item, prompt, is_edit=False)
            preview = _redacted_payload(payload)
            preview["job"] = index
            preview["endpoint"] = "/images/generations"
            preview["outputs"] = [
                str(path)
                for path in _output_paths(item.out, item.out_dir, _normalize_output_format(item.output_format), item.n)
            ]
            print(json.dumps(preview, indent=2, sort_keys=True))
        return

    client = _create_http_client()

    def run_job(index: int, item: argparse.Namespace) -> None:
        prompt = _augment_prompt(item.prompt, _fields_from_args(item), item.augment)
        payload = _base_payload(item, prompt, is_edit=False)
        label = f"[job {index}/{len(job_args)}]"
        body = _post_with_retries(
            client,
            "images/generations",
            payload,
            attempts=item.max_attempts,
            label=label,
        )
        _write_images(_extract_images(body, client), item)

    failures = []
    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(run_job, index, item): index
                for index, item in enumerate(job_args, start=1)
            }
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
    finally:
        client.close()
    if failures:
        raise GrokCliError(f"{len(failures)} batch job(s) failed: {'; '.join(failures)}")


def _add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--size", default="auto")
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
    parser = argparse.ArgumentParser(description="Generate or edit images with xAI Grok Imagine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="Create a new image")
    _add_shared_args(generate)
    generate.set_defaults(func=_run_single)
    edit = subparsers.add_parser("edit", help="Edit an image using up to three references")
    _add_shared_args(edit)
    edit.add_argument("--image", action="append", required=True)
    edit.add_argument("--mask")
    edit.add_argument("--input-fidelity")
    edit.set_defaults(func=_run_single)
    files = subparsers.add_parser("files", help="Manage xAI Imagine Files")
    file_commands = files.add_subparsers(dest="files_command", required=True)
    upload = file_commands.add_parser("upload", help="Upload an image file")
    upload.add_argument("--file", required=True)
    upload.add_argument("--purpose", default="vision")
    upload.set_defaults(func=_run_files)
    get_file = file_commands.add_parser("get", help="Get file metadata")
    get_file.add_argument("file_id")
    get_file.set_defaults(func=_run_files)
    list_files = file_commands.add_parser("list", help="List uploaded files")
    list_files.set_defaults(func=_run_files)
    delete_file = file_commands.add_parser("delete", help="Delete an uploaded file")
    delete_file.add_argument("file_id")
    delete_file.set_defaults(func=_run_files)
    batch = subparsers.add_parser("generate-batch", help="Generate JSONL jobs concurrently")
    _add_shared_args(batch)
    batch.add_argument("--input", required=True)
    batch.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    batch.add_argument("--fail-fast", action="store_true")
    batch.set_defaults(func=_run_batch)
    args = parser.parse_args()
    if hasattr(args, "max_attempts") and not 1 <= args.max_attempts <= 10:
        _die("--max-attempts must be between 1 and 10.")
    try:
        args.func(args)
    except GrokCliError as exc:
        _die(str(exc))
    except KeyboardInterrupt:
        _die("Interrupted.", 130)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
