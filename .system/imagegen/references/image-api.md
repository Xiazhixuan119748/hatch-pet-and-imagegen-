# Image API quick reference

Use this file for configured `imagegen.env` Provider mode, when the user explicitly asks for CLI/API/model controls, or after the user explicitly confirms the `gpt-image-1.5` true-transparency fallback path.

These parameters describe the Provider CLIs. Do not assume they are normal arguments on the built-in `image_gen` tool.

## Scope
- This fallback CLI is intended for GPT Image models (`gpt-image-2`, `gpt-image-1.5`, `gpt-image-1`, and `gpt-image-1-mini`).
- The built-in `image_gen` tool and the fallback CLI do not expose the same controls.

## Model summary

| Model | Quality | Input fidelity | Resolutions | Recommended use |
| --- | --- | --- | --- | --- |
| `gpt-image-2` | `low`, `medium`, `high`, `auto` | Always high fidelity for image inputs; do not set `input_fidelity` | `auto` or flexible sizes that satisfy the constraints below | Default for new CLI/API workflows: high-quality generation and editing, text-heavy images, photorealism, compositing, identity-sensitive edits, and workflows where fewer retries matter |
| `gpt-image-1.5` | `low`, `medium`, `high`, `auto` | `low`, `high` | `1024x1024`, `1024x1536`, `1536x1024`, `auto` | True transparent-background fallback and backward-compatible workflows |
| `gpt-image-1` | `low`, `medium`, `high`, `auto` | `low`, `high` | `1024x1024`, `1024x1536`, `1536x1024`, `auto` | Legacy compatibility |
| `gpt-image-1-mini` | `low`, `medium`, `high`, `auto` | `low`, `high` | `1024x1024`, `1024x1536`, `1536x1024`, `auto` | Cost-sensitive draft batches and lower-stakes previews |

## gpt-image-2 sizes

`gpt-image-2` accepts `auto` or any `WIDTHxHEIGHT` size that satisfies all constraints:

- Maximum edge length must be less than or equal to `3840px`.
- Both edges must be multiples of `16px`.
- Long edge to short edge ratio must not exceed `3:1`.
- Total pixels must be at least `655,360` and no more than `8,294,400`.

Popular sizes:

| Label | Size | Notes |
| --- | --- | --- |
| Square | `1024x1024` | Typical fast default |
| Landscape | `1536x1024` | Standard landscape |
| Portrait | `1024x1536` | Standard portrait |
| 2K square | `2048x2048` | Larger square output |
| 2K landscape | `2048x1152` | Widescreen output |
| 4K landscape | `3840x2160` | Widescreen 4K output |
| 4K portrait | `2160x3840` | Vertical 4K output |
| Auto | `auto` | Default size |

Square images are typically fastest to generate. For 4K-style output, use `3840x2160` or `2160x3840`.

## Endpoints
- Generate: `POST /v1/images/generations` (`client.images.generate(...)`)
- Edit: `POST /v1/images/edits` (`client.images.edit(...)`)

## Core parameters for GPT Image models
- `prompt`: text prompt
- `model`: image model
- `n`: number of images (1-10)
- `size`: `auto` by default for `gpt-image-2`; flexible `WIDTHxHEIGHT` sizes are allowed only for `gpt-image-2`; older GPT Image models use `1024x1024`, `1536x1024`, `1024x1536`, or `auto`
- `quality`: `low`, `medium`, `high`, or `auto`
- `background`: output transparency behavior (`transparent`, `opaque`, or `auto`) for generated output; this is not the same thing as the prompt's visual scene/backdrop
- `output_format`: `png` (default), `jpeg`, `webp`
- `output_compression`: 0-100 (jpeg/webp only)
- `moderation`: `auto` (default) or `low`

## Edit-specific parameters
- `image`: one or more input images. For GPT Image models, you can provide up to 16 images.
- `mask`: optional mask image
- `input_fidelity`: `low` or `high` only for models that support it; do not set this for `gpt-image-2`

Model-specific note for `input_fidelity`:
- `gpt-image-2` always uses high fidelity for image inputs and does not support setting `input_fidelity`.
- `gpt-image-1` and `gpt-image-1-mini` preserve all input images, but the first image gets richer textures and finer details.
- `gpt-image-1.5` preserves the first 5 input images with higher fidelity.

## Transparent backgrounds

`gpt-image-2` does not currently support the Image API `background=transparent` parameter. The skill's default transparent-image path uses the selected configured Provider or built-in tool with a flat chroma-key background, followed by local alpha extraction with `python "${CODEX_HOME:-$HOME/.codex}/skills/.system/imagegen/scripts/remove_chroma_key.py"`.

Use CLI `gpt-image-1.5` with `background=transparent` and a transparent-capable output format such as `png` or `webp` only after the user explicitly confirms that fallback, unless they already requested `gpt-image-1.5`, `scripts/image_gen.py`, or CLI fallback. If the user asks for true/native transparency, the subject is too complex for clean chroma-key removal, or local background removal fails validation, explain the tradeoff and ask before switching.

## Output
- `data[]` list with `b64_json` per image
- The bundled `scripts/image_gen.py` CLI decodes `b64_json` and writes output files for you.

## Limits and notes
- Input images and masks must be under 50MB.
- Use the edits endpoint when the user requests changes to an existing image.
- Masking is prompt-guided; exact shapes are not guaranteed.
- Large sizes and high quality increase latency and cost.
- Use `quality=low` for fast drafts, thumbnails, and quick iterations. Use `medium` or `high` for final assets, dense text, diagrams, identity-sensitive edits, or high-resolution outputs.
- High `input_fidelity` can materially increase input token usage on models that support it.
- If a request fails because a specific option is unsupported by the selected GPT Image model, retry manually without that option only when the option is not required by the user. If true transparent CLI output is required, ask before switching to `gpt-image-1.5` instead of dropping `background=transparent`, unless the user already explicitly chose that fallback.

