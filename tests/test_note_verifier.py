import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exporter import note_verifier
from exporter.note_verifier import (
    build_verification_artifact,
    normalize_verification_payload,
    render_qa_block,
    render_verification_md,
    verify_export,
)

SEGMENTS = [
    {"index": 0, "start": "00:02", "end": "00:04", "text": "we agreed to ship the new payment rail by friday"},
    {"index": 1, "start": "00:12", "end": "00:14", "text": "the compliance review blocked the previous launch"},
    {"index": 2, "start": "00:31", "end": "00:33", "text": "maria will own the client follow-up this week"},
]

CLEAN_PAYLOAD = '{"coverage_score": 0.9, "decision_gaps": [], "action_item_gaps": [], "speaker_attribution_risks": [], "unsupported_claims": [], "recommended_note_additions": []}'

RISKY_PAYLOAD = (
    '{"coverage_score": 0.4, '
    '"decision_gaps": [{"item": "budget owner not captured", "timestamps": ["00:12"]}], '
    '"action_item_gaps": ["deadline for maria missing"], '
    '"speaker_attribution_risks": [], '
    '"unsupported_claims": ["roi figure has no source"], '
    '"recommended_note_additions": ["add budget owner"]}'
)


class ItemToTextTests(unittest.TestCase):
    def test_plain_string_normalized(self):
        self.assertEqual(note_verifier._item_to_text("  spaced   text  "), "spaced text")

    def test_dict_item_with_timestamps(self):
        text = note_verifier._item_to_text(
            {"item": "gap", "timestamps": ["00:12", "00:40", "01:00", "02:00"]}
        )
        self.assertEqual(text, "gap (at 00:12, 00:40, 01:00)")

    def test_dict_description_fallback(self):
        self.assertEqual(note_verifier._item_to_text({"description": "desc"}), "desc")

    def test_other_types_empty(self):
        self.assertEqual(note_verifier._item_to_text(42), "")


class NormalizePayloadTests(unittest.TestCase):
    def test_clamps_coverage(self):
        self.assertEqual(normalize_verification_payload({"coverage_score": 1.5})["coverage_score"], 1.0)
        self.assertEqual(normalize_verification_payload({"coverage_score": -0.2})["coverage_score"], 0.0)

    def test_invalid_coverage_is_none(self):
        self.assertIsNone(normalize_verification_payload({"coverage_score": "high"})["coverage_score"])
        self.assertIsNone(normalize_verification_payload({})["coverage_score"])

    def test_caps_flag_lists(self):
        payload = {"decision_gaps": [f"gap {i}" for i in range(10)]}
        normalized = normalize_verification_payload(payload)
        self.assertEqual(len(normalized["decision_gaps"]), note_verifier.QA_MAX_ITEMS)

    def test_non_list_becomes_empty(self):
        normalized = normalize_verification_payload({"decision_gaps": "oops"})
        self.assertEqual(normalized["decision_gaps"], [])

    def test_non_dict_payload(self):
        normalized = normalize_verification_payload("junk")
        self.assertIsNone(normalized["coverage_score"])
        self.assertEqual(normalized["unsupported_claims"], [])


