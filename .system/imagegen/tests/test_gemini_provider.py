import argparse
import base64
import importlib.util
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image


SKILL_DIR = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gemini = load_module("gemini_image_gen", SKILL_DIR / "scripts" / "gemini_image_gen.py")
wrapper = load_module(
    "image_gen_with_codex_env", SKILL_DIR / "scripts" / "image_gen_with_codex_env.py"
)


class FakeGeminiClient:
    def __init__(self, response):
        self.interactions = self
        self.models = self
        self.response = response
        self.interaction_calls = []
        self.generate_content_calls = []

    def create(self, **payload):
        self.interaction_calls.append(payload)
        return self.response

    def generate_content(self, **payload):
        self.generate_content_calls.append(payload)
        return self.response


def args_for(**overrides):
    values = {
        "command": "generate",
        "model": gemini.DEFAULT_MODEL,
        "prompt": "test prompt",
        "prompt_file": None,
        "n": 1,
        "size": "1024x1024",
        "quality": None,
        "background": None,
        "output_format": "png",
        "output_compression": None,
        "moderation": None,
        "out": "output.png",
        "out_dir": None,
        "force": False,
        "dry_run": False,
        "max_attempts": 3,
        "augment": False,
        "use_case": None,
        "scene": None,
        "subject": None,
        "style": None,
        "composition": None,
        "lighting": None,
        "palette": None,
        "materials": None,
        "text": None,
        "constraints": None,
        "negative": None,
        "downscale_max_dim": None,
        "downscale_suffix": "-web",
        "mask": None,
        "input_fidelity": None,
        "image": [],
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class GeminiProviderTest(unittest.TestCase):
    def setUp(self):
        self.environment = mock.patch.dict(gemini.os.environ, {}, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_official_and_nano_banana_model_ids_are_supported(self):
        expected = {
            "gemini-3.1-flash-lite-image",
            "gemini-3.1-flash-image",
            "gemini-3-pro-image",
            "gemini-2.5-flash-image",
            "nano-banana-2-lite",
            "nano-banana-2",
            "nano-banana-pro",
            "nano-banana",
        }
        self.assertEqual(gemini.SUPPORTED_MODELS, expected)
        for model in expected:
            gemini._validate_model(model)
        with self.assertRaisesRegex(gemini.GeminiCliError, "Unsupported Gemini image model"):
            gemini._validate_model("gemini-3-pro-image-preview")

    def test_generate_content_is_default_and_mode_is_validated(self):
        self.assertEqual(gemini._api_mode(), "generate-content")
        with mock.patch.dict(gemini.os.environ, {"GEMINI_API_MODE": "interactions"}):
            self.assertEqual(gemini._api_mode(), "interactions")
        with mock.patch.dict(gemini.os.environ, {"GEMINI_API_MODE": "unknown"}):
            with self.assertRaisesRegex(gemini.GeminiCliError, "GEMINI_API_MODE"):
                gemini._api_mode()

    def test_size_mapping(self):
        self.assertEqual(gemini._parse_size("1024x1024", gemini.DEFAULT_MODEL), ("1:1", "1K"))
        self.assertEqual(gemini._parse_size("3840x2160", gemini.DEFAULT_MODEL), ("16:9", "4K"))

    def test_unsupported_ratio_fails_instead_of_rounding(self):
        with self.assertRaisesRegex(gemini.GeminiCliError, "does not support"):
            gemini._parse_size("1600x200", gemini.DEFAULT_MODEL)

    def test_lite_model_rejects_2k(self):
        with self.assertRaisesRegex(gemini.GeminiCliError, "supports only 1K"):
            gemini._parse_size("1920x1080", "gemini-3.1-flash-lite-image")

    def test_official_resolution_rules_apply_to_nano_aliases(self):
        self.assertEqual(gemini._parse_size("512x512", "gemini-3.1-flash-image"), ("1:1", "0.5K"))
        self.assertEqual(gemini._parse_size("512x512", "nano-banana-2"), ("1:1", "0.5K"))
        with self.assertRaisesRegex(gemini.GeminiCliError, "supports only 1K"):
            gemini._parse_size("2048x2048", "nano-banana-2-lite")
        self.assertEqual(gemini._parse_size("2048x2048", "nano-banana"), ("1:1", "2K"))

    def test_extracts_all_interaction_image_blocks(self):
        interaction = {
            "steps": [
                {"type": "tool", "content": []},
                {
                    "type": "model_output",
                    "content": [
                        {"type": "text", "text": "done"},
                        {
                            "type": "image",
                            "data": base64.b64encode(b"first").decode(),
                            "mime_type": "image/png",
                        },
                        {
                            "type": "image",
                            "data": base64.b64encode(b"second").decode(),
                            "mime_type": "image/jpeg",
                        },
                    ],
                },
            ]
        }
        self.assertEqual(
            gemini._extract_images(interaction, "interactions"),
            [(b"first", "image/png"), (b"second", "image/jpeg")],
        )

    def test_extracts_all_generate_content_image_parts(self):
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "done"},
                            {
                                "inlineData": {
                                    "data": base64.b64encode(b"first").decode(),
                                    "mimeType": "image/png",
                                }
                            },
                        ]
                    }
                },
                {
                    "content": {
                        "parts": [
                            {
                                "inline_data": {
                                    "data": base64.b64encode(b"second").decode(),
                                    "mime_type": "image/jpeg",
                                }
                            }
                        ]
                    }
                },
            ]
        }
        self.assertEqual(
            gemini._extract_images(response, "generate-content"),
            [(b"first", "image/png"), (b"second", "image/jpeg")],
        )

    def test_missing_image_block_fails(self):
        with self.assertRaisesRegex(gemini.GeminiCliError, "no image blocks"):
            gemini._extract_images({"candidates": []}, "generate-content")

    def test_edit_preserves_reference_order(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            first = Path(temporary_directory) / "first.png"
            second = Path(temporary_directory) / "second.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            response = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"inline_data": {"data": base64.b64encode(b"result").decode()}}
                            ]
                        }
                    }
                ]
            }
            client = FakeGeminiClient(response)
            args = args_for(command="edit", image=[str(first), str(second)])
            with mock.patch.object(gemini, "_create_client", return_value=client), mock.patch.object(
                gemini, "_write_images"
            ):
                gemini._run_single(args)
            contents = client.generate_content_calls[0]["contents"]
            self.assertEqual(
                [base64.b64decode(item["inline_data"]["data"]) for item in contents[1:]],
                [b"one", b"two"],
            )

    def test_mask_and_transparent_background_fail_explicitly(self):
        with self.assertRaisesRegex(gemini.GeminiCliError, "does not support --mask"):
            gemini._validate_options(args_for(command="edit", mask="mask.png"))
        with self.assertRaisesRegex(gemini.GeminiCliError, "chroma-key"):
            gemini._validate_options(args_for(background="transparent"))

    def test_n_creates_independent_generate_content_requests(self):
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"inline_data": {"data": base64.b64encode(b"result").decode()}}
                        ]
                    }
                }
            ]
        }
        client = FakeGeminiClient(response)
        with mock.patch.object(gemini, "_create_client", return_value=client), mock.patch.object(
            gemini, "_write_images"
        ) as write_images:
            gemini._run_single(args_for(n=3))
        self.assertEqual(len(client.generate_content_calls), 3)
        self.assertEqual(len(write_images.call_args.args[0]), 3)

    def test_interactions_mode_remains_supported(self):
        response = {
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {"type": "image", "data": base64.b64encode(b"result").decode()}
                    ],
                }
            ]
        }
        client = FakeGeminiClient(response)
        with mock.patch.dict(gemini.os.environ, {"GEMINI_API_MODE": "interactions"}), mock.patch.object(
            gemini, "_create_client", return_value=client
        ), mock.patch.object(gemini, "_write_images"):
            gemini._run_single(args_for())
        self.assertEqual(len(client.interaction_calls), 1)
        self.assertEqual(client.generate_content_calls, [])

    def test_dry_run_does_not_create_client_or_print_secret(self):
        secret = "test-secret-that-must-not-be-logged"
        output = StringIO()
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": secret}), mock.patch.object(
            gemini, "_create_client"
        ) as create_client, mock.patch("sys.stdout", output):
            gemini._run_single(args_for(dry_run=True))
        create_client.assert_not_called()
        self.assertNotIn(secret, output.getvalue())

    def test_batch_job_paths_are_unique(self):
        first = args_for(out_dir="results", out="001-first.png")
        second = args_for(out_dir="results", out="002-second.png")
        self.assertNotEqual(
            gemini._output_paths(first.out, first.out_dir, "png", 1),
            gemini._output_paths(second.out, second.out_dir, "png", 1),
        )

    def test_output_extension_tracks_actual_format(self):
        self.assertEqual(
            gemini._output_paths("result.png", None, "jpeg", 1),
            [Path("result.jpg")],
        )

    def test_local_conversion_and_downscale_write_valid_images(self):
        source = StringIO()
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw_buffer = BytesIO()
            Image.new("RGBA", (32, 16), (255, 0, 0, 128)).save(raw_buffer, format="PNG")
            args = args_for(
                output_format="jpeg",
                out=str(Path(temporary_directory) / "result.jpg"),
                downscale_max_dim=8,
            )
            with mock.patch("sys.stdout", source):
                outputs = gemini._write_images([(raw_buffer.getvalue(), "image/png")], args)
            with Image.open(outputs[0]) as full:
                self.assertEqual(full.format, "JPEG")
                self.assertEqual(full.size, (32, 16))
            with Image.open(Path(temporary_directory) / "result-web.jpg") as small:
                self.assertEqual(small.format, "JPEG")
                self.assertEqual(small.size, (8, 4))


