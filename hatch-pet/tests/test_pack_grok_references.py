import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pack_grok_references.py"
SPEC = importlib.util.spec_from_file_location("pack_grok_references", SCRIPT)
pack = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pack)


def make_image(path: Path, size=(640, 480), color=(255, 0, 0, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path, format="PNG")


class PackGrokReferencesTest(unittest.TestCase):
    def write_manifest(self, root: Path, inputs):
        manifest = root / "imagegen-jobs.json"
        manifest.write_text(json.dumps([{"id": "look-row-10", "input_images": inputs}]), encoding="utf-8")
        return manifest

    def test_packs_typical_look_row_into_three_inputs(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = {
                "layout": "references/layout-guides/look-row-10.png",
                "base": "references/canonical-base.png",
                "contact": "qa/contact-sheet.png",
                "anchors": "decoded/look-anchors-approved.png",
                "row9": "decoded/look-row-9.png",
            }
            for index, relative in enumerate(paths.values()):
                size = (1536, 208) if relative.startswith("decoded/") else (640, 480)
                make_image(root / relative, size=size, color=(index * 30, 100, 200, 255))
            manifest = self.write_manifest(
                root,
                [
                    {"path": paths["layout"], "role": "layout guide for 8 direction slots"},
                    {"path": paths["base"], "role": "canonical identity reference"},
                    {"path": paths["contact"], "role": "approved standard-row identity reference"},
                    {"path": paths["anchors"], "role": "approved cardinal direction strip"},
                    {"path": paths["row9"], "role": "look row continuity reference"},
                ],
            )
            output = root / "packed.json"
            result = pack.pack_job(root, manifest, "look-row-10", output)
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["packed_inputs"]), 3)
            self.assertEqual({board["category"] for board in result["boards"]}, {"identity", "direction"})
            for board in result["boards"]:
                board_path = root / board["path"]
                self.assertTrue(board_path.is_file())
                self.assertEqual(len(board["sha256"]), 64)
                with Image.open(board_path) as image:
                    self.assertEqual(image.size, pack.BOARD_SIZE)
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(written["original_inputs"][0]["path"], paths["layout"])

    def test_three_or_fewer_inputs_are_not_repacked(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inputs = []
            for index in range(3):
                relative = f"references/{index}.png"
                make_image(root / relative)
                inputs.append({"path": relative, "role": "pet reference"})
            result = pack.pack_job(root, self.write_manifest(root, inputs), "look-row-10", root / "out.json")
            self.assertEqual([item["path"] for item in result["packed_inputs"]], [item["path"] for item in inputs])
            self.assertEqual(result["boards"], [])

    def test_too_many_identity_references_fail_instead_of_becoming_unreadable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inputs = []
            layout = "references/layout-guides/row.png"
            make_image(root / layout)
            inputs.append({"path": layout, "role": "layout guide"})
            for index in range(5):
                relative = f"references/identity-{index}.png"
                make_image(root / relative)
                inputs.append({"path": relative, "role": "pet identity reference"})
            with self.assertRaisesRegex(SystemExit, "maximum is 4"):
                pack.pack_job(root, self.write_manifest(root, inputs), "look-row-10", root / "out.json")

    def test_input_cannot_escape_run_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "run"
            root.mkdir()
            outside = root.parent / "outside.png"
            make_image(outside)
            manifest = self.write_manifest(root, [{"path": "../outside.png", "role": "reference"}])
            with self.assertRaisesRegex(SystemExit, "escapes the run directory"):
                pack.pack_job(root, manifest, "look-row-10", root / "out.json")

    def test_prepared_jobs_publish_grok_input_policy(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory) / "run"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT.with_name("prepare_pet_run.py")),
                    "--pet-name",
                    "Grok Policy Test",
                    "--pet-notes",
                    "a simple mascot",
                    "--output-dir",
                    str(run_dir),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            manifest = json.loads((run_dir / "imagegen-jobs.json").read_text(encoding="utf-8"))
            jobs = manifest["jobs"]
            self.assertTrue(jobs)
            for job in jobs:
                policy = job["provider_input_preparation"]["grok"]
                self.assertEqual(policy["max_images"], 3)
                self.assertFalse(policy["silent_reference_dropping_allowed"])
                agnes_policy = job["provider_input_preparation"]["agnes"]
                self.assertEqual(agnes_policy["max_images"], 16)
                self.assertTrue(agnes_policy["use_original_inputs_in_order"])
                self.assertFalse(agnes_policy["documented_service_limit"])


if __name__ == "__main__":
    unittest.main()
