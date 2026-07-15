#!/usr/bin/env python3
"""Run the bundled image generation CLI with the Codex imagegen.env file."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ALLOWED_KEYS = {
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_IMAGE_MODEL",
}


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in ALLOWED_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def main() -> int:
    codex_home = _codex_home()
    env_path = codex_home / "imagegen.env"
    if not env_path.is_file():
        print(f"Error: Codex environment file not found: {env_path}", file=sys.stderr)
        return 2

    file_values = _parse_env(env_path)
    missing = [key for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL") if not file_values.get(key)]
    if missing:
        print(f"Error: Missing required setting(s) in {env_path}: {', '.join(missing)}", file=sys.stderr)
        return 2

    child_env = os.environ.copy()
    for key, value in file_values.items():
        if value:
            child_env[key] = value

    args = sys.argv[1:]
    configured_model = child_env.get("OPENAI_IMAGE_MODEL", "").strip()
    if configured_model and "--model" not in args:
        if not configured_model.startswith("gpt-image-"):
            print(
                "Error: OPENAI_IMAGE_MODEL must name a gpt-image-* model for imagegen.",
                file=sys.stderr,
            )
            return 2
        args = [*args, "--model", configured_model]

    cli = Path(__file__).with_name("image_gen.py")
    print(f"Using Codex image configuration from {env_path}.", file=sys.stderr)
    return subprocess.run([sys.executable, str(cli), *args], env=child_env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
