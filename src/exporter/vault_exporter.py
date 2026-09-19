import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from .config import (
    BRT,
    MEETINGS_DIR,
    ARTIFACTS_DIR,
    LLM_MODEL,
    EXPORT_QA_ENABLED,
    EXPORT_MANIFEST_ENABLED,
)
from .transcript_parser import parse_meetcap_transcript
from .summarizer import generate_summary
from .task_extractor import generate_task_suggestions
from .claim_extractor import (
    extract_claims,
    render_claims_block,
    build_evidence_artifact,
)
from .entity_resolver import (
    load_vocabulary,
    resolve_derived_surfaces,
    render_name_corrections,
)
from .note_verifier import (
    verify_export,
    render_qa_block,
    render_verification_md,
    build_verification_artifact,
)
from .room_manifest import (
    build_room_manifest,
    render_manifest_block,
)

# #38: graceful-degradation warnings emitted by summarizer/task_extractor.
STAGE_WARN_PATTERN = re.compile(
    r"\[!warning\]\s*(Summary|Task suggestion) generation failed:\s*(.*)", re.MULTILINE
)

def _stage(error: str | None) -> dict[str, Any]:
    """Normalize one pipeline stage into the {ok, error} contract shape."""
    return {"ok": error is None, "error": error}

def _atomic_commit(final_path: Path, content: str, *, staging_suffix: str) -> None:
    """#17: stage the artifact next to its final path (same filesystem), then
    commit atomically via os.replace. On any failure the staging file is
    removed so a crash or full disk can never leave a truncated file behind."""
    staging = final_path.with_suffix(staging_suffix)
    try:
        staging.write_text(content, encoding="utf-8")
        staging.replace(final_path)
    except BaseException:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise

def _warning_degradation(text: str) -> str | None:
    """Extract 'X generation failed: …' from a graceful-degradation warning block."""
    match = STAGE_WARN_PATTERN.search(text)
    return f"{match.group(1)} generation failed: {match.group(2)}" if match else None

def build_note_title(data: dict[str, Any], custom_title: str | None = None) -> str:
    """Generate a note title."""
    if custom_title:
        return custom_title
    return f"Meeting — {data['meeting_date']} ({data['duration_str']})"

def safe_name(name: str, max_length: int = 100) -> str:
    """Robust filename sanitization (#40).

    Replaces / and : with -, strips control chars and newlines,
    collapses whitespace, strips edge whitespace/dots, and truncates
    to ~100 characters while stripping trailing dots/whitespace.
    Falls back to 'untitled' if empty.
    """
    cleaned = name.replace("/", "-").replace(":", "-")
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(". ")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(". ")
    return cleaned or "untitled"

def _format_yaml_scalar(key: str, val: Any) -> str:
    """Format a single YAML frontmatter scalar with deterministic escaping (#40)."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return str(val)
    if key == "date" and isinstance(val, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", val):
        return val
    if key == "tags" and isinstance(val, list):
        return f"[{', '.join(str(t) for t in val)}]"
    if isinstance(val, list):
        return f"[{', '.join(_format_yaml_scalar('', item) for item in val)}]"
    return json.dumps(str(val), ensure_ascii=False)

def build_note_content(
    *,
    data: dict[str, Any],
    title: str,
    summary: str,
    task_suggestions: str,
    claims_block: str = "",
    corrections_block: str = "",
    qa_block: str = "",
    manifest_block: str = "",
    qa_needs_review: bool = False,
    qa_coverage_score: float | None = None,
    outcome: str = "ok",
    qa_audited: str = "post-entity-resolution",
    entity_corrections_count: int = 0,
    now: datetime | None = None,
) -> str:
    """Render the final Obsidian note content."""
    meta = data["meta"]
    now = now or datetime.now(BRT)

    claims_section = f"\n{claims_block}\n\n---\n" if claims_block else ""
    corrections_section = f"\n{corrections_block}\n\n---\n" if corrections_block else ""
    qa_section = f"\n{qa_block}\n\n---\n" if qa_block else ""
    manifest_section = f"\n{manifest_block}\n\n---\n" if manifest_block else ""

    frontmatter_fields: dict[str, Any] = {
        "title": title,
        "date": data["meeting_date"],
        "time": data["meeting_time"],
        "duration": data["duration_str"],
        "tags": ["meeting", "meeting-notes", "meetcap"],
        "model": meta.get("model", "unknown"),
        "language": meta.get("language", "unknown"),
        "created": now.strftime("%Y-%m-%d %H:%M"),
        "qa_needs_review": qa_needs_review,
        "qa_coverage_score": qa_coverage_score,
        "outcome": outcome,
        "qa_audited": qa_audited,
        "entity_corrections_count": entity_corrections_count,
    }
    frontmatter_lines = [
        f"{k}: {_format_yaml_scalar(k, v)}" for k, v in frontmatter_fields.items()
    ]
    frontmatter_yaml = "\n".join(frontmatter_lines)

    return f"""---
{frontmatter_yaml}
---

