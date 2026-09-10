import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exporter import task_extractor
from exporter import llm_client


CANARY_A = "CANARY-TASK-A-DO-NOT-EXPOSE"
CANARY_B = "CANARY-TASK-B-DO-NOT-EXPOSE"


class TasksPrivacyContractTests(unittest.TestCase):
    """CT-7: daily task text must not leak into the LLM prompt payload."""

    def test_pending_tasks_not_in_openrouter_messages(self):
        """The messages passed to call_openrouter must not contain pending task strings."""
        with tempfile.TemporaryDirectory() as tmp:
            tasks_dir = Path(tmp) / "Tasks"
            tasks_dir.mkdir()
            daily_note = tasks_dir / "Tasks — 2026-08-14.md"
            daily_note.write_text(
                "# Tasks — 2026-08-14\n\n"
                "## 📋 Tasks do Dia\n"
                f"- [ ] {CANARY_A}\n"
                f"- [ ] {CANARY_B}\n",
                encoding="utf-8",
            )

            captured_messages = []

            def capturing_call(*, messages, **kwargs):
                captured_messages.append(messages)
                return '{"matched_tasks":[],"new_suggested_tasks":[],"not_now_items":[]}'

            patches = [
                patch.object(task_extractor, "TASKS_DIR", tasks_dir),
                patch.object(task_extractor, "TASKS_ARCHIVE_DIR", tasks_dir / "Archive"),
                patch.object(task_extractor, "call_openrouter", side_effect=capturing_call),
            ]
            for p in patches:
                p.start()
                self.addCleanup(p.stop)

            result = task_extractor.generate_task_suggestions(
                meeting_date="2026-08-14",
                summary="## 📌 Summary\nwe talked about the project",
                transcript_text="[00:00 → 00:01] we discussed the project",
            )

            self.assertIsInstance(result, str)
            serialized = json.dumps(captured_messages)
            self.assertNotIn(
                CANARY_A,
                serialized,
                "contracts #25: canary {!r} leaked into the LLM prompt messages; "
                "daily task text must be excluded from the prompt for privacy".format(CANARY_A),
            )
            self.assertNotIn(
                CANARY_B,
                serialized,
                "contracts #25: canary {!r} leaked into the LLM prompt messages; "
                "daily task text must be excluded from the prompt for privacy".format(CANARY_B),
            )

    def test_compute_explicit_task_matches_receives_full_list(self):
        """compute_explicit_task_matches must still receive the complete pending-tasks list."""
        pending_tasks = [
            "- [ ] Review PRs",
            f"- [ ] {CANARY_A}",
            "- [ ] Deploy staging",
        ]
        summary = "We need to review PRs. We will deploy staging tomorrow."
        transcript = "We need to review PRs today."

        matches = task_extractor.compute_explicit_task_matches(pending_tasks, summary, transcript)
        # Should match the tasks that appear in the summary/transcript.
        self.assertIn("- [ ] Review PRs", matches)
        self.assertIn("- [ ] Deploy staging", matches)
        self.assertNotIn(f"- [ ] {CANARY_A}", matches)
        # The function should have received all three in its input.
        self.assertEqual(len(matches), 2)


if __name__ == "__main__":
    unittest.main()
