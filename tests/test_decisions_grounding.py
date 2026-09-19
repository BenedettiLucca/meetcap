import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exporter import room_manifest, vault_exporter
from exporter.room_manifest import (
    build_room_manifest,
    ground_decision,
    ground_decisions,
    render_decisions_block,
)

SEGMENTS = [
    {"index": 0, "start": "00:02", "end": "00:05", "text": "we decided to adopt the new payment rail"},
    {"index": 1, "start": "00:12", "end": "00:15", "text": "who owns the migration plan still open"},
    {"index": 2, "start": "00:20", "end": "00:25", "text": "we agreed to freeze hiring until Q4"},
]

GOOD_VERIFICATION = {
    "coverage_score": 0.9,
    "needs_human_review": False,
    "error": None,
    "decision_gaps": [],
    "action_item_gaps": [],
    "speaker_attribution_risks": [],
    "unsupported_claims": [],
    "recommended_note_additions": [],
}

CLAIMS_RESULT = {
    "claims": [
        {"claim": "Adopt payment rail", "timestamps": ["00:02"], "confidence": "high"},
    ],
    "dropped_unresolved": 0,
    "error": None,
}


class DecisionGroundingLogicTests(unittest.TestCase):
    def test_decision_with_valid_timestamp_is_verified(self):
        # Timestamp matching segment 0 start
        decision = {"text": "adopt payment rail", "timestamps": ["00:02"]}
        grounded = ground_decision(decision, SEGMENTS)
        self.assertEqual(grounded["status"], "verified")
        self.assertEqual(grounded["timestamps"], ["00:02"])
        self.assertEqual(grounded["segment_ids"], [0])
        self.assertEqual(grounded["text"], "adopt payment rail")

    def test_decision_with_interval_timestamp_in_string_is_verified(self):
        # 00:03 falls in [00:02, 00:05] of segment 0
        raw = "adopt the payment rail [00:03]"
        grounded = ground_decision(raw, SEGMENTS)
        self.assertEqual(grounded["status"], "verified")
        self.assertEqual(grounded["timestamps"], ["00:03"])
        self.assertEqual(grounded["segment_ids"], [0])
        self.assertEqual(grounded["text"], "adopt the payment rail")

    def test_decision_without_timestamp_present_in_transcript_is_candidate(self):
        # Present textually in segment 0
        raw = "adopt the payment rail"
        grounded = ground_decision(raw, SEGMENTS)
        self.assertEqual(grounded["status"], "candidate")
        self.assertEqual(grounded["timestamps"], [])
        self.assertEqual(grounded["segment_ids"], [])
        self.assertEqual(grounded["text"], "adopt the payment rail")

    def test_decision_with_unmatched_timestamp_but_present_textually_is_candidate(self):
        # Timestamp 99:99 does not exist in segments, but text matches segment 2
        raw = "freeze hiring until Q4 [99:99]"
        grounded = ground_decision(raw, SEGMENTS)
        self.assertEqual(grounded["status"], "candidate")
        self.assertEqual(grounded["timestamps"], [])
        self.assertEqual(grounded["text"], "freeze hiring until Q4")

    def test_decision_without_trace_is_unresolved(self):
        # Not in transcript, no timestamp
        raw = "migrate infrastructure to AWS"
        grounded = ground_decision(raw, SEGMENTS)
        self.assertEqual(grounded["status"], "unresolved")
        self.assertEqual(grounded["timestamps"], [])
        self.assertEqual(grounded["segment_ids"], [])
        self.assertEqual(grounded["text"], "migrate infrastructure to AWS")

    def test_decision_with_bad_timestamp_and_no_trace_is_unresolved_without_bogus_stamp(self):
        # Out of bounds timestamp and not in transcript
        raw = "migrate infrastructure to AWS [99:99]"
        grounded = ground_decision(raw, SEGMENTS)
        self.assertEqual(grounded["status"], "unresolved")
        self.assertEqual(grounded["timestamps"], [])
        self.assertEqual(grounded["text"], "migrate infrastructure to AWS")

    def test_ground_decisions_batch(self):
        items = [
            "adopt payment rail [00:02]",
            "who owns the migration plan",
            "completely fabricated item",
        ]
        results = ground_decisions(items, SEGMENTS)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["status"], "verified")
        self.assertEqual(results[1]["status"], "candidate")
        self.assertEqual(results[2]["status"], "unresolved")


