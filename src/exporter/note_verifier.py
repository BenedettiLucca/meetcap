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
from .transcript_parser import truncate_text

_FLAG_KEYS = (
    "decision_gaps",
    "action_item_gaps",
    "speaker_attribution_risks",
    "unsupported_claims",
)


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


def normalize_verification_payload(payload: Any) -> dict[str, Any]:
    """Validate and clamp a raw verification payload."""
    source = payload if isinstance(payload, dict) else {}

    try:
        coverage = float(source.get("coverage_score"))
    except (TypeError, ValueError):
        coverage = None
    if coverage is not None:
        coverage = min(1.0, max(0.0, coverage))

    normalized: dict[str, Any] = {"coverage_score": coverage}
    for key in _FLAG_KEYS:
        raw = source.get(key, [])
        if not isinstance(raw, list):
            raw = []
        items = [text for text in (_item_to_text(item) for item in raw) if text]
        normalized[key] = items[:QA_MAX_ITEMS]

    additions_raw = source.get("recommended_note_additions", [])
    if not isinstance(additions_raw, list):
        additions_raw = []
    additions = [text for text in (_item_to_text(item) for item in additions_raw) if text]
    normalized["recommended_note_additions"] = additions[:QA_MAX_ITEMS]

    return normalized


def _format_timestamps(segments: list[dict[str, Any]]) -> str:
    lines = []
    for segment in segments:
        lines.append(f"[{segment['start']} → {segment['end']}] {segment['text']}")
    return "\n".join(lines)


def _request_verification(
    segments: list[dict[str, Any]],
    exported_note: str,
    task_suggestions: str,
    context: dict[str, Any],
) -> str:
    messages = [
        {"role": "system", "content": VERIFICATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": VERIFICATION_USER_PROMPT.format(
                transcript=truncate_text(
                    _format_timestamps(segments),
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
        "needs_human_review": False,
        "error": None,
    }
    if not segments:
        result["error"] = "no timestamped segments in transcript"
        return result

    try:
        response_text = _request_verification(
            segments, exported_note, task_suggestions, context or {}
        )
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if not match:
                raise
            payload = json.loads(match.group(0))
    except Exception as exc:
        result["error"] = f"verification failed: {exc}"
        return result

    result.update(normalize_verification_payload(payload))
    coverage = result["coverage_score"]
    below_threshold = coverage is not None and coverage < QA_COVERAGE_THRESHOLD
    result["needs_human_review"] = bool(
        below_threshold
        or result["decision_gaps"]
        or result["speaker_attribution_risks"]
        or result["unsupported_claims"]
    )
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
        f"- Attribution risks: {len(verification.get('speaker_attribution_risks', []))}",
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
