import sys
import unittest
from pathlib import Path

# Add src to path so we can import exporter
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from exporter import prompts


class SummaryPromptOwnerDeadlineRulesTests(unittest.TestCase):
    """#47: Both summary prompts must carry explicit-owner/deadline instruction."""

    PROMPTS = [
        ("SUMMARY_SYSTEM_PROMPT", prompts.SUMMARY_SYSTEM_PROMPT),
        ("SUMMARY_USER_PROMPT", prompts.SUMMARY_USER_PROMPT),
    ]

    def _each(self, check):
        for name, text in self.PROMPTS:
            with self.subTest(prompt=name):
                check(name, text)

    def test_explicit_stated_instruction_present(self):
        """Both prompts must instruct to include owner/deadline when explicitly stated."""
        def check(name, text):
            self.assertIn(
                "explicitly stated",
                text,
                msg=f"{name}: missing 'explicitly stated' in owner/deadline rule",
            )
        self._each(check)

    def test_owner_unspecified_marker_present(self):
        """Both prompts must contain the '(owner unspecified)' marker."""
        def check(name, text):
            self.assertIn(
                "(owner unspecified)",
                text,
                msg=f"{name}: missing '(owner unspecified)' marker",
            )
        self._each(check)

    def test_deadline_unspecified_marker_present(self):
        """Both prompts must contain the '(deadline unspecified)' marker."""
        def check(name, text):
            self.assertIn(
                "(deadline unspecified)",
                text,
                msg=f"{name}: missing '(deadline unspecified)' marker",
            )
        self._each(check)

    def test_anti_invention_clause_present(self):
        """Both prompts must still contain the anti-hallucination 'never invent' clause."""
        def check(name, text):
            self.assertIn(
                "never invent",
                text.lower(),
                msg=f"{name}: missing anti-invention clause ('never invent')",
            )
        self._each(check)

    def test_old_blanket_prohibition_absent(self):
        """Neither prompt must contain the old 'Do not append owner labels' prohibition."""
        def check(name, text):
            self.assertNotIn(
                "Do not append owner labels",
                text,
                msg=f"{name}: old blanket prohibition still present",
            )
        self._each(check)

    def test_summary_system_prompt_formats_without_kwargs(self):
        """SUMMARY_SYSTEM_PROMPT takes no format kwargs (summarizer passes it as-is)."""
        # Should not raise
        result = prompts.SUMMARY_SYSTEM_PROMPT
        self.assertIsInstance(result, str)

    def test_summary_user_prompt_formats_with_transcript_kwarg(self):
        """SUMMARY_USER_PROMPT must format cleanly with {transcript} kwarg."""
        result = prompts.SUMMARY_USER_PROMPT.format(transcript="[00:00] Hello world.")
        self.assertIn("Hello world", result)


if __name__ == "__main__":
    unittest.main()
