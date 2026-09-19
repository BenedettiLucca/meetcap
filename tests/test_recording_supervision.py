"""Tests for recording supervision features.

Covers:
  #11 — ffmpeg death detected by watcher, state cleaned, error reported; stop
         on a ghost process returns a clear error.
  #33 — filename never collides; no -y passed against existing WAV.
  #42 — status payload exposes recording_since (ISO-8601) when recording, None otherwise.
"""
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import meetcap


def _make_mock_proc(returncode=None):
    """Return a Mock that behaves like subprocess.Popen.

    returncode=None  → process is alive (poll() returns None)
    returncode=<int> → process is dead (poll() returns that int)
    """
    proc = Mock()
    proc.returncode = returncode
    proc.poll.return_value = returncode
    proc.communicate.return_value = (b"", b"")
    return proc


def _reset_state():
    meetcap.state.is_recording = False
    meetcap.state.recording_proc = None
    meetcap.state.recording_file = None
    meetcap.state.recording_since = None
    meetcap.state.last_error = None
    if hasattr(meetcap.state, "_stop_watcher"):
        del meetcap.state._stop_watcher


class UniqueWavPathTests(unittest.TestCase):
    """#33 — _unique_wav_path never returns an existing path."""

    def test_returns_base_path_when_no_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            result = meetcap._unique_wav_path(d, "2026-09-10_12-00-00")
            self.assertEqual(result, d / "meeting-2026-09-10_12-00-00.wav")
            self.assertFalse(result.exists())

    def test_suffixes_dash_one_when_base_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            base = d / "meeting-2026-09-10_12-00-00.wav"
            base.touch()
            result = meetcap._unique_wav_path(d, "2026-09-10_12-00-00")
            self.assertEqual(result, d / "meeting-2026-09-10_12-00-00-1.wav")
            self.assertFalse(result.exists())

    def test_increments_suffix_past_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "meeting-2026-09-10_12-00-00.wav").touch()
            (d / "meeting-2026-09-10_12-00-00-1.wav").touch()
            result = meetcap._unique_wav_path(d, "2026-09-10_12-00-00")
            self.assertEqual(result, d / "meeting-2026-09-10_12-00-00-2.wav")
            self.assertFalse(result.exists())

    def test_two_starts_same_second_produce_distinct_paths(self):
        """Simulate two start_recording calls that would share a timestamp."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            ts = "2026-09-10_12-00-00"
            first = meetcap._unique_wav_path(d, ts)
            # Pretend the first recording created the file.
            first.touch()
            second = meetcap._unique_wav_path(d, ts)
            self.assertNotEqual(first, second)
            self.assertFalse(second.exists())


class NoOverwriteInStartRecordingTests(unittest.TestCase):
    """#33 — start_recording uses _unique_wav_path and never passes -y to ffmpeg."""

    def setUp(self):
        _reset_state()
        self.save_patcher = patch("meetcap.save_state")
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)
        self.notify_patcher = patch("meetcap.notify")
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_ffmpeg_command_does_not_contain_dash_y(self):
        """#33: -y must not appear in the ffmpeg command."""
        with tempfile.TemporaryDirectory() as tmp:
            captured_cmd = []

            def fake_popen(cmd, **kwargs):
                captured_cmd.extend(cmd)
                return _make_mock_proc(returncode=None)

            with patch("meetcap.get_audio_sources", return_value=("mic", "sys")), \
                 patch("meetcap.RECORDINGS_DIR", Path(tmp)), \
                 patch("meetcap.subprocess.Popen", side_effect=fake_popen), \
                 patch("meetcap.time.sleep"), \
                 patch("builtins.print"):
                meetcap.start_recording()

            self.assertNotIn("-y", captured_cmd,
                             "#33: ffmpeg command must not contain -y (would silently overwrite)")

    def test_two_starts_same_second_produce_different_files(self):
        """#33: two recordings at the same second must not target the same WAV."""
        with tempfile.TemporaryDirectory() as tmp:
            files_created = []
            ts_fixed = "2026-09-10_12-00-00"

            def fake_popen(cmd, **kwargs):
                # Create the file so the next call sees a collision.
                wav = Path(cmd[-1])
                wav.write_bytes(b"\x00" * 100)
                files_created.append(wav)
                return _make_mock_proc(returncode=None)

            with patch("meetcap.get_audio_sources", return_value=("mic", "sys")), \
                 patch("meetcap.RECORDINGS_DIR", Path(tmp)), \
                 patch("meetcap.subprocess.Popen", side_effect=fake_popen), \
                 patch("meetcap.time.sleep"), \
                 patch("meetcap.datetime") as mock_dt, \
                 patch("builtins.print"):
                # Force both calls to produce the same timestamp string.
                mock_dt.now.return_value.strftime.return_value = ts_fixed
                mock_dt.now.return_value.astimezone.return_value.isoformat.return_value = (
                    "2026-09-10T12:00:00-03:00"
                )

                res1 = meetcap.start_recording()

                # Reset state for second recording.
                _reset_state()

                res2 = meetcap.start_recording()

            self.assertTrue(res1["ok"], f"First start failed: {res1}")
            self.assertTrue(res2["ok"], f"Second start failed: {res2}")
            self.assertNotEqual(res1["file"], res2["file"],
                                "#33: two recordings at the same second produced the same WAV path")


