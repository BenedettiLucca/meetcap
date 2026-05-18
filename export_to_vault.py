#!/usr/bin/env python3
"""
Meetcap → Obsidian vault exporter with AI-powered summary.

Called automatically after transcription completes.
Reads the meetcap .txt transcript, generates a structured Obsidian note
with full transcript + AI summary + action items, saves to vault.

Can also be called standalone:
  python3 export_to_vault.py /path/to/transcript.txt [--title "Custom Title"]
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ── Config ──────────────────────────────────────────────────────────
VAULT = os.environ.get("OBSIDIAN_VAULT_PATH", "/home/lucca/HD2/vault")
MEETINGS_DIR = Path(VAULT) / "Meetings"

BRT = timezone(timedelta(hours=-3))

# LLM config for summary generation
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
LLM_MODEL = os.environ.get("MEETCAP_LLM_MODEL", "openai/gpt-4o-mini")
LLM_MAX_TOKENS = 2048

PROMPT_TEMPLATE = """You are a meeting assistant. Given this transcript, generate a structured summary.

TRANSCRIPT:
{transcript}

Respond in the SAME LANGUAGE as the transcript (Portuguese or English).
Format your response EXACTLY as follows — use the headers and bullet points:

## 📌 Summary
[2-4 paragraph summary of what was discussed. Be specific, not generic.]

## 🔑 Key Points
- [Point 1]
- [Point 2]
- [etc]

## ✅ Action Items
- [ ] [Action item 1] — @owner (if mentioned)
- [ ] [Action item 2] — @owner
- [etc]

## ⚠️ Open Questions / Risks
- [Any unresolved issues or risks identified]

Do NOT include any preamble or outro. Start directly with ## 📌 Summary."""


def load_openrouter_key() -> str:
    """Load OpenRouter API key from hermes .env if not in env."""
    if OPENROUTER_KEY:
        return OPENROUTER_KEY
    env_file = Path.home() / ".hermes" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return ""


def generate_summary(transcript_text: str) -> str:
    """Call OpenRouter LLM to generate meeting summary."""
    api_key = load_openrouter_key()
    if not api_key:
        return "> [!warning] Summary generation failed — no API key configured"

    # Truncate very long transcripts to ~15k chars for the prompt
    truncated = transcript_text
    if len(transcript_text) > 15000:
        truncated = transcript_text[:12000] + "\n\n[... transcript truncated for summary ...]\n" + transcript_text[-3000:]

    prompt = PROMPT_TEMPLATE.format(transcript=truncated)

    payload = json.dumps({
        "model": LLM_MODEL,
        "max_tokens": LLM_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    })

    try:
        result = subprocess.run(
            [
                "curl", "-s", "--max-time", "120",
                "https://openrouter.ai/api/v1/chat/completions",
                "-H", f"Authorization: Bearer {api_key}",
                "-H", "Content-Type: application/json",
                "-d", payload,
            ],
            capture_output=True, text=True, timeout=130,
        )
        resp = json.loads(result.stdout)
        if "choices" in resp and resp["choices"]:
            return resp["choices"][0]["message"]["content"].strip()
        return f"> [!warning] LLM error: {json.dumps(resp)[:200]}"
    except Exception as e:
        return f"> [!warning] Summary generation failed: {e}"


def parse_meetcap_transcript(txt_path: Path) -> dict:
    """Parse meetcap .txt transcript into structured data."""
    raw = txt_path.read_text(encoding="utf-8")
    lines = raw.splitlines()

    # Extract header metadata
    meta = {
        "date": "",
        "file": "",
        "model": "",
        "language": "",
        "duration": "",
    }

    content_start = 0
    for i, line in enumerate(lines):
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
            content_start = i + 1
            # Skip blank line after ---
            while content_start < len(lines) and not lines[content_start].strip():
                content_start += 1
            break

    transcript_lines = lines[content_start:]
    transcript_text = "\n".join(transcript_lines)

    # Extract date/time from filename if available
    # meeting-2026-05-18_12-44-12.wav
    fname = meta.get("file", "")
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})", fname)
    if date_match:
        meeting_date = date_match.group(1)
        meeting_time = date_match.group(2).replace("-", ":")
    else:
        meeting_date = meta.get("date", datetime.now(BRT).strftime("%Y-%m-%d"))
        meeting_time = datetime.now(BRT).strftime("%H:%M")

    duration_secs = 0
    dur_match = re.search(r"([\d.]+)s", meta.get("duration", ""))
    if dur_match:
        duration_secs = float(dur_match.group(1))

    duration_mins = int(duration_secs // 60)
    duration_str = f"{duration_mins} min" if duration_mins > 0 else f"{int(duration_secs)}s"

    return {
        "meta": meta,
        "meeting_date": meeting_date,
        "meeting_time": meeting_time,
        "duration_str": duration_str,
        "transcript_text": transcript_text,
        "line_count": len([l for l in transcript_lines if l.strip()]),
    }


def build_note_title(data: dict, custom_title: str | None = None) -> str:
    """Generate a note title."""
    if custom_title:
        return custom_title
    date = data["meeting_date"]
    mins = data["duration_str"]
    return f"Meeting — {date} ({mins})"


def export_note(txt_path: Path, custom_title: str | None = None) -> dict:
    """Main export: transcript → obsidian note with summary."""
    if not txt_path.exists():
        return {"success": False, "error": f"Transcript not found: {txt_path}"}

    data = parse_meetcap_transcript(txt_path)
    if not data["transcript_text"].strip():
        return {"success": False, "error": "Transcript is empty"}

    print(f"[EXPORT] Generating AI summary for {txt_path.name}...")
    summary = generate_summary(data["transcript_text"])
    print(f"[EXPORT] Summary generated ({len(summary)} chars)")

    title = build_note_title(data, custom_title)
    safe_name = title.replace("/", "-").replace(":", "-").replace("—", "—")
    filename = f"{safe_name}.md"

    MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(BRT)
    meta = data["meta"]

    content = f"""---
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

## 📝 Transcrição Completa

{data['transcript_text']}

---

> [!meta] Gerado automaticamente pelo Meetcap + Hermes
> Arquivo original: `{meta.get('file', 'N/A')}`
"""

    out_path = MEETINGS_DIR / filename
    out_path.write_text(content, encoding="utf-8")
    print(f"[EXPORT] Saved to {out_path}")

    return {
        "success": True,
        "path": str(out_path),
        "filename": filename,
        "transcript_lines": data["line_count"],
        "summary_length": len(summary),
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
