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

_TASK_STOPWORDS = {
    "a", "o", "os", "as", "um", "uma", "uns", "umas",
    "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas",
    "para", "por", "com", "e",
    "the", "an", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by",
}

def extract_summary_action_items(summary: str) -> list[str]:
    """Extract action item texts from the summary note."""
    if not summary:
        return []

    action_lines = extract_section_lines(summary, "## ✅ Action Items")
    if not action_lines:
        action_lines = extract_section_lines(summary, "## Action Items")
    if not action_lines:
        in_section = False
        for raw_line in summary.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if stripped.startswith("## "):
                norm_h = normalize_for_exact_match(stripped)
                if "action items" in norm_h or "acoes" in norm_h:
                    in_section = True
                    continue
                elif in_section:
                    break
            if in_section:
                action_lines.append(line)

    items: list[str] = []
    for line in action_lines:
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            task = strip_task_prefix(stripped)
            if task:
                items.append(task)
        elif stripped.startswith("- "):
            task = stripped[2:].strip()
            if task:
                items.append(task)
    return items

def clean_task_text_for_comparison(text: str) -> str:
    """Strip checkbox prefixes, owner/deadline metadata tags, and punctuation for comparison."""
    text = strip_task_prefix(text)
    text = re.sub(r"\s*\((?:owner|deadline)[^)]*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\.,;:!\?]+$", "", text.strip())
    return " ".join(text.split())

def normalize_for_task_comparison(text: str) -> str:
    """Clean and normalize a task string for deduplication."""
    cleaned = clean_task_text_for_comparison(text)
    norm = normalize_for_exact_match(cleaned)
    norm = re.sub(r"[^\w\s]", "", norm)
    return re.sub(r"\s+", " ", norm).strip()

def find_matching_task(candidate: str, existing_tasks: list[str]) -> str | None:
    """Return the matched existing task if candidate is a duplicate, else None."""
    cand_cleaned = clean_task_text_for_comparison(candidate)
    cand_norm = normalize_for_task_comparison(cand_cleaned)
    if not cand_norm:
        return None

    cand_tokens = {t for t in cand_norm.split() if t not in _TASK_STOPWORDS}

    for existing in existing_tasks:
        exist_cleaned = clean_task_text_for_comparison(existing)
        exist_norm = normalize_for_task_comparison(exist_cleaned)
        if not exist_norm:
            continue

        if cand_norm == exist_norm:
            return existing

        if len(cand_norm) >= 12 and len(exist_norm) >= 12:
            if cand_norm in exist_norm or exist_norm in cand_norm:
                return existing

        exist_tokens = {t for t in exist_norm.split() if t not in _TASK_STOPWORDS}
        if cand_tokens and exist_tokens:
            if len(cand_tokens) >= 3 and cand_tokens.issubset(exist_tokens):
                return existing
            if len(exist_tokens) >= 3 and exist_tokens.issubset(cand_tokens):
                return existing
            intersection = cand_tokens & exist_tokens
            union = cand_tokens | exist_tokens
            if union and (len(intersection) / len(union)) >= 0.8:
                return existing

    return None

def is_task_duplicate(candidate: str, existing_tasks: list[str]) -> bool:
    """Check if candidate task is duplicate or rewording of an existing task."""
    return find_matching_task(candidate, existing_tasks) is not None

def deduplicate_suggested_tasks(
    payload: dict[str, Any],
    summary: str = "",
    matched_tasks: list[str] | None = None,
) -> dict[str, Any]:
    """Deduplicate new_suggested_tasks against summary action items and matched tasks."""
    suggested = payload.get("new_suggested_tasks", [])
    not_now_items = list(payload.get("not_now_items", []))
    summary_action_items = extract_summary_action_items(summary) if summary else []
    known_matched = list(matched_tasks if matched_tasks is not None else payload.get("matched_tasks", []))

    filtered_suggested: list[str] = []
    admitted_tasks: list[str] = []

    for task in suggested:
        matched_summary = find_matching_task(task, summary_action_items)
        if matched_summary:
            clean_item = clean_task_text_for_comparison(task)
            clean_ref = clean_task_text_for_comparison(matched_summary)
            already_in_not_now = any(
                is_task_duplicate(clean_item, [item.get("item", "")])
                for item in not_now_items
            )
            if not already_in_not_now and clean_item:
                not_now_items.append({
                    "item": clean_item,
                    "reason": f"já coberto pelo summary: \"{clean_ref}\"",
                })
            continue

        if is_task_duplicate(task, known_matched):
            continue

        if is_task_duplicate(task, admitted_tasks):
            continue

        filtered_suggested.append(task)
        admitted_tasks.append(task)

    return {
        "matched_tasks": payload.get("matched_tasks", []),
        "new_suggested_tasks": filtered_suggested,
        "not_now_items": not_now_items,
    }

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

def normalize_task_suggestion_payload(
    payload: dict[str, Any],
    summary: str = "",
    matched_tasks: list[str] | None = None,
) -> dict[str, Any]:
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

    result = {
        "matched_tasks": list(dict.fromkeys(matched)),
        "new_suggested_tasks": list(dict.fromkeys(suggested)),
        "not_now_items": not_now_items,
    }
    if summary or matched_tasks:
        result = deduplicate_suggested_tasks(result, summary=summary, matched_tasks=matched_tasks)
    return result

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
        payload = deduplicate_suggested_tasks(payload, summary=summary, matched_tasks=payload["matched_tasks"])
        return render_task_suggestions(payload)
    except Exception as exc:
        path = task_context.get("path")
        path_hint = f" Nota diária: `{path}`." if path else ""
        return (
            "## 🧩 Sugestões de Tarefas\n\n"
            f"> [!warning] Task suggestion generation failed: {exc}.{path_hint}"
        )