## Important boundary
- `quality`, `input_fidelity`, explicit masks, `background`, `output_format`, and related parameters are fallback-only execution controls.
- Do not assume they are built-in `image_gen` tool arguments.

## Gemini Provider

Configured Gemini mode supports both Gemini image API formats, not the OpenAI Images endpoints. `GEMINI_API_MODE=generate-content` (the default) uses `client.models.generate_content(...)`; `GEMINI_API_MODE=interactions` uses `client.interactions.create(...)`. The mode is never changed automatically after a failed request. Recommended model: `gemini-3.1-flash-image`. Also accepted are `gemini-3.1-flash-lite-image`, `gemini-3-pro-image`, and `gemini-2.5-flash-image`. For third-party providers, the corresponding `nano-banana-2-lite`, `nano-banana-2`, `nano-banana-pro`, and `nano-banana` model IDs are also accepted and passed through unchanged. Only Gemini 3.1 Flash Lite Image and `nano-banana-2-lite` are locally restricted to `1K`, matching Google's explicit limit. Gemini 3.1 Flash Image and `nano-banana-2` additionally support the documented `0.5K` tier. No other model-specific resolution restriction is inferred locally.

The CLI maps pixel sizes to Gemini's declared aspect ratio and image-size controls:

| CLI size | Gemini response format |
| --- | --- |
| `1024x1024` | `1:1` + `1K` |
| `2048x2048` | `1:1` + `2K` |
| `4096x4096` | `1:1` + `4K` |
| `1920x1080` | `16:9` + `2K` |
| `3840x2160` | `16:9` + `4K` |
| `1536x1024` | `3:2` + `2K` |
| `1024x1536` | `2:3` + `2K` |
| `auto` | Model defaults |

Supported ratios are `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, and `21:9`. Unsupported ratios such as `8:1` fail explicitly.

Gemini edit input is an ordered list containing the text instruction followed by each base64 image block. Generate Content uses `contents` with `inline_data` and reads images from `candidates[].content.parts[].inline_data`; Interactions uses `input` image blocks and reads images from every `model_output` step. It has no mask equivalence in this implementation. A response without an image block fails. The response MIME type is inspected, and Pillow performs any requested PNG/JPEG/WebP conversion, compression, or downscaling locally.

## Grok Imagine JSON Provider

Configured Grok mode defaults to `grok-imagine-image-quality` and uses the JSON endpoints `POST /v1/images/generations` and `POST /v1/images/edits`. It accepts the official `grok-imagine-image` and `grok-imagine-image-quality` models plus their published aliases: `grok-imagine-image-2026-03-02`, `grok-imagine-image-quality-20260403`, `grok-imagine-image-quality-latest`, and `grok-imagine-image-pro`. xAI documents both primary models under the same generation, single-image edit, and multi-image edit request/response contracts; this Provider therefore changes only the `model` value between them. Generation is compatible with OpenAI SDK `images.generate()` when its base URL is `https://api.x.ai/v1`; editing is not compatible with OpenAI SDK `images.edit()` because that SDK sends multipart while xAI requires JSON.

Grok supports `1k` and `2k` plus these aspect ratios: `1:1`, `3:4`, `4:3`, `9:16`, `16:9`, `2:3`, `3:2`, `9:19.5`, `19.5:9`, `9:20`, `20:9`, `1:2`, `2:1`, and `auto`. The CLI maps exact pixel ratios and fails rather than rounding unsupported values.

Generation sends `model`, `prompt`, `n`, `aspect_ratio`, `resolution`, and `response_format=b64_json`. Same-prompt `n` is one request. Edit accepts local paths converted to Base64 data URIs, public image URLs, or xAI Files API references using `file_id:<id>`. One input is sent as `image`; two to three ordered inputs are sent as `images`; more than three fail. Single-image `size=auto` omits the aspect ratio so the source ratio is preserved. The `files` CLI subcommands expose upload, list, metadata, and delete operations through the xAI Files API.

Responses are read from every `data[]` item. The Provider prefers `b64_json`, downloads a temporary `url` when necessary, validates source bytes with Pillow, and performs PNG/JPEG/WebP conversion, compression, and downscaling locally. `mask` and native transparent output are unsupported.

## Agnes Image Provider

Configured Agnes mode supports `agnes-image-2.0-flash` and the preferred `agnes-image-2.1-flash` at `https://apihub.agnes-ai.com/v1`. Every workflow posts JSON to `/images/generations`, including edits. This differs from OpenAI edit: ordered input Data URIs go in `extra_body.image`, and edit output selection goes in `extra_body.response_format`. Do not use `/images/edits`, multipart files, top-level `response_format`, or `tags`.

Text generation sends `return_base64: true`. Same-prompt `n` creates independent requests because the Agnes image docs do not define an `n` field. Responses use OpenAI-style `created` plus `data[].url`, `data[].b64_json`, and `data[].revised_prompt`.

Image 2.1 accepts `1K`, `2K`, `3K`, or `4K` with `1:1`, `3:4`, `4:3`, `16:9`, `9:16`, `2:3`, `3:2`, or `21:9`. The CLI validates the declared mathematical ratio, chooses the smallest tier that contains the requested dimensions, and reports the model's documented output size. Image 2.0 keeps exact `WIDTHxHEIGHT` values and maps `auto` to `1024x1024`.

The Provider enforces a local 16-reference safety limit because the official image pages document an array but no service maximum. This is not a claim about Agnes server capacity. `mask` and native transparent output are unsupported; local format conversion and chroma-key removal remain unchanged.
