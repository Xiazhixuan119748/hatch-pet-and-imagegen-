import argparse
import base64
from io import BytesIO, StringIO
import importlib.util
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


grok = load_module("grok_image_gen", SKILL_DIR / "scripts" / "grok_image_gen.py")
wrapper = load_module(
    "grok_provider_wrapper", SKILL_DIR / "scripts" / "image_gen_with_codex_env.py"
)


def png_bytes(color=(255, 0, 0, 255), size=(16, 8)):
    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, status_code=200, body=None, content=b"", headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, responses=None, download=None):
        self.responses = list(responses or [])
        self.download = download
        self.posts = []
        self.gets = []
        self.closed = False

    def post(self, url, headers, json):
        self.posts.append((url, headers, json))
        return self.responses.pop(0)

    def get(self, url, headers):
        self.gets.append((url, headers))
        return self.download

    def request(self, method, url, headers, **kwargs):
        self.gets.append((method, url, headers, kwargs))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def args_for(**overrides):
    values = {
        "command": "generate",
        "model": grok.DEFAULT_MODEL,
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


class GrokProviderTest(unittest.TestCase):
    def test_official_image_models_and_aliases_are_supported(self):
        expected = {
            "grok-imagine-image",
            "grok-imagine-image-2026-03-02",
            "grok-imagine-image-quality",
            "grok-imagine-image-quality-20260403",
            "grok-imagine-image-quality-latest",
            "grok-imagine-image-pro",
        }
        self.assertEqual(grok.SUPPORTED_MODELS, expected)
        for model in expected:
            grok._validate_model(model)
        with self.assertRaisesRegex(grok.GrokCliError, "Unsupported Grok image model"):
            grok._validate_model("grok-imagine-video")

    def test_primary_models_share_generation_contract(self):
        payloads = []
        for model in ("grok-imagine-image", "grok-imagine-image-quality"):
            body = {"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}
            client = FakeClient([FakeResponse(body=body)])
            with mock.patch.object(grok, "_create_http_client", return_value=client), mock.patch.object(
                grok, "_write_images"
            ), mock.patch.dict(grok.os.environ, {"XAI_API_KEY": "secret"}, clear=True):
                grok._run_single(args_for(model=model, size="1920x1080"))
            self.assertTrue(client.posts[0][0].endswith("/images/generations"))
            payloads.append(client.posts[0][2])
        self.assertEqual(
            {key: value for key, value in payloads[0].items() if key != "model"},
            {key: value for key, value in payloads[1].items() if key != "model"},
        )

    def test_primary_models_share_edit_contract(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source.png"
            source.write_bytes(png_bytes())
            payloads = []
            for model in ("grok-imagine-image", "grok-imagine-image-quality"):
                body = {"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}
                client = FakeClient([FakeResponse(body=body)])
                with mock.patch.object(grok, "_create_http_client", return_value=client), mock.patch.object(
                    grok, "_write_images"
                ), mock.patch.dict(grok.os.environ, {"XAI_API_KEY": "secret"}, clear=True):
                    grok._run_single(
                        args_for(command="edit", model=model, image=[str(source)], size="auto")
                    )
                self.assertTrue(client.posts[0][0].endswith("/images/edits"))
                payloads.append(client.posts[0][2])
            self.assertEqual(
                {key: value for key, value in payloads[0].items() if key != "model"},
                {key: value for key, value in payloads[1].items() if key != "model"},
            )

    def test_size_mapping(self):
        self.assertEqual(grok._parse_size("1024x1024"), ("1:1", "1k", False))
        self.assertEqual(grok._parse_size("1920x1080"), ("16:9", "2k", False))
        self.assertEqual(grok._parse_size("2048x1024"), ("2:1", "2k", False))
        self.assertEqual(grok._parse_size("4096x4096"), ("1:1", "2k", True))

    def test_unsupported_ratio_fails(self):
        with self.assertRaisesRegex(grok.GrokCliError, "does not support"):
            grok._parse_size("1600x200")
        with self.assertRaisesRegex(grok.GrokCliError, "does not support"):
            grok._parse_size("1919x1080")

    def test_more_than_three_references_fails(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = []
            for index in range(4):
                path = Path(temporary_directory) / f"{index}.png"
                path.write_bytes(png_bytes())
                paths.append(str(path))
            with self.assertRaisesRegex(grok.GrokCliError, "at most 3"):
                grok._check_images(paths)

    def test_single_edit_uses_json_image_and_preserves_auto_ratio(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "source.png"
            image_path.write_bytes(png_bytes())
            body = {"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}
            client = FakeClient([FakeResponse(body=body)])
            args = args_for(command="edit", image=[str(image_path)], size="auto")
            with mock.patch.object(grok, "_create_http_client", return_value=client), mock.patch.object(
                grok, "_write_images"
            ), mock.patch.dict(grok.os.environ, {"XAI_API_KEY": "secret"}, clear=True):
                grok._run_single(args)
            payload = client.posts[0][2]
            self.assertIn("image", payload)
            self.assertNotIn("images", payload)
            self.assertNotIn("aspect_ratio", payload)
            self.assertTrue(payload["image"]["url"].startswith("data:image/png;base64,"))

    def test_multi_edit_preserves_order_and_adds_image_labels(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = []
            for index, color in enumerate(((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255))):
                path = Path(temporary_directory) / f"{index}.png"
                path.write_bytes(png_bytes(color))
                paths.append(str(path))
            body = {"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}
            client = FakeClient([FakeResponse(body=body)])
            args = args_for(command="edit", image=paths, size="1920x1080")
            with mock.patch.object(grok, "_create_http_client", return_value=client), mock.patch.object(
                grok, "_write_images"
            ), mock.patch.dict(grok.os.environ, {"XAI_API_KEY": "secret"}, clear=True):
                grok._run_single(args)
            payload = client.posts[0][2]
            decoded = [base64.b64decode(item["url"].split(",", 1)[1]) for item in payload["images"]]
            self.assertEqual(decoded, [Path(path).read_bytes() for path in paths])
            self.assertIn("<IMAGE_0>", payload["prompt"])
            self.assertIn("<IMAGE_2>", payload["prompt"])
            self.assertEqual(payload["aspect_ratio"], "16:9")

    def test_edit_accepts_public_url_and_file_id_references(self):
        body = {"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}
        client = FakeClient([FakeResponse(body=body)])
        args = args_for(
            command="edit",
            image=["https://example.test/reference.png", "file_id:file-123"],
            size="1920x1080",
        )
        with mock.patch.object(grok, "_create_http_client", return_value=client), mock.patch.object(
            grok, "_write_images"
        ), mock.patch.dict(grok.os.environ, {"XAI_API_KEY": "secret"}, clear=True):
            grok._run_single(args)
        references = client.posts[0][2]["images"]
        self.assertEqual(references[0], {"url": "https://example.test/reference.png"})
        self.assertEqual(references[1], {"file_id": "file-123"})

    def test_image_object_uses_xai_json_shape(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "source.png"
            path.write_bytes(png_bytes())
            self.assertEqual(set(grok._image_object(path)), {"url"})

    def test_n_uses_one_generation_request(self):
        images = [
            {"b64_json": base64.b64encode(png_bytes(color)).decode(), "mime_type": "image/png"}
            for color in ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255))
        ]
        client = FakeClient([FakeResponse(body={"data": images})])
        with mock.patch.object(grok, "_create_http_client", return_value=client), mock.patch.object(
            grok, "_write_images"
        ) as write_images, mock.patch.dict(grok.os.environ, {"XAI_API_KEY": "secret"}, clear=True):
            grok._run_single(args_for(n=3))
        self.assertEqual(len(client.posts), 1)
        self.assertEqual(client.posts[0][2]["n"], 3)
        self.assertEqual(len(write_images.call_args.args[0]), 3)

    def test_extracts_base64_and_url_images(self):
        raw = png_bytes()
        download = FakeResponse(content=raw, headers={"content-type": "image/png"})
        client = FakeClient(download=download)
        body = {
            "data": [
                {"b64_json": base64.b64encode(raw).decode(), "mime_type": "image/png"},
                {"url": "https://imgen.x.ai/example.jpeg"},
            ]
        }
        self.assertEqual(grok._extract_images(body, client), [(raw, "image/png"), (raw, "image/png")])
        self.assertEqual(len(client.gets), 1)

    def test_missing_image_data_fails(self):
        with self.assertRaisesRegex(grok.GrokCliError, "no image data"):
            grok._extract_images({"data": []}, FakeClient())

    def test_retryable_status_retries_but_400_does_not(self):
        success = FakeResponse(body={"data": [{"b64_json": "unused"}]})
        retrying = FakeClient(
            [FakeResponse(status_code=429, body={"error": {"message": "slow"}}, headers={"retry-after": "0"}), success]
        )
        with mock.patch.dict(grok.os.environ, {"XAI_API_KEY": "secret"}, clear=True), mock.patch.object(
            grok.time, "sleep"
        ):
            result = grok._post_with_retries(retrying, "images/generations", {}, attempts=2, label="test")
        self.assertIs(result, success._body)
        self.assertEqual(len(retrying.posts), 2)

        failing = FakeClient([FakeResponse(status_code=400, body={"error": {"message": "bad"}})])
        with mock.patch.dict(grok.os.environ, {"XAI_API_KEY": "secret"}, clear=True):
            with self.assertRaises(grok.GrokHttpError):
                grok._post_with_retries(failing, "images/generations", {}, attempts=3, label="test")
        self.assertEqual(len(failing.posts), 1)

    def test_dry_run_never_creates_client_or_prints_base64_or_secret(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "source.png"
            image_path.write_bytes(png_bytes())
            output = StringIO()
            secret = "secret-not-for-output"
            args = args_for(command="edit", image=[str(image_path)], dry_run=True)
            with mock.patch.object(grok, "_create_http_client") as create_client, mock.patch(
                "sys.stdout", output
            ), mock.patch.dict(grok.os.environ, {"XAI_API_KEY": secret}, clear=True):
                grok._run_single(args)
            create_client.assert_not_called()
            rendered = output.getvalue()
            self.assertNotIn(secret, rendered)
            self.assertNotIn(base64.b64encode(image_path.read_bytes()).decode(), rendered)
            self.assertIn("<base64:", rendered)

    def test_mask_and_transparency_fail_explicitly(self):
        with self.assertRaisesRegex(grok.GrokCliError, "does not support --mask"):
            grok._validate_options(args_for(command="edit", mask="mask.png"))
        with self.assertRaisesRegex(grok.GrokCliError, "chroma-key"):
            grok._validate_options(args_for(background="transparent"))

    def test_local_conversion_and_downscale(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            args = args_for(
                output_format="jpeg",
                out=str(Path(temporary_directory) / "result.jpg"),
                downscale_max_dim=8,
            )
            with mock.patch("sys.stdout", StringIO()):
                outputs = grok._write_images([(png_bytes(size=(32, 16)), "image/png")], args)
            with Image.open(outputs[0]) as full:
                self.assertEqual((full.format, full.size), ("JPEG", (32, 16)))
            with Image.open(Path(temporary_directory) / "result-web.jpg") as small:
                self.assertEqual((small.format, small.size), ("JPEG", (8, 4)))


class GrokWrapperTest(unittest.TestCase):
    def run_wrapper(self, env_text, inherited=None):
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            (home / "imagegen.env").write_text(env_text, encoding="utf-8")
            completed = mock.Mock(returncode=0)
            with mock.patch.object(wrapper, "_codex_home", return_value=home), mock.patch.object(
                wrapper.subprocess, "run", return_value=completed
            ) as run, mock.patch.object(sys, "argv", ["wrapper", "generate", "--prompt", "test"]), mock.patch.dict(
                wrapper.os.environ, inherited or {}, clear=True
            ):
                status = wrapper.main()
            return status, run

    def test_grok_route_and_credential_isolation(self):
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=grok\nXAI_API_KEY=xai-secret\nXAI_IMAGE_MODEL=grok-imagine-image-quality\n",
            inherited={"OPENAI_API_KEY": "openai", "GEMINI_API_KEY": "gemini"},
        )
        self.assertEqual(status, 0)
        command = run.call_args.args[0]
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(Path(command[1]).name, "grok_image_gen.py")
        self.assertEqual(child_env["XAI_API_KEY"], "xai-secret")
        self.assertNotIn("OPENAI_API_KEY", child_env)
        self.assertNotIn("GEMINI_API_KEY", child_env)

    def test_primary_grok_image_model_passes_wrapper_validation(self):
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=grok\nXAI_API_KEY=xai-secret\n"
            "XAI_IMAGE_MODEL=grok-imagine-image\n"
        )
        self.assertEqual(status, 0)
        self.assertEqual(run.call_args.args[0][-2:], ["--model", "grok-imagine-image"])

    def test_other_routes_do_not_inherit_xai_key(self):
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=openai\nOPENAI_API_KEY=openai\nOPENAI_BASE_URL=https://example.test/v1\n",
            inherited={"XAI_API_KEY": "xai"},
        )
        self.assertEqual(status, 0)
        self.assertNotIn("XAI_API_KEY", run.call_args.kwargs["env"])

    def test_files_command_does_not_receive_image_model(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            (home / "imagegen.env").write_text(
                "IMAGE_PROVIDER=grok\nXAI_API_KEY=xai-secret\n"
                "XAI_IMAGE_MODEL=grok-imagine-image-quality\n",
                encoding="utf-8",
            )
            completed = mock.Mock(returncode=0)
            with mock.patch.object(wrapper, "_codex_home", return_value=home), mock.patch.object(
                wrapper.subprocess, "run", return_value=completed
            ) as run, mock.patch.object(
                sys, "argv", ["wrapper", "files", "list"]
            ), mock.patch.dict(wrapper.os.environ, {}, clear=True):
                self.assertEqual(wrapper.main(), 0)
            command = run.call_args.args[0]
            self.assertIn("files", command)
            self.assertNotIn("--model", command)

    def test_missing_key_and_wrong_model_fail_before_subprocess(self):
        status, run = self.run_wrapper("IMAGE_PROVIDER=grok\n")
        self.assertEqual(status, 2)
        run.assert_not_called()
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=grok\nXAI_API_KEY=secret\nXAI_IMAGE_MODEL=gpt-image-2\n"
        )
        self.assertEqual(status, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
