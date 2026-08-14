import json
import re
from datetime import date
from typing import Any

from .config import (
    MANIFEST_MAX_TOKENS,
    MANIFEST_TEMPERATURE,
    MANIFEST_TRANSCRIPT_MAX_CHARS,
    MANIFEST_MAX_ITEMS,
)
from .prompts import (
    MANIFEST_SYSTEM_PROMPT,
    MANIFEST_USER_PROMPT,
)
from .llm_client import call_openrouter, should_retry_without_structured_output
from .transcript_parser import truncate_text

AUTHORITY_MIXES = ("decision-heavy", "discussion-heavy", "mixed")
FRESHNESS_VALUES = ("same-day", "aging", "stale-follow-up", "unknown")
LANES = ("daily-tasks", "wiki", "content", "client-followup", "reference-only")

_LANE_LABELS = {
    "daily-tasks": "task carry-over",
    "wiki": "wiki update",
    "content": "content briefing",
    "client-followup": "client follow-up",
    "reference-only": "reference only",
}

_CHECKBOX_PATTERN = re.compile(r"^\s*-\s+\[ \]\s+(.+?)\s*$", re.MULTILINE)


def compute_freshness(meeting_date: str, today: date | None = None) -> str:
    """Classify meeting freshness: same-day / aging / stale-follow-up."""
    try:
        parsed = date.fromisoformat(str(meeting_date).strip()[:10])
    except (TypeError, ValueError):
        return "unknown"
    today = today or date.today()
    age = (today - parsed).days
    if age <= 0:
        return "same-day"
    if age < 7:
        return "aging"
    return "stale-follow-up"


def extract_actions(*texts: str) -> list[str]:
    """Extract checkbox action items from note/task surfaces."""
    actions: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _CHECKBOX_PATTERN.finditer(text or ""):
            action = " ".join(match.group(1).split())
            if action and action.lower() not in seen:
                seen.add(action.lower())
                actions.append(action)
    return actions[:MANIFEST_MAX_ITEMS * 2]


def suggest_downstream_lanes(
    *,
    decisions: list[str],
    actions: list[str],
    high_confidence_claims: int,
    client_followup: bool,
    coverage_ok: bool,
) -> list[str]:
    """Heuristic routing. Low-confidence material routes to reference-only."""
    if not coverage_ok and not decisions:
        return ["reference-only"]

    lanes: list[str] = []
    if actions:
        lanes.append("daily-tasks")
    if decisions:
        lanes.append("wiki")
    if high_confidence_claims > 0:
        lanes.append("content")
    if client_followup:
        lanes.append("client-followup")
    if not lanes:
        lanes.append("reference-only")
    if not coverage_ok:
        lanes.append("reference-only")
    return lanes


def normalize_manifest_payload(payload: Any) -> dict[str, Any]:
    """Validate and clamp the LLM routing classification."""
    source = payload if isinstance(payload, dict) else {}

    authority = str(source.get("authorityMix", "mixed")).strip().lower()
    if authority not in AUTHORITY_MIXES:
        authority = "mixed"

    def _strings(key: str) -> list[str]:
        raw = source.get(key, [])
        if not isinstance(raw, list):
            return []
        items = [text for text in (" ".join(str(i).split()) for i in raw) if text]
        return items[:MANIFEST_MAX_ITEMS]

    followup = source.get("suggestsClientFollowup", False)
    if isinstance(followup, str):
        followup = followup.strip().lower() in ("true", "yes", "1")
    elif not isinstance(followup, bool):
        followup = bool(followup)

    return {
        "authorityMix": authority,
        "decisions": _strings("decisions"),
        "openQuestions": _strings("openQuestions"),
        "suggestsClientFollowup": followup,
    }


def _format_claims(claims: list[dict[str, Any]]) -> str:
    lines = []
    for claim in claims:
        timestamps = ", ".join(claim.get("timestamps", []))
        lines.append(
            f"- {claim.get('claim', '')} [{timestamps}] ({claim.get('confidence', 'medium')})"
        )
    return "\n".join(lines)


