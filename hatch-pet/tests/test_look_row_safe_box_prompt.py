import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
PREPARE = SKILL_DIR / "scripts" / "prepare_pet_run.py"


class LookRowSafeBoxPromptTest(unittest.TestCase):
    def prepare_run(self, temporary_directory: str) -> Path:
        run_dir = Path(temporary_directory) / "run"
        subprocess.run(
            [
                sys.executable,
                str(PREPARE),
                "--pet-name",
                "Safe Box Test",
                "--pet-notes",
                "a simple mascot",
                "--output-dir",
                str(run_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return run_dir

    def test_look_row_prompts_delegate_exact_slot_geometry_to_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = self.prepare_run(temporary_directory)

            prompt_paths = [
                run_dir / "prompts" / "rows" / "look-row-9.md",
                run_dir / "prompts" / "rows" / "look-row-10.md",
                run_dir / "prompts" / "row-retries" / "look-row-9.md",
                run_dir / "prompts" / "row-retries" / "look-row-10.md",
            ]
            for prompt_path in prompt_paths:
                prompt = prompt_path.read_text()
                self.assertIn("DETERMINISTIC REGISTRATION", prompt)
                self.assertIn("eight separated pose groups", prompt)
                self.assertIn("shared scale and baseline", prompt)
                self.assertNotIn(
                    "reject this result if any foreground enters a blue safety band", prompt
                )

    def test_look_row_prompts_lock_construction_and_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = self.prepare_run(temporary_directory)
            prompt_paths = [
                run_dir / "prompts" / "rows" / "look-row-9.md",
                run_dir / "prompts" / "rows" / "look-row-10.md",
                run_dir / "prompts" / "row-retries" / "look-row-9.md",
                run_dir / "prompts" / "row-retries" / "look-row-10.md",
            ]
            for prompt_path in prompt_paths:
                prompt = prompt_path.read_text()
                self.assertIn("HARD LAYOUT AND CONTINUITY CONTRACT", prompt)
                self.assertIn("same body height, head size, baseline", prompt)
                self.assertIn("same coordinates across all eight frames", prompt)
                self.assertIn("reject this result", prompt)

            row_9 = (run_dir / "prompts" / "rows" / "look-row-9.md").read_text()
            row_10 = (run_dir / "prompts" / "rows" / "look-row-10.md").read_text()
            self.assertIn("157.5 must be one even 22.5-degree step before 180", row_9)
            self.assertIn("180 must continue directly from row 9's 157.5", row_10)
            self.assertIn("337.5 must be one even 22.5-degree step before 000", row_10)

    def test_look_row_prompts_keep_direction_targets_without_pixel_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = self.prepare_run(temporary_directory)

            for prompt_kind in ("rows", "row-retries"):
                row_9 = (run_dir / "prompts" / prompt_kind / "look-row-9.md").read_text()
                row_10 = (run_dir / "prompts" / prompt_kind / "look-row-10.md").read_text()

                self.assertIn("DIRECTION TARGETS", row_9)
                self.assertIn("`045`: horizontal SCREEN-RIGHT and vertical UP", row_9)
                self.assertIn("`135`: horizontal SCREEN-RIGHT and vertical DOWN", row_9)
                self.assertIn("`225`: horizontal SCREEN-LEFT and vertical DOWN", row_10)
                self.assertIn("`315`: horizontal SCREEN-LEFT and vertical UP", row_10)
                self.assertIn("Minor pupil, nose, eyelid", row_9)
                self.assertIn("Minor pupil, nose, eyelid", row_10)
                self.assertIn("not as pixel-level landmark gates", row_9)
                self.assertIn("not as pixel-level landmark gates", row_10)


if __name__ == "__main__":
    unittest.main()