class RenderDecisionsBlockTests(unittest.TestCase):
    def test_renders_empty_when_no_decisions(self):
        self.assertEqual(render_decisions_block([]), "")
        self.assertEqual(render_decisions_block({"decisions": []}), "")

    def test_renders_empty_when_all_decisions_unresolved(self):
        decisions = [
            {"text": "fake decision 1", "status": "unresolved", "timestamps": []},
            {"text": "fake decision 2", "status": "unresolved", "timestamps": []},
        ]
        self.assertEqual(render_decisions_block(decisions), "")

    def test_renders_block_when_at_least_one_verified(self):
        decisions = [
            {"text": "adopt payment rail", "status": "verified", "timestamps": ["00:02"]},
        ]
        block = render_decisions_block(decisions)
        self.assertIn("## Decisões", block)
        self.assertIn("- adopt payment rail (at 00:02)", block)

    def test_renders_block_when_at_least_one_candidate(self):
        decisions = [
            {"text": "freeze hiring", "status": "candidate", "timestamps": []},
        ]
        block = render_decisions_block(decisions)
        self.assertIn("## Decisões", block)
        self.assertIn("- freeze hiring", block)
        self.assertNotIn("[unverified]", block)

    def test_unresolved_rendered_with_unverified_marker_when_mixed(self):
        decisions = [
            {"text": "adopt payment rail", "status": "verified", "timestamps": ["00:02"]},
            {"text": "freeze hiring", "status": "candidate", "timestamps": []},
            {"text": "migrate to AWS", "status": "unresolved", "timestamps": []},
        ]
        block = render_decisions_block(decisions)
        self.assertIn("## Decisões", block)
        self.assertIn("- adopt payment rail (at 00:02)", block)
        self.assertIn("- freeze hiring", block)
        self.assertIn("- migrate to AWS [unverified]", block)

    def test_never_renders_unverified_timestamp(self):
        # Even if unverified item somehow had stamps or raw text had stamp, it must never show
        decisions = [
            {"text": "adopt payment rail", "status": "verified", "timestamps": ["00:02"]},
            {"text": "bad decision [99:99]", "status": "unresolved", "timestamps": []},
        ]
        block = render_decisions_block(decisions)
        self.assertNotIn("99:99", block)
        self.assertIn("- bad decision [unverified]", block)


class BuildRoomManifestDecisionsGroundingTests(unittest.TestCase):
    def test_build_room_manifest_grounds_decisions(self):
        classification = json.dumps({
            "authorityMix": "decision-heavy",
            "decisions": [
                "adopt payment rail [00:02]",
                "freeze hiring until Q4",
                "buy luxury yacht",
            ],
            "openQuestions": [],
            "suggestsClientFollowup": False,
        })
        with patch.object(room_manifest, "call_openrouter", return_value=classification):
            manifest = build_room_manifest(
                segments=SEGMENTS,
                summary="## Summary",
                task_suggestions="## Tasks",
                claims_result=CLAIMS_RESULT,
                verification=GOOD_VERIFICATION,
                meeting_date="2026-08-14",
                title="Alignment",
                note_path="/vault/Meetings/Alignment.md",
            )
        decisions = manifest["decisions"]
        self.assertEqual(len(decisions), 3)
        self.assertEqual(decisions[0]["status"], "verified")
        self.assertEqual(decisions[0]["timestamps"], ["00:02"])
        self.assertEqual(decisions[1]["status"], "candidate")
        self.assertEqual(decisions[2]["status"], "unresolved")


class VaultExporterDecisionsWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meetings = Path(self.tmp.name) / "Meetings"

    def test_build_note_content_includes_decisions_block(self):
        data = {
            "meta": {"model": "m", "language": "en", "file": "f.wav"},
            "meeting_date": "2026-08-14",
            "meeting_time": "10:00",
            "duration_str": "10 min",
            "transcript_text": "raw text",
            "line_count": 2,
        }
        decisions_block = "## Decisões\n\n- adopt payment rail (at 00:02)"
        content = vault_exporter.build_note_content(
            data=data,
            title="Meeting",
            summary="## Summary",
            task_suggestions="## Tasks",
            decisions_block=decisions_block,
        )
        self.assertIn("## Decisões", content)
        self.assertIn("- adopt payment rail (at 00:02)", content)
        # Order check: task suggestions before decisions before transcript
        self.assertLess(content.index("## Tasks"), content.index("## Decisões"))
        self.assertLess(content.index("## Decisões"), content.index("## 📝 Transcrição Completa"))

    def test_export_note_wires_decisions(self):
        transcript_content = """# Meetcap Transcript
Date: 2026-08-14 10:00
File: meeting-2026-08-14_10-00-00.wav
Language: en (99.0%)
Duration: 600.0s

---

[00:02 → 00:05] we decided to adopt the new payment rail
[00:20 → 00:25] we agreed to freeze hiring until Q4
"""
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(transcript_content)
            transcript_file = Path(handle.name)
        self.addCleanup(transcript_file.unlink)

        classification = json.dumps({
            "authorityMix": "decision-heavy",
            "decisions": [
                "adopt payment rail [00:02]",
                "freeze hiring until Q4",
            ],
            "openQuestions": [],
            "suggestsClientFollowup": False,
        })

        patches = [
            patch.object(vault_exporter, "MEETINGS_DIR", self.meetings),
            patch.object(vault_exporter, "ARTIFACTS_DIR", self.meetings / ".meetcap"),
            patch.object(vault_exporter, "generate_summary", return_value="## Summary"),
            patch.object(vault_exporter, "generate_task_suggestions", return_value="## Tasks"),
            patch.object(vault_exporter, "extract_claims", return_value=CLAIMS_RESULT),
            patch.object(vault_exporter, "load_vocabulary", return_value={"canonical": [], "aliases": {}}),
            patch.object(vault_exporter, "EXPORT_QA_ENABLED", False),
            patch.object(vault_exporter, "EXPORT_MANIFEST_ENABLED", True),
            patch.object(room_manifest, "call_openrouter", return_value=classification),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        result = vault_exporter.export_note(transcript_file, "Decisions Note")
        self.assertTrue(result["success"])
        content = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("## Decisões", content)
        self.assertIn("- adopt payment rail (at 00:02)", content)
        self.assertIn("- freeze hiring until Q4", content)


if __name__ == "__main__":
    unittest.main()
