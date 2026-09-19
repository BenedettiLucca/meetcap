"""Shared transcript writer path: aggregate whisper segments into readable spans.

Used by both transcription writer paths in meetcap.py (router JSON and local
faster-whisper) so the `[MM:SS → MM:SS] text` line format and merge policy stay
in one place. Aggregation happens at export time only; the exporter consumes the
.txt as-is.
"""


def aggregate_segments(segments: list[dict], gap_s: float = 0.5) -> list[dict]:
    """Merge contiguous whisper segments into sentence/turn-sized spans.

    Merge when next["start"] - prev["end"] < gap_s (overlaps, i.e. negative
    gaps, also merge). Each input: {"start": float, "end": float, "text": str}.
    Output keeps start of first and end of last; text = " ".join(stripped).
    No text may be lost or reordered.
    """
    out: list[dict] = []
    for seg in segments:
        if out and seg["start"] - out[-1]["end"] < gap_s:
            out[-1]["end"] = seg["end"]
            out[-1]["text"] = f"{out[-1]['text']} {seg['text'].strip()}"
        else:
            out.append({"start": seg["start"], "end": seg["end"], "text": seg["text"].strip()})
    return out


def _format_timestamp(seconds: float) -> str:
    """MM:SS under one hour, H:MM:SS at/over one hour (meetcap formatter semantics)."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_transcript_lines(segments: list[dict], gap_s: float = 0.5) -> list[str]:
    """Aggregate segments and emit the `[start → end] text` transcript lines."""
    return [
        f"[{_format_timestamp(span['start'])} → {_format_timestamp(span['end'])}] {span['text']}"
        for span in aggregate_segments(segments, gap_s=gap_s)
    ]
