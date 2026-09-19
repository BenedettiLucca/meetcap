import json
import re
from typing import Any

from .config import (
    QA_MAX_TOKENS,
    QA_TEMPERATURE,
    QA_TRANSCRIPT_MAX_CHARS,
    QA_NOTE_MAX_CHARS,
    QA_COVERAGE_THRESHOLD,
    QA_MAX_ITEMS,
)
from .prompts import (
    VERIFICATION_SYSTEM_PROMPT,
    VERIFICATION_USER_PROMPT,
)
from .llm_client import call_openrouter, should_retry_without_structured_output
from .transcript_parser import split_transcript_into_chunks, truncate_text

_FLAG_KEYS = (
    "decision_gaps",
    "action_item_gaps",
    "speaker_attribution_risks",
    "unsupported_claims",
)


def _parse_timestamp(value: Any) -> int | None:
    """Parse 'MM:SS' or 'HH:MM:SS' to seconds; None when malformed.

    'MM:SS' tolerates minutes >= 60 (e.g. '73:10' = 73m 10s); 'HH:MM:SS'
    requires minutes and seconds within 0-59.
    """
    parts = str(value).strip().split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        return None
    if len(parts) == 2:
        minutes, seconds = int(parts[0]), int(parts[1])
        if seconds > 59:
            return None
        return minutes * 60 + seconds
    hours, minutes, seconds = (int(part) for part in parts)
    if minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _item_to_text(item: Any) -> str:
    """Normalize a gap item (string or {item, timestamps}) to plain text."""
    if isinstance(item, str):
        return " ".join(item.split())
    if isinstance(item, dict):
        text = " ".join(str(item.get("item") or item.get("description") or "").split())
        stamps = item.get("timestamps") or []
        if isinstance(stamps, list):
            stamps = [str(s) for s in stamps if str(s).strip()][:3]
            if stamps and text:
                text = f"{text} (at {', '.join(stamps)})"
        return text
    return ""


