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

    def test_action_gaps_alone_trigger_review(self):
        payload = (
            '{"coverage_score": 0.9, "decision_gaps": [], '
            '"action_item_gaps": ["missing deadline"], '
            '"speaker_attribution_risks": [], "unsupported_claims": [], '
            '"recommended_note_additions": []}'
        )
        with patch.object(note_verifier, "call_openrouter", return_value=payload):
            result = verify_export(SEGMENTS, "note", "tasks")
        self.assertTrue(result["needs_human_review"])
        self.assertEqual(result["action_item_gaps"], ["missing deadline"])
        block = render_qa_block(result)
        self.assertIn("## 🚩 QA Flags", block)
        self.assertIn("Action item gaps: 1", block)
        self.assertIn("Needs review: yes", block)

    def test_speaker_attribution_risks_do_not_trigger_review_and_marked_not_assessable(self):
        payload = (
            '{"coverage_score": 0.9, "decision_gaps": [], '
            '"action_item_gaps": [], '
            '"speaker_attribution_risks": [{"item": "maria proposal misattributed", "timestamps": ["00:31"]}], '
            '"unsupported_claims": [], '
            '"recommended_note_additions": []}'
        )
        with patch.object(note_verifier, "call_openrouter", return_value=payload):
            result = verify_export(SEGMENTS, "note", "tasks")
        self.assertFalse(result["needs_human_review"])
        self.assertEqual(result["speaker_attribution"], "not_assessable:no-diarization")
        self.assertEqual(len(result["speaker_attribution_risks"]), 1)
        self.assertIn("[not_assessable]", result["speaker_attribution_risks"][0])
        block = render_qa_block(result)
        self.assertEqual(block, "")


    def test_chunked_verification_audits_full_transcript_with_weighted_coverage_and_gap_union(self):
        segments = []
        for i in range(20):
            segments.append({
                "index": i,
                "start": f"{i:02d}:00",
                "end": f"{i:02d}:30",
                "text": f"segment content line {i} " + ("word " * 250),
            })
        chunk1_payload = (
            '{"coverage_score": 0.9, '
            '"decision_gaps": [{"item": "early budget gap", "timestamps": ["00:00"]}], '
            '"action_item_gaps": [], "speaker_attribution_risks": [], '
            '"unsupported_claims": [], "recommended_note_additions": []}'
        )
        chunk2_payload = (
            '{"coverage_score": 0.5, '
            '"decision_gaps": [{"item": "late architecture gap", "timestamps": ["15:00"]}], '
            '"action_item_gaps": ["late action item"], "speaker_attribution_risks": [], '
            '"unsupported_claims": [], "recommended_note_additions": []}'
        )
        with patch.object(note_verifier, "call_openrouter", side_effect=[chunk1_payload, chunk2_payload]) as mock_call:
            result = verify_export(segments, "note", "tasks")

        self.assertEqual(mock_call.call_count, 2)
        self.assertIsNone(result["error"])
        self.assertIsNotNone(result["coverage_score"])
        self.assertTrue(0.5 < result["coverage_score"] < 0.9)
        gap_items = " ".join(result["decision_gaps"])
        self.assertIn("early budget gap", gap_items)
        self.assertIn("late architecture gap", gap_items)
        self.assertIn("late action item", result["action_item_gaps"])
        self.assertTrue(result["needs_human_review"])

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

    def test_render_qa_block_explains_attribution_not_assessable_without_fabricating_risks(self):
        verification = {
            "coverage_score": 0.42, "needs_human_review": True, "error": None,
            "decision_gaps": ["g1"], "action_item_gaps": [],
            "speaker_attribution_risks": ["s1 [not_assessable]"],
            "speaker_attribution": "not_assessable:no-diarization",
            "unsupported_claims": [],
        }
        block = render_qa_block(verification)
        self.assertIn("## 🚩 QA Flags", block)
        self.assertNotIn("Attribution risks:", block)
        self.assertIn("- Speaker attribution: not assessable (no diarization)", block)



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


class TimestampParsingTests(unittest.TestCase):
    def test_mm_ss(self):
        self.assertEqual(note_verifier._parse_timestamp("00:12"), 12)
        self.assertEqual(note_verifier._parse_timestamp("73:10"), 73 * 60 + 10)

    def test_hh_mm_ss(self):
        self.assertEqual(note_verifier._parse_timestamp("00:00:42"), 42)
        self.assertEqual(note_verifier._parse_timestamp("1:02:03"), 3723)

    def test_invalid(self):
        self.assertIsNone(note_verifier._parse_timestamp("99:99"))
        self.assertIsNone(note_verifier._parse_timestamp("1:2:3:4"))
        self.assertIsNone(note_verifier._parse_timestamp("abc"))
        self.assertIsNone(note_verifier._parse_timestamp("00:60"))

    def test_whitespace_tolerated(self):
        self.assertEqual(note_verifier._parse_timestamp("  00:12 "), 12)