class FfmpegWatcherTests(unittest.TestCase):
    """#11 — watcher detects ffmpeg death and cleans state."""

    def setUp(self):
        _reset_state()
        self.save_patcher = patch("meetcap.save_state")
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)
        self.notify_patcher = patch("meetcap.notify")
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_watcher_clears_state_on_unexpected_death(self):
        """#11: if ffmpeg dies without stop(), watcher clears is_recording and sets last_error."""
        proc = _make_mock_proc(returncode=0)
        wav = Path("/tmp/fake-watcher-test.wav")
        stop_event = threading.Event()

        # Inject proc as the current recording proc.
        meetcap.state.recording_proc = proc
        meetcap.state.is_recording = True
        meetcap.state.recording_since = "2026-09-10T12:00:00-03:00"
        meetcap.state.recording_file = wav

        # Run watcher synchronously (it exits after first poll finds dead proc).
        meetcap._recording_watcher(proc, wav, stop_event)

        self.assertFalse(meetcap.state.is_recording,
                         "#11: is_recording must be False after ffmpeg dies")
        self.assertIsNone(meetcap.state.recording_proc,
                          "#11: recording_proc must be None after ffmpeg dies")
        self.assertIsNone(meetcap.state.recording_since,
                          "#11: recording_since must be None after ffmpeg dies")
        self.assertIsNotNone(meetcap.state.last_error,
                             "#11: last_error must be set after unexpected ffmpeg death")
        self.assertIn("died unexpectedly", meetcap.state.last_error)

    def test_watcher_does_not_interfere_after_clean_stop(self):
        """#11: if stop_recording already cleared recording_proc, watcher does nothing."""
        proc = _make_mock_proc(returncode=0)
        wav = Path("/tmp/fake-watcher-noop.wav")
        stop_event = threading.Event()

        meetcap.state.recording_proc = None  # stop_recording already cleared it
        meetcap.state.is_recording = False
        meetcap.state.last_error = None

        meetcap._recording_watcher(proc, wav, stop_event)

        # State should be untouched (watcher exits early).
        self.assertFalse(meetcap.state.is_recording)
        self.assertIsNone(meetcap.state.last_error)

    def test_watcher_exits_when_stop_event_set(self):
        """#11: watcher exits cleanly when stop_event is set (normal stop path)."""
        proc = _make_mock_proc(returncode=None)  # still alive
        wav = Path("/tmp/fake-watcher-exit.wav")
        stop_event = threading.Event()
        stop_event.set()  # pre-set: watcher should exit immediately on first wait()

        meetcap.state.recording_proc = proc
        meetcap.state.is_recording = True

        meetcap._recording_watcher(proc, wav, stop_event)

        # Watcher should not have touched state (proc was alive; stop was signalled).
        self.assertTrue(meetcap.state.is_recording)


