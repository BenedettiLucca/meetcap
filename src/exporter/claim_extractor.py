import json
import re
from difflib import SequenceMatcher
from typing import Any

from .config import (
    CLAIMS_MAX_TOKENS,
    CLAIMS_TEMPERATURE,
    CLAIMS_MAX,
    CLAIMS_TRANSCRIPT_MAX_CHARS,
    CLAIMS_QUOTE_HEAD_WORDS,
    CLAIMS_MAX_TIMESTAMPS,
    LLM_MODEL,
)
from .prompts import (
    CLAIM_EXTRACTION_SYSTEM_PROMPT,
    CLAIM_EXTRACTION_USER_PROMPT,
)
from .llm_client import call_openrouter, should_retry_without_structured_output
from .transcript_parser import truncate_text


def normalize_for_match(text: str) -> str:
    """Lowercase and strip punctuation for evidence matching."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def quote_head(text: str, words: int) -> str:
    """First N words of a quote, used as a stable matching anchor."""
    return " ".join(text.split()[:words])


def resolve_claim_grounding(
    quote: str,
    segments: list[dict[str, Any]],
    *,
    max_timestamps: int = CLAIMS_MAX_TIMESTAMPS,
) -> dict[str, Any]:
    """Resolve timestamps and grounding level for a quote against segments.

    Grounding levels:
    - 'exact': exact normalized quote or quote head (>= 3 words) substring match.
    - 'fuzzy': fallback SequenceMatcher ratio >= 0.6 against quote head.
    - None: unresolvable (dropped).
    """
    if not quote or not segments:
        return {"timestamps": [], "match_method": None}

    normalized_quote = normalize_for_match(quote)
    head_quote = normalize_for_match(quote_head(quote, CLAIMS_QUOTE_HEAD_WORDS))
    if not normalized_quote:
        return {"timestamps": [], "match_method": None}

    quote_words = normalized_quote.split()
    exact_matches: list[int] = []
    fuzzy_matches: list[tuple[int, float]] = []

    for segment in segments:
        segment_text = normalize_for_match(segment.get("text", ""))
        if not segment_text:
            continue
        is_exact = normalized_quote in segment_text
        if not is_exact and len(quote_words) >= 3:
            for n in range(min(len(quote_words), CLAIMS_QUOTE_HEAD_WORDS), 2, -1):
                if " ".join(quote_words[:n]) in segment_text:
                    is_exact = True
                    break

        if is_exact:
            exact_matches.append(segment["index"])
        else:
            ratio = SequenceMatcher(None, head_quote, segment_text).ratio()
            if ratio >= 0.6:
                fuzzy_matches.append((segment["index"], ratio))

    if exact_matches:
        matched_indexes = exact_matches[:max_timestamps]
        matched_indexes.sort()
        return {
            "timestamps": [segments[index]["start"] for index in matched_indexes],
            "match_method": "exact",
        }

    if fuzzy_matches:
        fuzzy_matches.sort(key=lambda item: (-item[1], item[0]))
        matched_indexes = [index for index, _ in fuzzy_matches[:max_timestamps]]
        matched_indexes.sort()
        return {
            "timestamps": [segments[index]["start"] for index in matched_indexes],
            "match_method": "fuzzy",
        }

    return {"timestamps": [], "match_method": None}


def resolve_claim_timestamps(
    quote: str,
    segments: list[dict[str, Any]],
    *,
    max_timestamps: int = CLAIMS_MAX_TIMESTAMPS,
) -> list[str]:
    """Locate the transcript segment timestamps that back a verbatim quote.

    Only exact/substring matches produce verified timestamps; fallback fuzzy
    matches (ratio >= 0.6) are not verified timestamps (#23).
    """
    grounding = resolve_claim_grounding(quote, segments, max_timestamps=max_timestamps)
    if grounding["match_method"] == "exact":
        return grounding["timestamps"]
    return []


def normalize_claims_payload(payload: Any) -> list[dict[str, str]]:
    """Validate and clamp a raw claims payload to well-formed claims."""
    if isinstance(payload, dict):
        claims_source = payload.get("claims", [])
    elif isinstance(payload, list):
        claims_source = payload
    else:
        claims_source = []

    claims: list[dict[str, str]] = []
    for item in claims_source:
        if not isinstance(item, dict):
            continue
        claim = " ".join(str(item.get("claim", "")).strip().split())
        quote = " ".join(str(item.get("quote_excerpt", "")).strip().split())
        if not claim or not quote:
            continue
        confidence = str(item.get("confidence", "medium")).strip().lower()
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"
        claims.append({
            "claim": claim,
            "why_it_matters": " ".join(str(item.get("why_it_matters", "")).strip().split()),
            "quote_excerpt": quote,
            "confidence": confidence,
        })
        if len(claims) >= CLAIMS_MAX:
            break

    return claims


def _request_claims(transcript_text: str) -> str:
    messages = [
        {"role": "system", "content": CLAIM_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": CLAIM_EXTRACTION_USER_PROMPT.format(
                transcript=truncate_text(
                    transcript_text,
                    max_chars=CLAIMS_TRANSCRIPT_MAX_CHARS,
                    head_chars=24000,
                    tail_chars=5000,
                )
            ),
        },
    ]
    try:
        return call_openrouter(
            messages=messages,
            max_tokens=CLAIMS_MAX_TOKENS,
            temperature=CLAIMS_TEMPERATURE,
            reasoning_effort="none",
            response_format={"type": "json_object"},
        )
    except RuntimeError as exc:
        if not should_retry_without_structured_output(exc):
            raise
        return call_openrouter(
            messages=messages,
            max_tokens=CLAIMS_MAX_TOKENS,
            temperature=CLAIMS_TEMPERATURE,
            reasoning_effort="none",
            response_format=None,
        )


def extract_claims(
    segments: list[dict[str, Any]],
    transcript_text: str,
) -> dict[str, Any]:
    """Extract 3-5 evidence-backed claims with real transcript timestamps.

    Returns {"claims": [...], "dropped_unresolved": N, "error": None}.
    Never raises: LLM failures surface as {"claims": [], "error": "..."}.
    """
    result: dict[str, Any] = {"claims": [], "dropped_unresolved": 0, "error": None}
    if not segments:
        result["error"] = "no timestamped segments in transcript"
        return result

    try:
        response_text = _request_claims(transcript_text)
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if not match:
                raise
            payload = json.loads(match.group(0))
    except Exception as exc:
        result["error"] = f"claim extraction failed: {exc}"
        return result

    verified: list[dict[str, Any]] = []
    dropped = 0
    for claim in normalize_claims_payload(payload):
        grounding = resolve_claim_grounding(claim["quote_excerpt"], segments)
        if not grounding["match_method"]:
            dropped += 1
            continue
        verified.append({
            **claim,
            "timestamps": grounding["timestamps"],
            "match_method": grounding["match_method"],
            "speakers": None,
            "speaker_status": "not_preserved",
        })

    result["claims"] = verified
    result["dropped_unresolved"] = dropped
    result["speakers"] = None
    result["speaker_status"] = "not_preserved"
    return result


def render_claims_block(claims_result: dict[str, Any]) -> str:
    """Render the '## 🔎 Claims & Evidence' note block (or a warning)."""
    if claims_result.get("error"):
        return (
            "## 🔎 Claims & Evidence\n\n"
            f"> [!warning] Claim extraction failed: {claims_result['error']}"
        )

    claims = claims_result.get("claims", [])
    lines = ["## 🔎 Claims & Evidence"]
    if not claims:
        lines.append("")
        lines.append("- No evidence-backed claims were verified for this meeting.")
        return "\n".join(lines)

    for claim in claims:
        timestamps = ", ".join(claim.get("timestamps", []))
        match_method = claim.get("match_method", "exact")
        if match_method == "fuzzy":
            evidence = f"{timestamps} (fuzzy, unverified)" if timestamps else "unverified"
        else:
            evidence = timestamps or "unverified"
        lines.extend([
            "",
            f"- **Claim:** {claim['claim']}",
            f"  - Why it matters: {claim.get('why_it_matters') or '—'}",
            f"  - Evidence: {evidence}",
            f"  - Confidence: {claim.get('confidence', 'medium')}",
        ])

    return "\n".join(lines)


def build_evidence_artifact(
    claims_result: dict[str, Any],
    *,
    meeting_date: str,
    transcript_file: str,
    model: str = LLM_MODEL,
) -> dict[str, Any]:
    """Build the evidence.json payload for downstream machine use."""
    return {
        "schema": "meetcap.evidence/1",
        "meeting_date": meeting_date,
        "transcript_file": transcript_file,
        "model": model,
        "claims": claims_result.get("claims", []),
        "dropped_unresolved": claims_result.get("dropped_unresolved", 0),
        "speakers": None,
        "speaker_status": "not_preserved",
        "error": claims_result.get("error"),
    }
