import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Add src to path so we can import exporter
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from exporter import summarizer, vault_exporter


class ConcurrencyConfigTests(unittest.TestCase):
    """(1) MEETCAP_EXPORT_MAX_CONCURRENCY (env, default 2, max 4, min 1)."""

    def test_get_max_concurrency_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(summarizer.get_max_concurrency(), 2)

    def test_get_max_concurrency_empty_or_invalid(self):
        with patch.dict(os.environ, {"MEETCAP_EXPORT_MAX_CONCURRENCY": ""}):
            self.assertEqual(summarizer.get_max_concurrency(), 2)
        with patch.dict(os.environ, {"MEETCAP_EXPORT_MAX_CONCURRENCY": "invalid"}):
            self.assertEqual(summarizer.get_max_concurrency(), 2)

    def test_get_max_concurrency_valid_values(self):
        for val in (1, 2, 3, 4):
            with patch.dict(os.environ, {"MEETCAP_EXPORT_MAX_CONCURRENCY": str(val)}):
                self.assertEqual(summarizer.get_max_concurrency(), val)

    def test_get_max_concurrency_bounds(self):
        with patch.dict(os.environ, {"MEETCAP_EXPORT_MAX_CONCURRENCY": "0"}):
            self.assertEqual(summarizer.get_max_concurrency(), 1)
        with patch.dict(os.environ, {"MEETCAP_EXPORT_MAX_CONCURRENCY": "-5"}):
            self.assertEqual(summarizer.get_max_concurrency(), 1)
        with patch.dict(os.environ, {"MEETCAP_EXPORT_MAX_CONCURRENCY": "5"}):
            self.assertEqual(summarizer.get_max_concurrency(), 4)
        with patch.dict(os.environ, {"MEETCAP_EXPORT_MAX_CONCURRENCY": "100"}):
            self.assertEqual(summarizer.get_max_concurrency(), 4)


class ChunkSummarizationConcurrencyTests(unittest.TestCase):
    """(1) ThreadPoolExecutor for chunk summarization in parallel."""

    def test_chunks_summarized_in_parallel(self):
        """Verify that multiple chunks are processed concurrently via barrier."""
        transcript = "\n".join(
            [
                "[00:00 → 00:05] chunk one line 1",
                "[00:05 → 00:10] chunk one line 2",
                "[00:10 → 00:15] chunk two line 1",
                "[00:15 → 00:20] chunk two line 2",
            ]
        )

        barrier = threading.Barrier(2, timeout=2.0)

        def mock_summarize(chunk_text, index, total):
            barrier.wait()  # Times out if chunks run serially
            return f"Summary {index}"

        with patch.dict(os.environ, {"MEETCAP_EXPORT_MAX_CONCURRENCY": "2"}), \
             patch.object(summarizer, "summarize_transcript_chunk", side_effect=mock_summarize), \
             patch.object(summarizer, "reduce_chunk_summaries", side_effect=lambda chunks, max_c: "final"):
            result = summarizer.generate_summary(
                transcript,
                single_pass_max_chars=30,
                chunk_max_chars=80,
                merge_max_chars=500,
            )
            self.assertEqual(result, "final")

    def test_chunk_summaries_preserve_order_when_completion_order_differs(self):
        """Even if chunk 2 finishes before chunk 1, chunk 1 must be first in reduce."""
        transcript = "\n".join(
            [
                "[00:00 → 00:05] chunk one line 1",
                "[00:05 → 00:10] chunk one line 2",
                "[00:10 → 00:15] chunk two line 1",
                "[00:15 → 00:20] chunk two line 2",
            ]
        )

        def mock_summarize(chunk_text, index, total):
            if index == 1:
                time.sleep(0.05)
            return f"Summary {index}"

        recorded_chunks = []

        def mock_reduce(chunks, max_c):
            recorded_chunks.extend(chunks)
            return "merged"

        with patch.dict(os.environ, {"MEETCAP_EXPORT_MAX_CONCURRENCY": "2"}), \
             patch.object(summarizer, "summarize_transcript_chunk", side_effect=mock_summarize), \
             patch.object(summarizer, "reduce_chunk_summaries", side_effect=mock_reduce):
            result = summarizer.generate_summary(
                transcript,
                single_pass_max_chars=30,
                chunk_max_chars=80,
                merge_max_chars=500,
            )
            self.assertEqual(result, "merged")
            self.assertEqual(recorded_chunks, ["Summary 1", "Summary 2"])

    def test_chunk_summarization_error_degrades_gracefully(self):
        """Exception in parallel chunk worker returns warning block."""
        transcript = "\n".join(
            [
                "[00:00 → 00:05] chunk one line 1",
                "[00:05 → 00:10] chunk one line 2",
                "[00:10 → 00:15] chunk two line 1",
                "[00:15 → 00:20] chunk two line 2",
            ]
        )

        def failing_summarize(chunk_text, index, total):
            if index == 2:
                raise RuntimeError("LLM rate limit")
            return f"Summary {index}"

        with patch.dict(os.environ, {"MEETCAP_EXPORT_MAX_CONCURRENCY": "2"}), \
             patch.object(summarizer, "summarize_transcript_chunk", side_effect=failing_summarize):
            result = summarizer.generate_summary(
                transcript,
                single_pass_max_chars=30,
                chunk_max_chars=80,
                merge_max_chars=500,
            )
            self.assertIn("> [!warning] Summary generation failed:", result)
            self.assertIn("LLM rate limit", result)


