import json
import re
import unicodedata
from pathlib import Path
from typing import Any
from .config import (
    TASKS_DIR,
    TASKS_ARCHIVE_DIR,
    TASK_SUGGESTIONS_MAX_TOKENS,
    TASK_SUGGESTIONS_TEMPERATURE,
    TASK_SUGGESTIONS_TRANSCRIPT_MAX_CHARS,
)
from .prompts import (
    TASK_SUGGESTIONS_SYSTEM_PROMPT,
    TASK_SUGGESTIONS_USER_PROMPT,
    JSON_REPAIR_SYSTEM_PROMPT,
)
from .llm_client import call_openrouter, should_retry_without_structured_output
from .transcript_parser import truncate_text

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
    transcript_excerpt = truncate_text(
        transcript_text,
        max_chars=TASK_SUGGESTIONS_TRANSCRIPT_MAX_CHARS,
        head_chars=8000,
        tail_chars=1500,
    )

    messages = [
        {"role": "system", "content": TASK_SUGGESTIONS_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": TASK_SUGGESTIONS_USER_PROMPT.format(
                meeting_date=meeting_date,
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
