#!/usr/bin/env python3
"""waybar-meetcap.py — Waybar custom/meetcap indicator.

Queries the Meetcap daemon over its UNIX socket and prints a Waybar JSON block.

Usage (in waybar config):
  "exec": "waybar-meetcap.py"

Flags:
  --once             Print one JSON line and exit (useful for testing).
  --blink-fallback   Fallback for setups without the CSS blink: alternate the
                     recording glyph between 🔴 and ⚪ each second. CSS owns
                     the blink by default; render() stays static without this.

render(status, now_s=None) -> dict
  Pure function: given a daemon status dict, returns a Waybar JSON block.
  Importable without waybar or a running daemon.

State table (#46):
  - daemon unreachable / idle / after activity  -> blank ("", "", "")
  - recording                                    -> "🎙 MM:SS" (class "recording")
  - transcribing (and not recording)             -> "📝" (class "transcribing")
"""

import argparse
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Runtime path resolution (mirrors rofi-meetcap.sh order)
# ---------------------------------------------------------------------------

def _resolve_socket() -> Path:
    """Resolve daemon socket path using the same precedence as rofi-meetcap.sh."""
    meetcap_rt = os.environ.get("MEETCAP_RUNTIME_DIR")
    if meetcap_rt:
        runtime_dir = Path(meetcap_rt)
    else:
        xdg_rt = os.environ.get("XDG_RUNTIME_DIR")
        if xdg_rt:
            runtime_dir = Path(xdg_rt) / "meetcap"
        else:
            uid = os.getuid() if hasattr(os, "getuid") else 1000
            runtime_dir = Path(f"/run/user/{uid}/meetcap")
    return runtime_dir / "meetcap.sock"


# ---------------------------------------------------------------------------
# Pure render function — no I/O, no imports beyond stdlib datetime
# ---------------------------------------------------------------------------

# CLI flag --blink-fallback, wired in main(). When active, render() may
# alternate the recording glyph 🔴/⚪ by int(now_s) % 2. Without it the
# module stays static and the CSS blink animation owns the toggling.
BLINK_FALLBACK = False


def _elapsed_label(recording_since: str) -> str:
    """Return 'HH:MM' or 'MM:SS' elapsed string from an ISO-8601 timestamp."""
    try:
        # Accept both offset-aware and naive timestamps
        ts = datetime.fromisoformat(recording_since)
        # Normalise to UTC for arithmetic
        if ts.tzinfo is None:
            # Treat naive as local time; get UTC offset from local system
            local_offset = datetime.now(timezone.utc).astimezone().utcoffset()
            ts = ts.replace(tzinfo=timezone.utc) - (local_offset or __import__('datetime').timedelta(0))
        now = datetime.now(timezone.utc)
        elapsed = int((now - ts).total_seconds())
        if elapsed < 0:
            elapsed = 0
    except (ValueError, TypeError, OverflowError):
        return "?:??"

    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def render(status: Optional[dict], now_s: Optional[float] = None) -> dict:
    """Build Waybar JSON block from daemon status dict.

    Pure function — no side effects, no I/O.

    State table (#46):
      - daemon unreachable (status is None / empty) -> blank
      - idle or after activity                      -> blank
      - recording                                    -> "🎙 MM:SS" (class "recording")
      - transcribing (and not recording)             -> "📝" (class "transcribing")

    Priority: recording > transcribing > blank. The blank block carries an
    empty tooltip so waybar does not show a ghost tooltip over an invisible
    module.

    Args:
        status: Dict from the daemon `status` response, or None if the daemon
            is unreachable.
        now_s: Optional current time (seconds, e.g. time.time()). With it and
            the global BLINK_FALLBACK active, the recording glyph alternates
            🔴/⚪ by int(now_s) % 2. With now_s=None render() is static —
            CSS owns the blink.

    Returns:
        Waybar-compatible dict with keys: text, tooltip, class.
    """
    if not status:
        # Daemon unreachable or nothing to report — blank, no ghost tooltip.
        return {"text": "", "tooltip": "", "class": ""}

    is_recording: bool = bool(status.get("recording", False))
    is_transcribing: bool = bool(status.get("transcribing", False))
    recording_since: Optional[str] = status.get("recording_since")
    error: Optional[str] = status.get("error") or None

    if is_recording:
        if recording_since:
            elapsed = _elapsed_label(recording_since)
            glyph = _recording_glyph(now_s)
            text = f"{glyph} {elapsed}"
            tooltip_parts = [f"Gravando há {elapsed}"]
        else:
            # Recording flag set but no timestamp available.
            text = _recording_glyph(now_s)
            tooltip_parts = ["Gravando"]
        if error:
            tooltip_parts.append(f"⚠️ {error}")
        return {"text": text, "tooltip": "\n".join(tooltip_parts), "class": "recording"}

    if is_transcribing:
        tooltip_parts = ["Transcrevendo"]
        if error:
            tooltip_parts.append(f"⚠️ {error}")
        return {"text": "📝", "tooltip": "\n".join(tooltip_parts), "class": "transcribing"}

    # Idle or after activity — blank again.
    return {"text": "", "tooltip": "", "class": ""}


def _recording_glyph(now_s: Optional[float] = None) -> str:
    """Glyph for the recording branch.

    Static "🎙" unless the --blink-fallback global is active AND the caller
    passed a per-render reference (now_s): then alternate 🔴 (odd) /
    ⚪ (even) by int(now_s) % 2. Purity rule: with now_s=None the glyph
    never alternates — CSS owns the blink.
    """
    if not BLINK_FALLBACK or now_s is None:
        return "🎙"
    return "🔴" if int(now_s) % 2 else "⚪"


# ---------------------------------------------------------------------------
# Daemon query — best-effort, silent on connection failure
# ---------------------------------------------------------------------------

def _query_daemon(sock_path: Path, timeout: float = 2.0) -> dict:
    """Query daemon status; return empty dict if daemon is absent or times out."""
    if not sock_path.exists():
        return {}
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(str(sock_path))
            s.sendall(b"status\n")
            chunks: list[bytes] = []
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
            if not raw:
                return {}
            return json.loads(raw)
        except (OSError, json.JSONDecodeError, socket.timeout):
            return {}
        finally:
            try:
                s.close()
            except OSError:
                pass
    except OSError:
        return {}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _print_block(sock_path: Path, now_s: Optional[float] = None) -> None:
    status = _query_daemon(sock_path)
    block = render(status, now_s=now_s)
    print(json.dumps(block, ensure_ascii=False), flush=True)


def main() -> None:
    global BLINK_FALLBACK

    parser = argparse.ArgumentParser(description="Meetcap Waybar indicator")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print one JSON line and exit (for testing / scripting)",
    )
    parser.add_argument(
        "--blink-fallback",
        action="store_true",
        help="Alternate the recording glyph 🔴/⚪ each second instead of "
             "relying on the CSS blink animation",
    )
    args = parser.parse_args()

    # CSS owns the blink by default; only the flag opts into glyph alternation.
    BLINK_FALLBACK = args.blink_fallback

    sock_path = _resolve_socket()

    if args.once:
        _print_block(sock_path)
        return

    # Loop: waybar re-invokes via exec+interval, but also works standalone
    try:
        while True:
            _print_block(sock_path, now_s=time.time())
            time.sleep(5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
