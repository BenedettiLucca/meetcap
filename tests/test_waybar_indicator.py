"""Tests for waybar-meetcap.py — render() pure function and socket query helpers.

Contract #46 (the state table is law):

  +---------------------------------------------+--------------------------------------+
  | State                                       | Output                                   |
  +---------------------------------------------+--------------------------------------+
  | daemon unreachable (status {} / None)       | {"text": "", "tooltip": "", "class": ""} |
  | idle, daemon ok                             | blank                                    |
  | recording                                   | "🎙 MM:SS" (H:MM:SS past 1h), class "recording" |
  | transcribing (and not recording)            | "📝", class "transcribing"              |
  | after activity                              | blank again                              |
  +---------------------------------------------+--------------------------------------+

Priority: recording > transcribing > blank. render() stays pure: with
now_s=None there is NO glyph alternation (CSS owns the blink). Only when the
module global BLINK_FALLBACK (wired by --blink-fallback in main()) is active
may the recording branch alternate 🔴/⚪ by int(now_s) % 2.

Covers:
  - render() every row of the #46 table, priority rule, empty-tooltip-on-blank
  - blink fallback parity: flag off / flag on / flag on without now_s (purity)
  - _elapsed_label() edge cases (offset-aware, naive, bad value)
  - _query_daemon() via real socketpair / tmpdir (no real daemon)
  - main() wiring --blink-fallback into the module global

No real daemon is started. Socket tests use os.socketpair() or a tmp
  listener thread — consistent with the repo pattern in conftest.py.
"""

import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

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

BLANK = {"text": "", "tooltip": "", "class": ""}


def _recording_status(seconds_ago: float = 75.0, **kwargs) -> dict:
    """Build a recording status dict with recording_since ``seconds_ago`` in the past."""
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


class TestRenderDaemonAbsent(unittest.TestCase):
    """Contract #46: daemon unreachable -> fully blank, no ghost tooltip."""

    def test_empty_dict_returns_blank(self):
        self.assertEqual(render({}), BLANK)

    def test_none_status_returns_blank(self):
        self.assertEqual(render(None), BLANK)  # type: ignore[arg-type]

    def test_none_status_blank_even_with_blink_fallback(self):
        with mock.patch.object(_mod, "BLINK_FALLBACK", True):
            self.assertEqual(render(None, now_s=1), BLANK)


class TestRenderIdle(unittest.TestCase):
    """Contract #46: idle (daemon ok) and after activity -> blank again."""

    def _idle_status(self, **kwargs):
        base = {"recording": False, "transcribing": False, "last_file": None, "error": None}
        base.update(kwargs)
        return base

    def test_idle_class_is_blank(self):
        self.assertEqual(render(self._idle_status())["class"], "")

    def test_idle_text_is_blank(self):
        self.assertEqual(render(self._idle_status())["text"], "")

    def test_idle_tooltip_is_blank(self):
        # No ghost tooltip over an invisible module.
        self.assertEqual(render(self._idle_status())["tooltip"], "")

    def test_idle_with_error_is_still_blank(self):
        # Idle is invisible even with a pending error; errors surface while
        # recording/transcribing (tooltip lines in those branches).
        result = render(self._idle_status(error="ffmpeg missing"))
        self.assertEqual(result, BLANK)

    def test_after_activity_is_blank(self):
        # "After activity": a status with no recording_since is not recording.
        status = {"recording": False, "transcribing": False,
                  "last_file": "synthetic.txt", "error": None}
        self.assertEqual(render(status), BLANK)


class TestRenderRecording(unittest.TestCase):
    """Contract #46: recording -> '🎙 MM:SS' (H:MM:SS past 1h), class recording."""

    def test_recording_class(self):
        self.assertEqual(render(_recording_status())["class"], "recording")

    def test_recording_text_includes_mic(self):
        self.assertIn("🎙", render(_recording_status())["text"])

    def test_recording_text_includes_elapsed(self):
        # 75 seconds ago → "01:15"
        text = render(_recording_status(seconds_ago=75))["text"]
        self.assertIn("01:15", text)

    def test_recording_text_hours(self):
        # 3665 seconds → 1:01:05
        text = render(_recording_status(seconds_ago=3665))["text"]
        self.assertIn("1:01:05", text)

    def test_recording_tooltip_mentions_elapsed(self):
        tooltip = render(_recording_status(seconds_ago=60))["tooltip"]
        # Tooltip should mention the elapsed time
        self.assertIn("01:00", tooltip)

    def test_recording_with_error_shows_warning(self):
        result = render(_recording_status(error="high CPU"))
        self.assertIn("⚠️", result["tooltip"])
        self.assertIn("high CPU", result["tooltip"])

    def test_recording_no_recording_since(self):
        """recording=True but no recording_since — still shows recording class."""
        status = {"recording": True, "transcribing": False, "error": None, "last_file": None}
        result = render(status)
        self.assertEqual(result["class"], "recording")
        self.assertEqual(result["text"].strip(), "🎙")


