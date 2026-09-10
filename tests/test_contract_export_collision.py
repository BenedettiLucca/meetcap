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


class ExportCollisionContractTests(unittest.TestCase):
    """CT-5: export_note must not overwrite an existing note; it must generate a collision suffix."""

    def test_export_does_not_overwrite_existing_note(self):
        """Second export with the same title/data must produce a new path, not overwrite."""
        with tempfile.TemporaryDirectory() as tmp:
            meetings = Path(tmp) / "Meetings"
            artifacts = meetings / ".meetcap"

            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
                handle.write(TRANSCRIPT)
                transcript = Path(handle.name)
            self.addCleanup(transcript.unlink)

            patches = [
                patch.object(vault_exporter, "MEETINGS_DIR", meetings),
                patch.object(vault_exporter, "ARTIFACTS_DIR", artifacts),
                patch.object(vault_exporter, "generate_summary", return_value="## 📌 Summary\ncontent"),
                patch.object(vault_exporter, "generate_task_suggestions", return_value="## 🧩 Tasks\n- [ ] item"),
                patch.object(vault_exporter, "extract_claims", return_value={
                    "claims": [], "dropped_unresolved": 0, "error": None,
                }),
                patch.object(vault_exporter, "load_vocabulary", return_value={"canonical": [], "aliases": {}}),
                patch.object(vault_exporter, "resolve_derived_surfaces", side_effect=lambda text, vocab: (text, [])),
                patch.object(vault_exporter, "EXPORT_QA_ENABLED", False),
                patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", False),
            ]
            for p in patches:
                p.start()
                self.addCleanup(p.stop)

            # First export.
            first = vault_exporter.export_note(transcript, "Meeting — 2026-08-14 (10 min)")
            self.assertTrue(first["success"])
            first_path = Path(first["path"])
            self.assertTrue(first_path.exists())
            first_content = first_path.read_text(encoding="utf-8")

            # Second export with the same title must NOT overwrite the first.
            second = vault_exporter.export_note(transcript, "Meeting — 2026-08-14 (10 min)")
            second_path = Path(second["path"])

            self.assertTrue(
                second["success"],
                "contracts #12: second export reported failure; it should succeed with a new path",
            )
            self.assertNotEqual(
                str(second_path),
                str(first_path),
                "contracts #12: export_note overwrote the existing note instead of generating "
                "a collision-suffixed path (e.g. -a, -b); first path was {}, second was {}".format(
                    first_path, second_path
                ),
            )
            self.assertTrue(second_path.exists())
            # First note must remain intact.
            self.assertEqual(first_path.read_text(encoding="utf-8"), first_content)
            # Filenames should carry the collision suffix.
            self.assertIn("-a", second_path.name)


if __name__ == "__main__":
    unittest.main()
