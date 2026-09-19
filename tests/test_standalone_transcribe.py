"""Tests for standalone `transcribe <wav>` subcommand (#29).

Contract (issue #29, option 1 — keep both, distinct names):
- `transcribe <path.wav>` = standalone: runs local transcribe(Path(path)) WITHOUT daemon.
  Missing file → explicit error, non-zero exit.
  Extra unexpected args → explicit error, never silently ignored.
- `transcribe-last` = the existing daemon command (backward compat).
- bare `transcribe` with no arg in CLI falls through to the daemon (`transcribe-last` semantics).
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import meetcap


class StandaloneTranscribeCLITests(unittest.TestCase):
    """#29 — standalone_transcribe_cmd dispatches to local transcribe() with exact path."""

    def test_standalone_transcribe_calls_transcribe_with_wav_a(self):
        """wav_a is forwarded to transcribe(); wav_b is not."""
        with tempfile.TemporaryDirectory() as tmp:
            wav_a = Path(tmp) / "meeting-a.wav"
            wav_a.write_bytes(b"\x00" * 100)

            with patch("meetcap.transcribe", return_value=str(wav_a.with_suffix(".txt"))) as mock_t, \
                 patch("meetcap.notify"):
                result = meetcap.standalone_transcribe_cmd(str(wav_a))

        self.assertTrue(result["ok"])
        mock_t.assert_called_once_with(Path(str(wav_a)))

    def test_standalone_transcribe_calls_transcribe_with_wav_b(self):
        """wav_b is forwarded to transcribe(); wav_a is not used."""
        with tempfile.TemporaryDirectory() as tmp:
            wav_b = Path(tmp) / "meeting-b.wav"
            wav_b.write_bytes(b"\x00" * 100)

            with patch("meetcap.transcribe", return_value=str(wav_b.with_suffix(".txt"))) as mock_t, \
                 patch("meetcap.notify"):
                result = meetcap.standalone_transcribe_cmd(str(wav_b))

        self.assertTrue(result["ok"])
        mock_t.assert_called_once_with(Path(str(wav_b)))

    def test_two_different_paths_not_confused(self):
        """Invoking standalone_transcribe_cmd twice with different paths passes the correct path each time."""
        with tempfile.TemporaryDirectory() as tmp:
            wav_a = Path(tmp) / "meeting-a.wav"
            wav_a.write_bytes(b"\x00" * 100)
            wav_b = Path(tmp) / "meeting-b.wav"
            wav_b.write_bytes(b"\x00" * 100)

            received = []

            def capture_path(path):
                received.append(path)
                return str(path.with_suffix(".txt"))

            with patch("meetcap.transcribe", side_effect=capture_path), \
                 patch("meetcap.notify"):
                meetcap.standalone_transcribe_cmd(str(wav_a))
                meetcap.standalone_transcribe_cmd(str(wav_b))

        self.assertEqual(received[0], Path(str(wav_a)))
        self.assertEqual(received[1], Path(str(wav_b)))
        self.assertNotEqual(received[0], received[1])

    def test_missing_file_returns_error_non_zero(self):
        """Missing WAV → explicit error dict with ok=False; main() must sys.exit non-zero."""
        result = meetcap.standalone_transcribe_cmd("/tmp/does-not-exist-12345.wav")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)
        self.assertTrue(len(result["error"]) > 0)

    def test_transcribe_exception_returns_error(self):
        """If transcribe() raises, standalone_transcribe_cmd returns ok=False."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "meeting.wav"
            wav.write_bytes(b"\x00" * 100)

            with patch("meetcap.transcribe", side_effect=RuntimeError("whisper crash")), \
                 patch("meetcap.notify"):
                result = meetcap.standalone_transcribe_cmd(str(wav))

        self.assertFalse(result["ok"])
        self.assertIn("whisper crash", result["error"])


class MainTranscribeRoutingTests(unittest.TestCase):
    """#29 — main() routes 'transcribe <path>' to standalone, bare 'transcribe' to daemon."""

    def _run_main(self, argv):
        with patch.object(sys, "argv", argv):
            try:
                meetcap.main()
            except SystemExit as e:
                return e.code
        return 0

    def test_transcribe_with_path_routes_to_standalone(self):
        """main() with 'transcribe /path/to.wav' calls standalone_transcribe_cmd, not daemon."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "meeting.wav"
            wav.write_bytes(b"\x00" * 100)

            with patch("meetcap.standalone_transcribe_cmd", return_value={"ok": True, "file": str(wav)}) as mock_s, \
                 patch("meetcap.send_command") as mock_send, \
                 patch("builtins.print"):
                self._run_main(["meetcap.py", "transcribe", str(wav)])

        mock_s.assert_called_once_with(str(wav))
        mock_send.assert_not_called()

    def test_bare_transcribe_routes_to_daemon(self):
        """main() with bare 'transcribe' (no path) sends to daemon as transcribe-last."""
        with patch("meetcap.send_command", return_value='{"ok": true}') as mock_send, \
             patch("meetcap.SOCKET_PATH") as mock_sock, \
             patch("builtins.print"):
            mock_sock.exists.return_value = True
            self._run_main(["meetcap.py", "transcribe"])

        # Should send to daemon; standalone NOT called
        mock_send.assert_called_once()
        sent_cmd = mock_send.call_args[0][0]
        self.assertEqual(sent_cmd, "transcribe-last")

    def test_extra_args_after_path_returns_error(self):
        """main() with 'transcribe path.wav extra' exits non-zero with explicit error."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "meeting.wav"
            wav.write_bytes(b"\x00" * 100)

            with patch("builtins.print"), patch("sys.stderr"):
                rc = self._run_main(["meetcap.py", "transcribe", str(wav), "extra-arg"])

        self.assertNotEqual(rc, 0)

    def test_transcribe_last_command_routes_to_daemon(self):
        """main() with 'transcribe-last' sends to daemon verbatim."""
        with patch("meetcap.send_command", return_value='{"ok": true}') as mock_send, \
             patch("meetcap.SOCKET_PATH") as mock_sock, \
             patch("builtins.print"):
            mock_sock.exists.return_value = True
            self._run_main(["meetcap.py", "transcribe-last"])

        mock_send.assert_called_once_with("transcribe-last")


if __name__ == "__main__":
    unittest.main()
