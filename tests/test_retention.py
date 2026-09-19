"""Tests for #36: retention/disk budget for raw WAV recordings.

Covers:
  1. MEETCAP_RETENTION_DAYS (default 0 = disabled) — purge WAVs older than N days
     ONLY if corresponding .txt exists and is complete (_is_transcript_complete).
     Never delete WAV without complete transcript.
  2. MEETCAP_MIN_FREE_GB (default 2.0) — preflight in start_recording refusing to
     record (clear error + notify) if free space < minimum.
  3. Purge runs on daemon startup and after each successful recording, logging removals.
"""
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import meetcap


def _reset_state():
    meetcap.state.is_recording = False
    meetcap.state.recording_proc = None
    meetcap.state.recording_file = None
    meetcap.state.recording_since = None
    meetcap.state.last_error = None
    if hasattr(meetcap.state, "_stop_watcher"):
        del meetcap.state._stop_watcher


def _make_mock_proc(returncode=None):
    proc = Mock()
    proc.returncode = returncode
    proc.poll.return_value = returncode
    proc.communicate.return_value = (b"", b"")
    return proc


class RetentionPurgeTests(unittest.TestCase):
    """Tests for WAV retention purge logic."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        _reset_state()

    def _create_recording(self, stem: str, age_days: float, txt_content: str | None = None) -> tuple[Path, Path | None]:
        wav = self.dir / f"{stem}.wav"
        wav.write_bytes(b"\x00" * 1024)
        mtime = time.time() - (age_days * 86400.0)
        os.utime(wav, (mtime, mtime))

        txt = None
        if txt_content is not None:
            txt = self.dir / f"{stem}.txt"
            txt.write_text(txt_content, encoding="utf-8")
            os.utime(txt, (mtime, mtime))
        return wav, txt

    def test_purge_disabled_by_default(self):
        """When MEETCAP_RETENTION_DAYS is 0 (or default), purge removes nothing."""
        wav, _ = self._create_recording("meeting-old", age_days=30, txt_content="# Header\n---\nBody")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEETCAP_RETENTION_DAYS", None)
            purged = meetcap.purge_old_recordings(recordings_dir=self.dir)
            self.assertEqual(purged, [])
            self.assertTrue(wav.exists())

    def test_purge_deletes_wav_older_than_n_days_with_complete_transcript(self):
        """WAV older than N days with complete transcript is deleted; .txt is preserved."""
        wav, txt = self._create_recording(
            "meeting-old", age_days=10, txt_content="# Header\nDate: 2026-09-01\n---\nHello world"
        )
        purged = meetcap.purge_old_recordings(recordings_dir=self.dir, retention_days=7)
        self.assertIn(wav, purged)
        self.assertFalse(wav.exists(), "WAV should have been purged")
        self.assertTrue(txt.exists(), "Transcript .txt must be preserved")

    def test_purge_preserves_wav_newer_than_n_days(self):
        """WAV newer than N days is NOT purged, even if transcript is complete."""
        wav, txt = self._create_recording(
            "meeting-recent", age_days=3, txt_content="# Header\nDate: 2026-09-16\n---\nHello world"
        )
        purged = meetcap.purge_old_recordings(recordings_dir=self.dir, retention_days=7)
        self.assertEqual(purged, [])
        self.assertTrue(wav.exists())
        self.assertTrue(txt.exists())

    def test_purge_never_deletes_wav_without_transcript(self):
        """CRITICAL: WAV older than N days without .txt is NEVER deleted."""
        wav, _ = self._create_recording("meeting-no-txt", age_days=15, txt_content=None)
        purged = meetcap.purge_old_recordings(recordings_dir=self.dir, retention_days=7)
        self.assertEqual(purged, [])
        self.assertTrue(wav.exists(), "Never delete WAV without transcript")

    def test_purge_never_deletes_wav_with_empty_transcript(self):
        """CRITICAL: WAV older than N days with empty (0-byte) .txt is NEVER deleted."""
        wav, _ = self._create_recording("meeting-empty-txt", age_days=15, txt_content="")
        purged = meetcap.purge_old_recordings(recordings_dir=self.dir, retention_days=7)
        self.assertEqual(purged, [])
        self.assertTrue(wav.exists(), "Never delete WAV with 0-byte transcript")

    def test_purge_never_deletes_wav_with_incomplete_transcript(self):
        """CRITICAL: WAV older than N days with incomplete transcript (missing '---') is NEVER deleted."""
        wav, _ = self._create_recording(
            "meeting-incomplete-txt", age_days=15, txt_content="# Meetcap Transcript\nIncomplete without separator"
        )
        purged = meetcap.purge_old_recordings(recordings_dir=self.dir, retention_days=7)
        self.assertEqual(purged, [])
        self.assertTrue(wav.exists(), "Never delete WAV with incomplete transcript")

    def test_purge_logs_removed_files(self):
        """Purge prints log output for each removed file."""
        wav, _ = self._create_recording("meeting-purge-log", age_days=10, txt_content="# H\n---\nBody")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            meetcap.purge_old_recordings(recordings_dir=self.dir, retention_days=7)
            output = mock_stdout.getvalue()
            self.assertIn("[RETENTION]", output)
            self.assertIn("meeting-purge-log.wav", output)

    def test_purge_handles_missing_dir_gracefully(self):
        """Purge handles nonexistent recordings_dir without crashing."""
        nonexistent = self.dir / "does_not_exist"
        purged = meetcap.purge_old_recordings(recordings_dir=nonexistent, retention_days=7)
        self.assertEqual(purged, [])


class PreflightDiskSpaceTests(unittest.TestCase):
    """Tests for MEETCAP_MIN_FREE_GB disk space preflight in start_recording."""

    def setUp(self):
        _reset_state()
        self.save_patcher = patch("meetcap.save_state")
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)
        self.notify_patcher = patch("meetcap.notify")
        self.mock_notify = self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_start_recording_fails_when_disk_space_below_default_minimum(self):
        """start_recording refuses to record and notifies if free space < 2.0 GB default."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Simulate 1.0 GB free (less than default 2.0 GB)
            free_bytes = int(1.0 * (1024 ** 3))
            mock_usage = Mock(free=free_bytes, total=100 * (1024 ** 3), used=99 * (1024 ** 3))

            with patch("meetcap.RECORDINGS_DIR", tmp_path), \
                 patch("shutil.disk_usage", return_value=mock_usage), \
                 patch("meetcap.subprocess.Popen") as mock_popen:
                res = meetcap.start_recording()

                self.assertFalse(res["ok"])
                self.assertIn("disk space", res["error"].lower())
                self.assertFalse(meetcap.state.is_recording)
                self.assertIsNotNone(meetcap.state.last_error)
                self.assertIn("disk space", meetcap.state.last_error.lower())
                mock_popen.assert_not_called()
                self.mock_notify.assert_called_once()
                notify_args = self.mock_notify.call_args[0]
                self.assertIn("Meetcap", notify_args[0])
                self.assertTrue(any("disk space" in str(arg).lower() for arg in notify_args))

    def test_start_recording_succeeds_when_disk_space_sufficient(self):
        """start_recording proceeds when free space is above minimum."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            free_bytes = int(5.0 * (1024 ** 3))
            mock_usage = Mock(free=free_bytes, total=100 * (1024 ** 3), used=95 * (1024 ** 3))

            def fake_popen(cmd, **kwargs):
                return _make_mock_proc(returncode=None)

            with patch("meetcap.RECORDINGS_DIR", tmp_path), \
                 patch("shutil.disk_usage", return_value=mock_usage), \
                 patch("meetcap.get_audio_sources", return_value=("mic", "sys")), \
                 patch("meetcap.subprocess.Popen", side_effect=fake_popen), \
                 patch("meetcap.time.sleep"), \
                 patch("builtins.print"):
                res = meetcap.start_recording()

                self.assertTrue(res["ok"], f"start_recording failed: {res}")
                self.assertTrue(meetcap.state.is_recording)
                self.assertIsNone(meetcap.state.last_error)

    def test_start_recording_respects_custom_meetcap_min_free_gb(self):
        """start_recording respects MEETCAP_MIN_FREE_GB environment variable."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Free is 3.0 GB, but minimum set to 5.0 GB
            free_bytes = int(3.0 * (1024 ** 3))
            mock_usage = Mock(free=free_bytes, total=100 * (1024 ** 3), used=97 * (1024 ** 3))

            with patch.dict(os.environ, {"MEETCAP_MIN_FREE_GB": "5.0"}), \
                 patch("meetcap.RECORDINGS_DIR", tmp_path), \
                 patch("shutil.disk_usage", return_value=mock_usage), \
                 patch("meetcap.subprocess.Popen") as mock_popen:
                res = meetcap.start_recording()

                self.assertFalse(res["ok"])
                self.assertIn("disk space", res["error"].lower())
                mock_popen.assert_not_called()


