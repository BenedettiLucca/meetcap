import json
from datetime import datetime
from pathlib import Path
from typing import Any
from .config import BRT, MEETINGS_DIR, ARTIFACTS_DIR, LLM_MODEL
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

def build_note_title(data: dict[str, Any], custom_title: str | None = None) -> str:
    """Generate a note title."""
    if custom_title:
        return custom_title
    return f"Meeting — {data['meeting_date']} ({data['duration_str']})"

def build_note_content(
    *,
    data: dict[str, Any],
    title: str,
    summary: str,
    task_suggestions: str,
    claims_block: str = "",
    corrections_block: str = "",
    now: datetime | None = None,
) -> str:
    """Render the final Obsidian note content."""
    meta = data["meta"]
    now = now or datetime.now(BRT)

    claims_section = f"\n{claims_block}\n\n---\n" if claims_block else ""
    corrections_section = f"\n{corrections_block}\n\n---\n" if corrections_block else ""

    return f"""---
title: "{title}"
date: {data['meeting_date']}
time: {data['meeting_time']}
duration: "{data['duration_str']}"
tags: [meeting, meeting-notes, meetcap]
model: "{meta.get('model', 'unknown')}"
language: "{meta.get('language', 'unknown')}"
created: "{now.strftime('%Y-%m-%d %H:%M')}"
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
{claims_section}{corrections_section}
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
    print(
        f"[EXPORT] Claims extracted ({len(claims_result['claims'])} verified, "
        f"{claims_result['dropped_unresolved']} dropped)"
    )

    title = build_note_title(data, custom_title)
    safe_name = title.replace("/", "-").replace(":", "-")
    filename = f"{safe_name}.md"

    MEETINGS_DIR.mkdir(parents=True, exist_ok=True)
    content = build_note_content(
        data=data,
        title=title,
        summary=summary,
        task_suggestions=task_suggestions,
        claims_block=claims_block,
        corrections_block=corrections_block,
    )

    out_path = MEETINGS_DIR / filename
    out_path.write_text(content, encoding="utf-8")
    print(f"[EXPORT] Saved to {out_path}")

    artifacts: list[str] = []
    artifact_dir = ARTIFACTS_DIR / safe_name
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        evidence = build_evidence_artifact(
            claims_result,
            meeting_date=data["meeting_date"],
            transcript_file=data["meta"].get("file", ""),
        )
        evidence_path = artifact_dir / "evidence.json"
        evidence_path.write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        artifacts.append(str(evidence_path))
        if corrections:
            corrections_path = artifact_dir / "corrections.json"
            corrections_path.write_text(
                json.dumps(
                    {"schema": "meetcap.corrections/1", "corrections": corrections},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            artifacts.append(str(corrections_path))
    except OSError as exc:
        print(f"[EXPORT] Artifact write failed: {exc}")

    return {
        "success": True,
        "path": str(out_path),
        "filename": filename,
        "transcript_lines": data["line_count"],
        "summary_length": len(summary),
        "task_suggestions_length": len(task_suggestions),
        "claims_verified": len(claims_result["claims"]),
        "entity_corrections": len(corrections),
        "artifacts": artifacts,
        "duration": data["duration_str"],
    }
