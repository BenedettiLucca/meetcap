#!/usr/bin/env python3
"""
Meetcap → Obsidian vault exporter with AI-powered summary.

Called automatically after transcription completes.
Reads the meetcap .txt transcript, generates a structured Obsidian note
with full transcript + AI summary + task suggestions, saves to vault.

Can also be called standalone:
  python3 export_to_vault.py /path/to/transcript.txt [--title "Custom Title"]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ── Config ──────────────────────────────────────────────────────────
VAULT = os.environ.get("OBSIDIAN_VAULT_PATH", "/home/lucca/HD2/vault")
MEETINGS_DIR = Path(VAULT) / "Meetings"
TASKS_DIR = Path(VAULT) / "Tasks"
TASKS_ARCHIVE_DIR = TASKS_DIR / "Archive"

BRT = timezone(timedelta(hours=-3))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
LLM_MODEL = os.environ.get("MEETCAP_LLM_MODEL", "deepseek/deepseek-v4-flash")
SUMMARY_MAX_TOKENS = 2048
TASK_SUGGESTIONS_MAX_TOKENS = 1400
SUMMARY_TEMPERATURE = 1.0
TASK_SUGGESTIONS_TEMPERATURE = 0.0

SUMMARY_SYSTEM_PROMPT = """You are a senior meeting summarizer.

Write a clear, specific meeting note from a raw transcript.

Rules:
- Respond in the SAME LANGUAGE as the transcript.
- Be concrete. Avoid generic filler such as "the meeting discussed various topics".
- Only include claims supported by the transcript.
- Distinguish decisions, action items, and open risks.
- If an owner is not explicitly clear, do not invent one.
- Do not append owner labels or speaker placeholders to action items; write the action only.
- If something is uncertain or ambiguous, say so briefly instead of hallucinating certainty.
- Deduplicate overlapping bullets.
- In `## ✅ Action Items`, every bullet must start with `- [ ] `.
- In `## 🔑 Key Points` and `## ⚠️ Open Questions / Risks`, every bullet must start with `- `.

Output EXACTLY with these sections and headings:
## 📌 Summary
[2-4 short paragraphs]

## 🔑 Key Points
- [specific point]

## ✅ Action Items
- [ ] [specific action]

## ⚠️ Open Questions / Risks
- [specific risk or unresolved question]

Start directly with ## 📌 Summary."""

SUMMARY_USER_PROMPT = """Task: summarize the meeting transcript below.

Transcript:
{transcript}

Reminder after reading the transcript:
- Same language as the transcript.
- Keep the summary specific and decision-useful.
- Do not invent owners.
- Do not append owner labels, role labels, or speaker placeholders to action items.
- Use checkbox bullets in `## ✅ Action Items`.
- Start directly with ## 📌 Summary."""

TASK_SUGGESTIONS_SYSTEM_PROMPT = """You compare a meeting against Lucca's current daily task list and suggest tasks for manual review.

Important:
- This is a manual-review workflow, not an auto-planning workflow.
- Suggest only tasks that Lucca can copy and paste into "Tasks do Dia".
- Be conservative: false negatives are better than false positives.
- If a task is already clearly present in the daily task list, put it in matched_tasks instead of new_suggested_tasks.
- Only use matched_tasks when the meeting mentions the SAME deliverable or clearly the same follow-up. Shared theme is not enough.
- Do not match generic mentions of ads, design, content, or community to client-specific tasks unless the same client/project is explicit.
- If something is vague, belongs to someone else, or is just context, put it in not_now_items or omit it.
- Use the same language as the meeting/task list.
- Keep task wording short, concrete, and actionable.
- No duplicates.
- Return json only.

Return valid json with exactly this shape:
{
  "matched_tasks": ["- [ ] Existing task"],
  "new_suggested_tasks": ["- [ ] New copy-paste-ready task"],
  "not_now_items": [
    {"item": "Short item", "reason": "Short reason"}
  ]
}

Use empty arrays when needed. Return json only, no markdown fences."""

TASK_SUGGESTIONS_USER_PROMPT = """Generate json only.

Meeting date: {meeting_date}

Current daily tasks:
{daily_tasks}

Meeting summary:
{summary}

Transcript excerpt:
{transcript_excerpt}

Task again: compare the meeting against the current daily tasks and produce copy-paste-ready task suggestions in json only."""

JSON_REPAIR_SYSTEM_PROMPT = """You repair malformed json.

Return valid json only.
Do not add commentary.
Preserve the original meaning and keys when possible.
Target schema:
{
  "matched_tasks": ["- [ ] Existing task"],
  "new_suggested_tasks": ["- [ ] New copy-paste-ready task"],
  "not_now_items": [
    {"item": "Short item", "reason": "Short reason"}
  ]
}
Use empty arrays when needed."""