# {title}

**📅 Data:** {data['meeting_date']} às {data['meeting_time']}
**⏱ Duração:** {data['duration_str']}
**🤖 Modelo:** {meta.get('model', 'N/A')}
**🌐 Idioma:** {meta.get('language', 'N/A')}
**📝 Segmentos:** {data['line_count']}

---

{summary}

---

{task_suggestions}

---
{claims_section}{corrections_section}{qa_section}{manifest_section}
## 📝 Transcrição Completa

{data['transcript_text']}

---

> [!meta] Gerado automaticamente pelo Meetcap + Hermes
> Arquivo original: `{meta.get('file', 'N/A')}`
> Resumo e sugestões: `{LLM_MODEL}`
"""

def export_note(txt_path: Path, custom_title: str | None = None) -> dict[str, Any]:
    """Main export: transcript → Obsidian note with summary + task suggestions."""
    if not txt_path.exists():
        return {"success": False, "error": f"Transcript not found: {txt_path}"}

    data = parse_meetcap_transcript(txt_path)
    if not data["transcript_text"].strip():
        return {"success": False, "error": "Transcript is empty"}

    print(f"[EXPORT] Generating AI summary for {txt_path.name}...")
    summary = generate_summary(data["transcript_text"])
    print(f"[EXPORT] Summary generated ({len(summary)} chars)")

    print("[EXPORT] Generating task suggestions...")
    task_suggestions = generate_task_suggestions(
        meeting_date=data["meeting_date"],
        summary=summary,
        transcript_text=data["transcript_text"],
    )
    print(f"[EXPORT] Task suggestions generated ({len(task_suggestions)} chars)")

    # #38: per-stage health for the outcome contract — degradation is not success.
    stages: dict[str, dict[str, Any]] = {
        "summary": _stage(_warning_degradation(summary)),
        "tasks": _stage(_warning_degradation(task_suggestions)),
    }

    # Canonical entity resolution: derived surfaces only, raw transcript untouched.
    vocabulary = load_vocabulary()
    summary, summary_corrections = resolve_derived_surfaces(summary, vocabulary)
    task_suggestions, task_corrections = resolve_derived_surfaces(task_suggestions, vocabulary)
    corrections = summary_corrections + task_corrections
    corrections_block = render_name_corrections(corrections)
    if corrections:
        print(f"[EXPORT] Entity resolver: {len(corrections)} correction(s) flagged")

    print("[EXPORT] Extracting evidence-backed claims...")
    claims_result = extract_claims(data["segments"], data["transcript_text"])
    claims_block = render_claims_block(claims_result)
    stages["claims"] = _stage(claims_result.get("error"))
    print(
        f"[EXPORT] Claims extracted ({len(claims_result['claims'])} verified, "
        f"{claims_result['dropped_unresolved']} dropped)"
    )

    title = build_note_title(data, custom_title)
    safe_base = safe_name(title)
    filename = f"{safe_base}.md"
    out_path = MEETINGS_DIR / filename

    # #12 no-clobber: if note already exists, find first free collision suffix (-a, -b, …).
    if out_path.exists():
        for suffix_char in "abcdefghijklmnopqrstuvwxyz":
            prefix = safe_base[:97] if len(safe_base) > 97 else safe_base
            candidate_base = f"{prefix.rstrip('. ')}-{suffix_char}"
            candidate_path = MEETINGS_DIR / f"{candidate_base}.md"
            if not candidate_path.exists():
                safe_base = candidate_base
                filename = f"{safe_base}.md"
                out_path = candidate_path
                break
        else:
            # Extremely unlikely: all 26 suffixes taken.
            return {"success": False, "error": "No free collision suffix available for note"}

    verification: dict[str, Any] = {
        "coverage_score": None, "needs_human_review": False, "error": None,
        "decision_gaps": [], "action_item_gaps": [],
        "speaker_attribution_risks": [], "unsupported_claims": [],
        "recommended_note_additions": [],
    }
    manifest: dict[str, Any] | None = None
    qa_block = ""
    manifest_block = ""

    if EXPORT_QA_ENABLED:
        print("[EXPORT] Running QA verification pass...")
        note_surfaces = "\n\n".join(
            part for part in (summary, claims_block, task_suggestions) if part
        )
        verification = verify_export(
            data["segments"],
            note_surfaces,
            task_suggestions,
            context={
                "meeting_date": data["meeting_date"],
                "title": title,
                "model": LLM_MODEL,
            },
        )
        stages["qa"] = _stage(verification.get("error"))
        qa_block = render_qa_block(verification)
        if verification.get("error"):
            print(f"[EXPORT] QA verification degraded: {verification['error']}")
        else:
            print(
                f"[EXPORT] QA coverage {verification['coverage_score']} "
                f"(needs review: {verification['needs_human_review']})"
            )

    if EXPORT_MANIFEST_ENABLED:
        print("[EXPORT] Building room manifest...")
        manifest = build_room_manifest(
            segments=data["segments"],
            summary=summary,
            task_suggestions=task_suggestions,
            claims_result=claims_result,
            verification=verification,
            meeting_date=data["meeting_date"],
            title=title,
            note_path=str(out_path),
        )
        manifest_block = render_manifest_block(manifest)
        stages["manifest"] = _stage(manifest.get("error"))
        print(f"[EXPORT] Room manifest lanes: {manifest['downstreamLanes']}")

    # Determine outcome of stages prior to note write for frontmatter (#51)
    stage_errors = [f"{name}: {stage['error']}" for name, stage in stages.items() if not stage["ok"]]
    pre_outcome = "ok" if not stage_errors else "degraded"
    if not stages["summary"]["ok"] or (stage_errors and len(stage_errors) == len(stages)):
        pre_outcome = "failed"

    MEETINGS_DIR.mkdir(parents=True, exist_ok=True)
    content = build_note_content(
        data=data,
        title=title,
        summary=summary,
        task_suggestions=task_suggestions,
        claims_block=claims_block,
        corrections_block=corrections_block,
        qa_block=qa_block,
        manifest_block=manifest_block,
        qa_needs_review=verification.get("needs_human_review", False),
        qa_coverage_score=verification.get("coverage_score"),
        outcome=pre_outcome,
        qa_audited="post-entity-resolution",
        entity_corrections_count=len(corrections),
    )

    # #17 atomic commit protocol: stage → validate → os.replace. Failures never
    # leave truncated files or staging litter; required artifacts (evidence,
    # corrections) failing flips outcome to failed, optional ones (QA/manifest)
    # degrade through the stages contract (#38).
    artifact_dir = ARTIFACTS_DIR / safe_base
    commit_errors: list[str] = []    # required artifact failures
    optional_errors: list[str] = []  # optional artifact failures
    artifacts: list[str] = []

    def _record_failure(name: str, exc: Exception, required: bool) -> None:
        if isinstance(exc, (ValueError, TypeError)):
            message = f"JSON validation failed: {name} ({exc})"
        else:
            message = f"{name}: write failed ({exc})"
        (commit_errors if required else optional_errors).append(message)

    def _commit_json(name: str, payload: Any, required: bool) -> None:
        """Stage JSON to .tmp, validate it parses, then atomically commit."""
        path = artifact_dir / name
        staged = path.with_name(f"{name}.tmp")
        try:
            staged.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            json.loads(staged.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            staged.unlink(missing_ok=True)
            _record_failure(name, exc, required)
            return
        staged.replace(path)
        artifacts.append(str(path))

    def _commit_file(name: str, text: str, required: bool) -> None:
        """Atomically commit a plain-text artifact via .tmp staging."""
        path = artifact_dir / name
        staged = path.with_name(f"{name}.tmp")
        try:
            staged.write_text(text, encoding="utf-8")
        except OSError as exc:
            staged.unlink(missing_ok=True)
            _record_failure(name, exc, required)
            return
        staged.replace(path)
        artifacts.append(str(path))

    partial_path = out_path.with_suffix(".partial")
    try:
        partial_path.write_text(content, encoding="utf-8")
        partial_path.replace(out_path)
    except OSError as exc:
        partial_path.unlink(missing_ok=True)
        print(f"[EXPORT] Note write failed: {exc}")
        return {
            "success": False,
            "outcome": "failed",
            "stages": {**stages, "artifacts": _stage(f"note: {exc}")},
            "errors": [f"note: {exc}"],
            "path": "",
            "filename": filename,
            "transcript_lines": data["line_count"],
            "summary_length": len(summary),
            "task_suggestions_length": len(task_suggestions),
            "claims_verified": len(claims_result["claims"]),
            "entity_corrections": len(corrections),
            "qa_needs_review": verification.get("needs_human_review", False),
            "qa_coverage_score": verification.get("coverage_score"),
            "downstream_lanes": manifest["downstreamLanes"] if manifest else [],
            "artifacts": [],
            "duration": data["duration_str"],
        }
    print(f"[EXPORT] Saved to {out_path}")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    try:
        evidence = build_evidence_artifact(
            claims_result,
            meeting_date=data["meeting_date"],
            transcript_file=data["meta"].get("file", ""),
        )
        _commit_json("evidence.json", evidence, required=True)
        if corrections:
            _commit_json(
                "corrections.json",
                {"schema": "meetcap.corrections/1", "corrections": corrections},
                required=True,
            )
        if EXPORT_QA_ENABLED:
            _commit_json(
                "verification.json",
                build_verification_artifact(
                    verification,
                    meeting_date=data["meeting_date"],
                    transcript_file=data["meta"].get("file", ""),
                ),
                required=False,
            )
            _commit_file(
                "verification.md", render_verification_md(verification), required=False
            )
        if EXPORT_MANIFEST_ENABLED and manifest is not None:
            _commit_json("room_manifest.json", manifest, required=False)
    except OSError as exc:
        # A raising builder means the artifact never got committed at all.
        commit_errors.append(f"artifacts: build failed ({exc})")

    stages["artifacts"] = _stage("; ".join(commit_errors + optional_errors) or None)

    # #38/#17: outcome is first-class — degradation is not a plain success, and
    # a hard failure (summary or required artifact) flips both outcome and
    # success (rc!=0) even though the note itself was written gracefully. A
    # failed summary is a failed export: the note's core deliverable is the
    # summary.
    stage_errors = [f"{name}: {stage['error']}" for name, stage in stages.items() if not stage["ok"]]
    outcome = "ok" if not stage_errors else "degraded"
    success = True
    if (
        commit_errors
        or not stages["summary"]["ok"]
        or (stage_errors and len(stage_errors) == len(stages))
    ):
        outcome = "failed"
        success = False
    stage_errors.extend(optional_errors)
    stage_errors.extend(commit_errors)

    return {
        "success": success,
        "outcome": outcome,
        "stages": stages,
        "errors": stage_errors,
        "path": str(out_path),
        "filename": filename,
        "transcript_lines": data["line_count"],
        "summary_length": len(summary),
        "task_suggestions_length": len(task_suggestions),
        "claims_verified": len(claims_result["claims"]),
        "entity_corrections": len(corrections),
        "qa_needs_review": verification.get("needs_human_review", False),
        "qa_coverage_score": verification.get("coverage_score"),
        "downstream_lanes": manifest["downstreamLanes"] if manifest else [],
        "artifacts": artifacts,
        "duration": data["duration_str"],
    }