class ExportNoteConcurrencyTests(unittest.TestCase):
    """(2) in export_note: claims in parallel with summary via ThreadPoolExecutor."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault_path = Path(self.tmp.name)
        self.meetings_dir = self.vault_path / "Meetings"
        self.meetings_dir.mkdir(parents=True)

        self.transcript_file = self.vault_path / "transcript.txt"
        self.transcript_file.write_text(
            "[00:00:00 -> 00:00:05] Speaker: Alpha meeting start.\n"
            "[00:00:05 -> 00:00:10] Speaker: Beta meeting finish.\n",
            encoding="utf-8",
        )
        self.patch_vocab = patch.object(
            vault_exporter, "load_vocabulary", return_value={"canonical": [], "aliases": {}}
        )
        self.patch_vocab.start()

    def tearDown(self):
        self.patch_vocab.stop()
        self.tmp.cleanup()

    def test_summary_and_claims_run_concurrently(self):
        """Verify summary and claims run in parallel via barrier."""
        barrier = threading.Barrier(2, timeout=2.0)

        def mock_summary(text, **kwargs):
            barrier.wait()
            return "## 📌 Summary\nTest summary"

        def mock_claims(segments, text):
            barrier.wait()
            return {"claims": [], "dropped_unresolved": 0, "error": None}

        with patch.object(vault_exporter, "MEETINGS_DIR", self.meetings_dir), \
             patch.object(vault_exporter, "ARTIFACTS_DIR", self.meetings_dir / ".meetcap"), \
             patch.object(vault_exporter, "generate_summary", side_effect=mock_summary), \
             patch.object(vault_exporter, "extract_claims", side_effect=mock_claims), \
             patch.object(vault_exporter, "generate_task_suggestions", return_value="## 🧩 Tasks"), \
             patch.object(vault_exporter, "EXPORT_QA_ENABLED", False), \
             patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", False):
            result = vault_exporter.export_note(self.transcript_file, "Test Note")
            self.assertTrue(result["success"])

    def test_task_suggestions_waits_for_summary_completion(self):
        """task_suggestions must strictly start AFTER summary finishes."""
        events = []

        def mock_summary(text, **kwargs):
            time.sleep(0.05)
            events.append("summary_done")
            return "## 📌 Summary\nTest summary"

        def mock_claims(segments, text):
            events.append("claims_start")
            return {"claims": [], "dropped_unresolved": 0, "error": None}

        def mock_tasks(*args, **kwargs):
            events.append("tasks_start")
            return "## 🧩 Tasks"

        with patch.object(vault_exporter, "MEETINGS_DIR", self.meetings_dir), \
             patch.object(vault_exporter, "ARTIFACTS_DIR", self.meetings_dir / ".meetcap"), \
             patch.object(vault_exporter, "generate_summary", side_effect=mock_summary), \
             patch.object(vault_exporter, "extract_claims", side_effect=mock_claims), \
             patch.object(vault_exporter, "generate_task_suggestions", side_effect=mock_tasks), \
             patch.object(vault_exporter, "EXPORT_QA_ENABLED", False), \
             patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", False):
            result = vault_exporter.export_note(self.transcript_file, "Test Note")
            self.assertTrue(result["success"])

        self.assertIn("summary_done", events)
        self.assertIn("tasks_start", events)
        self.assertLess(events.index("summary_done"), events.index("tasks_start"))

    def test_claims_failure_maintains_graceful_degradation(self):
        """If claims extraction raises an unexpected error, export_note handles it gracefully."""
        def failing_claims(segments, text):
            raise RuntimeError("Claims service crash")

        with patch.object(vault_exporter, "MEETINGS_DIR", self.meetings_dir), \
             patch.object(vault_exporter, "ARTIFACTS_DIR", self.meetings_dir / ".meetcap"), \
             patch.object(vault_exporter, "generate_summary", return_value="## 📌 Summary\nValid"), \
             patch.object(vault_exporter, "extract_claims", side_effect=failing_claims), \
             patch.object(vault_exporter, "generate_task_suggestions", return_value="## 🧩 Tasks"), \
             patch.object(vault_exporter, "EXPORT_QA_ENABLED", False), \
             patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", False):
            result = vault_exporter.export_note(self.transcript_file, "Test Note")
            # Note is still exported, outcome is degraded, stages['claims']['ok'] is False
            self.assertEqual(result["outcome"], "degraded")
            self.assertFalse(result["stages"]["claims"]["ok"])
            self.assertIn("Claims service crash", result["stages"]["claims"]["error"])

    def test_summary_failure_maintains_graceful_degradation(self):
        """If summary raises an unexpected error, export_note handles it gracefully."""
        def failing_summary(text, **kwargs):
            raise RuntimeError("Summary service crash")

        with patch.object(vault_exporter, "MEETINGS_DIR", self.meetings_dir), \
             patch.object(vault_exporter, "ARTIFACTS_DIR", self.meetings_dir / ".meetcap"), \
             patch.object(vault_exporter, "generate_summary", side_effect=failing_summary), \
             patch.object(vault_exporter, "extract_claims", return_value={"claims": [], "dropped_unresolved": 0, "error": None}), \
             patch.object(vault_exporter, "generate_task_suggestions", return_value="## 🧩 Tasks"), \
             patch.object(vault_exporter, "EXPORT_QA_ENABLED", False), \
             patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", False):
            result = vault_exporter.export_note(self.transcript_file, "Test Note")
            # Summary failure flips outcome to failed per contract #38
            self.assertFalse(result["success"])
            self.assertEqual(result["outcome"], "failed")
            self.assertFalse(result["stages"]["summary"]["ok"])


if __name__ == "__main__":
    unittest.main()
