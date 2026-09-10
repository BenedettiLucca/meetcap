"""Tests for waybar-meetcap.py — render() pure function and socket query helpers.

Covers:
  - render() with no status (daemon absent)
  - render() while recording with recording_since (elapsed label)
  - render() while recording without recording_since
  - render() while idle (no recording, no error)
  - render() with error field present
  - _elapsed_label() edge cases (offset-aware, naive, bad value)
  - _query_daemon() via real socketpair / tmpdir (no real daemon)

No real daemon is started. Socket tests use os.socketpair() or a tmp
  listener thread — consistent with the repo pattern in conftest.py.
"""

import importlib.util
import json
import os
import socket
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure repo root is on path so we can import waybar-meetcap as a module
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# waybar-meetcap.py has a hyphen, so import via spec
_WAYBAR_PATH = REPO_ROOT / "waybar-meetcap.py"
_spec = importlib.util.spec_from_file_location("waybar_meetcap", _WAYBAR_PATH)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

render = _mod.render
_elapsed_label = _mod._elapsed_label
_query_daemon = _mod._query_daemon
_resolve_socket = _mod._resolve_socket


class TestRenderDaemonAbsent(unittest.TestCase):
    """render() when status is empty (daemon unreachable)."""

    def test_empty_dict_returns_inactive(self):
        result = render({})
        self.assertEqual(result["class"], "inactive")

    def test_empty_dict_text_is_mic_icon(self):
        result = render({})
        self.assertIn("🎙", result["text"])

    def test_empty_dict_has_tooltip(self):
        result = render({})
        self.assertIsInstance(result.get("tooltip"), str)
        self.assertTrue(len(result["tooltip"]) > 0)


class TestRenderIdle(unittest.TestCase):
    """render() when daemon is up but not recording."""

    def _idle_status(self, **kwargs):
        base = {"recording": False, "transcribing": False, "last_file": None, "error": None}
        base.update(kwargs)
        return base

    def test_idle_class_is_inactive(self):
        self.assertEqual(render(self._idle_status())["class"], "inactive")

    def test_idle_text_is_mic_only(self):
        # No elapsed time appended when not recording
        text = render(self._idle_status())["text"]
        self.assertEqual(text.strip(), "🎙")

    def test_idle_with_error_shows_error_in_tooltip(self):
        result = render(self._idle_status(error="ffmpeg missing"))
        self.assertIn("ffmpeg missing", result["tooltip"])

    def test_idle_no_error_tooltip_has_no_warning(self):
        result = render(self._idle_status())
        self.assertNotIn("⚠️", result["tooltip"])


class TestRenderRecording(unittest.TestCase):
    """render() when recording=True with a valid recording_since."""

    def _recording_status(self, seconds_ago: float = 75.0, **kwargs):
        """Build a status dict with recording_since set ``seconds_ago`` seconds in the past."""
        ts = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
        base = {
            "recording": True,
            "transcribing": False,
            "recording_since": ts.isoformat(),
            "last_file": None,
            "error": None,
        }
        base.update(kwargs)
        return base

    def test_recording_class(self):
        self.assertEqual(render(self._recording_status())["class"], "recording")

    def test_recording_text_includes_mic(self):
        self.assertIn("🎙", render(self._recording_status())["text"])

    def test_recording_text_includes_elapsed(self):
        # 75 seconds ago → "01:15"
        text = render(self._recording_status(seconds_ago=75))["text"]
        self.assertIn("01:15", text)

    def test_recording_text_hours(self):
        # 3665 seconds → 1:01:05
        text = render(self._recording_status(seconds_ago=3665))["text"]
        self.assertIn("1:01:05", text)

    def test_recording_tooltip_mentions_elapsed(self):
        tooltip = render(self._recording_status(seconds_ago=60))["tooltip"]
        # Tooltip should mention the elapsed time
        self.assertIn("01:00", tooltip)

    def test_recording_with_error_shows_warning(self):
        result = render(self._recording_status(error="high CPU"))
        self.assertIn("⚠️", result["tooltip"])
        self.assertIn("high CPU", result["tooltip"])

    def test_recording_no_recording_since(self):
        """recording=True but no recording_since — still shows recording class."""
        status = {"recording": True, "transcribing": False, "error": None, "last_file": None}
        result = render(status)
        self.assertEqual(result["class"], "recording")
        self.assertEqual(result["text"].strip(), "🎙")


