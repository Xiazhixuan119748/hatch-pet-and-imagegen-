#!/usr/bin/env python3
"""Run the bundled image generation CLI with the Codex imagegen.env file."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


OPENAI_KEYS = {
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_IMAGE_MODEL",
}
GEMINI_KEYS = {
    "GEMINI_API_KEY",
    "GEMINI_API_MODE",
    "GEMINI_BASE_URL",
    "GEMINI_IMAGE_MODEL",
}
GROK_KEYS = {
    "XAI_API_KEY",
    "XAI_BASE_URL",
    "XAI_IMAGE_MODEL",
}
AGNES_KEYS = {
    "AGNES_API_KEY",
    "AGNES_BASE_URL",
    "AGNES_IMAGE_MODEL",
}
ALLOWED_KEYS = {
    "IMAGE_PROVIDER",
    *OPENAI_KEYS,
    *GEMINI_KEYS,
    *GROK_KEYS,
    *AGNES_KEYS,
}
SUPPORTED_PROVIDERS = {"openai", "gemini", "grok", "agnes"}


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
    provider = file_values.get("IMAGE_PROVIDER", "openai").strip().lower() or "openai"
    if provider not in SUPPORTED_PROVIDERS:
        print(
            f"Error: IMAGE_PROVIDER must be one of: {', '.join(sorted(SUPPORTED_PROVIDERS))}.",
            file=sys.stderr,
        )
        return 2

    required = {
        "openai": ("OPENAI_API_KEY",),
        "gemini": ("GEMINI_API_KEY",),
        "grok": ("XAI_API_KEY",),
        "agnes": ("AGNES_API_KEY",),
    }[provider]
    missing = [key for key in required if not file_values.get(key)]
    if missing:
        print(f"Error: Missing required setting(s) in {env_path}: {', '.join(missing)}", file=sys.stderr)
        return 2

    child_env = os.environ.copy()
    for key in OPENAI_KEYS | GEMINI_KEYS | GROK_KEYS | AGNES_KEYS | {"IMAGE_PROVIDER"}:
        child_env.pop(key, None)
    provider_keys = {
        "openai": OPENAI_KEYS,
        "gemini": GEMINI_KEYS,
        "grok": GROK_KEYS,
        "agnes": AGNES_KEYS,
    }[provider]
    for key in provider_keys:
        value = file_values.get(key, "")
        if value:
            child_env[key] = value
    child_env["IMAGE_PROVIDER"] = provider

    args = sys.argv[1:]
    model_key = {
        "openai": "OPENAI_IMAGE_MODEL",
        "gemini": "GEMINI_IMAGE_MODEL",
        "grok": "XAI_IMAGE_MODEL",
        "agnes": "AGNES_IMAGE_MODEL",
    }[provider]
    configured_model = child_env.get(model_key, "").strip()
    has_explicit_model = any(arg == "--model" or arg.startswith("--model=") for arg in args)
    image_commands = {"generate", "edit", "generate-batch"}
    uses_image_model = bool(args) and args[0] in image_commands
    if configured_model and uses_image_model and not has_explicit_model:
        expected_prefixes = {
            "openai": ("gpt-image-",),
            "gemini": ("gemini-", "nano-banana"),
            "grok": ("grok-imagine-image",),
            "agnes": ("agnes-image-",),
        }[provider]
        if not configured_model.startswith(expected_prefixes):
            print(
                f"Error: {model_key} must start with one of "
                f"{', '.join(expected_prefixes)} for imagegen.",
                file=sys.stderr,
            )
            return 2
        args = [*args, "--model", configured_model]

    cli_name = {
        "openai": "image_gen.py",
        "gemini": "gemini_image_gen.py",
        "grok": "grok_image_gen.py",
        "agnes": "agnes_image_gen.py",
    }[provider]
    cli = Path(__file__).with_name(cli_name)
    print(
        f"Using {provider} image provider from Codex image configuration at {env_path}.",
        file=sys.stderr,
    )
    return subprocess.run([sys.executable, str(cli), *args], env=child_env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
