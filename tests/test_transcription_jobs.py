"""Tests for transcription jobs, watchdog, atomic writes, and partial resume.

Covers:
  #15 — Watchdog does not clear is_transcribing while the worker thread is alive.
        Watchdog cleans up stuck state if the worker thread died unexpectedly.
        do_transcribe_last resets is_transcribing in a finally block on all exit paths.
  #37 — Atomic .txt writing via .tmp + os.replace in transcribe().
        Empty (0 bytes) or headerless (missing '---') .txt files are treated as
        untranscribed/resumable in do_transcribe_last.
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import meetcap


def _reset_state():
    meetcap.state.is_recording = False
    meetcap.state.recording_proc = None
    meetcap.state.recording_file = None
    meetcap.state.recording_since = None
    meetcap.state.is_transcribing = False
    meetcap.state.last_error = None


class WatchdogLivenessTests(unittest.TestCase):
    """#15 — Watchdog behavior on live vs dead transcription threads."""

    def setUp(self):
        _reset_state()
        self.save_patcher = patch("meetcap.save_state")
        self.mock_save_state = self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)

        self.notify_patcher = patch("meetcap.notify")
        self.mock_notify = self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_watchdog_does_not_clear_state_when_thread_is_alive(self):
        """#15: If the transcription thread is still alive after timeout, watchdog leaves state intact."""
        meetcap.state.is_transcribing = True

        mock_thread = Mock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True

        meetcap._transcription_watchdog(mock_thread, timeout=0.01)

        mock_thread.join.assert_called_once_with(timeout=0.01)
        self.assertTrue(
            meetcap.state.is_transcribing,
            "#15: watchdog must not reset is_transcribing when thread is still alive",
        )
        self.mock_save_state.assert_not_called()
        self.mock_notify.assert_not_called()

    def test_watchdog_with_real_alive_thread_leaves_state_intact(self):
        """#15: Live background thread does not lose its transcribing state on watchdog timeout."""
        meetcap.state.is_transcribing = True
        stop_event = threading.Event()

        def long_running_task():
            stop_event.wait(5.0)

        t = threading.Thread(target=long_running_task, daemon=True)
        t.start()
        try:
            meetcap._transcription_watchdog(t, timeout=0.02)
            self.assertTrue(t.is_alive())
            self.assertTrue(
                meetcap.state.is_transcribing,
                "#15: state must remain True while real worker thread is active",
            )
            self.mock_save_state.assert_not_called()
            self.mock_notify.assert_not_called()
        finally:
            stop_event.set()
            t.join(timeout=1.0)

    def test_watchdog_clears_state_when_thread_dies_without_reset(self):
        """#15: If the thread died and left is_transcribing=True, watchdog clears state and alerts."""
        meetcap.state.is_transcribing = True

        mock_thread = Mock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False

        meetcap._transcription_watchdog(mock_thread, timeout=0.01)

        mock_thread.join.assert_called_once_with(timeout=0.01)
        self.assertFalse(
            meetcap.state.is_transcribing,
            "#15: watchdog must reset is_transcribing when thread is dead and state is stuck",
        )
        self.mock_save_state.assert_called_once()
        self.mock_notify.assert_called_once_with(
            "Meetcap",
            "⚠️ Transcription watchdog triggered",
            "Thread finished but state was stuck",
        )

    def test_watchdog_noop_when_thread_finished_cleanly(self):
        """#15: When thread is dead and is_transcribing is already False, watchdog does nothing."""
        meetcap.state.is_transcribing = False

        mock_thread = Mock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False

        meetcap._transcription_watchdog(mock_thread, timeout=0.01)

        self.assertFalse(meetcap.state.is_transcribing)
        self.mock_save_state.assert_not_called()
        self.mock_notify.assert_not_called()