class VerifyExportTests(unittest.TestCase):
    def test_no_segments_sets_error(self):
        result = verify_export([], "note", "tasks")
        self.assertIn("segments", result["error"])
        self.assertFalse(result["needs_human_review"])

    def test_clean_verification(self):
        with patch.object(note_verifier, "call_openrouter", return_value=CLEAN_PAYLOAD):
            result = verify_export(SEGMENTS, "note", "tasks", context={"title": "T"})
        self.assertIsNone(result["error"])
        self.assertEqual(result["coverage_score"], 0.9)
        self.assertFalse(result["needs_human_review"])

    def test_risky_verification_needs_review(self):
        with patch.object(note_verifier, "call_openrouter", return_value=RISKY_PAYLOAD):
            result = verify_export(SEGMENTS, "note", "tasks")
        self.assertTrue(result["needs_human_review"])
        self.assertEqual(result["decision_gaps"], ["budget owner not captured (at 00:12)"])
        self.assertEqual(len(result["unsupported_claims"]), 1)

    def test_action_gaps_alone_do_not_trigger_review(self):
        payload = (
            '{"coverage_score": 0.9, "decision_gaps": [], '
            '"action_item_gaps": ["missing deadline"], '
            '"speaker_attribution_risks": [], "unsupported_claims": [], '
            '"recommended_note_additions": []}'
        )
        with patch.object(note_verifier, "call_openrouter", return_value=payload):
            result = verify_export(SEGMENTS, "note", "tasks")
        self.assertFalse(result["needs_human_review"])
        self.assertEqual(result["action_item_gaps"], ["missing deadline"])

    def test_llm_failure_is_nonfatal(self):
        with patch.object(
            note_verifier, "call_openrouter", side_effect=RuntimeError("no API key configured")
        ):
            result = verify_export(SEGMENTS, "note", "tasks")
        self.assertIn("no API key", result["error"])
        self.assertIsNone(result["coverage_score"])
        self.assertFalse(result["needs_human_review"])

    def test_malformed_json_repaired(self):
        fenced = f"```json\n{RISKY_PAYLOAD}\n```"
        with patch.object(note_verifier, "call_openrouter", return_value=fenced):
            result = verify_export(SEGMENTS, "note", "tasks")
        self.assertIsNone(result["error"])
        self.assertTrue(result["needs_human_review"])


class RenderQaBlockTests(unittest.TestCase):
    def test_clean_renders_empty(self):
        verification = {
            "coverage_score": 0.95, "needs_human_review": False, "error": None,
            "decision_gaps": [], "action_item_gaps": [],
            "speaker_attribution_risks": [], "unsupported_claims": [],
        }
        self.assertEqual(render_qa_block(verification), "")

    def test_error_renders_warning(self):
        block = render_qa_block({"coverage_score": None, "needs_human_review": False, "error": "boom"})
        self.assertIn("[!warning]", block)
        self.assertIn("boom", block)

    def test_risky_renders_flags(self):
        verification = {
            "coverage_score": 0.42, "needs_human_review": True, "error": None,
            "decision_gaps": ["g1"], "action_item_gaps": [],
            "speaker_attribution_risks": ["s1"], "unsupported_claims": [],
        }
        block = render_qa_block(verification)
        self.assertIn("## 🚩 QA Flags", block)
        self.assertIn("Coverage score: 0.42", block)
        self.assertIn("Missing decisions: 1", block)
        self.assertIn("Needs review: yes", block)


class RenderVerificationMdTests(unittest.TestCase):
    def test_report_sections(self):
        verification = {
            "coverage_score": 0.5, "needs_human_review": True, "error": None,
            "decision_gaps": ["gap"], "action_item_gaps": [],
            "speaker_attribution_risks": [], "unsupported_claims": ["u"],
            "recommended_note_additions": ["add"],
        }
        report = render_verification_md(verification)
        self.assertIn("# QA Verification Report", report)
        self.assertIn("## Decision gaps", report)
        self.assertIn("- gap", report)
        self.assertIn("## Unsupported claims", report)
        self.assertIn("- None", report)


class ArtifactTests(unittest.TestCase):
    def test_artifact_shape(self):
        artifact = build_verification_artifact(
            {"coverage_score": 0.8, "needs_human_review": False, "error": None},
            meeting_date="2026-08-14",
            transcript_file="m.wav",
        )
        self.assertEqual(artifact["schema"], "meetcap.verification/1")
        self.assertEqual(artifact["transcript_file"], "m.wav")
        self.assertEqual(artifact["coverage_score"], 0.8)


if __name__ == "__main__":
    unittest.main()
