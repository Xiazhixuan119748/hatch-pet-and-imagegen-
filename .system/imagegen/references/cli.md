# CLI reference

Read this file for configured `imagegen.env` Provider mode or when the user explicitly asks to use `scripts/image_gen.py` / CLI / API / model controls. OpenAI true-transparency fallback still requires the user's explicit confirmation unless they already requested that path.

`generate-batch` is a CLI subcommand, not a top-level mode of the skill.
The word `batch` in a user request is not CLI opt-in by itself.

## What this CLI does
- `generate`: generate a new image from a prompt
- `edit`: edit one or more existing images
- `generate-batch`: run many generation jobs from a JSONL file after the user explicitly chooses CLI/API/model controls

Real API calls require network access and the selected Provider's API key. `--dry-run` does not.

For the user-authorized Codex configuration path, invoke `scripts/image_gen_with_codex_env.py` with the same subcommand and arguments. It reads `IMAGE_PROVIDER` from `${CODEX_HOME:-$HOME/.codex}/imagegen.env`, never prints credential values, and delegates to the matching OpenAI, Gemini, Grok, or Agnes CLI. `$hatch-pet` visual jobs must use this wrapper.

## Configured Provider mode

`IMAGE_PROVIDER` accepts `openai`, `gemini`, `grok`, or `agnes` and defaults to `openai`. The wrapper passes only the selected Provider's managed variables to the child process.

OpenAI configuration:

```dotenv
IMAGE_PROVIDER=openai
OPENAI_API_KEY=replace-with-your-api-key
# Optional for the official endpoint; set this for an OpenAI-compatible relay.
# OPENAI_BASE_URL=https://your-image-api.example/v1
OPENAI_IMAGE_MODEL=gpt-image-2
```

Gemini configuration:

```dotenv
IMAGE_PROVIDER=gemini
GEMINI_API_KEY=replace-with-your-gemini-api-key
GEMINI_IMAGE_MODEL=gemini-3.1-flash-image
GEMINI_API_MODE=generate-content
# GEMINI_BASE_URL=https://your-gemini-relay.example
```

Grok configuration:

```dotenv
IMAGE_PROVIDER=grok
XAI_API_KEY=replace-with-your-xai-api-key
XAI_IMAGE_MODEL=grok-imagine-image-quality
# XAI_BASE_URL=https://api.x.ai/v1
```

Agnes configuration:

```dotenv
IMAGE_PROVIDER=agnes
AGNES_API_KEY=replace-with-your-agnes-api-key
AGNES_IMAGE_MODEL=agnes-image-2.1-flash
# AGNES_BASE_URL=https://apihub.agnes-ai.com/v1
```

Install configured-mode dependencies with `py -m pip install --upgrade openai google-genai httpx pillow`. Dry-run does not call any API.

Gemini keeps the `generate`, `edit`, and `generate-batch` command names. `GEMINI_API_MODE` accepts `generate-content` or `interactions` and defaults to `generate-content` when omitted. It selects the request and response schema before the request; failures never switch API formats automatically. Provider-specific behavior:

- Official Google IDs and common third-party IDs are both accepted: `gemini-3.1-flash-lite-image` / `nano-banana-2-lite`, `gemini-3.1-flash-image` / `nano-banana-2`, `gemini-3-pro-image` / `nano-banana-pro`, and `gemini-2.5-flash-image` / `nano-banana`. Alias values are passed to the configured provider unchanged.
- Local model-specific restrictions are enforced only when Google documents them explicitly: 3.1 Flash Lite and `nano-banana-2-lite` are limited to `1K`; 3.1 Flash Image and `nano-banana-2` may also select `0.5K`. Other aliases are not assigned inferred limits.
- `--n` creates N independent requests; every returned image block is saved.
- Repeated `--image` values retain command-line order in an edit request.
- `--size` maps to a supported aspect ratio and `1K`, `2K`, or `4K`. Unsupported ratios fail instead of rounding; Lite models accept only `1K`.
- `--mask` and `--background transparent` fail explicitly. Use a flat chroma-key background plus local removal.
- Explicit `--quality`, `--input-fidelity`, and `--moderation` values warn because they are not mapped. `--output-compression`, WebP conversion, and downscaling are local Pillow operations.
- `generate-batch` remains immediate concurrent execution, not Google Batch API.

Configured Gemini dry-run:

```bash
python "$CODEX_HOME/skills/.system/imagegen/scripts/image_gen_with_codex_env.py" generate \
  --prompt "Test" \
  --size 1024x1024 \
  --out output/imagegen/gemini-test.png \
  --dry-run
```

