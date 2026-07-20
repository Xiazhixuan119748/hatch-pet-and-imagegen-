#!/usr/bin/env python3
"""Fallback CLI for explicit image generation or editing with GPT Image models.

Used only when the user explicitly opts into CLI fallback mode, or when explicit
transparent output requires the `gpt-image-1.5` fallback path.

Defaults to gpt-image-2 and a structured prompt augmentation workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import AbstractContextManager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from io import BytesIO

DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "medium"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_CONCURRENCY = 5
DEFAULT_DOWNSCALE_SUFFIX = "-web"
DEFAULT_OUTPUT_PATH = "output/imagegen/output.png"
GPT_IMAGE_MODEL_PREFIX = "gpt-image-"

ALLOWED_LEGACY_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}
ALLOWED_QUALITIES = {"low", "medium", "high", "auto"}
ALLOWED_BACKGROUNDS = {"transparent", "opaque", "auto", None}
ALLOWED_INPUT_FIDELITIES = {"low", "high", None}

GPT_IMAGE_2_MODEL = "gpt-image-2"
GPT_IMAGE_2_MIN_PIXELS = 655_360
GPT_IMAGE_2_MAX_PIXELS = 8_294_400
GPT_IMAGE_2_MAX_EDGE = 3840
GPT_IMAGE_2_MAX_RATIO = 3.0
ASPECT_RATIO_TOLERANCE = 0.01
STALE_LOCK_SECONDS = 60 * 60

MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_BATCH_JOBS = 500


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def _dependency_hint(package: str, *, upgrade: bool = False) -> str:
    command = f"uv pip install {'-U ' if upgrade else ''}{package}"
    return (
        "Activate the repo-selected environment first, then install it with "
        f"`{command}`. If this repo uses a local virtualenv, start with "
        "`source .venv/bin/activate`; otherwise use this repo's configured shared fallback "
        "environment. If your project declares dependencies, prefer that project's normal "
        "`uv sync` flow."
    )


def _ensure_api_key(dry_run: bool) -> None:
    if os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is set.", file=sys.stderr)
        return
    if dry_run:
        _warn("OPENAI_API_KEY is not set; dry-run only.")
        return
    _die("OPENAI_API_KEY is not set. Export it before running.")


def _read_prompt(prompt: Optional[str], prompt_file: Optional[str]) -> str:
    if prompt and prompt_file:
        _die("Use --prompt or --prompt-file, not both.")
    if prompt_file:
        path = Path(prompt_file)
        if not path.exists():
            _die(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()
    if prompt:
        return prompt.strip()
    _die("Missing prompt. Use --prompt or --prompt-file.")
    return ""  # unreachable


def _check_image_paths(paths: Iterable[str]) -> List[Path]:
    resolved: List[Path] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            _die(f"Image file not found: {path}")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            _warn(f"Image exceeds 50MB limit: {path}")
        resolved.append(path)
    return resolved


def _normalize_output_format(fmt: Optional[str]) -> str:
    if not fmt:
        return DEFAULT_OUTPUT_FORMAT
    fmt = fmt.lower()
    if fmt not in {"png", "jpeg", "jpg", "webp"}:
        _die("output-format must be png, jpeg, jpg, or webp.")
    return "jpeg" if fmt == "jpg" else fmt


def _parse_size(size: str) -> Optional[Tuple[int, int]]:
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", size)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _parse_aspect_ratio(value: str) -> Tuple[int, int]:
    match = re.fullmatch(r"([1-9][0-9]*):([1-9][0-9]*)", value.strip())
    if not match:
        _die("aspect-ratio must use W:H, for example 3:4.")
    return int(match.group(1)), int(match.group(2))


def _validate_requested_aspect_ratio(size: str, aspect_ratio: Optional[str]) -> None:
    if not aspect_ratio:
        return
    target_width, target_height = _parse_aspect_ratio(aspect_ratio)
    parsed = _parse_size(size)
    if parsed is None:
        _die("--aspect-ratio requires an explicit --size; --size auto is not allowed.")
    width, height = parsed
    if width * target_height != height * target_width:
        _die(
            f"--size {size} does not exactly match requested aspect ratio {aspect_ratio}."
        )


def _image_dimensions_and_validate(raw: bytes, aspect_ratio: Optional[str]) -> Tuple[int, int]:
    try:
        from PIL import Image
    except Exception:
        _die(f"Image validation requires Pillow. {_dependency_hint('pillow')}")
    try:
        with Image.open(BytesIO(raw)) as image:
            image.load()
            width, height = image.size
    except Exception as exc:
        _die(f"Generated image is invalid or unreadable: {exc}")
    if aspect_ratio:
        target_width, target_height = _parse_aspect_ratio(aspect_ratio)
        actual = width / height
        expected = target_width / target_height
        relative_error = abs(actual - expected) / expected
        if relative_error > ASPECT_RATIO_TOLERANCE:
            _die(
                f"Generated image is {width}x{height} ({actual:.4f}), which differs from "
                f"requested aspect ratio {aspect_ratio} by {relative_error:.2%}; maximum is 1%."
            )
    return width, height


def _validate_gpt_image_2_size(size: str) -> None:
    if size == "auto":
        return

    parsed = _parse_size(size)
    if parsed is None:
        _die("size must be auto or WIDTHxHEIGHT, for example 1024x1024.")

    width, height = parsed
    max_edge = max(width, height)
    min_edge = min(width, height)
    total_pixels = width * height

    if max_edge > GPT_IMAGE_2_MAX_EDGE:
        _die("gpt-image-2 size maximum edge length must be less than or equal to 3840px.")
    if width % 16 != 0 or height % 16 != 0:
        _die("gpt-image-2 size width and height must be multiples of 16px.")
    if max_edge / min_edge > GPT_IMAGE_2_MAX_RATIO:
        _die("gpt-image-2 size long edge to short edge ratio must not exceed 3:1.")
    if total_pixels < GPT_IMAGE_2_MIN_PIXELS or total_pixels > GPT_IMAGE_2_MAX_PIXELS:
        _die(
            "gpt-image-2 size total pixels must be at least 655,360 and no more than 8,294,400."
        )


def _validate_size(size: str, model: str) -> None:
    if model == GPT_IMAGE_2_MODEL:
        _validate_gpt_image_2_size(size)
        return

    if size not in ALLOWED_LEGACY_SIZES:
        _die(
            "size must be one of 1024x1024, 1536x1024, 1024x1536, or auto for this GPT Image model."
        )


def _validate_quality(quality: str) -> None:
    if quality not in ALLOWED_QUALITIES:
        _die("quality must be one of low, medium, high, or auto.")


def _validate_background(background: Optional[str]) -> None:
    if background not in ALLOWED_BACKGROUNDS:
        _die("background must be one of transparent, opaque, or auto.")


def _validate_input_fidelity(input_fidelity: Optional[str]) -> None:
    if input_fidelity not in ALLOWED_INPUT_FIDELITIES:
        _die("input-fidelity must be one of low or high.")


def _validate_model(model: str) -> None:
    if not model.startswith(GPT_IMAGE_MODEL_PREFIX):
        _die(
            "model must be a GPT Image model (for example gpt-image-1.5, gpt-image-1, or gpt-image-1-mini)."
        )


def _validate_transparency(background: Optional[str], output_format: str) -> None:
    if background == "transparent" and output_format not in {"png", "webp"}:
        _die("transparent background requires output-format png or webp.")


def _validate_model_specific_options(
    *,
    model: str,
    background: Optional[str],
    input_fidelity: Optional[str] = None,
) -> None:
    if model != GPT_IMAGE_2_MODEL:
        return
    if background == "transparent":
        _die(
            "transparent backgrounds are not supported in gpt-image-2, the latest model. "
            "Use --model gpt-image-1.5 --background transparent --output-format png instead."
        )
    if input_fidelity is not None:
        _die(
            "input_fidelity is not supported in gpt-image-2 because image inputs always use high fidelity for this model."
        )


def _validate_generate_payload(payload: Dict[str, Any]) -> None:
    model = str(payload.get("model", DEFAULT_MODEL))
    _validate_model(model)
    n = int(payload.get("n", 1))
    if n < 1 or n > 10:
        _die("n must be between 1 and 10")
    size = str(payload.get("size", DEFAULT_SIZE))
    quality = str(payload.get("quality", DEFAULT_QUALITY))
    background = payload.get("background")
    _validate_size(size, model)
    _validate_quality(quality)
    _validate_background(background)
    _validate_model_specific_options(model=model, background=background)
    oc = payload.get("output_compression")
    if oc is not None and not (0 <= int(oc) <= 100):
        _die("output_compression must be between 0 and 100")


def _build_output_paths(
    out: str,
    output_format: str,
    count: int,
    out_dir: Optional[str],
) -> List[Path]:
    ext = "." + output_format

    if out_dir:
        out_base = Path(out_dir)
        out_base.mkdir(parents=True, exist_ok=True)
        return [out_base / f"image_{i}{ext}" for i in range(1, count + 1)]

    out_path = Path(out)
    if out_path.exists() and out_path.is_dir():
        out_path.mkdir(parents=True, exist_ok=True)
        return [out_path / f"image_{i}{ext}" for i in range(1, count + 1)]

    if out_path.suffix == "":
        out_path = out_path.with_suffix(ext)
    elif output_format and out_path.suffix.lstrip(".").lower() != output_format:
        _warn(
            f"Output extension {out_path.suffix} does not match output-format {output_format}."
        )

    if count == 1:
        return [out_path]

    return [
        out_path.with_name(f"{out_path.stem}-{i}{out_path.suffix}")
        for i in range(1, count + 1)
    ]


def _augment_prompt(args: argparse.Namespace, prompt: str) -> str:
    fields = _fields_from_args(args)
    return _augment_prompt_fields(args.augment, prompt, fields)


def _augment_prompt_fields(augment: bool, prompt: str, fields: Dict[str, Optional[str]]) -> str:
    if not augment:
        return prompt

    sections: List[str] = []
    if fields.get("use_case"):
        sections.append(f"Use case: {fields['use_case']}")
    sections.append(f"Primary request: {prompt}")
    if fields.get("scene"):
        sections.append(f"Scene/background: {fields['scene']}")
    if fields.get("subject"):
        sections.append(f"Subject: {fields['subject']}")
    if fields.get("style"):
        sections.append(f"Style/medium: {fields['style']}")
    if fields.get("composition"):
        sections.append(f"Composition/framing: {fields['composition']}")
    if fields.get("lighting"):
        sections.append(f"Lighting/mood: {fields['lighting']}")
    if fields.get("palette"):
        sections.append(f"Color palette: {fields['palette']}")
    if fields.get("materials"):
        sections.append(f"Materials/textures: {fields['materials']}")
    if fields.get("text"):
        sections.append(f"Text (verbatim): \"{fields['text']}\"")
    if fields.get("constraints"):
        sections.append(f"Constraints: {fields['constraints']}")
    if fields.get("negative"):
        sections.append(f"Avoid: {fields['negative']}")

    return "\n".join(sections)


def _fields_from_args(args: argparse.Namespace) -> Dict[str, Optional[str]]:
    return {
        "use_case": getattr(args, "use_case", None),
        "scene": getattr(args, "scene", None),
        "subject": getattr(args, "subject", None),
        "style": getattr(args, "style", None),
        "composition": getattr(args, "composition", None),
        "lighting": getattr(args, "lighting", None),
        "palette": getattr(args, "palette", None),
        "materials": getattr(args, "materials", None),
        "text": getattr(args, "text", None),
        "constraints": getattr(args, "constraints", None),
        "negative": getattr(args, "negative", None),
    }


def _print_request(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lock_path(output: Path) -> Path:
    return output.with_name(f"{output.name}.imagegen.lock")


class _OutputReservations(AbstractContextManager["_OutputReservations"]):
    def __init__(self, outputs: Iterable[Path], *, force: bool):
        self.outputs = list(dict.fromkeys(outputs))
        self.force = force
        self.acquired: List[Path] = []

    def _acquire(self, output: Path) -> None:
        lock = _lock_path(output)
        lock.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "started_at": time.time(),
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "output": str(output.resolve()),
        }
        while True:
            try:
                with lock.open("x", encoding="utf-8") as handle:
                    json.dump(payload, handle, sort_keys=True)
                    handle.write("\n")
                self.acquired.append(lock)
                return
            except FileExistsError:
                try:
                    current = json.loads(lock.read_text(encoding="utf-8"))
                    pid = int(current.get("pid", 0))
                    started_at = float(current.get("started_at", lock.stat().st_mtime))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pid = 0
                    started_at = lock.stat().st_mtime
                age = max(0.0, time.time() - started_at)
                if _pid_is_running(pid):
                    _die(
                        f"Output is already reserved by active image generation PID {pid}: {output}"
                    )
                if age < STALE_LOCK_SECONDS:
                    _die(
                        f"Output has a recent orphaned generation lock ({age:.0f}s old): {lock}. "
                        "Inspect the original request before retrying."
                    )
                _warn(f"Removing stale image generation lock: {lock}")
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass

    def __enter__(self) -> "_OutputReservations":
        try:
            for output in self.outputs:
                if output.exists() and not self.force:
                    _die(f"Output already exists: {output} (use --force to overwrite)")
            for output in self.outputs:
                self._acquire(output)
        except BaseException:
            self._release()
            raise
        return self

    def _release(self) -> None:
        for lock in reversed(self.acquired):
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
        self.acquired.clear()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._release()
        return False


def _all_output_paths(
    outputs: Iterable[Path], downscale_max_dim: Optional[int], downscale_suffix: str
) -> List[Path]:
    paths = list(outputs)
    if downscale_max_dim is not None:
        paths.extend(_derive_downscale_path(path, downscale_suffix) for path in list(paths))
    return paths


def _decode_and_write(
    images: List[str], outputs: List[Path], force: bool, aspect_ratio: Optional[str] = None
) -> None:
    for idx, image_b64 in enumerate(images):
        if idx >= len(outputs):
            break
        out_path = outputs[idx]
        if out_path.exists() and not force:
            _die(f"Output already exists: {out_path} (use --force to overwrite)")
        raw = base64.b64decode(image_b64)
        width, height = _image_dimensions_and_validate(raw, aspect_ratio)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw)
        print(f"Wrote {out_path}")
        print(f"Validated {out_path}: {width}x{height}", file=sys.stderr)


def _derive_downscale_path(path: Path, suffix: str) -> Path:
    if suffix and not suffix.startswith("-") and not suffix.startswith("_"):
        suffix = "-" + suffix
    return path.with_name(f"{path.stem}{suffix}{path.suffix}")


def _downscale_image_bytes(image_bytes: bytes, *, max_dim: int, output_format: str) -> bytes:
    try:
        from PIL import Image
    except Exception:
        _die(f"Downscaling requires Pillow. {_dependency_hint('pillow')}")

    if max_dim < 1:
        _die("--downscale-max-dim must be >= 1")

    with Image.open(BytesIO(image_bytes)) as img:
        img.load()
        w, h = img.size
        scale = min(1.0, float(max_dim) / float(max(w, h)))
        target = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))

        resized = img if target == (w, h) else img.resize(target, Image.Resampling.LANCZOS)

        fmt = output_format.lower()
        if fmt == "jpg":
            fmt = "jpeg"

        if fmt == "jpeg":
            if resized.mode in ("RGBA", "LA") or ("transparency" in getattr(resized, "info", {})):
                bg = Image.new("RGB", resized.size, (255, 255, 255))
                bg.paste(resized.convert("RGBA"), mask=resized.convert("RGBA").split()[-1])
                resized = bg
            else:
                resized = resized.convert("RGB")

        out = BytesIO()
        resized.save(out, format=fmt.upper())
        return out.getvalue()


def _decode_write_and_downscale(
    images: List[str],
    outputs: List[Path],
    *,
    force: bool,
    downscale_max_dim: Optional[int],
    downscale_suffix: str,
    output_format: str,
    aspect_ratio: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if len(images) != len(outputs):
        _die(f"Provider returned {len(images)} image(s), expected {len(outputs)}.")
    validated: List[Dict[str, Any]] = []
    prepared: List[Tuple[Path, bytes, Optional[bytes], int, int]] = []
    for idx, image_b64 in enumerate(images):
        out_path = outputs[idx]
        if out_path.exists() and not force:
            _die(f"Output already exists: {out_path} (use --force to overwrite)")

        raw = base64.b64decode(image_b64)
        width, height = _image_dimensions_and_validate(raw, aspect_ratio)
        derived_bytes = None
        if downscale_max_dim is None:
            derived = None
        else:
            derived = _derive_downscale_path(out_path, downscale_suffix)
            if derived.exists() and not force:
                _die(f"Output already exists: {derived} (use --force to overwrite)")
            derived_bytes = _downscale_image_bytes(raw, max_dim=downscale_max_dim, output_format=output_format)
        prepared.append((out_path, raw, derived_bytes, width, height))

    for out_path, raw, derived_bytes, width, height in prepared:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        part = out_path.with_name(f"{out_path.name}.part")
        part.write_bytes(raw)
        part.replace(out_path)
        print(f"Wrote {out_path}")
        print(f"Validated {out_path}: {width}x{height}", file=sys.stderr)
        item: Dict[str, Any] = {"path": str(out_path), "dimensions": f"{width}x{height}"}
        validated.append(item)
        if derived_bytes is not None:
            derived = _derive_downscale_path(out_path, downscale_suffix)
            derived_part = derived.with_name(f"{derived.name}.part")
            derived_part.write_bytes(derived_bytes)
            derived_part.replace(derived)
            print(f"Wrote {derived}")
            item["downscaled_path"] = str(derived)
    return validated


def _create_client():
    try:
        from openai import OpenAI
    except ImportError:
        _die(f"openai SDK not installed in the active environment. {_dependency_hint('openai')}")
    return OpenAI(max_retries=0)


def _create_async_client():
    try:
        from openai import AsyncOpenAI
    except ImportError:
        try:
            import openai as _openai  # noqa: F401
        except ImportError:
            _die(
                f"openai SDK not installed in the active environment. {_dependency_hint('openai')}"
            )
        _die(
            "AsyncOpenAI not available in this openai SDK version. "
            f"{_dependency_hint('openai', upgrade=True)}"
        )
    return AsyncOpenAI(max_retries=0)


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:60] if value else "job"


def _normalize_job(job: Any, idx: int) -> Dict[str, Any]:
    if isinstance(job, str):
        prompt = job.strip()
        if not prompt:
            _die(f"Empty prompt at job {idx}")
        return {"prompt": prompt}
    if isinstance(job, dict):
        if "prompt" not in job or not str(job["prompt"]).strip():
            _die(f"Missing prompt for job {idx}")
        return job
    _die(f"Invalid job at index {idx}: expected string or object.")
    return {}  # unreachable


def _read_jobs_jsonl(path: str) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        _die(f"Input file not found: {p}")
    jobs: List[Dict[str, Any]] = []
    for line_no, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            item: Any
            if line.startswith("{"):
                item = json.loads(line)
            else:
                item = line
            jobs.append(_normalize_job(item, idx=line_no))
        except json.JSONDecodeError as exc:
            _die(f"Invalid JSON on line {line_no}: {exc}")
    if not jobs:
        _die("No jobs found in input file.")
    if len(jobs) > MAX_BATCH_JOBS:
        _die(f"Too many jobs ({len(jobs)}). Max is {MAX_BATCH_JOBS}.")
    return jobs


def _merge_non_null(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(dst)
    for k, v in src.items():
        if v is not None:
            merged[k] = v
    return merged


def _job_output_paths(
    *,
    out_dir: Path,
    output_format: str,
    idx: int,
    prompt: str,
    n: int,
    explicit_out: Optional[str],
) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = "." + output_format

    if explicit_out:
        base = Path(explicit_out)
        if base.suffix == "":
            base = base.with_suffix(ext)
        elif base.suffix.lstrip(".").lower() != output_format:
            _warn(
                f"Job {idx}: output extension {base.suffix} does not match output-format {output_format}."
            )
        base = out_dir / base.name
    else:
        slug = _slugify(prompt[:80])
        base = out_dir / f"{idx:03d}-{slug}{ext}"

    if n == 1:
        return [base]
    return [
        base.with_name(f"{base.stem}-{i}{base.suffix}")
        for i in range(1, n + 1)
    ]


def _extract_retry_after_seconds(exc: Exception) -> Optional[float]:
    # Best-effort: openai SDK errors vary by version. Prefer a conservative fallback.
    for attr in ("retry_after", "retry_after_seconds"):
        val = getattr(exc, attr, None)
        if isinstance(val, (int, float)) and val >= 0:
            return float(val)
    msg = str(exc)
    m = re.search(r"retry[- ]after[:= ]+([0-9]+(?:\\.[0-9]+)?)", msg, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


def _is_rate_limit_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


def _is_transient_error(exc: Exception) -> bool:
    # A timeout or connection reset can occur after the server accepted a paid
    # generation. Only retry an explicit rate-limit rejection automatically.
    return _is_rate_limit_error(exc)


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.part")
    part.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    part.replace(path)


def _unknown_request_error(exc: BaseException) -> bool:
    if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError, TimeoutError)):
        return True
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return any(token in name or token in message for token in ("timeout", "timed out", "connection reset", "connection error"))


def _batch_state_path(state_dir: Path, job_id: str, attempt: int) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", job_id).strip("-") or "job"
    return state_dir / f"{safe}-attempt-{attempt}.json"


async def _run_generate_batch(args: argparse.Namespace) -> int:
    jobs = _read_jobs_jsonl(args.input)
    out_dir = Path(args.out_dir)
    configured_state_dir = getattr(args, "state_dir", None)
    configured_summary_out = getattr(args, "summary_out", None)
    state_dir = Path(configured_state_dir) if configured_state_dir else out_dir / ".imagegen-state"
    summary_out = Path(configured_summary_out) if configured_summary_out else out_dir / "imagegen-batch-summary.json"

    base_fields = _fields_from_args(args)
    base_payload = {
        "model": args.model,
        "n": args.n,
        "size": args.size,
        "quality": args.quality,
        "background": args.background,
        "output_format": args.output_format,
        "output_compression": args.output_compression,
        "moderation": args.moderation,
    }

    if args.dry_run:
        for i, job in enumerate(jobs, start=1):
            prompt = str(job["prompt"]).strip()
            fields = _merge_non_null(base_fields, job.get("fields", {}))
            # Allow flat job keys as well (use_case, scene, etc.)
            fields = _merge_non_null(fields, {k: job.get(k) for k in base_fields.keys()})
            augmented = _augment_prompt_fields(args.augment, prompt, fields)

            job_payload = dict(base_payload)
            job_payload["prompt"] = augmented
            job_payload = _merge_non_null(job_payload, {k: job.get(k) for k in base_payload.keys()})
            job_payload = {k: v for k, v in job_payload.items() if v is not None}

            _validate_generate_payload(job_payload)
            effective_output_format = _normalize_output_format(job_payload.get("output_format"))
            _validate_transparency(job_payload.get("background"), effective_output_format)
            job_payload["output_format"] = effective_output_format

            n = int(job_payload.get("n", 1))
            outputs = _job_output_paths(
                out_dir=out_dir,
                output_format=effective_output_format,
                idx=i,
                prompt=prompt,
                n=n,
                explicit_out=job.get("out"),
            )
            downscaled = None
            if args.downscale_max_dim is not None:
                downscaled = [
                    str(_derive_downscale_path(p, args.downscale_suffix)) for p in outputs
                ]
            _print_request(
                {
                    "endpoint": "/v1/images/generations",
                    "job": i,
                    "outputs": [str(p) for p in outputs],
                    "outputs_downscaled": downscaled,
                    **job_payload,
                }
            )
        return 0

    client = None
    sem = asyncio.Semaphore(args.concurrency)
    job_ids = [str(job.get("id") or f"job-{i:03d}") for i, job in enumerate(jobs, start=1)]
    if len(job_ids) != len(set(job_ids)):
        _die("Batch job ids must be unique.")

    async def run_job(i: int, job: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal client
        prompt = str(job["prompt"]).strip()
        job_id = str(job.get("id") or f"job-{i:03d}")
        attempt = int(job.get("attempt", 1))
        state_path = _batch_state_path(state_dir, job_id, attempt)
        job_label = f"[job {job_id}]"
        state: Dict[str, Any] = {
            "job_id": job_id,
            "attempt": attempt,
            "attempt_id": f"{job_id}-attempt-{attempt}",
            "status": "pending",
            "model": job.get("model", args.model),
            "output_paths": [],
            "error": None,
            "started_at": None,
            "finished_at": None,
        }
        _write_json_atomic(state_path, state)

        try:
            fields = _merge_non_null(base_fields, job.get("fields", {}))
            fields = _merge_non_null(fields, {k: job.get(k) for k in base_fields.keys()})
            augmented = _augment_prompt_fields(args.augment, prompt, fields)
            payload = dict(base_payload)
            payload["prompt"] = augmented
            payload = _merge_non_null(payload, {k: job.get(k) for k in base_payload.keys()})
            payload = {k: v for k, v in payload.items() if v is not None}
            n = int(payload.get("n", 1))
            _validate_generate_payload(payload)
            effective_output_format = _normalize_output_format(payload.get("output_format"))
            _validate_transparency(payload.get("background"), effective_output_format)
            payload["output_format"] = effective_output_format
            aspect_ratio = job.get("aspect_ratio", args.aspect_ratio)
            _validate_requested_aspect_ratio(str(payload.get("size", DEFAULT_SIZE)), aspect_ratio)
            outputs = _job_output_paths(
                out_dir=out_dir, output_format=effective_output_format, idx=i,
                prompt=prompt, n=n, explicit_out=job.get("out"),
            )
            state["output_paths"] = [str(path) for path in outputs]
            state["status"] = "running"
            state["started_at"] = datetime.now(timezone.utc).isoformat()
            _write_json_atomic(state_path, state)
            reserved = _all_output_paths(outputs, args.downscale_max_dim, args.downscale_suffix)
            with _OutputReservations(reserved, force=getattr(args, "force", False)):
                async with sem:
                    # Create the client only after local preflight and lock
                    # acquisition, so rejected duplicate jobs make no SDK/API
                    # setup attempt at all.
                    if client is None:
                        client = _create_async_client()
                    print(f"{job_label} starting attempt {attempt}", file=sys.stderr)
                    started = time.time()
                    result = await client.images.generate(**payload)
                    print(f"{job_label} completed in {time.time() - started:.1f}s", file=sys.stderr)
                images = [item.b64_json for item in result.data]
                written = _decode_write_and_downscale(
                    images, outputs, force=getattr(args, "force", False),
                    downscale_max_dim=args.downscale_max_dim,
                    downscale_suffix=args.downscale_suffix,
                    output_format=effective_output_format,
                    aspect_ratio=aspect_ratio,
                )
            state.update({"status": "succeeded", "outputs": written, "finished_at": datetime.now(timezone.utc).isoformat()})
        except BaseException as exc:
            state.update({
                "status": "unknown" if _unknown_request_error(exc) else "failed",
                "error": f"{exc.__class__.__name__}: {exc}",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            print(f"{job_label} {state['status']}: {exc}", file=sys.stderr)
        _write_json_atomic(state_path, state)
        return state

    tasks = [asyncio.create_task(run_job(i, job)) for i, job in enumerate(jobs, start=1)]

    records = list(await asyncio.gather(*tasks))
    succeeded = [item for item in records if item["status"] == "succeeded"]
    failed = [item for item in records if item["status"] == "failed"]
    unknown = [item for item in records if item["status"] == "unknown"]
    abandoned = [item for item in records if item["status"] == "abandoned"]
    if succeeded and (failed or unknown or abandoned):
        status = "partial_success"
    elif succeeded:
        status = "succeeded"
    elif unknown:
        status = "unknown"
    else:
        status = "failed"
    summary = {
        "status": status,
        "total": len(records),
        "succeeded": [{"job_id": item["job_id"], **output} for item in succeeded for output in item.get("outputs", [])],
        "failed": [{"job_id": item["job_id"], "error": item["error"]} for item in failed],
        "unknown": [{"job_id": item["job_id"], "error": item["error"]} for item in unknown],
        "abandoned": [{"job_id": item["job_id"], "error": item["error"]} for item in abandoned],
    }
    _write_json_atomic(summary_out, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if status == "unknown":
        return 2
    if status == "failed":
        return 1
    return 0


def _generate_batch(args: argparse.Namespace) -> None:
    exit_code = asyncio.run(_run_generate_batch(args))
    if exit_code:
        raise SystemExit(exit_code)


def _generate(args: argparse.Namespace) -> None:
    prompt = _read_prompt(args.prompt, args.prompt_file)
    prompt = _augment_prompt(args, prompt)

    payload = {
        "model": args.model,
        "prompt": prompt,
        "n": args.n,
        "size": args.size,
        "quality": args.quality,
        "background": args.background,
        "output_format": args.output_format,
        "output_compression": args.output_compression,
        "moderation": args.moderation,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    output_format = _normalize_output_format(args.output_format)
    _validate_transparency(args.background, output_format)
    _validate_requested_aspect_ratio(args.size, args.aspect_ratio)
    payload["output_format"] = output_format
    output_paths = _build_output_paths(args.out, output_format, args.n, args.out_dir)
    downscaled = None
    if args.downscale_max_dim is not None:
        downscaled = [str(_derive_downscale_path(p, args.downscale_suffix)) for p in output_paths]

    if args.dry_run:
        _print_request(
            {
                "endpoint": "/v1/images/generations",
                "outputs": [str(p) for p in output_paths],
                "outputs_downscaled": downscaled,
                "expected_aspect_ratio": args.aspect_ratio,
                **payload,
            }
        )
        return

    reserved = _all_output_paths(output_paths, args.downscale_max_dim, args.downscale_suffix)
    with _OutputReservations(reserved, force=args.force):
        print(
            "Calling Image API (generation). This can take up to several minutes; do not restart it after a client wait timeout.",
            file=sys.stderr,
        )
        started = time.time()
        client = _create_client()
        result = client.images.generate(**payload)
        elapsed = time.time() - started
        print(f"Generation completed in {elapsed:.1f}s.", file=sys.stderr)

        images = [item.b64_json for item in result.data]
        _decode_write_and_downscale(
            images,
            output_paths,
            force=args.force,
            downscale_max_dim=args.downscale_max_dim,
            downscale_suffix=args.downscale_suffix,
            output_format=output_format,
            aspect_ratio=args.aspect_ratio,
        )


def _edit(args: argparse.Namespace) -> None:
    prompt = _read_prompt(args.prompt, args.prompt_file)
    prompt = _augment_prompt(args, prompt)

    image_paths = _check_image_paths(args.image)
    mask_path = Path(args.mask) if args.mask else None
    if mask_path:
        if not mask_path.exists():
            _die(f"Mask file not found: {mask_path}")
        if mask_path.suffix.lower() != ".png":
            _warn(f"Mask should be a PNG with an alpha channel: {mask_path}")
        if mask_path.stat().st_size > MAX_IMAGE_BYTES:
            _warn(f"Mask exceeds 50MB limit: {mask_path}")

    payload = {
        "model": args.model,
        "prompt": prompt,
        "n": args.n,
        "size": args.size,
        "quality": args.quality,
        "background": args.background,
        "output_format": args.output_format,
        "output_compression": args.output_compression,
        "input_fidelity": args.input_fidelity,
        "moderation": args.moderation,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    output_format = _normalize_output_format(args.output_format)
    _validate_transparency(args.background, output_format)
    _validate_requested_aspect_ratio(args.size, args.aspect_ratio)
    payload["output_format"] = output_format
    _validate_input_fidelity(args.input_fidelity)
    output_paths = _build_output_paths(args.out, output_format, args.n, args.out_dir)
    downscaled = None
    if args.downscale_max_dim is not None:
        downscaled = [str(_derive_downscale_path(p, args.downscale_suffix)) for p in output_paths]

    if args.dry_run:
        payload_preview = dict(payload)
        payload_preview["image"] = [str(p) for p in image_paths]
        if mask_path:
            payload_preview["mask"] = str(mask_path)
        _print_request(
            {
                "endpoint": "/v1/images/edits",
                "outputs": [str(p) for p in output_paths],
                "outputs_downscaled": downscaled,
                "expected_aspect_ratio": args.aspect_ratio,
                **payload_preview,
            }
        )
        return

    reserved = _all_output_paths(output_paths, args.downscale_max_dim, args.downscale_suffix)
    with _OutputReservations(reserved, force=args.force):
        print(
            f"Calling Image API (edit) with {len(image_paths)} image(s); do not restart it after a client wait timeout.",
            file=sys.stderr,
        )
        started = time.time()
        client = _create_client()

        with _open_files(image_paths) as image_files, _open_mask(mask_path) as mask_file:
            request = dict(payload)
            request["image"] = image_files if len(image_files) > 1 else image_files[0]
            if mask_file is not None:
                request["mask"] = mask_file
            result = client.images.edit(**request)

        elapsed = time.time() - started
        print(f"Edit completed in {elapsed:.1f}s.", file=sys.stderr)
        images = [item.b64_json for item in result.data]
        _decode_write_and_downscale(
            images,
            output_paths,
            force=args.force,
            downscale_max_dim=args.downscale_max_dim,
            downscale_suffix=args.downscale_suffix,
            output_format=output_format,
            aspect_ratio=args.aspect_ratio,
        )


def _open_files(paths: List[Path]):
    return _FileBundle(paths)


def _open_mask(mask_path: Optional[Path]):
    if mask_path is None:
        return _NullContext()
    return _SingleFile(mask_path)


class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


class _SingleFile:
    def __init__(self, path: Path):
        self._path = path
        self._handle = None

    def __enter__(self):
        self._handle = self._path.open("rb")
        return self._handle

    def __exit__(self, exc_type, exc, tb):
        if self._handle:
            try:
                self._handle.close()
            except Exception:
                pass
        return False


class _FileBundle:
    def __init__(self, paths: List[Path]):
        self._paths = paths
        self._handles: List[object] = []

    def __enter__(self):
        self._handles = [p.open("rb") for p in self._paths]
        return self._handles

    def __exit__(self, exc_type, exc, tb):
        for handle in self._handles:
            try:
                handle.close()
            except Exception:
                pass
        return False


def _add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument(
        "--aspect-ratio",
        help="Required output ratio as W:H; requires an explicit exactly matching --size",
    )
    parser.add_argument("--quality", default=DEFAULT_QUALITY)
    parser.add_argument("--background")
    parser.add_argument("--output-format")
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--moderation")
    parser.add_argument("--out", default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--out-dir")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--augment", dest="augment", action="store_true")
    parser.add_argument("--no-augment", dest="augment", action="store_false")
    parser.set_defaults(augment=True)

    # Prompt augmentation hints
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

    # Post-processing (optional): generate an additional downscaled copy for fast web loading.
    parser.add_argument("--downscale-max-dim", type=int)
    parser.add_argument("--downscale-suffix", default=DEFAULT_DOWNSCALE_SUFFIX)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fallback CLI for explicit image generation or editing via GPT Image models"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen_parser = subparsers.add_parser("generate", help="Create a new image")
    _add_shared_args(gen_parser)
    gen_parser.set_defaults(func=_generate)

    batch_parser = subparsers.add_parser(
        "generate-batch",
        help="Generate multiple prompts concurrently (JSONL input)",
    )
    _add_shared_args(batch_parser)
    batch_parser.add_argument("--input", required=True, help="Path to JSONL file (one job per line)")
    batch_parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    batch_parser.add_argument("--max-attempts", type=int, default=1, help="Deprecated; automatic retries are disabled")
    batch_parser.add_argument("--state-dir", help="Directory for per-job state files")
    batch_parser.add_argument("--summary-out", help="Path for the batch result summary JSON")
    batch_parser.add_argument("--fail-fast", action="store_true")
    batch_parser.set_defaults(func=_generate_batch)

    edit_parser = subparsers.add_parser("edit", help="Edit an existing image")
    _add_shared_args(edit_parser)
    edit_parser.add_argument("--image", action="append", required=True)
    edit_parser.add_argument("--mask")
    edit_parser.add_argument("--input-fidelity")
    edit_parser.set_defaults(func=_edit)

    args = parser.parse_args()
    if args.n < 1 or args.n > 10:
        _die("--n must be between 1 and 10")
    if getattr(args, "concurrency", 1) < 1 or getattr(args, "concurrency", 1) > 25:
        _die("--concurrency must be between 1 and 25")
    if getattr(args, "max_attempts", 3) < 1 or getattr(args, "max_attempts", 3) > 10:
        _die("--max-attempts must be between 1 and 10")
    if args.command == "generate-batch" and args.max_attempts != 1:
        _die("Automatic batch retries are disabled; retry a failed job as a new explicit attempt.")
    if args.output_compression is not None and not (0 <= args.output_compression <= 100):
        _die("--output-compression must be between 0 and 100")
    if args.command == "generate-batch" and not args.out_dir:
        _die("generate-batch requires --out-dir")
    if getattr(args, "downscale_max_dim", None) is not None and args.downscale_max_dim < 1:
        _die("--downscale-max-dim must be >= 1")

    _validate_model(args.model)
    _validate_size(args.size, args.model)
    _validate_quality(args.quality)
    _validate_background(args.background)
    _validate_model_specific_options(
        model=args.model,
        background=args.background,
        input_fidelity=getattr(args, "input_fidelity", None),
    )
    _ensure_api_key(args.dry_run)

    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
