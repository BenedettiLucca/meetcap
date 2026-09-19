import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exporter import claim_extractor
from exporter.claim_extractor import (
    build_evidence_artifact,
    extract_claims,
    normalize_claims_payload,
    quote_head,
    render_claims_block,
    resolve_claim_timestamps,
)

SEGMENTS = [
    {"index": 0, "start": "00:02", "end": "00:04", "text": "we agreed to ship the new payment rail by friday"},
    {"index": 1, "start": "00:12", "end": "00:14", "text": "the compliance review blocked the previous launch"},
    {"index": 2, "start": "00:31", "end": "00:33", "text": "maria will own the client follow-up this week"},
]

CLAIMS_JSON = (
    '{"claims": ['
    '{"claim": "Ship payment rail by Friday", "why_it_matters": "hard deadline", '
    '"quote_excerpt": "ship the new payment rail by friday", "confidence": "high"}, '
    '{"claim": "Compliance blocked previous launch", "why_it_matters": "risk context", '
    '"quote_excerpt": "compliance review blocked the previous launch", "confidence": "medium"}, '
    '{"claim": "Maria owns client follow-up", "why_it_matters": "ownership", '
    '"quote_excerpt": "maria will own the client follow-up", "confidence": "high"}'
    "]}"
)


class QuoteHeadTests(unittest.TestCase):
    def test_takes_first_words(self):
        self.assertEqual(quote_head("one two three four", 2), "one two")


class ResolveTimestampsTests(unittest.TestCase):
    def test_exact_substring_match(self):
        stamps = resolve_claim_timestamps("ship the new payment rail by friday", SEGMENTS)
        self.assertEqual(stamps, ["00:02"])

    def test_head_words_match(self):
        stamps = resolve_claim_timestamps("compliance review blocked something new", SEGMENTS)
        self.assertEqual(stamps, ["00:12"])

    def test_unresolvable_quote(self):
        stamps = resolve_claim_timestamps("totally unrelated gibberish words here", SEGMENTS)
        self.assertEqual(stamps, [])

    def test_multiple_matches_capped_and_ordered(self):
        repeated = SEGMENTS + [
            {"index": 3, "start": "01:00", "end": "01:02", "text": "maria will own the client follow-up later"},
            {"index": 4, "start": "00:50", "end": "00:52", "text": "maria will own the client follow-up again"},
        ]
        stamps = resolve_claim_timestamps("maria will own the client follow-up", repeated)
        self.assertEqual(len(stamps), 3)
        self.assertEqual(stamps[0], "00:31")

    def test_empty_inputs(self):
        self.assertEqual(resolve_claim_timestamps("", SEGMENTS), [])
        self.assertEqual(resolve_claim_timestamps("some quote", []), [])

    def test_fuzzy_fallback_not_returned_as_verified_timestamp(self):
        stamps = resolve_claim_timestamps("we agreed shipping payment rail by friday", SEGMENTS)
        self.assertEqual(stamps, [])


class NormalizePayloadTests(unittest.TestCase):
    def test_valid_payload_preserved(self):
        claims = normalize_claims_payload({"claims": [
            {"claim": "A", "quote_excerpt": "a b", "why_it_matters": "w", "confidence": "high"},
        ]})
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["confidence"], "high")

    def test_drops_claims_without_quote(self):
        claims = normalize_claims_payload({"claims": [
            {"claim": "A", "quote_excerpt": ""},
            {"claim": "B", "quote_excerpt": "ok quote"},
        ]})
        self.assertEqual([c["claim"] for c in claims], ["B"])

    def test_clamps_to_max(self):
        payload = {"claims": [
            {"claim": f"c{i}", "quote_excerpt": f"q {i}"} for i in range(10)
        ]}
        self.assertEqual(len(normalize_claims_payload(payload)), claim_extractor.CLAIMS_MAX)

    def test_invalid_confidence_defaults(self):
        claims = normalize_claims_payload({"claims": [
            {"claim": "A", "quote_excerpt": "x", "confidence": "certain"},
        ]})
        self.assertEqual(claims[0]["confidence"], "medium")

    def test_tolerates_bare_list(self):
        claims = normalize_claims_payload([{"claim": "A", "quote_excerpt": "x"}])
        self.assertEqual(len(claims), 1)