def _ground_timestamps(stamps: list[str], segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Mechanically resolve timestamps against real segment intervals.

    A timestamp grounds when it falls inside [start, end] of a segment
    (match_method 'interval') or equals a segment start ('start').
    Malformed or out-of-duration timestamps are returned as unresolved.
    """
    intervals: list[tuple[int, int, Any]] = []
    for segment in segments:
        start = _parse_timestamp(segment.get("start"))
        end = _parse_timestamp(segment.get("end"))
        if start is not None and end is not None:
            intervals.append((start, end, segment.get("index")))

    grounded: list[str] = []
    unresolved: list[str] = []
    segment_ids: list[Any] = []
    match_method: str | None = None
    for raw in stamps:
        seconds = _parse_timestamp(raw)
        if seconds is None:
            unresolved.append(raw)
            continue
        matched = [seg_idx for seg_start, seg_end, seg_idx in intervals
                   if seg_start <= seconds <= seg_end]
        if not matched:
            unresolved.append(raw)
            continue
        if match_method is None:
            match_method = "start" if any(seg_start == seconds for seg_start, _, _ in intervals) else "interval"
        for seg_idx in matched:
            if seg_idx not in segment_ids:
                segment_ids.append(seg_idx)
        grounded.append(raw)
    return {
        "grounded_timestamps": grounded,
        "unresolved_timestamps": unresolved,
        "segment_ids": segment_ids,
        "match_method": match_method,
    }


def _grounded_item(item: Any, key: str, segments: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    """Render one QA flag item and record its mechanical evidence grounding.

    Grounded timestamps are rendered as '(at ...)' only when they resolve
    to real segments; an invalid, missing, or out-of-range timestamp is
    never presented as evidence (rendered with an explicit marker instead).
    """
    if not isinstance(item, dict):
        base_text = _item_to_text(item)
        if not base_text:
            return "", None
        if key == "speaker_attribution_risks":
            text = f"{base_text} [not_assessable]" if not base_text.endswith("[not_assessable]") else base_text
            record = {
                "section": key,
                "status": "not_assessable",
                "grounded_timestamps": [],
                "unresolved_timestamps": [],
                "segment_ids": [],
                "match_method": None,
            }
            return text, record
        return base_text, None

    text = " ".join(str(item.get("item") or item.get("description") or "").split())
    if not text:
        return "", None
    stamps = item.get("timestamps") or []
    if not isinstance(stamps, list):
        stamps = []
    stamps = [str(s) for s in stamps if str(s).strip()][:3]
    grounded = _ground_timestamps(stamps, segments)
    if grounded["grounded_timestamps"]:
        text = f"{text} (at {', '.join(grounded['grounded_timestamps'])})"

    if key == "speaker_attribution_risks":
        text = f"{text} [not_assessable]" if not text.endswith("[not_assessable]") else text
        record = {
            "section": key,
            "status": "not_assessable",
            "grounded_timestamps": grounded["grounded_timestamps"],
            "unresolved_timestamps": grounded["unresolved_timestamps"],
            "segment_ids": grounded["segment_ids"],
            "match_method": grounded["match_method"],
        }
        return text, record

    if grounded["unresolved_timestamps"] or not stamps:
        text = f"{text} [unresolved evidence]"
    record = {
        "section": key,
        "status": "grounded" if stamps and not grounded["unresolved_timestamps"] else "unresolved",
        "grounded_timestamps": grounded["grounded_timestamps"],
        "unresolved_timestamps": grounded["unresolved_timestamps"],
        "segment_ids": grounded["segment_ids"],
        "match_method": grounded["match_method"],
    }
    return text, record


def normalize_verification_payload(
    payload: Any,
    segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate and clamp a raw verification payload.

    With ``segments`` given, QA flag items are mechanically grounded
    against the real transcript intervals and an ``evidence_grounding``
    record list is produced; without it, legacy rendering is preserved.
    """
    source = payload if isinstance(payload, dict) else {}

    try:
        coverage = float(source.get("coverage_score"))
    except (TypeError, ValueError):
        coverage = None
    if coverage is not None:
        coverage = min(1.0, max(0.0, coverage))

    normalized: dict[str, Any] = {
        "coverage_score": coverage,
        "speaker_attribution": "not_assessable:no-diarization",
    }
    grounding_records: list[dict[str, Any]] = []
    for key in _FLAG_KEYS:
        raw = source.get(key, [])
        if not isinstance(raw, list):
            raw = []
        if segments is None:
            if key == "speaker_attribution_risks":
                items = [
                    f"{text} [not_assessable]" if not text.endswith("[not_assessable]") else text
                    for text in (_item_to_text(item) for item in raw)
                    if text
                ]
            else:
                items = [text for text in (_item_to_text(item) for item in raw) if text]
            normalized[key] = items[:QA_MAX_ITEMS]
            continue
        pairs = [_grounded_item(item, key, segments) for item in raw]
        kept = [pair for pair in pairs if pair[0]][:QA_MAX_ITEMS]
        normalized[key] = [text for text, _ in kept]
        grounding_records.extend(record for _, record in kept if record)

    additions_raw = source.get("recommended_note_additions", [])
    if not isinstance(additions_raw, list):
        additions_raw = []
    additions = [text for text in (_item_to_text(item) for item in additions_raw) if text]
    normalized["recommended_note_additions"] = additions[:QA_MAX_ITEMS]
    normalized["evidence_grounding"] = grounding_records

    return normalized


def _format_timestamps(segments: list[dict[str, Any]]) -> str:
    lines = []
    for segment in segments:
        lines.append(f"[{segment['start']} → {segment['end']}] {segment['text']}")
    return "\n".join(lines)


def _union_raw_items(payloads: list[dict[str, Any]], key: str) -> list[Any]:
    seen = set()
    result = []
    for payload in payloads:
        raw = payload.get(key, [])
        if not isinstance(raw, list):
            continue
        for item in raw:
            item_text = _item_to_text(item)
            if item_text and item_text not in seen:
                seen.add(item_text)
                result.append(item)
    return result


def _request_verification(
    transcript: list[dict[str, Any]] | str,
    exported_note: str,
    task_suggestions: str,
    context: dict[str, Any],
) -> str:
    transcript_text = (
        _format_timestamps(transcript)
        if isinstance(transcript, list)
        else str(transcript)
    )
    messages = [
        {"role": "system", "content": VERIFICATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": VERIFICATION_USER_PROMPT.format(
                transcript=truncate_text(
                    transcript_text,
                    max_chars=QA_TRANSCRIPT_MAX_CHARS,
                    head_chars=18000,
                    tail_chars=5000,
                ),
                note=truncate_text(
                    exported_note,
                    max_chars=QA_NOTE_MAX_CHARS,
                    head_chars=6000,
                    tail_chars=1500,
                ),
                tasks=truncate_text(
                    task_suggestions, max_chars=4000, head_chars=3000, tail_chars=800
                ),
                context=json.dumps(context, ensure_ascii=False, default=str),
            ),
        },
    ]
    try:
        return call_openrouter(
            messages=messages,
            max_tokens=QA_MAX_TOKENS,
            temperature=QA_TEMPERATURE,
            reasoning_effort="none",
            response_format={"type": "json_object"},
        )
    except RuntimeError as exc:
        if not should_retry_without_structured_output(exc):
            raise
        return call_openrouter(
            messages=messages,
            max_tokens=QA_MAX_TOKENS,
            temperature=QA_TEMPERATURE,
            reasoning_effort="none",
            response_format=None,
        )