class DoTranscribeLastFinallyTests(unittest.TestCase):
    """#15 — do_transcribe_last resets is_transcribing in finally block."""

    def setUp(self):
        _reset_state()
        self.save_patcher = patch("meetcap.save_state")
        self.mock_save_state = self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)

        self.notify_patcher = patch("meetcap.notify")
        self.mock_notify = self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_finally_resets_state_on_success(self):
        """#15: is_transcribing is reset to False on successful transcription."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "meeting-test.wav"
            wav.write_bytes(b"\x00" * 100)

            with patch("meetcap.RECORDINGS_DIR", Path(tmp)), \
                 patch("meetcap.transcribe", return_value=str(wav.with_suffix(".txt"))), \
                 patch("meetcap.auto_export"):
                result = meetcap.do_transcribe_last()

            self.assertTrue(result["ok"])
            self.assertFalse(meetcap.state.is_transcribing)
            self.mock_save_state.assert_called()

    def test_finally_resets_state_on_exception(self):
        """#15: is_transcribing is reset to False and error saved when transcribe raises."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "meeting-test.wav"
            wav.write_bytes(b"\x00" * 100)

            with patch("meetcap.RECORDINGS_DIR", Path(tmp)), \
                 patch("meetcap.transcribe", side_effect=RuntimeError("Whisper crashed")):
                result = meetcap.do_transcribe_last()

            self.assertFalse(result["ok"])
            self.assertIn("Whisper crashed", result["error"])
            self.assertFalse(meetcap.state.is_transcribing)
            self.assertEqual(meetcap.state.last_error, "Whisper crashed")
            self.mock_save_state.assert_called()

    def test_state_remains_intact_if_already_transcribing(self):
        """#15: Calling do_transcribe_last while already transcribing returns error without clearing state."""
        meetcap.state.is_transcribing = True

        result = meetcap.do_transcribe_last()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Already transcribing")
        self.assertTrue(meetcap.state.is_transcribing)


class AtomicTranscriptWriteTests(unittest.TestCase):
    """#37 — Atomic .txt writing via .tmp and os.replace in transcribe()."""

    def setUp(self):
        _reset_state()

    def test_atomic_write_uses_tmp_file_and_os_replace(self):
        """#37: transcribe() writes to .tmp first and renames to .txt via os.replace."""
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "meeting-test.wav"
            wav_path.write_bytes(b"\x00" * 100)

            mock_model = Mock()
            mock_model.transcribe.return_value = (
                [Mock(start=0.0, end=1.5, text="Hello world")],
                Mock(language="en", language_probability=0.98, duration=1.5),
            )

            replaced_sources = []
            replaced_targets = []
            orig_replace = os.replace

            def spying_replace(src, dst):
                src_p = Path(src)
                dst_p = Path(dst)
                replaced_sources.append(src_p)
                replaced_targets.append(dst_p)
                # Verify .tmp exists and has content before replacement
                self.assertTrue(src_p.exists(), "Source .tmp must exist before os.replace")
                self.assertIn("# Meetcap Transcript", src_p.read_text())
                return orig_replace(src, dst)

            with patch("faster_whisper.WhisperModel", return_value=mock_model), \
                 patch("meetcap.os.replace", side_effect=spying_replace) as mock_replace:
                result_txt = meetcap.transcribe(wav_path)

            mock_replace.assert_called_once()
            self.assertEqual(len(replaced_sources), 1)
            src_file = replaced_sources[0]
            dst_file = replaced_targets[0]

            self.assertEqual(src_file.suffix, ".tmp")
            self.assertEqual(dst_file.suffix, ".txt")
            self.assertEqual(src_file.parent, dst_file.parent)
            self.assertEqual(dst_file, wav_path.with_suffix(".txt"))

            # After replace, final .txt exists and .tmp does not
            self.assertTrue(Path(result_txt).exists())
            self.assertFalse(src_file.exists(), ".tmp file should not linger after replace")
            content = Path(result_txt).read_text()
            self.assertIn("# Meetcap Transcript", content)
            self.assertIn("---", content)
            self.assertIn("[00:00 → 00:01] Hello world", content)

    def test_atomic_write_safely_overwrites_existing_partial_txt(self):
        """#37: transcribe() atomically replaces an existing broken/partial .txt file."""
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "meeting-replace.wav"
            wav_path.write_bytes(b"\x00" * 100)
            txt_path = wav_path.with_suffix(".txt")
            txt_path.write_text("corrupted partial content")

            mock_model = Mock()
            mock_model.transcribe.return_value = (
                [Mock(start=0.0, end=2.0, text="Overwritten cleanly")],
                Mock(language="pt", language_probability=0.95, duration=2.0),
            )

            with patch("faster_whisper.WhisperModel", return_value=mock_model):
                out = meetcap.transcribe(wav_path)

            self.assertEqual(Path(out), txt_path)
            content = txt_path.read_text()
            self.assertNotIn("corrupted partial content", content)
            self.assertIn("Overwritten cleanly", content)