class TestElapsedLabel(unittest.TestCase):
    """Unit tests for _elapsed_label() helper."""

    def _ts(self, seconds_ago: float) -> str:
        return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()

    def test_under_one_hour(self):
        self.assertEqual(_elapsed_label(self._ts(90)), "01:30")

    def test_zero_seconds(self):
        label = _elapsed_label(self._ts(0))
        self.assertRegex(label, r"^\d{2}:\d{2}$")

    def test_over_one_hour(self):
        label = _elapsed_label(self._ts(3661))
        self.assertTrue(label.startswith("1:"), f"Expected hours prefix, got {label!r}")

    def test_invalid_string_returns_question_mark(self):
        self.assertEqual(_elapsed_label("not-a-date"), "?:??")

    def test_none_returns_question_mark(self):
        self.assertEqual(_elapsed_label(None), "?:??")  # type: ignore[arg-type]

    def test_naive_timestamp_does_not_raise(self):
        # Naive ISO timestamp (no offset) — should parse without exception
        naive = datetime.now().replace(tzinfo=None).isoformat()
        label = _elapsed_label(naive)
        self.assertIsInstance(label, str)


class TestQueryDaemon(unittest.TestCase):
    """_query_daemon() against a real in-process UNIX socket listener."""

    def _start_fake_daemon(self, response: bytes, tmp_path: Path) -> Path:
        """Start a thread that accepts one connection and writes ``response``."""
        sock_path = tmp_path / "fake.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(1)
        srv.settimeout(3.0)

        def _serve():
            try:
                conn, _ = srv.accept()
                conn.recv(64)  # consume the "status\n" probe
                conn.sendall(response)
                conn.close()
            except OSError:
                pass
            finally:
                srv.close()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        return sock_path

    def test_absent_socket_returns_empty(self):
        result = _query_daemon(Path("/nonexistent/path/fake.sock"), timeout=0.5)
        self.assertEqual(result, {})

    def test_valid_json_response(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            payload = json.dumps({"recording": False, "error": None}).encode()
            sock_path = self._start_fake_daemon(payload, tmp)
            # Small sleep to ensure listener is ready
            import time
            time.sleep(0.05)
            result = _query_daemon(sock_path, timeout=2.0)
        self.assertEqual(result.get("recording"), False)

    def test_empty_response_returns_empty_dict(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sock_path = self._start_fake_daemon(b"", tmp)
            import time
            time.sleep(0.05)
            result = _query_daemon(sock_path, timeout=2.0)
        self.assertEqual(result, {})

    def test_invalid_json_returns_empty_dict(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sock_path = self._start_fake_daemon(b"not json at all", tmp)
            import time
            time.sleep(0.05)
            result = _query_daemon(sock_path, timeout=2.0)
        self.assertEqual(result, {})


class TestResolveSocket(unittest.TestCase):
    """_resolve_socket() path resolution matches MEETCAP_RUNTIME_DIR precedence."""

    def test_uses_meetcap_runtime_dir(self):
        env_backup = os.environ.copy()
        try:
            os.environ["MEETCAP_RUNTIME_DIR"] = "/custom/rt"
            os.environ.pop("XDG_RUNTIME_DIR", None)
            p = _resolve_socket()
            self.assertEqual(str(p), "/custom/rt/meetcap.sock")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    def test_falls_back_to_xdg(self):
        env_backup = os.environ.copy()
        try:
            os.environ.pop("MEETCAP_RUNTIME_DIR", None)
            os.environ["XDG_RUNTIME_DIR"] = "/run/user/1234"
            p = _resolve_socket()
            self.assertEqual(str(p), "/run/user/1234/meetcap/meetcap.sock")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)


if __name__ == "__main__":
    unittest.main()
