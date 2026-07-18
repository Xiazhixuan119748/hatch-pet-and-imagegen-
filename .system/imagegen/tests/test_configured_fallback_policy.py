import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class ConfiguredRoutingPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.instructions = SKILL.read_text(encoding="utf-8")

    def test_canonical_config_uses_configured_wrapper_by_default(self) -> None:
        self.assertIn(
            "When it exists, use configured Codex `imagegen.env` CLI mode by default for normal generation and editing requests",
            self.instructions,
        )

    def test_prompt_does_not_need_to_name_env_file(self) -> None:
        self.assertIn(
            "the user does not need to name the file in the prompt",
            self.instructions,
        )

    def test_missing_canonical_config_uses_builtin(self) -> None:
        self.assertIn(
            "Use the built-in `image_gen` tool only when the canonical file does not exist",
            self.instructions,
        )

    def test_direct_fallback_requires_missing_canonical_file(self) -> None:
        self.assertIn(
            "Only offer the unconfigured `scripts/image_gen.py` fallback when the canonical `imagegen.env` file does not exist",
            self.instructions,
        )

    def test_wrapper_delegates_to_selected_provider(self) -> None:
        self.assertIn("delegates to the selected Provider CLI", self.instructions)


if __name__ == "__main__":
    unittest.main()
