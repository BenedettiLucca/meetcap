"""Minimum-privilege environment helper for Meetcap.

Safely loads a restricted allowlist of environment keys from simple .env files
without evaluating code, invoking a subshell, or leaking unrelated credentials.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterable


def _is_allowed(key: str, allowed: Iterable[str]) -> bool:
    """Check if key matches any entry or glob pattern in allowed."""
    for pattern in allowed:
        if pattern == key:
            return True
        if ("*" in pattern or "?" in pattern or "[" in pattern) and fnmatch.fnmatchcase(key, pattern):
            return True
    return False


def _parse_line(raw_line: str) -> tuple[str, str] | None:
    """Parse a single line from a simple .env file without shell/eval."""
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None

    if line.startswith("export "):
        line = line[7:].strip()

    if "=" not in line:
        return None

    key, raw_val = line.split("=", 1)
    key = key.strip()
    if not key or key.startswith("#"):
        return None

    val = raw_val.strip()
    if not val:
        return key, ""

    # Quoted values: parse between surrounding quotes
    if val.startswith(('"', "'")):
        quote_char = val[0]
        end_idx = val.find(quote_char, 1)
        if end_idx != -1:
            return key, val[1:end_idx]
        return key, val[1:]

    # Unquoted values: strip inline comment starting with ' #'
    comment_idx = val.find(" #")
    if comment_idx != -1:
        val = val[:comment_idx].rstrip()
    elif val.startswith("#"):
        val = ""

    return key, val


def load_env_keys(
    path: str | Path | None,
    allowed: Iterable[str] | None,
) -> dict[str, str]:
    """Read a simple .env file and return ONLY keys matching the allowlist.

    Parameters:
        path: Path to the .env file. If missing, unreadable, or None, returns {}.
        allowed: Iterable of allowed key names or glob patterns (e.g. ['OPENROUTER_API_KEY', 'MEETCAP_*']).

    Returns:
        Dict mapping allowed keys to their parsed string values.
    """
    if path is None or not allowed:
        return {}

    if isinstance(allowed, str):
        allowed_list = [allowed]
    else:
        allowed_list = list(allowed)

    if not allowed_list:
        return {}

    file_path = Path(path)
    try:
        if not file_path.is_file():
            return {}
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    result: dict[str, str] = {}
    for line in content.splitlines():
        parsed = _parse_line(line)
        if parsed is None:
            continue
        key, val = parsed
        if _is_allowed(key, allowed_list):
            result[key] = val

    return result