class ResumableTranscriptTests(unittest.TestCase):
    """#37 — Empty or headerless .txt transcripts treated as uncompleted/resumable."""

    def setUp(self):
        _reset_state()
        self.save_patcher = patch("meetcap.save_state")
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)
        self.notify_patcher = patch("meetcap.notify")
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_is_transcript_complete_missing_file(self):
        self.assertFalse(meetcap._is_transcript_complete(Path("/tmp/nonexistent-12345.txt")))

    def test_is_transcript_complete_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "empty.txt"
            p.touch()
            self.assertFalse(meetcap._is_transcript_complete(p))

    def test_is_transcript_complete_missing_header_separator(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "partial.txt"
            p.write_text("# Meetcap Transcript\nDate: 2026-09-10\nFile: test.wav\n")
            self.assertFalse(meetcap._is_transcript_complete(p))

    def test_is_transcript_complete_with_valid_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "valid.txt"
            p.write_text("# Meetcap Transcript\nDate: 2026-09-10\n---\n[00:00 → 00:01] hi\n")
            self.assertTrue(meetcap._is_transcript_complete(p))

    def test_do_transcribe_last_resumes_empty_txt(self):
        """#37: A WAV with a 0-byte .txt is selected for transcription instead of skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rec0 = d / "meeting-2026-09-10_10-00-00.wav"
            rec0.write_bytes(b"\x00" * 100)
            txt0 = rec0.with_suffix(".txt")
            txt0.write_text("# Meetcap Transcript\n---\n[00:00 → 00:01] done\n")

            time.sleep(0.01)
            rec1 = d / "meeting-2026-09-10_11-00-00.wav"
            rec1.write_bytes(b"\x00" * 100)
            txt1 = rec1.with_suffix(".txt")
            txt1.touch()  # 0 bytes, empty partial crash

            with patch("meetcap.RECORDINGS_DIR", d), \
                 patch("meetcap.transcribe", return_value=str(txt1)) as mock_transcribe, \
                 patch("meetcap.auto_export"):
                result = meetcap.do_transcribe_last()

            self.assertTrue(result["ok"])
            mock_transcribe.assert_called_once_with(rec1)

    def test_do_transcribe_last_resumes_headerless_partial_txt(self):
        """#37: A WAV whose .txt lacks '---' is resumed."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rec = d / "meeting-2026-09-10_12-00-00.wav"
            rec.write_bytes(b"\x00" * 100)
            txt = rec.with_suffix(".txt")
            txt.write_text("# Meetcap Transcript\nDate: 2026-09-10\nFile: test.wav\n")

            with patch("meetcap.RECORDINGS_DIR", d), \
                 patch("meetcap.transcribe", return_value=str(txt)) as mock_transcribe, \
                 patch("meetcap.auto_export"):
                result = meetcap.do_transcribe_last()

            self.assertTrue(result["ok"])
            mock_transcribe.assert_called_once_with(rec)

    def test_do_transcribe_last_skips_complete_and_transcribes_missing(self):
        """#37: Completed recordings are skipped, oldest untranscribed is picked."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rec_old = d / "meeting-2026-09-10_09-00-00.wav"
            rec_old.write_bytes(b"\x00" * 100)

            time.sleep(0.01)
            rec_new = d / "meeting-2026-09-10_10-00-00.wav"
            rec_new.write_bytes(b"\x00" * 100)
            txt_new = rec_new.with_suffix(".txt")
            txt_new.write_text("# Meetcap Transcript\n---\n[00:00 → 00:01] completed\n")

            with patch("meetcap.RECORDINGS_DIR", d), \
                 patch("meetcap.transcribe", return_value=str(rec_old.with_suffix(".txt"))) as mock_transcribe, \
                 patch("meetcap.auto_export"):
                result = meetcap.do_transcribe_last()

            self.assertTrue(result["ok"])
            mock_transcribe.assert_called_once_with(rec_old)


if __name__ == "__main__":
    unittest.main()
