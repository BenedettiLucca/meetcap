import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exporter import room_manifest
from exporter.room_manifest import (
    build_room_manifest,
    compute_freshness,
    extract_actions,
    normalize_manifest_payload,
    render_manifest_block,
    suggest_downstream_lanes,
)

SEGMENTS = [
    {"index": 0, "start": "00:02", "end": "00:04", "text": "we decided to adopt the new payment rail"},
    {"index": 1, "start": "00:12", "end": "00:14", "text": "who owns the migration plan still open"},
]

CLAIMS_RESULT = {
    "claims": [
        {"claim": "Adopt payment rail", "timestamps": ["00:02"], "confidence": "high"},
        {"claim": "Migration owner open", "timestamps": ["00:12"], "confidence": "medium"},
    ],
    "dropped_unresolved": 1,
    "error": None,
}

CLASSIFICATION_JSON = (
    '{"authorityMix": "decision-heavy", '
    '"decisions": ["adopt the payment rail"], '
    '"openQuestions": ["who owns the migration"], '
    '"suggestsClientFollowup": true}'
)

GOOD_VERIFICATION = {
    "coverage_score": 0.9, "needs_human_review": False, "error": None,
    "decision_gaps": [], "action_item_gaps": [],
    "speaker_attribution_risks": [], "unsupported_claims": [],
    "recommended_note_additions": [],
}


def _manifest(verification, classification=CLASSIFICATION_JSON, segments=None, **overrides):
    kwargs = dict(
        segments=segments if segments is not None else SEGMENTS,
        summary="## Summary\n- [ ] schedule migration",
        task_suggestions="## Tasks\n- [ ] schedule migration\n- [ ] notify client",
        claims_result=CLAIMS_RESULT,
        verification=verification,
        meeting_date="2026-08-14",
        title="Alignment",
        note_path="/vault/Meetings/Alignment.md",
        today=date(2026, 8, 14),
    )
    kwargs.update(overrides)
    with patch.object(room_manifest, "call_openrouter", return_value=classification):
        return build_room_manifest(**kwargs)


class FreshnessTests(unittest.TestCase):
    def test_same_day(self):
        self.assertEqual(compute_freshness("2026-08-14", date(2026, 8, 14)), "same-day")

    def test_aging(self):
        self.assertEqual(compute_freshness("2026-08-12", date(2026, 8, 14)), "aging")

    def test_stale(self):
        self.assertEqual(compute_freshness("2026-07-20", date(2026, 8, 14)), "stale-follow-up")

    def test_unknown_on_bad_date(self):
        self.assertEqual(compute_freshness("not a date"), "unknown")

    def test_datetime_string_truncated(self):
        self.assertEqual(compute_freshness("2026-08-14 10:00:00", date(2026, 8, 14)), "same-day")


class ExtractActionsTests(unittest.TestCase):
    def test_extracts_and_dedupes(self):
        actions = extract_actions("a\n- [ ] Ship it\n", "b\n- [ ] ship it\n- [ ] Call client\n")
        self.assertEqual(actions, ["Ship it", "Call client"])

    def test_cap(self):
        text = "\n".join(f"- [ ] action {i}" for i in range(20))
        self.assertEqual(len(extract_actions(text)), room_manifest.MANIFEST_MAX_ITEMS * 2)

    def test_no_actions(self):
        self.assertEqual(extract_actions("plain text", "", None), [])


class LaneTests(unittest.TestCase):
    def test_low_confidence_no_decisions_routes_reference_only(self):
        lanes = suggest_downstream_lanes(
            decisions=[], actions=["a"], high_confidence_claims=2,
            client_followup=True, coverage_ok=False,
        )
        self.assertEqual(lanes, ["reference-only"])

    def test_full_routing(self):
        lanes = suggest_downstream_lanes(
            decisions=["d"], actions=["a"], high_confidence_claims=1,
            client_followup=True, coverage_ok=True,
        )
        self.assertEqual(lanes, ["daily-tasks", "wiki", "content", "client-followup"])

    def test_empty_falls_back_to_reference_only(self):
        lanes = suggest_downstream_lanes(
            decisions=[], actions=[], high_confidence_claims=0,
            client_followup=False, coverage_ok=True,
        )
        self.assertEqual(lanes, ["reference-only"])

    def test_low_coverage_appends_reference_only(self):
        lanes = suggest_downstream_lanes(
            decisions=["d"], actions=[], high_confidence_claims=0,
            client_followup=False, coverage_ok=False,
        )
        self.assertEqual(lanes, ["wiki", "reference-only"])