Grok uses xAI JSON REST for both generation and editing. Its generation endpoint is OpenAI SDK compatible, but OpenAI SDK `images.edit()` is not: xAI edits require JSON instead of multipart. Provider behavior:

- Supported model IDs are `grok-imagine-image`, `grok-imagine-image-2026-03-02`, `grok-imagine-image-quality`, `grok-imagine-image-quality-20260403`, `grok-imagine-image-quality-latest`, and `grok-imagine-image-pro`. The default remains `grok-imagine-image-quality`.
- Same-prompt `--n` maps to one request and supports 1-10 images.
- Edit accepts one to three ordered `--image` values. Each value may be a local image path, an `http(s)` image URL, or `file_id:<id>` from the xAI Files API. Multiple inputs are referenced as `<IMAGE_0>`, `<IMAGE_1>`, and `<IMAGE_2>` in the prompt.
- `--size` maps to an official aspect ratio plus `1k` or `2k`; inputs above 2K warn and map down to `2k`.
- The Provider requests `b64_json` by default and can also consume temporary URL responses.
- `--mask` and `--background transparent` fail explicitly. Quality, input fidelity, and moderation controls are not mapped.
- `generate-batch` uses immediate concurrent requests, not xAI Batch API.

Grok Files API commands use the same `XAI_API_KEY`:

```bash
python "$CODEX_HOME/skills/.system/imagegen/scripts/image_gen_with_codex_env.py" files upload --file reference.png
python "$CODEX_HOME/skills/.system/imagegen/scripts/image_gen_with_codex_env.py" files list
python "$CODEX_HOME/skills/.system/imagegen/scripts/image_gen_with_codex_env.py" files get file-...
python "$CODEX_HOME/skills/.system/imagegen/scripts/image_gen_with_codex_env.py" files delete file-...
```

Configured Grok dry-run:

```bash
python "$CODEX_HOME/skills/.system/imagegen/scripts/image_gen_with_codex_env.py" generate \
  --prompt "Test" \
  --size 1920x1080 \
  --out output/imagegen/grok-test.png \
  --dry-run
```

Agnes uses OpenAI-style authentication and `data[]` responses, but its image request body is Provider-specific:

- All generation and editing requests post JSON to `/images/generations`.
- Edit images are ordered Data URIs under `extra_body.image`; `extra_body.response_format` selects `b64_json`.
- Text generation requests Base64 with `return_base64: true`.
- `agnes-image-2.1-flash` maps exact supported ratios to `1K`-`4K` tiers; `agnes-image-2.0-flash` keeps exact pixel sizes.
- Same-prompt `--n` creates independent requests because Agnes image docs do not define `n`.
- Up to 16 edit references are accepted as a local safety policy, not a documented service limit.
- `--mask` and native transparent output are unsupported.

Configured Agnes dry-run:

```bash
python "$CODEX_HOME/skills/.system/imagegen/scripts/image_gen_with_codex_env.py" generate \
  --prompt "Test" \
  --size 1920x1080 \
  --out output/imagegen/agnes-test.png \
  --dry-run
```

## Quick start (works from any repo)
Set a stable path to the skill CLI (default `CODEX_HOME` is `~/.codex`):

```
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
export IMAGE_GEN="$CODEX_HOME/skills/.system/imagegen/scripts/image_gen.py"
```

Install dependencies into that environment with its package manager. In uv-managed environments, `uv pip install ...` remains the preferred path.

## Quick start

Dry-run (no API call; no network required; does not require the `openai` package):

```bash
python "$IMAGE_GEN" generate \
  --prompt "Test" \
  --out output/imagegen/test.png \
  --dry-run
```

Notes:
- One-off dry-runs print the API payload and the computed output path(s).
- Repo-local finals should live under `output/imagegen/`.

Generate (requires `OPENAI_API_KEY` + network):

```bash
python "$IMAGE_GEN" generate \
  --prompt "A cozy alpine cabin at dawn" \
  --size 1024x1024 \
  --out output/imagegen/alpine-cabin.png
```

Edit:

```bash
python "$IMAGE_GEN" edit \
  --image input.png \
  --prompt "Replace only the background with a warm sunset" \
  --out output/imagegen/sunset-edit.png
```

## Guardrails
- Use the bundled CLI directly (`python "$IMAGE_GEN" ...`) after activating the correct environment.
- Do **not** create one-off runners (for example `gen_images.py`) unless the user explicitly asks for a custom wrapper.
- **Never modify** `scripts/image_gen.py`. If something is missing, ask the user before doing anything else.
- Do not silently downgrade from CLI `gpt-image-2` or built-in `image_gen` to CLI `gpt-image-1.5`; ask first unless the user already explicitly requested `gpt-image-1.5`, `scripts/image_gen.py`, or CLI fallback.
- For a requested ratio, use `--aspect-ratio W:H` with an explicit exactly matching `--size`. The returned image is validated with a maximum 1% pixel-ratio tolerance.
- Keep long-running commands in their original execution session. After a client/tool wait timeout, inspect the output and `.imagegen.lock` before any explicit retry; never automatically rerun an unknown request.