def verify_export(
    segments: list[dict[str, Any]],
    exported_note: str,
    task_suggestions: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Advisory QA pass: judge the exported note against the transcript.

    Returns a verification dict; never raises and never rewrites the note.
    LLM failures surface as {"error": "...", "coverage_score": None}.
    """
    result: dict[str, Any] = {
        "coverage_score": None,
        "decision_gaps": [],
        "action_item_gaps": [],
        "speaker_attribution_risks": [],
        "unsupported_claims": [],
        "recommended_note_additions": [],
        "evidence_grounding": [],
        "speaker_attribution": "not_assessable:no-diarization",
        "needs_human_review": False,
        "error": None,
    }
    if not segments:
        result["error"] = "no timestamped segments in transcript"
        return result

    formatted_transcript = _format_timestamps(segments)
    chunks = split_transcript_into_chunks(formatted_transcript, max_chars=QA_TRANSCRIPT_MAX_CHARS)

    chunk_payloads: list[dict[str, Any]] = []
    chunk_lengths: list[int] = []

    for chunk in chunks:
        try:
            response_text = _request_verification(
                chunk, exported_note, task_suggestions, context or {}
            )
            try:
                payload = json.loads(response_text)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", response_text, re.DOTALL)
                if not match:
                    raise
                payload = json.loads(match.group(0))
            chunk_payloads.append(payload if isinstance(payload, dict) else {})
            chunk_lengths.append(len(chunk))
        except Exception as exc:
            result["error"] = f"verification failed: {exc}"
            return result

    # Deterministic aggregation (#20):
    # Coverage: weighted average by character count
    total_weight = 0
    weighted_sum = 0.0
    has_valid_coverage = False
    for p, length in zip(chunk_payloads, chunk_lengths):
        try:
            cov = float(p.get("coverage_score"))
            cov = min(1.0, max(0.0, cov))
            weighted_sum += cov * length
            total_weight += length
            has_valid_coverage = True
        except (TypeError, ValueError):
            pass

    if has_valid_coverage and total_weight > 0:
        coverage_score = round(weighted_sum / total_weight, 2)
    else:
        coverage_score = None

    # Gaps: union across all chunks
    aggregated_payload = {
        "coverage_score": coverage_score,
        "decision_gaps": _union_raw_items(chunk_payloads, "decision_gaps"),
        "action_item_gaps": _union_raw_items(chunk_payloads, "action_item_gaps"),
        "speaker_attribution_risks": _union_raw_items(chunk_payloads, "speaker_attribution_risks"),
        "unsupported_claims": _union_raw_items(chunk_payloads, "unsupported_claims"),
        "recommended_note_additions": _union_raw_items(chunk_payloads, "recommended_note_additions"),
    }

    result.update(normalize_verification_payload(aggregated_payload, segments))
    coverage = result["coverage_score"]
    below_threshold = coverage is not None and coverage < QA_COVERAGE_THRESHOLD
    result["needs_human_review"] = bool(
        below_threshold
        or result["decision_gaps"]
        or result["action_item_gaps"]
        or result["unsupported_claims"]
    )
    result["speaker_attribution"] = "not_assessable:no-diarization"
    return result


def render_qa_block(verification: dict[str, Any]) -> str:
    """Render the compact '## 🚩 QA Flags' note block ('' when clean)."""
    if verification.get("error"):
        return (
            "## 🚩 QA Flags\n\n"
            f"> [!warning] QA verification unavailable: {verification['error']}"
        )

    if not verification.get("needs_human_review"):
        return ""

    coverage = verification.get("coverage_score")
    coverage_text = f"{coverage:.2f}" if coverage is not None else "unknown"
    lines = [
        "## 🚩 QA Flags",
        "",
        f"- Coverage score: {coverage_text}",
        f"- Needs review: yes",
        f"- Missing decisions: {len(verification.get('decision_gaps', []))}",
        f"- Action item gaps: {len(verification.get('action_item_gaps', []))}",
        "- Speaker attribution: not assessable (no diarization)",
        f"- Unsupported claims: {len(verification.get('unsupported_claims', []))}",
    ]
    return "\n".join(lines)


def render_verification_md(verification: dict[str, Any]) -> str:
    """Render the detailed verification.md report."""
    coverage = verification.get("coverage_score")
    lines = [
        "# QA Verification Report",
        "",
        f"- Coverage score: {coverage if coverage is not None else 'unknown'}",
        f"- Needs human review: {verification.get('needs_human_review', False)}",
    ]
    if verification.get("error"):
        lines.append(f"- Error: {verification['error']}")

    sections = (
        ("Decision gaps", "decision_gaps"),
        ("Action item gaps", "action_item_gaps"),
        ("Speaker attribution risks", "speaker_attribution_risks"),
        ("Unsupported claims", "unsupported_claims"),
        ("Recommended note additions", "recommended_note_additions"),
    )
    for heading, key in sections:
        items = verification.get(key, [])
        lines.extend(["", f"## {heading}", ""])
        if not items:
            lines.append("- None")
        else:
            lines.extend(f"- {item}" for item in items)

    return "\n".join(lines) + "\n"


def build_verification_artifact(
    verification: dict[str, Any],
    *,
    meeting_date: str,
    transcript_file: str,
) -> dict[str, Any]:
    """Build the verification.json payload for downstream machine use."""
    return {
        "schema": "meetcap.verification/1",
        "meeting_date": meeting_date,
        "transcript_file": transcript_file,
        **verification,
    }
