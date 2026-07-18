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


agnes = load_module("agnes_image_gen", SKILL_DIR / "scripts" / "agnes_image_gen.py")
wrapper = load_module(
    "agnes_provider_wrapper", SKILL_DIR / "scripts" / "image_gen_with_codex_env.py"
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

    def close(self):
        self.closed = True


def args_for(**overrides):
    values = {
        "command": "generate",
        "model": agnes.DEFAULT_MODEL,
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


class AgnesProviderTest(unittest.TestCase):
    def test_model_21_size_mapping(self):
        self.assertEqual(
            agnes._map_size("1024x1024", agnes.MODEL_21),
            {
                "size": "1K",
                "ratio": "1:1",
                "expected_width": 1024,
                "expected_height": 1024,
                "downgraded": False,
            },
        )
        mapped = agnes._map_size("1920x1080", agnes.MODEL_21)
        self.assertEqual((mapped["size"], mapped["ratio"]), ("2K", "16:9"))
        self.assertEqual((mapped["expected_width"], mapped["expected_height"]), (2624, 1472))
        self.assertEqual(agnes._map_size("3840x2160", agnes.MODEL_21)["size"], "3K")
        self.assertEqual(agnes._map_size("4096x4096", agnes.MODEL_21)["size"], "4K")

    def test_model_21_rejects_near_ratio(self):
        with self.assertRaisesRegex(agnes.AgnesCliError, "does not support"):
            agnes._map_size("1919x1080", agnes.MODEL_21)

    def test_model_20_preserves_exact_size(self):
        mapped = agnes._map_size("1024x768", agnes.MODEL_20)
        self.assertEqual((mapped["size"], mapped["ratio"]), ("1024x768", None))
        self.assertEqual(agnes._map_size("auto", agnes.MODEL_20)["size"], "1024x1024")

    def test_generation_uses_return_base64_without_extra_body(self):
        payload = agnes._build_payload(args_for(), "test", [])
        self.assertTrue(payload["return_base64"])
        self.assertNotIn("extra_body", payload)
        self.assertNotIn("response_format", payload)

    def test_edit_uses_nested_extra_body_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = []
            for index, color in enumerate(((255, 0, 0, 255), (0, 255, 0, 255))):
                path = Path(temporary_directory) / f"{index}.png"
                path.write_bytes(png_bytes(color))
                paths.append(path)
            payload = agnes._build_payload(args_for(command="edit"), "edit", paths)
            self.assertNotIn("response_format", payload)
            self.assertNotIn("return_base64", payload)
            self.assertEqual(payload["extra_body"]["response_format"], "b64_json")
            decoded = [base64.b64decode(value.split(",", 1)[1]) for value in payload["extra_body"]["image"]]
            self.assertEqual(decoded, [path.read_bytes() for path in paths])

    def test_seventeen_references_fail_local_safety_limit(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = []
            for index in range(17):
                path = Path(temporary_directory) / f"{index}.png"
                path.write_bytes(png_bytes())
                paths.append(str(path))
            with self.assertRaisesRegex(agnes.AgnesCliError, "local safety limit"):
                agnes._check_images(paths)

    def test_n_creates_independent_requests_to_generation_endpoint(self):
        body = {"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}
        client = FakeClient([FakeResponse(body=body), FakeResponse(body=body), FakeResponse(body=body)])
        with mock.patch.object(agnes, "_create_http_client", return_value=client), mock.patch.object(
            agnes, "_write_images"
        ) as write_images, mock.patch.dict(agnes.os.environ, {"AGNES_API_KEY": "secret"}, clear=True):
            agnes._run_single(args_for(n=3))
        self.assertEqual(len(client.posts), 3)
        self.assertTrue(all(url.endswith("/images/generations") for url, _headers, _json in client.posts))
        self.assertEqual(len(write_images.call_args.args[0]), 3)

    def test_extracts_base64_and_url(self):
        raw = png_bytes()
        client = FakeClient(download=FakeResponse(content=raw, headers={"content-type": "image/png"}))
        body = {
            "data": [
                {"b64_json": base64.b64encode(raw).decode()},
                {"url": "https://storage.googleapis.com/agnes-aigc/example.png"},
            ]
        }
        self.assertEqual(agnes._extract_images(body, client), [(raw, "image/png"), (raw, "image/png")])

    def test_retryable_status_retries_but_400_does_not(self):
        success = FakeResponse(body={"data": []})
        retrying = FakeClient(
            [FakeResponse(status_code=429, body={"error": {"message": "slow"}}, headers={"retry-after": "0"}), success]
        )
        with mock.patch.dict(agnes.os.environ, {"AGNES_API_KEY": "secret"}, clear=True), mock.patch.object(
            agnes.time, "sleep"
        ):
            agnes._post_with_retries(retrying, {}, attempts=2, label="test")
        self.assertEqual(len(retrying.posts), 2)
        failing = FakeClient([FakeResponse(status_code=400, body={"error": {"message": "bad"}})])
        with mock.patch.dict(agnes.os.environ, {"AGNES_API_KEY": "secret"}, clear=True):
            with self.assertRaises(agnes.AgnesHttpError):
                agnes._post_with_retries(failing, {}, attempts=3, label="test")
        self.assertEqual(len(failing.posts), 1)

    def test_dry_run_does_not_create_client_or_expose_secret_or_base64(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "source.png"
            path.write_bytes(png_bytes())
            output = StringIO()
            secret = "secret-not-for-output"
            with mock.patch.object(agnes, "_create_http_client") as create_client, mock.patch(
                "sys.stdout", output
            ), mock.patch.dict(agnes.os.environ, {"AGNES_API_KEY": secret}, clear=True):
                agnes._run_single(args_for(command="edit", image=[str(path)], dry_run=True))
            create_client.assert_not_called()
            rendered = output.getvalue()
            self.assertNotIn(secret, rendered)
            self.assertNotIn(base64.b64encode(path.read_bytes()).decode(), rendered)
            self.assertIn("<base64:", rendered)

    def test_mask_and_transparency_fail(self):
        with self.assertRaisesRegex(agnes.AgnesCliError, "does not support --mask"):
            agnes._validate_options(args_for(command="edit", mask="mask.png"))
        with self.assertRaisesRegex(agnes.AgnesCliError, "chroma-key"):
            agnes._validate_options(args_for(background="transparent"))

    def test_local_conversion_and_downscale(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            args = args_for(
                output_format="jpeg",
                out=str(Path(temporary_directory) / "result.jpg"),
                downscale_max_dim=8,
            )
            with mock.patch("sys.stdout", StringIO()):
                outputs = agnes._write_images([(png_bytes(size=(32, 16)), "image/png")], args)
            with Image.open(outputs[0]) as full:
                self.assertEqual((full.format, full.size), ("JPEG", (32, 16)))
            with Image.open(Path(temporary_directory) / "result-web.jpg") as small:
                self.assertEqual((small.format, small.size), ("JPEG", (8, 4)))


class AgnesWrapperTest(unittest.TestCase):
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

    def test_agnes_route_and_credential_isolation(self):
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=agnes\nAGNES_API_KEY=agnes-secret\nAGNES_IMAGE_MODEL=agnes-image-2.1-flash\n",
            inherited={"OPENAI_API_KEY": "openai", "GEMINI_API_KEY": "gemini", "XAI_API_KEY": "xai"},
        )
        self.assertEqual(status, 0)
        command = run.call_args.args[0]
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(Path(command[1]).name, "agnes_image_gen.py")
        self.assertEqual(child_env["AGNES_API_KEY"], "agnes-secret")
        self.assertNotIn("OPENAI_API_KEY", child_env)
        self.assertNotIn("GEMINI_API_KEY", child_env)
        self.assertNotIn("XAI_API_KEY", child_env)

    def test_other_route_does_not_inherit_agnes_key(self):
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=openai\nOPENAI_API_KEY=openai\nOPENAI_BASE_URL=https://example.test/v1\n",
            inherited={"AGNES_API_KEY": "agnes"},
        )
        self.assertEqual(status, 0)
        self.assertNotIn("AGNES_API_KEY", run.call_args.kwargs["env"])

    def test_missing_key_and_wrong_model_fail_before_subprocess(self):
        status, run = self.run_wrapper("IMAGE_PROVIDER=agnes\n")
        self.assertEqual(status, 2)
        run.assert_not_called()
        status, run = self.run_wrapper(
            "IMAGE_PROVIDER=agnes\nAGNES_API_KEY=secret\nAGNES_IMAGE_MODEL=gpt-image-2\n"
        )
        self.assertEqual(status, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