def load_openrouter_key() -> str:
    """Load OpenRouter API key from Hermes .env if not in env."""
    if OPENROUTER_KEY:
        return OPENROUTER_KEY

    env_file = Path.home() / ".hermes" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return ""


def truncate_text(text: str, max_chars: int, head_chars: int, tail_chars: int) -> str:
    """Truncate long text while keeping the beginning and end."""
    if len(text) <= max_chars:
        return text
    return text[:head_chars] + "\n\n[... content truncated ...]\n\n" + text[-tail_chars:]


def should_retry_without_structured_output(error: RuntimeError) -> bool:
    """Decide whether it is worth retrying without response_format."""
    message = str(error).lower()
    fatal_terms = (
        "no api key",
        "401",
        "403",
        "authorization",
        "insufficient",
        "rate limit",
        "quota",
    )
    return not any(term in message for term in fatal_terms)


def call_openrouter(
    *,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float | None,
    reasoning_effort: str = "none",
    response_format: dict[str, Any] | None = None,
) -> str:
    """Call OpenRouter and return the assistant content."""
    api_key = load_openrouter_key()
    if not api_key:
        raise RuntimeError("no API key configured")

    payload: dict[str, Any] = {
        "model": LLM_MODEL,
        "max_tokens": max_tokens,
        "messages": messages,
        "reasoning": {"effort": reasoning_effort},
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if response_format is not None:
        payload["response_format"] = response_format

    result = subprocess.run(
        [
            "curl",
            "-sS",
            "--max-time",
            "120",
            OPENROUTER_URL,
            "-H",
            f"Authorization: Bearer {api_key}",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload, ensure_ascii=False),
        ],
        capture_output=True,
        text=True,
        timeout=130,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"curl exited with {result.returncode}")

    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON response: {exc}") from exc

    if response.get("error"):
        error = response["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(message)

    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("no choices returned")

    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if not content.strip():
        raise RuntimeError("empty response content")

    return content.strip()


def generate_summary(transcript_text: str) -> str:
    """Generate the meeting summary block."""
    truncated = truncate_text(transcript_text, max_chars=15000, head_chars=12000, tail_chars=3000)
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": SUMMARY_USER_PROMPT.format(transcript=truncated)},
    ]

    try:
        return call_openrouter(
            messages=messages,
            max_tokens=SUMMARY_MAX_TOKENS,
            temperature=SUMMARY_TEMPERATURE,
            reasoning_effort="none",
        )
    except Exception as exc:
        return f"> [!warning] Summary generation failed: {exc}"


def extract_section_lines(note_text: str, section_header: str) -> list[str]:
    """Return lines inside a markdown section until the next level-2 header."""
    lines: list[str] = []
    in_section = False

    for raw_line in note_text.splitlines():
        line = raw_line.rstrip()
        if line.strip() == section_header:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            lines.append(line)

    return lines


def extract_pending_daily_tasks(note_text: str) -> list[str]:
    """Extract pending tasks from the 'Tasks do Dia' section only."""
    task_lines = extract_section_lines(note_text, "## 📋 Tasks do Dia")
    return [line.strip() for line in task_lines if line.strip().startswith("- [ ]")]


