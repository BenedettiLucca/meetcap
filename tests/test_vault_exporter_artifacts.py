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
    "dropped_unresolved": 1,
    "error": None,
}

CORRECTIONS = [{
    "surface": "TechKeyon",
    "canonical": "Project Tachyon",
    "confidence": 1.0,
    "rule": "alias",
    "auto_applied": True,
}]


class ExportNoteArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meetings = Path(self.tmp.name) / "Meetings"

    def _export(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(TRANSCRIPT)
            transcript = Path(handle.name)
        self.addCleanup(transcript.unlink)

        patches = [
            patch.object(vault_exporter, "MEETINGS_DIR", self.meetings),
            patch.object(vault_exporter, "ARTIFACTS_DIR", self.meetings / ".meetcap"),
            patch.object(vault_exporter, "generate_summary", return_value="## 📌 Summary\nTechKeyon content"),
            patch.object(vault_exporter, "generate_task_suggestions", return_value="## 🧩 Sugestões\n- [ ] item"),
            patch.object(vault_exporter, "extract_claims", return_value=dict(CLAIMS_RESULT)),
            patch.object(
                vault_exporter,
                "resolve_derived_surfaces",
                side_effect=lambda text, vocab: (
                    text.replace("TechKeyon", "Project Tachyon")
                    if "TechKeyon" in text else text,
                    list(CORRECTIONS) if "TechKeyon" in text else [],
                ),
            ),
            patch.object(vault_exporter, "load_vocabulary", return_value={"canonical": [], "aliases": {}}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return vault_exporter.export_note(transcript, "Alignment")

    def test_note_contains_claims_and_corrections_sections(self):
        result = self._export()
        self.assertTrue(result["success"])
        content = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("## 🔎 Claims & Evidence", content)
        self.assertIn("Evidence: 00:00", content)
        self.assertIn("## Name Corrections", content)
        self.assertIn("Project Tachyon", content)
        # section order: tasks before claims before transcript
        self.assertLess(content.index("## 🧩"), content.index("## 🔎 Claims"))
        self.assertLess(content.index("## 🔎 Claims"), content.index("## 📝 Transcrição Completa"))
        # raw transcript untouched
        self.assertIn("we agreed to ship the new payment rail by friday", content)

    def test_evidence_json_artifact_written(self):
        result = self._export()
        evidence_path = Path(result["artifacts"][0])
        self.assertTrue(evidence_path.exists())
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "meetcap.evidence/1")
        self.assertEqual(payload["claims"][0]["timestamps"], ["00:00"])
        self.assertEqual(payload["dropped_unresolved"], 1)
        self.assertIn(".meetcap", str(evidence_path))

    def test_corrections_json_artifact_written(self):
        result = self._export()
        corrections_path = Path(result["artifacts"][1])
        payload = json.loads(corrections_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "meetcap.corrections/1")
        self.assertEqual(payload["corrections"][0]["canonical"], "Project Tachyon")

    def test_result_counts(self):
        result = self._export()
        self.assertEqual(result["claims_verified"], 1)
        self.assertEqual(result["entity_corrections"], 1)
        self.assertEqual(len(result["artifacts"]), 2)


class ExportNoteDegradedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meetings = Path(self.tmp.name) / "Meetings"

    def test_claim_failure_degrades_to_warning_block(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(TRANSCRIPT)
            transcript = Path(handle.name)
        self.addCleanup(transcript.unlink)

        failing = {"claims": [], "dropped_unresolved": 0, "error": "claim extraction failed: no key"}
        patches = [
            patch.object(vault_exporter, "MEETINGS_DIR", self.meetings),
            patch.object(vault_exporter, "ARTIFACTS_DIR", self.meetings / ".meetcap"),
            patch.object(vault_exporter, "generate_summary", return_value="## 📌 Summary"),
            patch.object(vault_exporter, "generate_task_suggestions", return_value="## 🧩 Sugestões"),
            patch.object(vault_exporter, "extract_claims", return_value=failing),
            patch.object(vault_exporter, "load_vocabulary", return_value={"canonical": [], "aliases": {}}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        result = vault_exporter.export_note(transcript, "Degraded")
        self.assertTrue(result["success"])
        content = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("[!warning] Claim extraction failed", content)
        evidence = json.loads(Path(result["artifacts"][0]).read_text(encoding="utf-8"))
        self.assertEqual(evidence["claims"], [])
        self.assertIn("no key", evidence["error"])


class BuildNoteContentDefaultsTests(unittest.TestCase):
    def test_omitted_blocks_render_note_without_them(self):
        data = {
            "meta": {"model": "m", "language": "en", "file": "f.wav"},
            "meeting_date": "2026-08-14",
            "meeting_time": "10:00",
            "duration_str": "10 min",
            "transcript_text": "raw text",
            "line_count": 2,
        }
        content = vault_exporter.build_note_content(
            data=data, title="T", summary="S", task_suggestions="TS"
        )
        self.assertNotIn("Claims", content)
        self.assertNotIn("Name Corrections", content)
        self.assertIn("## 📝 Transcrição Completa", content)


if __name__ == "__main__":
    unittest.main()
