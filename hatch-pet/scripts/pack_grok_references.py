#!/usr/bin/env python3
"""Pack a Hatch Pet job's references into at most three Grok input images."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from PIL import Image


MAX_GROK_INPUTS = 3
MAX_GROUP_IMAGES = 4
BOARD_SIZE = (2048, 1536)
GUTTER = 24
MIN_RENDERED_SHORT_EDGE = 96


def _die(message: str) -> None:
    raise SystemExit(f"Error: {message}")


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return value or "job"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_input(run_dir: Path, raw_path: str) -> Path:
    path = (run_dir / raw_path).resolve()
    try:
        path.relative_to(run_dir.resolve())
    except ValueError:
        _die(f"Input escapes the run directory: {raw_path}")
    if not path.is_file():
        _die(f"Input image not found: {raw_path}")
    return path


def _category(item: dict[str, Any]) -> str:
    role = str(item.get("role", "")).lower()
    path = str(item.get("path", "")).lower()
    if "layout guide" in role or "layout-guides/" in path:
        return "layout"
    direction_markers = (
        "direction",
        "cardinal",
        "continuity",
        "gait reference",
        "look-row-9",
        "look-anchors",
        "running-right",
    )
    if any(marker in role or marker in path for marker in direction_markers):
        return "direction"
    return "identity"


def _source_record(run_dir: Path, item: dict[str, Any]) -> dict[str, Any]:
    raw_path = str(item.get("path", ""))
    if not raw_path:
        _die("Every input_images item must contain a path.")
    path = _resolve_input(run_dir, raw_path)
    with Image.open(path) as image:
        width, height = image.size
        image.verify()
    return {
        "path": raw_path.replace("\\", "/"),
        "role": str(item.get("role", "reference image")),
        "category": _category(item),
        "sha256": _sha256(path),
        "width": width,
        "height": height,
    }


def _board_layout(count: int) -> tuple[int, int]:
    columns = 1 if count == 1 else 2
    rows = math.ceil(count / columns)
    return columns, rows


def _make_board(
    run_dir: Path,
    sources: list[dict[str, Any]],
    output_path: Path,
) -> dict[str, Any]:
    if len(sources) > MAX_GROUP_IMAGES:
        _die(
            f"Cannot pack {len(sources)} {sources[0]['category']} references without making them unreadable; "
            f"maximum is {MAX_GROUP_IMAGES}."
        )
    columns, rows = _board_layout(len(sources))
    cell_width = (BOARD_SIZE[0] - GUTTER * (columns + 1)) // columns
    cell_height = (BOARD_SIZE[1] - GUTTER * (rows + 1)) // rows
    board = Image.new("RGBA", BOARD_SIZE, (127, 127, 127, 255))
    placements = []
    for index, source in enumerate(sources):
        source_path = _resolve_input(run_dir, str(source["path"]))
        with Image.open(source_path) as opened:
            image = opened.convert("RGBA")
        scale = min(1.0, cell_width / image.width, cell_height / image.height)
        target = (
            max(1, round(image.width * scale)),
            max(1, round(image.height * scale)),
        )
        if min(target) < MIN_RENDERED_SHORT_EDGE:
            _die(
                f"Packing would make {source['path']} too small to review ({target[0]}x{target[1]})."
            )
        if target != image.size:
            image = image.resize(target, Image.Resampling.LANCZOS)
        column = index % columns
        row = index // columns
        cell_x = GUTTER + column * (cell_width + GUTTER)
        cell_y = GUTTER + row * (cell_height + GUTTER)
        x = cell_x + (cell_width - image.width) // 2
        y = cell_y + (cell_height - image.height) // 2
        board.alpha_composite(image, (x, y))
        placements.append(
            {
                "source_path": source["path"],
                "source_sha256": source["sha256"],
                "box": [x, y, x + image.width, y + image.height],
                "rendered_size": [image.width, image.height],
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    board.save(output_path, format="PNG")
    return {
        "path": output_path.relative_to(run_dir).as_posix(),
        "sha256": _sha256(output_path),
        "width": BOARD_SIZE[0],
        "height": BOARD_SIZE[1],
        "placements": placements,
    }


def pack_job(run_dir: Path, manifest_path: Path, job_id: str, output_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    job_items = manifest.get("jobs") if isinstance(manifest, dict) else manifest
    if not isinstance(job_items, list):
        _die("imagegen-jobs.json must contain a jobs array or be a JSON array.")
    jobs = [job for job in job_items if isinstance(job, dict) and job.get("id") == job_id]
    if len(jobs) != 1:
        _die(f"Expected exactly one job with id {job_id!r}; found {len(jobs)}.")
    raw_inputs = jobs[0].get("input_images", [])
    if not isinstance(raw_inputs, list):
        _die(f"Job {job_id!r} input_images must be an array.")
    sources = [_source_record(run_dir, item) for item in raw_inputs]
    result: dict[str, Any] = {
        "job_id": job_id,
        "provider": "grok",
        "max_inputs": MAX_GROK_INPUTS,
        "original_inputs": sources,
        "packed_inputs": [],
        "boards": [],
    }
    if len(sources) <= MAX_GROK_INPUTS:
        result["packed_inputs"] = [
            {"path": source["path"], "role": source["role"], "source_images": [source["path"]]}
            for source in sources
        ]
    else:
        groups = {category: [item for item in sources if item["category"] == category] for category in ("layout", "identity", "direction")}
        if len(groups["layout"]) > 1:
            _die(f"Job {job_id!r} contains more than one layout guide.")
        if groups["layout"]:
            source = groups["layout"][0]
            result["packed_inputs"].append(
                {"path": source["path"], "role": source["role"], "source_images": [source["path"]]}
            )
        packed_dir = run_dir / "references" / "grok-packed" / _slug(job_id)
        for category in ("identity", "direction"):
            group = groups[category]
            if not group:
                continue
            if len(group) == 1:
                source = group[0]
                result["packed_inputs"].append(
                    {"path": source["path"], "role": source["role"], "source_images": [source["path"]]}
                )
                continue
            board = _make_board(run_dir, group, packed_dir / f"{category}-board.png")
            result["boards"].append({"category": category, **board})
            result["packed_inputs"].append(
                {
                    "path": board["path"],
                    "role": f"deterministic {category} reference board; preserve each source and do not copy gutters",
                    "source_images": [source["path"] for source in group],
                }
            )
    if len(result["packed_inputs"]) > MAX_GROK_INPUTS:
        _die(
            f"Packing produced {len(result['packed_inputs'])} inputs; Grok accepts at most {MAX_GROK_INPUTS}."
        )
    result["ok"] = True
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--manifest", default="imagegen-jobs.json")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        _die(f"Run directory not found: {run_dir}")
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = run_dir / manifest_path
    output_path = Path(args.json_out) if args.json_out else run_dir / "references" / "grok-packed" / _slug(args.job_id) / "inputs.json"
    if not output_path.is_absolute():
        output_path = run_dir / output_path
    result = pack_job(run_dir, manifest_path, args.job_id, output_path)
    print(output_path)
    print(json.dumps({"job_id": result["job_id"], "input_count": len(result["packed_inputs"]), "ok": True}))


if __name__ == "__main__":
    main()