class RetentionHooksTests(unittest.TestCase):
    """Tests that purge is hooked into stop_recording and daemon startup."""

    def setUp(self):
        _reset_state()
        self.save_patcher = patch("meetcap.save_state")
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)
        self.notify_patcher = patch("meetcap.notify")
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_purge_called_on_successful_stop_recording(self):
        """Successful stop_recording triggers purge_old_recordings."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "meeting-test.wav"
            wav.write_bytes(b"\x00" * 100)  # > 44 bytes

            proc = _make_mock_proc(returncode=None)
            meetcap.state.recording_proc = proc
            meetcap.state.recording_file = wav
            meetcap.state.is_recording = True

            with patch("meetcap.purge_old_recordings") as mock_purge, \
                 patch("builtins.print"):
                res = meetcap.stop_recording()

                self.assertTrue(res["ok"])
                mock_purge.assert_called_once()

    def test_purge_not_called_on_failed_stop_recording(self):
        """Failed stop_recording (e.g. empty WAV) does not trigger purge."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "meeting-empty.wav"
            wav.write_bytes(b"\x00" * 10)  # <= 44 bytes

            proc = _make_mock_proc(returncode=0)
            meetcap.state.recording_proc = proc
            meetcap.state.recording_file = wav
            meetcap.state.is_recording = True

            with patch("meetcap.purge_old_recordings") as mock_purge, \
                 patch("builtins.print"):
                res = meetcap.stop_recording()

                self.assertFalse(res["ok"])
                mock_purge.assert_not_called()

    def test_purge_called_on_daemon_startup(self):
        """run_server calls purge_old_recordings during startup."""
        mock_pid = Mock()
        mock_sock_path = Mock()
        mock_sock_path.parent = Path("/tmp")
        mock_sock_instance = MagicMock()
        mock_sock_instance.accept.side_effect = OSError("break loop")

        with patch("meetcap.acquire_single_instance", return_value=True), \
             patch("meetcap.PID_FILE", mock_pid), \
             patch("meetcap.SOCKET_PATH", mock_sock_path), \
             patch("meetcap.socket.socket", return_value=mock_sock_instance), \
             patch("meetcap.reconcile_export_jobs"), \
             patch("meetcap.start_export_worker"), \
             patch("meetcap.purge_old_recordings") as mock_purge, \
             patch("meetcap.runtime_paths.ensure_runtime_dir"), \
             patch("builtins.print"):
            try:
                meetcap.run_server()
            except OSError:
                pass

            mock_purge.assert_called_once()