class GroundingTests(unittest.TestCase):
    def _normalize(self, payload):
        return note_verifier.normalize_verification_payload(payload, SEGMENTS)

    def test_valid_start_timestamp_grounded(self):
        normalized = self._normalize(
            {"decision_gaps": [{"item": "gap", "timestamps": ["00:12"]}]}
        )
        self.assertEqual(normalized["decision_gaps"], ["gap (at 00:12)"])
        record = normalized["evidence_grounding"][0]
        self.assertEqual(record["status"], "grounded")
        self.assertEqual(record["segment_ids"], [1])
        self.assertEqual(record["match_method"], "start")
        self.assertEqual(record["unresolved_timestamps"], [])

    def test_valid_interval_timestamp_grounded(self):
        normalized = self._normalize(
            {"decision_gaps": [{"item": "gap", "timestamps": ["00:03"]}]}
        )
        record = normalized["evidence_grounding"][0]
        self.assertEqual(record["status"], "grounded")
        self.assertEqual(record["segment_ids"], [0])
        self.assertEqual(record["match_method"], "interval")

    def test_out_of_duration_timestamp_unresolved(self):
        normalized = self._normalize(
            {"decision_gaps": [{"item": "gap", "timestamps": ["73:10"]}]}
        )
        self.assertEqual(normalized["decision_gaps"], ["gap [unresolved evidence]"])
        record = normalized["evidence_grounding"][0]
        self.assertEqual(record["status"], "unresolved")
        self.assertEqual(record["unresolved_timestamps"], ["73:10"])
        self.assertEqual(record["segment_ids"], [])

    def test_invented_timestamp_unresolved(self):
        normalized = self._normalize(
            {"decision_gaps": [{"item": "gap", "timestamps": ["99:99"]}]}
        )
        self.assertEqual(normalized["decision_gaps"], ["gap [unresolved evidence]"])
        record = normalized["evidence_grounding"][0]
        self.assertEqual(record["status"], "unresolved")
        self.assertEqual(record["unresolved_timestamps"], ["99:99"])

    def test_timestamp_in_gap_between_segments_unresolved(self):
        normalized = self._normalize(
            {"decision_gaps": [{"item": "gap", "timestamps": ["00:30"]}]}
        )
        self.assertEqual(normalized["decision_gaps"], ["gap [unresolved evidence]"])
        self.assertEqual(normalized["evidence_grounding"][0]["status"], "unresolved")

    def test_mixed_grounded_and_unresolved(self):
        normalized = self._normalize(
            {"decision_gaps": [{"item": "gap", "timestamps": ["00:12", "99:99"]}]}
        )
        self.assertEqual(
            normalized["decision_gaps"], ["gap (at 00:12) [unresolved evidence]"]
        )
        record = normalized["evidence_grounding"][0]
        self.assertEqual(record["status"], "unresolved")
        self.assertEqual(record["grounded_timestamps"], ["00:12"])
        self.assertEqual(record["unresolved_timestamps"], ["99:99"])
        self.assertEqual(record["segment_ids"], [1])

    def test_missing_timestamps_marked_unresolved(self):
        normalized = self._normalize(
            {"decision_gaps": [{"item": "gap"}], "unsupported_claims": ["plain"]}
        )
        self.assertEqual(normalized["decision_gaps"], ["gap [unresolved evidence]"])
        self.assertEqual(normalized["unsupported_claims"], ["plain"])
        self.assertEqual(len(normalized["evidence_grounding"]), 1)
        self.assertEqual(normalized["evidence_grounding"][0]["status"], "unresolved")
        self.assertEqual(normalized["evidence_grounding"][0]["section"], "decision_gaps")

    def test_string_items_get_no_grounding_record(self):
        normalized = self._normalize({"action_item_gaps": ["plain"]})
        self.assertEqual(normalized["action_item_gaps"], ["plain"])
        self.assertEqual(normalized["evidence_grounding"], [])

    def test_legacy_call_without_segments_preserved(self):
        normalized = note_verifier.normalize_verification_payload(
            {"decision_gaps": [{"item": "gap", "timestamps": ["00:12"]}]}
        )
        self.assertEqual(normalized["decision_gaps"], ["gap (at 00:12)"])
        self.assertEqual(normalized["evidence_grounding"], [])

    def test_speaker_attribution_items_grounded_as_not_assessable(self):
        normalized = self._normalize(
            {"speaker_attribution_risks": [{"item": "risk", "timestamps": ["00:12"]}]}
        )
        self.assertEqual(normalized["speaker_attribution_risks"], ["risk (at 00:12) [not_assessable]"])
        self.assertEqual(normalized["speaker_attribution"], "not_assessable:no-diarization")
        record = normalized["evidence_grounding"][0]
        self.assertEqual(record["status"], "not_assessable")
        self.assertEqual(record["section"], "speaker_attribution_risks")

    def test_speaker_attribution_legacy_call_without_segments_marked_not_assessable(self):
        normalized = note_verifier.normalize_verification_payload(
            {"speaker_attribution_risks": ["plain risk"]}
        )
        self.assertEqual(normalized["speaker_attribution_risks"], ["plain risk [not_assessable]"])
        self.assertEqual(normalized["speaker_attribution"], "not_assessable:no-diarization")



