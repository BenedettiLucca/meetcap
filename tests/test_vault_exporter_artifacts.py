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

VERIFICATION_STUB = {
    "coverage_score": 0.91, "needs_human_review": False, "error": None,
    "decision_gaps": [], "action_item_gaps": [],
    "speaker_attribution_risks": [], "unsupported_claims": [],
    "recommended_note_additions": [],
}

MANIFEST_STUB = {
    "schema": "meetcap.room-manifest/1",
    "meeting": {"title": "Alignment", "date": "2026-08-14", "participants": [], "canonical_note_path": "x.md"},
    "authorityMix": "decision-heavy",
    "freshness": "same-day",
    "decisions": ["adopt rail"],
    "openQuestions": ["owner?"],
    "actions": ["notify client"],
    "claims": [{"claim": "Adopt rail", "timestamps": ["00:00"]}],
    "missingProof": [],
    "downstreamLanes": ["daily-tasks", "wiki"],
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
            patch.object(vault_exporter, "EXPORT_QA_ENABLED", False),
            patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", False),
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
            patch.object(vault_exporter, "EXPORT_QA_ENABLED", False),
            patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", False),
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


class ExportOutcomeTests(unittest.TestCase):
    """#38: the result contract must expose per-stage degradation so a dead
    LLM can never surface to the user as an unqualified success."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meetings = Path(self.tmp.name) / "Meetings"

    def _export(self, *, summary, tasks, claims=None, qa=False, manifest=False, evidence_error=None):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(TRANSCRIPT)
            transcript = Path(handle.name)
        self.addCleanup(transcript.unlink)

        patches = [
            patch.object(vault_exporter, "MEETINGS_DIR", self.meetings),
            patch.object(vault_exporter, "ARTIFACTS_DIR", self.meetings / ".meetcap"),
            patch.object(vault_exporter, "generate_summary", return_value=summary),
            patch.object(vault_exporter, "generate_task_suggestions", return_value=tasks),
            patch.object(vault_exporter, "extract_claims", return_value=claims or dict(CLAIMS_RESULT)),
            patch.object(vault_exporter, "load_vocabulary", return_value={"canonical": [], "aliases": {}}),
            patch.object(vault_exporter, "EXPORT_QA_ENABLED", qa),
            patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", manifest),
        ]
        if evidence_error:
            patches.append(patch.object(
                vault_exporter, "build_evidence_artifact", side_effect=OSError(evidence_error)
            ))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return vault_exporter.export_note(transcript, "Outcome")

    def test_full_llm_failure_is_outcome_failed_but_graceful(self):
        result = self._export(
            summary="> [!warning] Summary generation failed: no API key configured",
            tasks="> [!warning] Task suggestion generation failed: no API key configured.",
        )
        self.assertFalse(result["success"])  # failed: rc!=0 even though note was written gracefully
        self.assertEqual(result["outcome"], "failed")
        self.assertFalse(result["stages"]["summary"]["ok"])
        self.assertIn("failed", result["stages"]["summary"]["error"])
        self.assertFalse(result["stages"]["tasks"]["ok"])

    def test_partial_degradation_is_degraded(self):
        result = self._export(
            summary="## 📌 Summary",
            tasks="> [!warning] Task suggestion generation failed: boom.",
        )
        self.assertEqual(result["outcome"], "degraded")
        self.assertTrue(result["stages"]["summary"]["ok"])
        self.assertFalse(result["stages"]["tasks"]["ok"])
        self.assertIn("boom", result["stages"]["tasks"]["error"])

    def test_claims_error_is_degraded(self):
        claims = {"claims": [], "dropped_unresolved": 0, "error": "claim extraction failed: no key"}
        result = self._export(summary="## 📌 Summary", tasks="## 🧩", claims=claims)
        self.assertEqual(result["outcome"], "degraded")
        self.assertIn("no key", result["stages"]["claims"]["error"])

    def test_clean_export_is_outcome_ok(self):
        result = self._export(summary="## 📌 Summary", tasks="## 🧩")
        self.assertEqual(result["outcome"], "ok")
        self.assertTrue(all(stage["ok"] for stage in result["stages"].values()))
        self.assertIn("summary", result["stages"])
        self.assertNotIn("qa", result["stages"])  # disabled feature stays out of the contract

    def test_artifact_failure_is_failed_despite_healthy_stages(self):
        result = self._export(summary="## 📌 Summary", tasks="## 🧩", evidence_error="disk full")
        self.assertEqual(result["outcome"], "failed")
        self.assertTrue(result["stages"]["summary"]["ok"])


class QaManifestWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meetings = Path(self.tmp.name) / "Meetings"

    def _export(self, verification, manifest):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(TRANSCRIPT)
            transcript = Path(handle.name)
        self.addCleanup(transcript.unlink)

        patches = [
            patch.object(vault_exporter, "MEETINGS_DIR", self.meetings),
            patch.object(vault_exporter, "ARTIFACTS_DIR", self.meetings / ".meetcap"),
            patch.object(vault_exporter, "generate_summary", return_value="## 📌 Summary"),
            patch.object(vault_exporter, "generate_task_suggestions", return_value="## 🧩 Sugestões\n- [ ] item"),
            patch.object(vault_exporter, "extract_claims", return_value=dict(CLAIMS_RESULT)),
            patch.object(vault_exporter, "load_vocabulary", return_value={"canonical": [], "aliases": {}}),
            patch.object(vault_exporter, "EXPORT_QA_ENABLED", True),
            patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", True),
            patch.object(vault_exporter, "verify_export", return_value=verification),
            patch.object(vault_exporter, "build_room_manifest", return_value=manifest),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return vault_exporter.export_note(transcript, "QA Wiring")

    def test_clean_export_omits_qa_block_but_writes_manifest(self):
        result = self._export(dict(VERIFICATION_STUB), dict(MANIFEST_STUB))
        self.assertTrue(result["success"])
        content = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("## 🗺️ Room Manifest", content)
        self.assertNotIn("## 🚩 QA Flags", content)
        # order: claims -> corrections(absent) -> qa(absent) -> manifest -> transcript
        self.assertLess(content.index("## 🔎 Claims"), content.index("## 🗺️ Room Manifest"))
        self.assertLess(content.index("## 🗺️ Room Manifest"), content.index("## 📝 Transcrição Completa"))
        self.assertEqual(result["downstream_lanes"], ["daily-tasks", "wiki"])
        self.assertFalse(result["qa_needs_review"])
        self.assertEqual(result["qa_coverage_score"], 0.91)

    def test_risky_export_renders_qa_flags_block(self):
        verification = dict(
            VERIFICATION_STUB, coverage_score=0.4, needs_human_review=True,
            decision_gaps=["budget owner missing"],
        )
        result = self._export(verification, dict(MANIFEST_STUB))
        content = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("## 🚩 QA Flags", content)
        self.assertIn("Coverage score: 0.40", content)
        self.assertTrue(result["qa_needs_review"])
        self.assertLess(content.index("## 🚩 QA Flags"), content.index("## 🗺️ Room Manifest"))

    def test_verification_and_manifest_artifacts_written(self):
        result = self._export(dict(VERIFICATION_STUB), dict(MANIFEST_STUB))
        names = [Path(p).name for p in result["artifacts"]]
        self.assertIn("evidence.json", names)
        self.assertIn("verification.json", names)
        self.assertIn("verification.md", names)
        self.assertIn("room_manifest.json", names)
        verification = json.loads(
            (Path(result["artifacts"][0]).parent / "verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(verification["schema"], "meetcap.verification/1")
        manifest = json.loads(
            (Path(result["artifacts"][0]).parent / "room_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema"], "meetcap.room-manifest/1")

    def test_degraded_verification_renders_warning_not_crash(self):
        verification = dict(
            VERIFICATION_STUB, coverage_score=None, error="verification failed: no key",
        )
        result = self._export(verification, dict(MANIFEST_STUB))
        content = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("[!warning] QA verification unavailable", content)
        self.assertIsNone(result["qa_coverage_score"])


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