## Defaults
- Model: `gpt-image-2`
- Supported model family for this CLI: GPT Image models (`gpt-image-*`)
- Size: `auto`
- Quality: `medium`
- Output format: `png`
- Default one-off output path: `output/imagegen/output.png`
- Background: unspecified unless `--background` is set

## gpt-image-2 size and model guidance

`gpt-image-2` is the default model for new CLI fallback work.

- Use `--quality low` for fast drafts, thumbnails, and quick iterations.
- Use `--quality medium`, `--quality high`, or `--quality auto` for final assets, dense text, diagrams, identity-sensitive edits, and high-resolution outputs.
- Square images are typically fastest. Use `--size 1024x1024` for quick square drafts.
- If the user asks for 4K-style output, use `--size 3840x2160` for landscape or `--size 2160x3840` for portrait.
- For a 3:4 request, use a true 3:4 size such as `--size 1536x2048 --aspect-ratio 3:4`; `1024x1536` is 2:3 and must not be used as a substitute.
- Do not pass `--input-fidelity` with `gpt-image-2`; this model always uses high fidelity for image inputs.
- Do not use `--background transparent` with `gpt-image-2`; the default transparent-image workflow uses the selected configured Provider or built-in tool on a flat chroma-key background plus local removal. Use `gpt-image-1.5` only after the user explicitly confirms the true-transparent CLI fallback, unless they already requested `gpt-image-1.5`, `scripts/image_gen.py`, or CLI fallback.

Popular `gpt-image-2` sizes:
- `1024x1024`
- `1536x1024`
- `1024x1536`
- `2048x2048`
- `2048x1152`
- `3840x2160`
- `2160x3840`
- `auto`

`gpt-image-2` size constraints:
- max edge `<= 3840px`
- both edges multiples of `16px`
- long edge to short edge ratio `<= 3:1`
- total pixels between `655,360` and `8,294,400`
- outputs above `2560x1440` total pixels are experimental

Fast draft:

```bash
python "$IMAGE_GEN" generate \
  --prompt "A product thumbnail of a matte ceramic mug on a stone surface" \
  --quality low \
  --size 1024x1024 \
  --out output/imagegen/mug-draft.png
```

Final 2K landscape:

```bash
python "$IMAGE_GEN" generate \
  --prompt "A polished landing-page hero image of a matte ceramic mug on a stone surface" \
  --quality high \
  --size 2048x1152 \
  --out output/imagegen/mug-hero.png
```

4K landscape:

```bash
python "$IMAGE_GEN" generate \
  --prompt "A detailed architectural visualization at golden hour" \
  --size 3840x2160 \
  --quality high \
  --out output/imagegen/architecture-4k.png
```

True transparent fallback request:

Ask for confirmation before using this command unless the user already explicitly requested `gpt-image-1.5`, `scripts/image_gen.py`, or CLI fallback.

```bash
python "$IMAGE_GEN" generate \
  --model gpt-image-1.5 \
  --prompt "A clean product cutout on a transparent background" \
  --background transparent \
  --output-format png \
  --out output/imagegen/product-cutout.png
```

When using this path, explain briefly that the selected default path normally uses chroma-key removal, but this request needs true model-native transparency. `gpt-image-2` does not support `background=transparent`, so `gpt-image-1.5` is required for this confirmed fallback.

## Quality, input fidelity, and masks (CLI fallback only)
These are explicit CLI controls. They are not built-in `image_gen` tool arguments.

- `--quality` works for `generate`, `edit`, and `generate-batch`: `low|medium|high|auto`
- `--input-fidelity` is **edit-only** and validated as `low|high`; it is not supported for `gpt-image-2`
- `--mask` is **edit-only**

Example:

```bash
python "$IMAGE_GEN" edit \
  --model gpt-image-1.5 \
  --image input.png \
  --prompt "Change only the background" \
  --quality high \
  --input-fidelity high \
  --out output/imagegen/background-edit.png
```

Mask notes:
- For multi-image edits, pass repeated `--image` flags. Their order is meaningful, so describe each image by index and role in the prompt.
- The CLI accepts a single `--mask`.
- Image and mask must be the same size and format and each under 50MB.
- Masks must include an alpha channel.
- If multiple input images are provided, the mask applies to the first image.
- Masking is prompt-guided; do not promise exact pixel-perfect mask boundaries.
- Use a PNG mask when possible; the script treats mask handling as best-effort and does not perform full preflight validation beyond file checks/warnings.
- In the edit prompt, repeat invariants (`change only the background; keep the subject unchanged`) to reduce drift.