class ExtractClaimsTests(unittest.TestCase):
    def test_success_flow(self):
        with patch.object(claim_extractor, "call_openrouter", return_value=CLAIMS_JSON):
            result = extract_claims(SEGMENTS, "transcript text")
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["claims"]), 3)
        self.assertEqual(result["claims"][0]["timestamps"], ["00:02"])
        self.assertEqual(result["dropped_unresolved"], 0)

    def test_drops_unresolvable_claims(self):
        payload = '{"claims": [{"claim": "Ghost", "quote_excerpt": "not in transcript at all"}, ' \
                  '{"claim": "Real", "quote_excerpt": "ship the new payment rail by friday"}]}'
        with patch.object(claim_extractor, "call_openrouter", return_value=payload):
            result = extract_claims(SEGMENTS, "transcript text")
        self.assertEqual(len(result["claims"]), 1)
        self.assertEqual(result["dropped_unresolved"], 1)

    def test_llm_failure_is_nonfatal(self):
        with patch.object(
            claim_extractor, "call_openrouter", side_effect=RuntimeError("no API key configured")
        ):
            result = extract_claims(SEGMENTS, "transcript text")
        self.assertEqual(result["claims"], [])
        self.assertIn("no API key", result["error"])

    def test_no_segments_short_circuits(self):
        result = extract_claims([], "transcript text")
        self.assertEqual(result["claims"], [])
        self.assertIn("segments", result["error"])

    def test_repair_path_on_malformed_json(self):
        fenced = f"```json\n{CLAIMS_JSON}\n```"
        with patch.object(claim_extractor, "call_openrouter", return_value=fenced):
            result = extract_claims(SEGMENTS, "transcript text")
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["claims"]), 3)

    def test_grounding_levels_exact_and_fuzzy(self):
        payload = (
            '{"claims": ['
            '{"claim": "Ship rail", "quote_excerpt": "ship the new payment rail by friday"}, '
            '{"claim": "Ship rail paraphrased", "quote_excerpt": "we agreed shipping payment rail by friday"}'
            ']}'
        )
        with patch.object(claim_extractor, "call_openrouter", return_value=payload):
            result = extract_claims(SEGMENTS, "transcript text")
        self.assertEqual(len(result["claims"]), 2)
        exact_claim = result["claims"][0]
        fuzzy_claim = result["claims"][1]
        self.assertEqual(exact_claim["match_method"], "exact")
        self.assertEqual(exact_claim["timestamps"], ["00:02"])
        self.assertEqual(fuzzy_claim["match_method"], "fuzzy")
        self.assertEqual(fuzzy_claim["timestamps"], ["00:02"])


class RenderClaimsBlockTests(unittest.TestCase):
    def test_renders_claims_with_timestamps(self):
        result = {
            "claims": [{
                "claim": "Ship it", "why_it_matters": "deadline",
                "timestamps": ["00:02"], "confidence": "high", "speakers": None,
            }],
            "dropped_unresolved": 0, "error": None,
        }
        block = render_claims_block(result)
        self.assertIn("## 🔎 Claims & Evidence", block)
        self.assertIn("**Claim:** Ship it", block)
        self.assertIn("Evidence: 00:02", block)

    def test_renders_warning_on_error(self):
        block = render_claims_block({"claims": [], "dropped_unresolved": 0, "error": "boom"})
        self.assertIn("[!warning]", block)
        self.assertIn("boom", block)

    def test_renders_placeholder_when_empty(self):
        block = render_claims_block({"claims": [], "dropped_unresolved": 0, "error": None})
        self.assertIn("No evidence-backed claims", block)

    def test_fuzzy_quote_never_presents_verbatim_verified_timestamp(self):
        result = {
            "claims": [{
                "claim": "Ship it",
                "why_it_matters": "deadline",
                "timestamps": ["00:02"],
                "match_method": "fuzzy",
                "confidence": "high",
                "speakers": None,
            }],
            "dropped_unresolved": 0,
            "error": None,
        }
        block = render_claims_block(result)
        self.assertIn("## 🔎 Claims & Evidence", block)
        self.assertNotIn("Evidence: 00:02\n", block)
        self.assertIn("Evidence: 00:02 (fuzzy, unverified)", block)


class EvidenceArtifactTests(unittest.TestCase):
    def test_artifact_shape(self):
        claims_result = {"claims": [], "dropped_unresolved": 2, "error": None}
        artifact = build_evidence_artifact(
            claims_result, meeting_date="2026-08-14", transcript_file="m.wav", model="m"
        )
        self.assertEqual(artifact["schema"], "meetcap.evidence/1")
        self.assertEqual(artifact["meeting_date"], "2026-08-14")
        self.assertEqual(artifact["dropped_unresolved"], 2)

    def test_evidence_artifact_records_match_method(self):
        claims_result = {
            "claims": [
                {"claim": "Exact", "timestamps": ["00:02"], "match_method": "exact"},
                {"claim": "Fuzzy", "timestamps": ["00:02"], "match_method": "fuzzy"},
            ],
            "dropped_unresolved": 0,
            "error": None,
        }
        artifact = build_evidence_artifact(
            claims_result, meeting_date="2026-08-14", transcript_file="m.wav", model="m"
        )
        self.assertEqual(artifact["claims"][0]["match_method"], "exact")
        self.assertEqual(artifact["claims"][1]["match_method"], "fuzzy")


if __name__ == "__main__":
    unittest.main()
