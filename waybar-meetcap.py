#!/usr/bin/env python3
"""waybar-meetcap.py — Waybar custom/meetcap indicator.

Queries the Meetcap daemon over its UNIX socket and prints a Waybar JSON block.

Usage (in waybar config):
  "exec": "waybar-meetcap.py"

Flags:
  --once    Print one JSON line and exit (useful for testing).

render(status_dict) -> dict
  Pure function: given a daemon status dict, returns a Waybar JSON block.
  Importable without waybar or a running daemon.
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


def render(status: dict) -> dict:
    """Build Waybar JSON block from daemon status dict.

    Pure function — no side effects, no I/O.

    Args:
        status: Dict from daemon `status` response (may be empty if daemon is down).

    Returns:
        Waybar-compatible dict with keys: text, tooltip, class.
    """
    if not status:
        # Daemon absent or unreachable — show dim icon, no spam
        return {"text": "🎙", "tooltip": "Meetcap: daemon inativo", "class": "inactive"}

    recording_since: Optional[str] = status.get("recording_since")
    is_recording: bool = bool(status.get("recording", False))
    error: Optional[str] = status.get("error") or None

    if is_recording and recording_since:
        elapsed = _elapsed_label(recording_since)
        text = f"🎙 {elapsed}"
        tooltip_parts = [f"Gravando há {elapsed}"]
        if error:
            tooltip_parts.append(f"⚠️ {error}")
        tooltip = "\n".join(tooltip_parts)
        css_class = "recording"
    elif is_recording:
        # Recording flag set but no timestamp available
        text = "🎙"
        tooltip_parts = ["Gravando"]
        if error:
            tooltip_parts.append(f"⚠️ {error}")
        tooltip = "\n".join(tooltip_parts)
        css_class = "recording"
    else:
        text = "🎙"
        tooltip_parts = ["Meetcap: inativo"]
        if error:
            tooltip_parts.append(f"⚠️ {error}")
        tooltip = "\n".join(tooltip_parts)
        css_class = "inactive"

    return {"text": text, "tooltip": tooltip, "class": css_class}


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

def _print_block(sock_path: Path) -> None:
    status = _query_daemon(sock_path)
    block = render(status)
    print(json.dumps(block, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Meetcap Waybar indicator")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print one JSON line and exit (for testing / scripting)",
    )
    args = parser.parse_args()

    sock_path = _resolve_socket()

    if args.once:
        _print_block(sock_path)
        return

    # Loop: waybar re-invokes via exec+interval, but also works standalone
    try:
        while True:
            _print_block(sock_path)
            time.sleep(5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