class NormalizePayloadTests(unittest.TestCase):
    def test_valid_payload(self):
        normalized = normalize_manifest_payload({
            "authorityMix": "decision-heavy",
            "decisions": ["d1"],
            "openQuestions": ["q1"],
            "suggestsClientFollowup": True,
        })
        self.assertEqual(normalized["authorityMix"], "decision-heavy")
        self.assertTrue(normalized["suggestsClientFollowup"])

    def test_invalid_authority_defaults_to_mixed(self):
        self.assertEqual(normalize_manifest_payload({"authorityMix": "chaos"})["authorityMix"], "mixed")

    def test_caps_lists(self):
        normalized = normalize_manifest_payload({"decisions": [f"d{i}" for i in range(20)]})
        self.assertEqual(len(normalized["decisions"]), room_manifest.MANIFEST_MAX_ITEMS)

    def test_non_bool_followup_coerced(self):
        self.assertFalse(normalize_manifest_payload({"suggestsClientFollowup": "no"})["suggestsClientFollowup"])


class BuildManifestTests(unittest.TestCase):
    def test_success_flow(self):
        manifest = _manifest(GOOD_VERIFICATION)
        self.assertEqual(manifest["schema"], "meetcap.room-manifest/1")
        self.assertEqual(manifest["meeting"]["title"], "Alignment")
        self.assertEqual(manifest["meeting"]["canonical_note_path"], "/vault/Meetings/Alignment.md")
        self.assertEqual(manifest["authorityMix"], "decision-heavy")
        self.assertEqual(manifest["freshness"], "same-day")
        self.assertEqual(manifest["actions"], ["schedule migration", "notify client"])
        self.assertEqual(manifest["claims"][0], {"claim": "Adopt payment rail", "timestamps": ["00:02"]})
        self.assertIsNone(manifest["error"])
        self.assertIn("daily-tasks", manifest["downstreamLanes"])
        self.assertIn("client-followup", manifest["downstreamLanes"])

    def test_missing_proof_includes_dropped_claims(self):
        manifest = _manifest(GOOD_VERIFICATION)
        self.assertTrue(
            any("1 claim(s)" in item for item in manifest["missingProof"])
        )

    def test_unsupported_claims_flow_into_missing_proof(self):
        verification = dict(GOOD_VERIFICATION, unsupported_claims=["roi figure unsourced"])
        manifest = _manifest(verification)
        self.assertIn("roi figure unsourced", manifest["missingProof"])

    def test_low_confidence_verification_routes_reference_only(self):
        verification = dict(
            GOOD_VERIFICATION,
            coverage_score=0.4,
            needs_human_review=True,
            unsupported_claims=["u"],
        )
        manifest = _manifest(verification)
        self.assertIn("reference-only", manifest["downstreamLanes"])

    def test_classification_failure_degrades(self):
        manifest = _manifest(GOOD_VERIFICATION, classification=None)
        # classification=None makes call_openrouter return None -> JSON parse fails -> error path
        self.assertIsNotNone(manifest["error"])
        self.assertEqual(manifest["decisions"], [])
        self.assertEqual(manifest["authorityMix"], "mixed")
        # lanes still computed mechanically: actions -> daily-tasks, high claims -> content
        self.assertEqual(manifest["downstreamLanes"], ["daily-tasks", "content"])

    def test_no_segments_sets_error(self):
        manifest = _manifest(GOOD_VERIFICATION, segments=[])
        self.assertIn("segments", manifest["error"])
        self.assertEqual(manifest["actions"], ["schedule migration", "notify client"])

    def test_participants_default_empty(self):
        manifest = _manifest(GOOD_VERIFICATION)
        self.assertEqual(manifest["meeting"]["participants"], [])


class RenderManifestBlockTests(unittest.TestCase):
    def test_renders_block(self):
        manifest = _manifest(GOOD_VERIFICATION)
        block = render_manifest_block(manifest)
        self.assertIn("## 🗺️ Room Manifest", block)
        self.assertIn("Authority mix: decision-heavy", block)
        self.assertIn("Freshness: same-day", block)
        self.assertIn("task carry-over", block)
        self.assertIn("Decisions: 1", block)

    def test_error_line(self):
        manifest = _manifest(GOOD_VERIFICATION, segments=[])
        block = render_manifest_block(manifest)
        self.assertIn("⚠️", block)


if __name__ == "__main__":
    unittest.main()