## Output handling
- Use `tmp/imagegen/` for temporary JSONL inputs or scratch files.
- Use `output/imagegen/` for final outputs.
- Reruns fail if a target file already exists unless you pass `--force`.
- `--out-dir` changes one-off naming to `image_1.<ext>`, `image_2.<ext>`, and so on.
- Downscaled copies use the default suffix `-web` unless you override it.

## Common recipes

Generate with augmentation fields:

```bash
python "$IMAGE_GEN" generate \
  --prompt "A minimal hero image of a ceramic coffee mug" \
  --use-case "product-mockup" \
  --style "clean product photography" \
  --composition "wide product shot with usable negative space for page copy" \
  --constraints "no logos, no text" \
  --out output/imagegen/mug-hero.png
```

Generate + also write a downscaled copy for fast web loading:

```bash
python "$IMAGE_GEN" generate \
  --prompt "A cozy alpine cabin at dawn" \
  --size 1024x1024 \
  --downscale-max-dim 1024 \
  --out output/imagegen/alpine-cabin.png
```

Generate multiple prompts concurrently (async batch):

```bash
mkdir -p tmp/imagegen output/imagegen/batch
cat > tmp/imagegen/prompts.jsonl << 'EOF'
{"prompt":"Cavernous hangar interior with a compact shuttle parked near the center","use_case":"stylized-concept","composition":"wide-angle, low-angle","lighting":"volumetric light rays through drifting fog","constraints":"no logos or trademarks; no watermark","size":"1536x1024"}
{"prompt":"Gray wolf in profile in a snowy forest","use_case":"photorealistic-natural","composition":"eye-level","constraints":"no logos or trademarks; no watermark","size":"1024x1024"}
EOF

python "$IMAGE_GEN" generate-batch \
  --input tmp/imagegen/prompts.jsonl \
  --out-dir output/imagegen/batch \
  --concurrency 5

rm -f tmp/imagegen/prompts.jsonl
```

Notes:
- `generate-batch` requires `--out-dir`.
- generate-batch requires --out-dir.
- Use `--concurrency` to control parallelism (default `5`).
- Each JSONL line is an independent job. Jobs may run concurrently, but each job sends exactly one API request and waits for that request's result.
- Automatic retries are disabled, including for timeouts, connection failures, and HTTP 429. A retry must be an explicit new attempt after a clear per-job `failed` result; never retry an `unknown` request automatically.
- Each job writes a state file under `--state-dir` (default `<out-dir>/.imagegen-state`). Use `--summary-out` to choose the batch summary path. States are `pending`, `running`, `succeeded`, `failed`, `unknown`, or `abandoned`.
- The summary is written even when some jobs fail. Always deliver all entries in `succeeded` first, then report `failed`, `unknown`, and `abandoned` entries. Partial success exits `0`; all explicit failures exit `1`; no success with an unknown job exits `2`.
- Keep the original execution session alive for long API calls. Do not loop with `Start-Sleep` and resubmit when a tool wait expires. Check the output and generation lock first; an unconfirmed request is `unknown` until its state is known.
- Per-job overrides are supported in JSONL (for example `size`, `quality`, `background`, `output_format`, `output_compression`, `moderation`, `n`, `model`, `out`, and prompt-augmentation fields).
- `--n` generates multiple variants for a single prompt; `generate-batch` is for many different prompts.
- In batch mode, per-job `out` is treated as a filename under `--out-dir`.
- For many requested deliverable assets, provide one prompt/job per distinct asset and use semantic filenames when possible.

## CLI notes
- Supported sizes depend on the model. `gpt-image-2` supports flexible constrained sizes; older GPT Image models support `1024x1024`, `1536x1024`, `1024x1536`, or `auto`.
- True transparent CLI outputs require `output_format` to be `png` or `webp` and are not supported by `gpt-image-2`.
- `--prompt-file`, `--output-compression`, `--moderation`, `--max-attempts`, `--fail-fast`, `--force`, and `--no-augment` are supported.
- This CLI is intended for GPT Image models. Do not assume older non-GPT image-model behavior applies here.

## See also
- API parameter quick reference for fallback CLI mode: `references/image-api.md`
- Prompt examples shared across both top-level modes: `references/sample-prompts.md`
- Network/sandbox notes for fallback CLI mode: `references/codex-network.md`
- Built-in-first transparent image workflow: `SKILL.md` and `$CODEX_HOME/skills/.system/imagegen/scripts/remove_chroma_key.py`