def _request_classification(
    summary: str,
    claims: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> str:
    transcript_lines = [
        f"[{segment['start']} → {segment['end']}] {segment['text']}"
        for segment in segments
    ]
    messages = [
        {"role": "system", "content": MANIFEST_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": MANIFEST_USER_PROMPT.format(
                summary=truncate_text(summary, max_chars=8000, head_chars=6000, tail_chars=1500),
                claims=_format_claims(claims) or "- none",
                transcript=truncate_text(
                    "\n".join(transcript_lines),
                    max_chars=MANIFEST_TRANSCRIPT_MAX_CHARS,
                    head_chars=12000,
                    tail_chars=3000,
                ),
            ),
        },
    ]
    try:
        return call_openrouter(
            messages=messages,
            max_tokens=MANIFEST_MAX_TOKENS,
            temperature=MANIFEST_TEMPERATURE,
            reasoning_effort="none",
            response_format={"type": "json_object"},
        )
    except RuntimeError as exc:
        if not should_retry_without_structured_output(exc):
            raise
        return call_openrouter(
            messages=messages,
            max_tokens=MANIFEST_MAX_TOKENS,
            temperature=MANIFEST_TEMPERATURE,
            reasoning_effort="none",
            response_format=None,
        )


def build_room_manifest(
    *,
    segments: list[dict[str, Any]],
    summary: str,
    task_suggestions: str,
    claims_result: dict[str, Any],
    verification: dict[str, Any],
    meeting_date: str,
    title: str,
    note_path: str,
    participants: list[str] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Build the room_manifest.json payload for downstream agent routing.

    Reuses evidence claims and QA verification instead of re-deriving them.
    Never raises; LLM classification failures degrade to empty decisions.
    """
    claims = claims_result.get("claims", [])
    manifest: dict[str, Any] = {
        "schema": "meetcap.room-manifest/1",
        "meeting": {
            "title": title,
            "date": meeting_date,
            "participants": participants or [],
            "canonical_note_path": note_path,
        },
        "authorityMix": "mixed",
        "freshness": compute_freshness(meeting_date, today),
        "decisions": [],
        "openQuestions": [],
        "actions": extract_actions(task_suggestions, summary),
        "claims": [
            {"claim": c.get("claim", ""), "timestamps": c.get("timestamps", [])}
            for c in claims
        ],
        "missingProof": list(verification.get("unsupported_claims", [])),
        "downstreamLanes": [],
        "error": None,
    }
    if claims_result.get("dropped_unresolved"):
        manifest["missingProof"].append(
            f"{claims_result['dropped_unresolved']} claim(s) could not be located in the transcript"
        )

    classification: dict[str, Any] = {}
    if segments:
        try:
            response_text = _request_classification(summary, claims, segments)
            try:
                payload = json.loads(response_text)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", response_text, re.DOTALL)
                if not match:
                    raise
                payload = json.loads(match.group(0))
            classification = normalize_manifest_payload(payload)
        except Exception as exc:
            manifest["error"] = f"manifest classification failed: {exc}"
    else:
        manifest["error"] = "no timestamped segments in transcript"

    manifest["authorityMix"] = classification.get("authorityMix", "mixed")
    manifest["decisions"] = classification.get("decisions", [])
    manifest["openQuestions"] = classification.get("openQuestions", [])

    coverage = verification.get("coverage_score")
    coverage_ok = (
        not verification.get("error")
        and coverage is not None
        and coverage >= 0.75
        and not verification.get("needs_human_review")
    )
    manifest["downstreamLanes"] = suggest_downstream_lanes(
        decisions=manifest["decisions"],
        actions=manifest["actions"],
        high_confidence_claims=sum(
            1 for c in claims if c.get("confidence") == "high"
        ),
        client_followup=bool(classification.get("suggestsClientFollowup")),
        coverage_ok=coverage_ok,
    )
    return manifest


def render_manifest_block(manifest: dict[str, Any]) -> str:
    """Render the compact '## 🗺️ Room Manifest' note block."""
    lanes = manifest.get("downstreamLanes", [])
    best_used_for = ", ".join(_LANE_LABELS.get(lane, lane) for lane in lanes) or "—"
    lines = [
        "## 🗺️ Room Manifest",
        "",
        f"- Authority mix: {manifest.get('authorityMix', 'mixed')}",
        f"- Freshness: {manifest.get('freshness', 'unknown')}",
        f"- Best used for: {best_used_for}",
        f"- Decisions: {len(manifest.get('decisions', []))} · "
        f"Open questions: {len(manifest.get('openQuestions', []))} · "
        f"Actions: {len(manifest.get('actions', []))}",
    ]
    if manifest.get("error"):
        lines.append(f"- ⚠️ {manifest['error']}")
    return "\n".join(lines)