class TestRenderTranscribing(unittest.TestCase):
    """Contract #46: transcribing (and not recording) -> '📝', class transcribing."""

    def _transcribing_status(self, **kwargs):
        base = {
            "recording": False,
            "transcribing": True,
            "recording_since": None,
            "last_file": "synthetic.txt",
            "error": None,
        }
        base.update(kwargs)
        return base

    def test_transcribing_text_is_note(self):
        self.assertEqual(render(self._transcribing_status())["text"], "📝")

    def test_transcribing_class(self):
        self.assertEqual(render(self._transcribing_status())["class"], "transcribing")

    def test_transcribing_tooltip_mentions_activity(self):
        self.assertIn("Transcrevendo", render(self._transcribing_status())["tooltip"])

    def test_transcribing_with_error_shows_warning(self):
        result = render(self._transcribing_status(error="whisper OOM"))
        self.assertIn("⚠️", result["tooltip"])
        self.assertIn("whisper OOM", result["tooltip"])

    def test_transcribing_never_blinks(self):
        # The blink fallback applies to the recording branch only.
        with mock.patch.object(_mod, "BLINK_FALLBACK", True):
            self.assertEqual(render(self._transcribing_status(), now_s=1)["text"], "📝")


class TestStatePriority(unittest.TestCase):
    """Contract #46: recording beats transcribing beats blank."""

    def test_recording_beats_transcribing(self):
        status = {
            "recording": True,
            "transcribing": True,
            "recording_since": (datetime.now(timezone.utc) - timedelta(seconds=75)).isoformat(),
            "error": None,
        }
        result = render(status)
        self.assertEqual(result["class"], "recording")
        self.assertIn("🎙", result["text"])
        self.assertNotIn("📝", result["text"])


class TestBlinkFallback(unittest.TestCase):
    """--blink-fallback: recording glyph alternates 🔴/⚪ by int(now_s) % 2.

    Odd int(now_s) -> "🔴", even -> "⚪". Flag off or now_s=None -> static "🎙".
    """

    def setUp(self):
        self._saved_flag = getattr(_mod, "BLINK_FALLBACK", False)
        _mod.BLINK_FALLBACK = False

    def tearDown(self):
        _mod.BLINK_FALLBACK = self._saved_flag

    def test_flag_off_never_alternates(self):
        even = render(_recording_status(), now_s=0.0)["text"]
        odd = render(_recording_status(), now_s=1.0)["text"]
        self.assertTrue(even.startswith("🎙"), even)
        self.assertTrue(odd.startswith("🎙"), odd)
        self.assertEqual(even, odd)

    def test_flag_on_even_parity_glyph(self):
        _mod.BLINK_FALLBACK = True
        text = render(_recording_status(), now_s=2.7)["text"]
        self.assertTrue(text.startswith("⚪"), text)

    def test_flag_on_odd_parity_glyph(self):
        _mod.BLINK_FALLBACK = True
        text = render(_recording_status(), now_s=3.2)["text"]
        self.assertTrue(text.startswith("🔴"), text)

    def test_flag_on_parity_consistent(self):
        _mod.BLINK_FALLBACK = True
        even = render(_recording_status(), now_s=0.0)["text"]
        odd = render(_recording_status(), now_s=1.0)["text"]
        same_even = render(_recording_status(), now_s=2.0)["text"]
        self.assertEqual(even, same_even)
        self.assertNotEqual(even, odd)

    def test_purity_no_now_s_never_alternates(self):
        # Purity: with now_s=None the glyph must not alternate, even flag on.
        _mod.BLINK_FALLBACK = True
        self.assertEqual(render(_recording_status())["text"].split(" ")[0], "🎙")
        self.assertEqual(
            render(_recording_status(), now_s=None)["text"].split(" ")[0], "🎙"
        )

    def test_flag_on_without_recording_since(self):
        # Glyph-only recording branch (no timestamp): fallback drives the glyph.
        _mod.BLINK_FALLBACK = True
        status = {"recording": True, "transcribing": False, "error": None, "last_file": None}
        self.assertEqual(render(status, now_s=1)["text"], "🔴")
        self.assertEqual(render(status, now_s=0)["text"], "⚪")


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
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sock_path = self._start_fake_daemon(b"", tmp)
            import time
            time.sleep(0.05)
            result = _query_daemon(sock_path, timeout=2.0)
        self.assertEqual(result, {})

    def test_invalid_json_returns_empty_dict(self):
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


class TestMainBlinkFlag(unittest.TestCase):
    """main() wires the --blink-fallback CLI flag into the module global."""

    def setUp(self):
        self._saved_flag = getattr(_mod, "BLINK_FALLBACK", False)
        # Point the runtime dir at a temp dir so --once is a no-op socket query.
        self._tmp = tempfile.TemporaryDirectory()
        self._env_patch = mock.patch.dict(
            os.environ, {"MEETCAP_RUNTIME_DIR": self._tmp.name}, clear=False
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._tmp.cleanup()
        _mod.BLINK_FALLBACK = self._saved_flag

    def _run_main_once(self, *extra_args: str):
        argv = ["waybar-meetcap.py", "--once", *extra_args]
        with mock.patch.object(sys, "argv", argv):
            _mod.main()

    def test_main_default_flag_off(self):
        _mod.BLINK_FALLBACK = True
        self._run_main_once()
        self.assertFalse(_mod.BLINK_FALLBACK)

    def test_main_parses_blink_fallback(self):
        self._run_main_once("--blink-fallback")
        self.assertTrue(_mod.BLINK_FALLBACK)


if __name__ == "__main__":
    unittest.main()
