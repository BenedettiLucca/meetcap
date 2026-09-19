"""#17: export_note commit protocol — stage → validate → atomic os.replace.

Guards: no truncated/partial artifacts on failure, JSON validated before
commit, no .tmp/.partial litter on success, optional (QA/manifest) artifact
failures degrade instead of killing success, and disabled features stay out
of the stages contract (#38).
"""
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

VERIFICATION_STUB = {
    "coverage_score": 0.91, "needs_human_review": False, "error": None,
    "decision_gaps": [], "action_item_gaps": [],
    "speaker_attribution_risks": [], "unsupported_claims": [],
    "recommended_note_additions": [],
}

MANIFEST_STUB = {
    "schema": "meetcap.room-manifest/1",
    "meeting": {"title": "Optional Failure", "date": "2026-08-14", "participants": [], "canonical_note_path": "x.md"},
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


class AtomicExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meetings = Path(self.tmp.name) / "Meetings"

    def _make_patches(self, *, qa=False, manifest=False):
        patches = [
            patch.object(vault_exporter, "MEETINGS_DIR", self.meetings),
            patch.object(vault_exporter, "ARTIFACTS_DIR", self.meetings / ".meetcap"),
            patch.object(vault_exporter, "generate_summary", return_value="## 📌 Summary\ncontent"),
            patch.object(vault_exporter, "generate_task_suggestions", return_value="## 🧩 Tasks\n- [ ] item"),
            patch.object(vault_exporter, "extract_claims", return_value=dict(CLAIMS_RESULT)),
            patch.object(vault_exporter, "load_vocabulary", return_value={"canonical": [], "aliases": {}}),
            patch.object(vault_exporter, "resolve_derived_surfaces", side_effect=lambda text, vocab: (text, [])),
            patch.object(vault_exporter, "EXPORT_QA_ENABLED", qa),
            patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", manifest),
        ]
        if qa:
            patches.append(patch.object(vault_exporter, "verify_export", return_value=dict(VERIFICATION_STUB)))
        if manifest:
            patches.append(patch.object(vault_exporter, "build_room_manifest", return_value=dict(MANIFEST_STUB)))
        return patches

    def _run(self, title, *extra_patches, qa=False, manifest=False):
        patches = self._make_patches(qa=qa, manifest=manifest)
        patches.extend(extra_patches)
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(TRANSCRIPT)
            transcript = Path(handle.name)
        self.addCleanup(transcript.unlink)
        return vault_exporter.export_note(transcript, title)

    def _artifact_dir(self, title):
        return self.meetings / ".meetcap" / title

    def test_note_write_failure_leaves_no_partial_note(self):
        """#17: a failed note write must not leave a .partial staging file behind."""
        original_write_text = Path.write_text

        def failing_note_write(self, *args, **kwargs):
            result = original_write_text(self, *args, **kwargs)
            if self.name.endswith(".partial"):
                raise OSError("simulated disk full after write")
            return result

        patches = self._make_patches()
        patches.append(patch.object(Path, "write_text", failing_note_write))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(TRANSCRIPT)
            transcript = Path(handle.name)
        self.addCleanup(transcript.unlink)
        result = vault_exporter.export_note(transcript, "Note Failure")
        self.assertFalse(result["success"])
        self.assertEqual(result["outcome"], "failed")
        self.assertFalse((self.meetings / "Note Failure.md").exists())
        leftovers = [p for p in self.meetings.glob("*") if p.name.endswith((".partial", ".tmp"))]
        self.assertEqual(leftovers, [])

    def test_required_sidecar_failure_does_not_yield_success(self):
        """#17: a sidecar commit failure must keep success=false and name the missing artifact."""
        original_write_text = Path.write_text

        def failing_tmp_write(self, *args, **kwargs):
            if self.name.endswith(".tmp"):
                raise OSError("simulated disk full")
            return original_write_text(self, *args, **kwargs)

        result = self._run("Sidecar Failure", patch.object(Path, "write_text", failing_tmp_write))
        self.assertFalse(result["success"])
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["artifacts"], [])
        self.assertTrue(
            any("evidence.json" in e for e in result["errors"]),
            result["errors"],
        )
        self.assertEqual(list(self._artifact_dir("Sidecar Failure").glob("*.tmp")), [])

    def test_optional_sidecar_failure_degrades_without_killing_success(self):
        """#17: a failed optional (QA) artifact must surface in stages/errors with the
        missing artifact name, while success stays true."""
        original_write_text = Path.write_text

        def failing_qa_write(self, *args, **kwargs):
            if self.name.endswith("verification.json.tmp"):
                raise OSError("simulated disk full")
            return original_write_text(self, *args, **kwargs)

        result = self._run(
            "Optional Failure",
            patch.object(Path, "write_text", failing_qa_write),
            qa=True,
            manifest=True,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["outcome"], "degraded")
        self.assertFalse(result["stages"]["artifacts"]["ok"])
        self.assertTrue(
            any("verification.json" in e for e in result["errors"]),
            result["errors"],
        )
        names = [Path(p).name for p in result["artifacts"]]
        self.assertIn("evidence.json", names)
        self.assertIn("verification.md", names)
        self.assertIn("room_manifest.json", names)
        self.assertNotIn("verification.json", names)

    def test_invalid_json_payload_is_rejected_before_commit(self):
        """#17: JSON payloads must be validated before the commit step; a bad payload
        leaves no evidence.json and no .tmp litter."""
        result = self._run(
            "Bad JSON",
            patch.object(
                vault_exporter,
                "build_evidence_artifact",
                return_value={"claims": object()},
            ),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["artifacts"], [])
        self.assertTrue(
            any("JSON validation failed" in e for e in result["errors"]),
            result["errors"],
        )
        artifact_dir = self._artifact_dir("Bad JSON")
        self.assertFalse((artifact_dir / "evidence.json").exists())
        self.assertEqual(list(artifact_dir.glob("*.tmp")), [])

    def test_success_leaves_no_staging_litter(self):
        """#17: a successful export must not leave .tmp/.partial staging files behind."""
        result = self._run("Clean", qa=True, manifest=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["outcome"], "ok")
        self.assertEqual(list(self.meetings.glob("*.partial")), [])
        artifact_dir = self._artifact_dir("Clean")
        self.assertEqual(list(artifact_dir.glob("*.tmp")), [])
        for name in [Path(p).name for p in result["artifacts"]]:
            self.assertTrue((artifact_dir / name).exists())
            if name.endswith(".json"):
                json.loads((artifact_dir / name).read_text(encoding="utf-8"))

    def test_disabled_features_stay_out_of_stages_and_artifacts(self):
        """#17/#38: EXPORT_*_ENABLED=False must not add qa/manifest stages or artifacts."""
        result = self._run("Disabled")
        self.assertTrue(result["success"])
        self.assertEqual(result["outcome"], "ok")
        self.assertNotIn("qa", result["stages"])
        self.assertNotIn("manifest", result["stages"])
        names = [Path(p).name for p in result["artifacts"]]
        self.assertNotIn("verification.json", names)
        self.assertNotIn("room_manifest.json", names)


if __name__ == "__main__":
    unittest.main()
