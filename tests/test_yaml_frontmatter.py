"""Tests for YAML frontmatter escaping and safe_name (#40, #51)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml

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


def _extract_frontmatter(content: str) -> dict:
    """Extract and parse YAML frontmatter between the opening and closing ---."""
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError("No YAML frontmatter found in content")
    return yaml.safe_load(parts[1])


class YamlFrontmatterEscapingTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "meeting_date": "2026-08-14",
            "meeting_time": "10:00",
            "duration_str": "10 min",
            "line_count": 3,
            "transcript_text": "line 1\nline 2",
            "meta": {
                "file": "test.wav",
                "model": "large-v3-turbo (cuda/float16)",
                "language": "pt (99.9%)",
            },
        }

    def test_title_with_quotes_and_colons_and_backslashes(self):
        title = 'Review: "Phase 1" & \\Launch\\ [Draft]'
        content = vault_exporter.build_note_content(
            data=self.data,
            title=title,
            summary="Summary",
            task_suggestions="Tasks",
        )
        fm = _extract_frontmatter(content)
        self.assertEqual(fm["title"], title)

    def test_title_with_newlines_escaped(self):
        title = "Title with \n newline and \r carriage return"
        content = vault_exporter.build_note_content(
            data=self.data,
            title=title,
            summary="Summary",
            task_suggestions="Tasks",
        )
        fm = _extract_frontmatter(content)
        self.assertEqual(fm["title"], title)

    def test_meta_fields_with_special_characters_escaped(self):
        self.data["meta"]["model"] = 'model "special": [v1.0] {fast}'
        self.data["meta"]["language"] = 'en: "US" (100%)'
        self.data["duration_str"] = '10 "min": approx'
        content = vault_exporter.build_note_content(
            data=self.data,
            title="Clean Title",
            summary="Summary",
            task_suggestions="Tasks",
        )
        fm = _extract_frontmatter(content)
        self.assertEqual(fm["model"], 'model "special": [v1.0] {fast}')
        self.assertEqual(fm["language"], 'en: "US" (100%)')
        self.assertEqual(fm["duration"], '10 "min": approx')
        self.assertEqual(fm["time"], "10:00")


class SafeNameTests(unittest.TestCase):
    def test_safe_name_replaces_slash_and_colon(self):
        self.assertEqual(
            vault_exporter.safe_name("Meeting / 2026:08:14"),
            "Meeting - 2026-08-14",
        )

    def test_safe_name_strips_edge_whitespace_and_dots(self):
        self.assertEqual(
            vault_exporter.safe_name("  ...Meeting Name...  "),
            "Meeting Name",
        )

    def test_safe_name_removes_control_chars_and_newlines(self):
        cleaned = vault_exporter.safe_name("Meeting\n\rTitle\t\x00with\x1fcontrols\x7f")
        self.assertEqual(cleaned, "Meeting Title with controls")

    def test_safe_name_collapses_whitespace(self):
        self.assertEqual(
            vault_exporter.safe_name("Meeting    with   lots    of    spaces"),
            "Meeting with lots of spaces",
        )

    def test_safe_name_limits_length_to_approx_100_chars(self):
        long_title = "A" * 150
        result = vault_exporter.safe_name(long_title)
        self.assertLessEqual(len(result), 100)
        self.assertEqual(result, "A" * 100)

    def test_safe_name_truncation_strips_trailing_dots_and_spaces(self):
        title = "A" * 98 + " . B"
        result = vault_exporter.safe_name(title)
        self.assertLessEqual(len(result), 100)
        self.assertFalse(result.endswith("."))
        self.assertFalse(result.endswith(" "))

    def test_safe_name_fallback_for_empty_input(self):
        self.assertEqual(vault_exporter.safe_name("   ....   "), "untitled")
        self.assertEqual(vault_exporter.safe_name(""), "untitled")


class FrontmatterHealthAndProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "meeting_date": "2026-08-14",
            "meeting_time": "10:00",
            "duration_str": "10 min",
            "line_count": 3,
            "transcript_text": "line 1\nline 2",
            "meta": {
                "file": "test.wav",
                "model": "large-v3-turbo",
                "language": "pt",
            },
        }

    def test_default_health_and_provenance_fields(self):
        content = vault_exporter.build_note_content(
            data=self.data,
            title="Meeting Note",
            summary="Summary",
            task_suggestions="Tasks",
        )
        fm = _extract_frontmatter(content)
        self.assertIs(fm["qa_needs_review"], False)
        self.assertIsNone(fm["qa_coverage_score"])
        self.assertEqual(fm["outcome"], "ok")
        self.assertEqual(fm["qa_audited"], "post-entity-resolution")
        self.assertEqual(fm["entity_corrections_count"], 0)

    def test_custom_health_and_provenance_fields(self):
        content = vault_exporter.build_note_content(
            data=self.data,
            title="Meeting Note",
            summary="Summary",
            task_suggestions="Tasks",
            qa_needs_review=True,
            qa_coverage_score=0.78,
            outcome="degraded",
            qa_audited="post-entity-resolution",
            entity_corrections_count=3,
        )
        fm = _extract_frontmatter(content)
        self.assertIs(fm["qa_needs_review"], True)
        self.assertEqual(fm["qa_coverage_score"], 0.78)
        self.assertEqual(fm["outcome"], "degraded")
        self.assertEqual(fm["qa_audited"], "post-entity-resolution")
        self.assertEqual(fm["entity_corrections_count"], 3)


class ExportNoteFrontmatterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meetings = Path(self.tmp.name) / "Meetings"

    def _export(
        self,
        *,
        title="Alignment",
        summary="## 📌 Summary\nTechKeyon content",
        tasks="## 🧩 Tasks\n- [ ] task",
        corrections=None,
        verification=None,
        qa=False,
    ):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(TRANSCRIPT)
            transcript = Path(handle.name)
        self.addCleanup(transcript.unlink)

        resolved_corrections = corrections if corrections is not None else []
        qa_stub = verification or {
            "coverage_score": 0.91,
            "needs_human_review": False,
            "error": None,
        }

        patches = [
            patch.object(vault_exporter, "MEETINGS_DIR", self.meetings),
            patch.object(vault_exporter, "ARTIFACTS_DIR", self.meetings / ".meetcap"),
            patch.object(vault_exporter, "generate_summary", return_value=summary),
            patch.object(vault_exporter, "generate_task_suggestions", return_value=tasks),
            patch.object(vault_exporter, "extract_claims", return_value=dict(CLAIMS_RESULT)),
            patch.object(
                vault_exporter,
                "resolve_derived_surfaces",
                side_effect=lambda text, vocab: (
                    text,
                    list(resolved_corrections) if "TechKeyon" in text else [],
                ),
            ),
            patch.object(vault_exporter, "load_vocabulary", return_value={"canonical": [], "aliases": {}}),
            patch.object(vault_exporter, "EXPORT_QA_ENABLED", qa),
            patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", False),
        ]
        if qa:
            patches.append(patch.object(vault_exporter, "verify_export", return_value=qa_stub))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        return vault_exporter.export_note(transcript, title)

    def test_export_note_populates_frontmatter_healthy(self):
        result = self._export(qa=True)
        self.assertTrue(result["success"])
        note_content = Path(result["path"]).read_text(encoding="utf-8")
        fm = _extract_frontmatter(note_content)
        self.assertEqual(fm["outcome"], "ok")
        self.assertIs(fm["qa_needs_review"], False)
        self.assertEqual(fm["qa_coverage_score"], 0.91)
        self.assertEqual(fm["qa_audited"], "post-entity-resolution")
        self.assertEqual(fm["entity_corrections_count"], 0)

    def test_export_note_populates_frontmatter_with_corrections_and_qa_review(self):
        corrections = [{
            "surface": "TechKeyon",
            "canonical": "Project Tachyon",
            "confidence": 1.0,
            "rule": "alias",
            "auto_applied": True,
        }]
        verification = {
            "coverage_score": 0.45,
            "needs_human_review": True,
            "error": None,
        }
        result = self._export(
            corrections=corrections,
            verification=verification,
            qa=True,
        )
        self.assertTrue(result["success"])
        note_content = Path(result["path"]).read_text(encoding="utf-8")
        fm = _extract_frontmatter(note_content)
        self.assertEqual(fm["outcome"], "ok")
        self.assertIs(fm["qa_needs_review"], True)
        self.assertEqual(fm["qa_coverage_score"], 0.45)
        self.assertEqual(fm["qa_audited"], "post-entity-resolution")
        self.assertEqual(fm["entity_corrections_count"], 1)

    def test_export_note_populates_frontmatter_degraded_outcome(self):
        result = self._export(
            tasks="> [!warning] Task suggestion generation failed: timeout",
        )
        self.assertEqual(result["outcome"], "degraded")
        note_content = Path(result["path"]).read_text(encoding="utf-8")
        fm = _extract_frontmatter(note_content)
        self.assertEqual(fm["outcome"], "degraded")

    def test_export_note_collision_preserves_uniqueness_with_robust_name(self):
        result1 = self._export(title="Review: Special")
        result2 = self._export(title="Review: Special")
        self.assertTrue(result1["success"])
        self.assertTrue(result2["success"])
        self.assertEqual(Path(result1["path"]).name, "Review- Special.md")
        self.assertEqual(Path(result2["path"]).name, "Review- Special-a.md")
        self.assertTrue(Path(result1["path"]).exists())
        self.assertTrue(Path(result2["path"]).exists())


if __name__ == "__main__":
    unittest.main()
