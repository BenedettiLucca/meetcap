import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exporter import vault_exporter


TRANSCRIPT = """# Meetcap Transcript
Date: 2026-08-14 10:00
File: meeting-2026-08-14_10-00-00.wav
Language: en (99.0%)
Duration: 600.0s

---

[00:00 → 00:02] we agreed to ship the new payment rail by friday
[00:02 → 00:04] compliance review blocked the previous launch attempt
[00:04 → 00:06] maria will own the client follow-up this week
"""

CLAIMS_RESULT = {
    "claims": [{
        "claim": "Ship payment rail by Friday",
        "why_it_matters": "hard deadline",
        "quote_excerpt": "ship the new payment rail by friday",
        "confidence": "high",
        "timestamps": ["00:00"],
        "speakers": None,
    }],
    "dropped_unresolved": 0,
    "error": None,
}


class AtomicWriteContractTests(unittest.TestCase):
    """CT-6: export_note must use atomic writes (.partial + os.replace) and report partial failure."""

    def _make_patches(self, meetings):
        return [
            patch.object(vault_exporter, "MEETINGS_DIR", meetings),
            patch.object(vault_exporter, "ARTIFACTS_DIR", meetings / ".meetcap"),
            patch.object(vault_exporter, "generate_summary", return_value="## 📌 Summary\ncontent"),
            patch.object(vault_exporter, "generate_task_suggestions", return_value="## 🧩 Tasks\n- [ ] item"),
            patch.object(vault_exporter, "extract_claims", return_value=dict(CLAIMS_RESULT)),
            patch.object(vault_exporter, "load_vocabulary", return_value={"canonical": [], "aliases": {}}),
            patch.object(vault_exporter, "resolve_derived_surfaces", side_effect=lambda text, vocab: (text, [])),
            patch.object(vault_exporter, "EXPORT_QA_ENABLED", False),
            patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", False),
        ]

    def test_uses_partial_file_for_atomic_note_write(self):
        """The note file must be written via a .partial temp file + os.replace, not direct write."""
        with tempfile.TemporaryDirectory() as tmp:
            meetings = Path(tmp) / "Meetings"

            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
                handle.write(TRANSCRIPT)
                transcript = Path(handle.name)
            self.addCleanup(transcript.unlink)

            replace_calls = []
            original_replace = Path.replace

            def capturing_replace(self, dst, **kwargs):
                replace_calls.append(str(self))
                return original_replace(self, dst, **kwargs)

            patches = self._make_patches(meetings)
            patches.append(patch.object(Path, "replace", capturing_replace))
            for p in patches:
                p.start()
                self.addCleanup(p.stop)

            result = vault_exporter.export_note(transcript, "Alignment")
            self.assertTrue(result["success"])
            self.assertTrue(
                any(".partial" in call for call in replace_calls),
                "contracts #17: export_note did not write the note via a .partial file + os.replace; "
                "atomic writes are required to prevent truncated notes on interruption",
            )

    def test_partial_failure_reports_non_success_with_only_written_artifacts(self):
        """If an artifact write fails, result must not claim success with a full artifacts list."""
        with tempfile.TemporaryDirectory() as tmp:
            meetings = Path(tmp) / "Meetings"

            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
                handle.write(TRANSCRIPT)
                transcript = Path(handle.name)
            self.addCleanup(transcript.unlink)

            write_count = {"n": 0}

            def failing_write_text(self, *args, **kwargs):
                write_count["n"] += 1
                if write_count["n"] >= 2:
                    raise OSError("simulated disk full")
                return type("", (), {"n": write_count["n"]})()

            original_write_text = Path.write_text

            def patched_write_text(self, *args, **kwargs):
                write_count["n"] += 1
                if write_count["n"] >= 2:
                    raise OSError("simulated disk full")
                return original_write_text(self, *args, **kwargs)

            patches = self._make_patches(meetings)
            patches.append(patch.object(Path, "write_text", patched_write_text))
            for p in patches:
                p.start()
                self.addCleanup(p.stop)

            result = vault_exporter.export_note(transcript, "Partial Failure")

            self.assertFalse(
                result.get("success"),
                "contracts #17: export_note reported success=True after an artifact write failed; "
                "it must report success=False (or a partial indicator) when artifacts are incomplete",
            )
            # Artifacts list must only contain what actually exists.
            existing = [a for a in result.get("artifacts", []) if Path(a).exists()]
            self.assertEqual(
                result.get("artifacts", []),
                existing,
                "contracts #17: artifacts list contains paths that were not actually written; "
                "it must only include artifacts that exist on disk",
            )


if __name__ == "__main__":
    unittest.main()