def normalize_for_exact_match(text: str) -> str:
    """Lowercase, de-accent, and collapse whitespace for conservative matching."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_task_prefix(task: str) -> str:
    task = re.sub(r"^-\s*\[.\]\s*", "", task.strip())
    task = task.replace("🔄", "").strip()
    return task


def compute_explicit_task_matches(pending_tasks: list[str], summary: str, transcript_text: str) -> list[str]:
    """Match only tasks that are explicitly named in the meeting artifacts."""
    haystack = normalize_for_exact_match(f"{summary}\n{transcript_text}")
    matches: list[str] = []

    for task in pending_tasks:
        candidate = strip_task_prefix(task)
        normalized_candidate = normalize_for_exact_match(candidate)
        if normalized_candidate and len(normalized_candidate) >= 6 and normalized_candidate in haystack:
            matches.append(task)

    return matches


def find_daily_task_note(meeting_date: str) -> Path | None:
    """Locate the daily task note for the meeting date."""
    filename = f"Tasks — {meeting_date}.md"
    for candidate in (TASKS_DIR / filename, TASKS_ARCHIVE_DIR / filename):
        if candidate.exists():
            return candidate
    return None


def load_daily_task_context(meeting_date: str) -> dict[str, Any]:
    """Load the daily task note and extracted pending tasks."""
    path = find_daily_task_note(meeting_date)
    if not path:
        return {"path": None, "raw_note": "", "pending_tasks": []}

    raw_note = path.read_text(encoding="utf-8")
    return {
        "path": path,
        "raw_note": raw_note,
        "pending_tasks": extract_pending_daily_tasks(raw_note),
    }


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating accidental prose around it."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def repair_json_object(text: str) -> dict[str, Any]:
    """Ask the model to repair malformed JSON into the expected shape."""
    messages = [
        {"role": "system", "content": JSON_REPAIR_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    repaired_text = call_openrouter(
        messages=messages,
        max_tokens=TASK_SUGGESTIONS_MAX_TOKENS,
        temperature=0.0,
        reasoning_effort="none",
        response_format={"type": "json_object"},
    )
    return extract_json_object(repaired_text)


def normalize_checkbox_task(task: str) -> str:
    task = " ".join(task.strip().split())
    if not task:
        return ""
    if task.startswith("- [ ] "):
        return task
    task = re.sub(r"^-\s*", "", task)
    task = re.sub(r"^\[ \]\s*", "", task)
    return f"- [ ] {task}"


def normalize_task_suggestion_payload(payload: dict[str, Any]) -> dict[str, Any]:
    matched_source = payload.get("matched_tasks", [])
    if isinstance(matched_source, str):
        matched_iterable: list[Any] = [matched_source]
    elif isinstance(matched_source, list):
        matched_iterable = matched_source
    else:
        matched_iterable = []

    suggested_source = payload.get("new_suggested_tasks", [])
    if isinstance(suggested_source, str):
        suggested_iterable: list[Any] = [suggested_source]
    elif isinstance(suggested_source, list):
        suggested_iterable = suggested_source
    else:
        suggested_iterable = []

    not_now_source = payload.get("not_now_items", [])
    if isinstance(not_now_source, dict):
        not_now_iterable: list[Any] = [not_now_source]
    elif isinstance(not_now_source, list):
        not_now_iterable = not_now_source
    else:
        not_now_iterable = []

    matched = [
        normalize_checkbox_task(item)
        for item in matched_iterable
        if isinstance(item, str) and normalize_checkbox_task(item)
    ]
    suggested = [
        normalize_checkbox_task(item)
        for item in suggested_iterable
        if isinstance(item, str) and normalize_checkbox_task(item)
    ]

    not_now_items: list[dict[str, str]] = []
    for item in not_now_iterable:
        if not isinstance(item, dict):
            continue
        label = " ".join(str(item.get("item", "")).strip().split())
        reason_raw = item.get("reason", "")
        reason = "" if reason_raw is None else " ".join(str(reason_raw).strip().split())
        if label:
            not_now_items.append({"item": label, "reason": reason})

    return {
        "matched_tasks": list(dict.fromkeys(matched)),
        "new_suggested_tasks": list(dict.fromkeys(suggested)),
        "not_now_items": not_now_items,
    }


def render_task_suggestions(payload: dict[str, Any]) -> str:
    """Render task suggestions as markdown inside the meeting note."""
    matched_tasks = payload.get("matched_tasks", [])
    new_suggested_tasks = payload.get("new_suggested_tasks", [])
    not_now_items = payload.get("not_now_items", [])

    lines = [
        "## 🧩 Sugestões de Tarefas",
        "",
        "### ✅ Já estavam programadas e apareceram na reunião",
    ]
    if matched_tasks:
        lines.extend(matched_tasks)
    else:
        lines.append("- Nenhuma")

    lines.extend([
        "",
        "### 🆕 Novas tarefas sugeridas pela reunião",
    ])
    if new_suggested_tasks:
        lines.extend(new_suggested_tasks)
    else:
        lines.append("- Nenhuma")

    lines.extend([
        "",
        "### ⚠️ Itens citados na reunião mas que NÃO viram tarefa agora",
    ])
    if not_now_items:
        for item in not_now_items:
            reason = item.get("reason", "").strip()
            suffix = f" — {reason}" if reason else ""
            lines.append(f"- {item.get('item', '').strip()}{suffix}")
    else:
        lines.append("- Nenhum")

    return "\n".join(lines)


def generate_task_suggestions(meeting_date: str, summary: str, transcript_text: str) -> str:
    """Generate a second-pass task suggestion block for the meeting note."""
    task_context = load_daily_task_context(meeting_date)
    pending_tasks = task_context["pending_tasks"]
    daily_tasks_block = "\n".join(pending_tasks) if pending_tasks else "(nenhuma task programada encontrada para esta data)"
    transcript_excerpt = truncate_text(transcript_text, max_chars=10000, head_chars=8000, tail_chars=1500)

    messages = [
        {"role": "system", "content": TASK_SUGGESTIONS_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": TASK_SUGGESTIONS_USER_PROMPT.format(
                meeting_date=meeting_date,
                daily_tasks=daily_tasks_block,
                summary=summary,
                transcript_excerpt=transcript_excerpt,
            ),
        },
    ]

    try:
        try:
            response_text = call_openrouter(
                messages=messages,
                max_tokens=TASK_SUGGESTIONS_MAX_TOKENS,
                temperature=TASK_SUGGESTIONS_TEMPERATURE,
                reasoning_effort="none",
                response_format={"type": "json_object"},
            )
        except RuntimeError as exc:
            if not should_retry_without_structured_output(exc):
                raise
            response_text = call_openrouter(
                messages=messages,
                max_tokens=TASK_SUGGESTIONS_MAX_TOKENS,
                temperature=TASK_SUGGESTIONS_TEMPERATURE,
                reasoning_effort="none",
                response_format=None,
            )
        try:
            parsed_payload = extract_json_object(response_text)
        except json.JSONDecodeError:
            parsed_payload = repair_json_object(response_text)
        payload = normalize_task_suggestion_payload(parsed_payload)
        payload["matched_tasks"] = compute_explicit_task_matches(pending_tasks, summary, transcript_text)
        return render_task_suggestions(payload)
    except Exception as exc:
        path = task_context.get("path")
        path_hint = f" Nota diária: `{path}`." if path else ""
        return (
            "## 🧩 Sugestões de Tarefas\n\n"
            f"> [!warning] Task suggestion generation failed: {exc}.{path_hint}"
        )


def parse_meetcap_transcript(txt_path: Path) -> dict[str, Any]:
    """Parse meetcap .txt transcript into structured data."""
    raw = txt_path.read_text(encoding="utf-8")
    lines = raw.splitlines()

    meta = {
        "date": "",
        "file": "",
        "model": "",
        "language": "",
        "duration": "",
    }

    content_start = 0
    for index, line in enumerate(lines):
        if line.startswith("Date:"):
            meta["date"] = line.split(":", 1)[1].strip()
        elif line.startswith("File:"):
            meta["file"] = line.split(":", 1)[1].strip()
        elif line.startswith("Model:"):
            meta["model"] = line.split(":", 1)[1].strip()
        elif line.startswith("Language:"):
            meta["language"] = line.split(":", 1)[1].strip()
        elif line.startswith("Duration:"):
            meta["duration"] = line.split(":", 1)[1].strip()
        elif line.strip() == "---":
            content_start = index + 1
            while content_start < len(lines) and not lines[content_start].strip():
                content_start += 1
            break

    transcript_lines = lines[content_start:]
    transcript_text = "\n".join(transcript_lines)

    fname = meta.get("file", "")
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})", fname)
    if date_match:
        meeting_date = date_match.group(1)
        meeting_time = date_match.group(2).replace("-", ":")
    else:
        meeting_date = meta.get("date", datetime.now(BRT).strftime("%Y-%m-%d"))
        meeting_time = datetime.now(BRT).strftime("%H:%M")

    duration_secs = 0.0
    duration_match = re.search(r"([\d.]+)s", meta.get("duration", ""))
    if duration_match:
        duration_secs = float(duration_match.group(1))

    duration_mins = int(duration_secs // 60)
    duration_str = f"{duration_mins} min" if duration_mins > 0 else f"{int(duration_secs)}s"

    return {
        "meta": meta,
        "meeting_date": meeting_date,
        "meeting_time": meeting_time,
        "duration_str": duration_str,
        "transcript_text": transcript_text,
        "line_count": len([line for line in transcript_lines if line.strip()]),
    }


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
    now: datetime | None = None,
) -> str:
    """Render the final Obsidian note content."""
    meta = data["meta"]
    now = now or datetime.now(BRT)

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

    title = build_note_title(data, custom_title)
    safe_name = title.replace("/", "-").replace(":", "-")
    filename = f"{safe_name}.md"

    MEETINGS_DIR.mkdir(parents=True, exist_ok=True)
    content = build_note_content(
        data=data,
        title=title,
        summary=summary,
        task_suggestions=task_suggestions,
    )

    out_path = MEETINGS_DIR / filename
    out_path.write_text(content, encoding="utf-8")
    print(f"[EXPORT] Saved to {out_path}")

    return {
        "success": True,
        "path": str(out_path),
        "filename": filename,
        "transcript_lines": data["line_count"],
        "summary_length": len(summary),
        "task_suggestions_length": len(task_suggestions),
        "duration": data["duration_str"],
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export meetcap transcript to Obsidian vault with AI summary")
    parser.add_argument("transcript", help="Path to meetcap .txt transcript")
    parser.add_argument("--title", help="Custom note title", default=None)
    args = parser.parse_args()

    result = export_note(Path(args.transcript), args.title)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("success") else 1)
