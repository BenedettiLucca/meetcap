import re
from datetime import datetime
from pathlib import Path
from typing import Any
from .config import BRT

def truncate_text(text: str, max_chars: int, head_chars: int, tail_chars: int) -> str:
    """Truncate long text while keeping the beginning and end."""
    if len(text) <= max_chars:
        return text
    return text[:head_chars] + "\n\n[... content truncated ...]\n\n" + text[-tail_chars:]

def split_oversized_line(line: str, max_chars: int) -> list[str]:
    """Split a single oversized line while preserving word order."""
    if len(line) <= max_chars:
        return [line]

    pieces: list[str] = []
    current = ""
    for word in line.split(" "):
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            pieces.append(current)
            current = word
        else:
            pieces.append(word[:max_chars])
            remainder = word[max_chars:]
            while len(remainder) > max_chars:
                pieces.append(remainder[:max_chars])
                remainder = remainder[max_chars:]
            current = remainder
    if current:
        pieces.append(current)
    return pieces

def split_transcript_into_chunks(transcript_text: str, max_chars: int) -> list[str]:
    """Split a long transcript into line-preserving chunks under a character budget."""
    if len(transcript_text) <= max_chars:
        return [transcript_text]

    chunks: list[str] = []
    current_lines: list[str] = []
    current_len = 0

    for raw_line in transcript_text.splitlines():
        line_parts = split_oversized_line(raw_line, max_chars)
        for line in line_parts:
            projected = len(line) if not current_lines else current_len + 1 + len(line)
            if current_lines and projected > max_chars:
                chunks.append("\n".join(current_lines))
                current_lines = [line]
                current_len = len(line)
            else:
                current_lines.append(line)
                current_len = projected if current_lines[:-1] else len(line)

    if current_lines:
        chunks.append("\n".join(current_lines))

    return chunks or [transcript_text]

# Timestamp: total-minutes MM:SS (any width, #26) or H:MM:SS; second field padded.
SEGMENT_PATTERN = re.compile(
    r"^\[(\d+:\d{2}(?::\d{2})?)\s*(?:→|->|—|to)\s*(\d+:\d{2}(?::\d{2})?)\]\s*(.*)$"
)

def parse_transcript_segments(transcript_text: str) -> list[dict[str, Any]]:
    """Parse timestamped segments ('[MM:SS → MM:SS] text') from transcript text."""
    segments: list[dict[str, Any]] = []
    for index, line in enumerate(transcript_text.splitlines()):
        match = SEGMENT_PATTERN.match(line.strip())
        if not match:
            continue
        segments.append({
            "index": len(segments),
            "start": match.group(1),
            "end": match.group(2),
            "text": match.group(3).strip(),
        })
    return segments

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
        "segments": parse_transcript_segments(transcript_text),
        "line_count": len([line for line in transcript_lines if line.strip()]),
    }