class GroundedVerifyExportTests(unittest.TestCase):
    def _export(self, payload):
        with patch.object(note_verifier, "call_openrouter", return_value=payload):
            return verify_export(SEGMENTS, "note", "tasks")

    def test_invented_timestamp_never_rendered_as_evidence(self):
        payload = (
            '{"coverage_score": 0.4, '
            '"decision_gaps": [{"item": "budget owner", "timestamps": ["99:99"]}], '
            '"action_item_gaps": [], "speaker_attribution_risks": [], '
            '"unsupported_claims": [], "recommended_note_additions": []}'
        )
        result = self._export(payload)
        self.assertEqual(result["decision_gaps"], ["budget owner [unresolved evidence]"])
        self.assertNotIn("(at 99:99)", result["decision_gaps"][0])
        md = render_verification_md(result)
        self.assertNotIn("(at 99:99)", md)
        self.assertIn("unresolved", md)

    def test_valid_timestamp_grounded_in_artifact(self):
        payload = (
            '{"coverage_score": 0.4, '
            '"decision_gaps": [{"item": "budget owner", "timestamps": ["00:12"]}], '
            '"action_item_gaps": [], "speaker_attribution_risks": [], '
            '"unsupported_claims": [], "recommended_note_additions": []}'
        )
        result = self._export(payload)
        self.assertEqual(result["decision_gaps"], ["budget owner (at 00:12)"])
        record = result["evidence_grounding"][0]
        self.assertEqual(record["status"], "grounded")
        self.assertEqual(record["segment_ids"], [1])
        self.assertEqual(record["match_method"], "start")

    def test_out_of_duration_timestamp_marked_unresolved(self):
        payload = (
            '{"coverage_score": 0.4, '
            '"decision_gaps": [{"item": "budget owner", "timestamps": ["73:10"]}], '
            '"action_item_gaps": [], "speaker_attribution_risks": [], '
            '"unsupported_claims": [], "recommended_note_additions": []}'
        )
        result = self._export(payload)
        self.assertEqual(result["decision_gaps"], ["budget owner [unresolved evidence]"])
        record = result["evidence_grounding"][0]
        self.assertEqual(record["status"], "unresolved")
        self.assertEqual(record["unresolved_timestamps"], ["73:10"])

    def test_no_segments_result_has_grounding_key(self):
        result = verify_export([], "note", "tasks")
        self.assertIn("evidence_grounding", result)


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

    def test_grounding_flowed_into_artifact(self):
        grounding = [
            {"section": "decision_gaps", "item": "gap", "status": "unresolved",
             "grounded_timestamps": [], "unresolved_timestamps": ["99:99"],
             "segment_ids": [], "match_method": None},
        ]
        artifact = build_verification_artifact(
            {"coverage_score": 0.4, "needs_human_review": True, "error": None,
             "evidence_grounding": grounding},
            meeting_date="2026-08-14",
            transcript_file="m.wav",
        )
        self.assertEqual(artifact["evidence_grounding"], grounding)

    def test_artifact_records_speaker_attribution_status(self):
        artifact = build_verification_artifact(
            {
                "coverage_score": 0.8,
                "needs_human_review": False,
                "error": None,
                "speaker_attribution": "not_assessable:no-diarization",
            },
            meeting_date="2026-08-14",
            transcript_file="m.wav",
        )
        self.assertEqual(artifact["speaker_attribution"], "not_assessable:no-diarization")



if __name__ == "__main__":
    unittest.main()