class ProviderWrapperTest(unittest.TestCase):
    def run_wrapper(self, env_text: str, inherited=None):
        inherited = inherited or {}
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            (home / "imagegen.env").write_text(env_text, encoding="utf-8")
            completed = mock.Mock(returncode=0)
            with mock.patch.object(wrapper, "_codex_home", return_value=home), mock.patch.object(
                wrapper.subprocess, "run", return_value=completed
            ) as run, mock.patch.object(sys, "argv", ["wrapper", "generate", "--prompt", "test"]), mock.patch.dict(
                wrapper.os.environ, inherited, clear=True
            ):
                status = wrapper.main()
            return status, run

    def test_missing_provider_defaults_to_openai(self):
        status, run = self.run_wrapper(
            "OPENAI_API_KEY=openai-secret\nOPENAI_BASE_URL=https://example.test/v1\n"
        )
        self.assertEqual(status, 0)
        command = run.call_args.args[0]
        self.assertEqual(Path(command[1]).name, "image_gen.py")

    def test_gemini_route_does_not_inherit_openai_credentials(self):
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=gemini\nGEMINI_API_KEY=gemini-secret\nGEMINI_IMAGE_MODEL=gemini-3.1-flash-image\n",
            inherited={"OPENAI_API_KEY": "inherited-openai", "OPENAI_BASE_URL": "https://wrong.test/v1"},
        )
        self.assertEqual(status, 0)
        command = run.call_args.args[0]
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(Path(command[1]).name, "gemini_image_gen.py")
        self.assertNotIn("OPENAI_API_KEY", child_env)
        self.assertNotIn("OPENAI_BASE_URL", child_env)
        self.assertEqual(child_env["GEMINI_API_KEY"], "gemini-secret")

    def test_gemini_api_mode_is_passed_to_child(self):
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=gemini\nGEMINI_API_KEY=gemini-secret\nGEMINI_API_MODE=interactions\n"
        )
        self.assertEqual(status, 0)
        self.assertEqual(run.call_args.kwargs["env"]["GEMINI_API_MODE"], "interactions")

    def test_nano_banana_model_is_forwarded_unchanged(self):
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=gemini\nGEMINI_API_KEY=gemini-secret\n"
            "GEMINI_IMAGE_MODEL=nano-banana-2\n"
        )
        self.assertEqual(status, 0)
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["--model", "nano-banana-2"])

    def test_openai_route_does_not_inherit_gemini_credentials(self):
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=openai\nOPENAI_API_KEY=openai-secret\nOPENAI_BASE_URL=https://example.test/v1\n",
            inherited={"GEMINI_API_KEY": "inherited-gemini"},
        )
        self.assertEqual(status, 0)
        self.assertNotIn("GEMINI_API_KEY", run.call_args.kwargs["env"])

    def test_invalid_provider_and_wrong_model_fail_without_subprocess(self):
        status, run = self.run_wrapper("IMAGE_PROVIDER=other\n")
        self.assertEqual(status, 2)
        run.assert_not_called()
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=gemini\nGEMINI_API_KEY=secret\nGEMINI_IMAGE_MODEL=gpt-image-2\n"
        )
        self.assertEqual(status, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