class StaleGhostProcTests(unittest.TestCase):
    """#11 — lingering dead proc handled on next start/stop call."""

    def setUp(self):
        _reset_state()
        self.save_patcher = patch("meetcap.save_state")
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)
        self.notify_patcher = patch("meetcap.notify")
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_stop_on_dead_proc_returns_error_not_success(self):
        """#11: stop_recording on a proc that's already dead must return ok=False."""
        dead_proc = _make_mock_proc(returncode=1)
        meetcap.state.recording_proc = dead_proc
        meetcap.state.is_recording = True
        meetcap.state.recording_file = Path("/tmp/fake.wav")
        meetcap.state.recording_since = "2026-09-10T12:00:00-03:00"

        result = meetcap.stop_recording()

        self.assertFalse(result["ok"], "#11: stop on dead proc must return ok=False")
        self.assertIn("already died", result["error"])
        # State must be cleaned up.
        self.assertIsNone(meetcap.state.recording_proc)
        self.assertFalse(meetcap.state.is_recording)
        self.assertIsNone(meetcap.state.recording_since)

    def test_start_after_dead_proc_cleans_state_and_allows_new_recording(self):
        """#11: start_recording cleans stale dead proc and allows a fresh start."""
        dead_proc = _make_mock_proc(returncode=1)
        meetcap.state.recording_proc = dead_proc
        meetcap.state.is_recording = False  # watcher may have already cleared is_recording
        meetcap.state.recording_file = Path("/tmp/old.wav")

        with tempfile.TemporaryDirectory() as tmp:
            def fake_popen(cmd, **kwargs):
                return _make_mock_proc(returncode=None)

            with patch("meetcap.get_audio_sources", return_value=("mic", "sys")), \
                 patch("meetcap.RECORDINGS_DIR", Path(tmp)), \
                 patch("meetcap.subprocess.Popen", side_effect=fake_popen), \
                 patch("meetcap.time.sleep"), \
                 patch("builtins.print"):
                result = meetcap.start_recording()

        self.assertTrue(result["ok"],
                        f"#11: start after dead proc must succeed, got: {result}")
        # The current recording_proc must NOT be the old dead one.
        self.assertIsNot(meetcap.state.recording_proc, dead_proc,
                         "#11: recording_proc must be replaced, not the old dead process")


class RecordingSinceFieldTests(unittest.TestCase):
    """#42 — status exposes recording_since when recording, None otherwise."""

    def setUp(self):
        _reset_state()

    def test_status_recording_since_is_none_when_not_recording(self):
        """#42: recording_since is None in status when not recording."""
        meetcap.state.is_recording = False
        meetcap.state.recording_since = None
        status = meetcap.handle_command("status")
        self.assertIn("recording_since", status,
                      "#42: status payload must contain 'recording_since' key")
        self.assertIsNone(status["recording_since"],
                          "#42: recording_since must be None when not recording")

    def test_status_recording_since_present_when_recording(self):
        """#42: recording_since is an ISO-8601 string when recording."""
        meetcap.state.is_recording = True
        meetcap.state.recording_since = "2026-09-10T12:00:00-03:00"
        status = meetcap.handle_command("status")
        self.assertEqual(status["recording_since"], "2026-09-10T12:00:00-03:00",
                         "#42: recording_since must equal the stamped ISO-8601 string")

    def test_recording_since_set_on_successful_start(self):
        """#42: start_recording must set recording_since to a non-None ISO-8601 string."""
        with tempfile.TemporaryDirectory() as tmp, \
             patch("meetcap.save_state"), \
             patch("meetcap.notify"), \
             patch("meetcap.get_audio_sources", return_value=("mic", "sys")), \
             patch("meetcap.RECORDINGS_DIR", Path(tmp)), \
             patch("meetcap.subprocess.Popen", return_value=_make_mock_proc(returncode=None)), \
             patch("meetcap.time.sleep"), \
             patch("builtins.print"):
            result = meetcap.start_recording()

        self.assertTrue(result["ok"], f"start_recording failed: {result}")
        self.assertIsNotNone(meetcap.state.recording_since,
                             "#42: recording_since must be set after successful start")
        # Must be parseable as an ISO-8601 string.
        since = meetcap.state.recording_since
        self.assertIsInstance(since, str)
        self.assertRegex(since, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
                         "#42: recording_since must be ISO-8601 format")

    def test_recording_since_cleared_on_stop(self):
        """#42: stop_recording must clear recording_since back to None."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "meeting-test.wav"
            wav.write_bytes(b"\x00" * 100)  # non-empty, bigger than 44 bytes

            proc = _make_mock_proc(returncode=None)
            meetcap.state.is_recording = True
            meetcap.state.recording_proc = proc
            meetcap.state.recording_file = wav
            meetcap.state.recording_since = "2026-09-10T12:00:00-03:00"

            with patch("meetcap.save_state"), patch("meetcap.notify"), patch("builtins.print"):
                result = meetcap.stop_recording()

        self.assertTrue(result["ok"], f"stop_recording failed: {result}")
        self.assertIsNone(meetcap.state.recording_since,
                          "#42: recording_since must be None after stop_recording")


if __name__ == "__main__":
    unittest.main()
