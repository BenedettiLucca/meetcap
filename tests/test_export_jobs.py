"""Tests for durable, reconcilable export jobs (#16).

Acceptance criteria:
  - restart during export doesn't lose the job: simulate blocked subprocess.run, run
    reconcile on fresh 'process' → job resurfaces.
  - startup reconcile enqueues incomplete export exactly once (idempotency assertion).
  - status differentiates transcribing from exporting.
  - failure produces retryable state; export-retry re-enqueues it.
  - re-running a complete job is a no-op.
  - shutdown while exporter is blocked does not raise and leaves reconcilable state.
"""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import meetcap


def _make_tmp_runtime(tmp):
    """Return a temp runtime dir and set MEETCAP_RUNTIME_DIR for isolation."""
    rt = Path(tmp) / "runtime"
    rt.mkdir()
    return rt


class ExportJobStateTests(unittest.TestCase):
    """#16 — Job state persisted as JSON in the daemon state dir."""

    def setUp(self):
        self.save_patcher = patch("meetcap.save_state")
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)
        self.notify_patcher = patch("meetcap.notify")
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_enqueue_export_creates_queued_job(self):
        """enqueue_export_job() writes a queued job entry to the state dir."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            txt = Path(tmp) / "meeting.txt"
            txt.write_text("transcript")

            with patch("meetcap.export_jobs_path", return_value=rt / "export_jobs.json"):
                meetcap.enqueue_export_job(str(txt))
                jobs = meetcap.load_export_jobs(rt / "export_jobs.json")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["transcript"], str(txt))
        self.assertEqual(jobs[0]["status"], "queued")
        self.assertIn("updated_at", jobs[0])

    def test_complete_job_is_not_reenqueued(self):
        """A transcript already 'complete' is never re-exported."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            jobs_path = rt / "export_jobs.json"
            txt = str(Path(tmp) / "meeting.txt")
            # Pre-populate with a complete job
            jobs_data = [{"transcript": txt, "status": "complete", "updated_at": "2026-01-01T00:00:00"}]
            jobs_path.write_text(json.dumps(jobs_data))

            with patch("meetcap.export_jobs_path", return_value=jobs_path):
                meetcap.enqueue_export_job(txt)
                jobs = meetcap.load_export_jobs(jobs_path)

        # Should still be exactly 1 job and still complete
        complete_jobs = [j for j in jobs if j["transcript"] == txt]
        self.assertEqual(len(complete_jobs), 1)
        self.assertEqual(complete_jobs[0]["status"], "complete")


class ReconcileTests(unittest.TestCase):
    """#16 — On startup, reconcile stuck exporting/queued jobs."""

    def setUp(self):
        self.save_patcher = patch("meetcap.save_state")
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)
        self.notify_patcher = patch("meetcap.notify")
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_reconcile_marks_exporting_as_failed(self):
        """Jobs stuck in 'exporting' from a previous process become 'failed'."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            jobs_path = rt / "export_jobs.json"
            txt = str(Path(tmp) / "meeting.txt")
            jobs_data = [{"transcript": txt, "status": "exporting", "updated_at": "2026-01-01T00:00:00"}]
            jobs_path.write_text(json.dumps(jobs_data))

            with patch("meetcap.export_jobs_path", return_value=jobs_path):
                meetcap.reconcile_export_jobs()
                jobs = meetcap.load_export_jobs(jobs_path)

        self.assertEqual(jobs[0]["status"], "failed")

    def test_reconcile_marks_queued_as_failed(self):
        """Jobs stuck in 'queued' from a previous process become 'failed'."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            jobs_path = rt / "export_jobs.json"
            txt = str(Path(tmp) / "meeting.txt")
            jobs_data = [{"transcript": txt, "status": "queued", "updated_at": "2026-01-01T00:00:00"}]
            jobs_path.write_text(json.dumps(jobs_data))

            with patch("meetcap.export_jobs_path", return_value=jobs_path):
                meetcap.reconcile_export_jobs()
                jobs = meetcap.load_export_jobs(jobs_path)

        self.assertEqual(jobs[0]["status"], "failed")

    def test_reconcile_does_not_change_complete_jobs(self):
        """Complete jobs are never re-exported during reconcile."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            jobs_path = rt / "export_jobs.json"
            txt = str(Path(tmp) / "meeting.txt")
            jobs_data = [{"transcript": txt, "status": "complete", "updated_at": "2026-01-01T00:00:00"}]
            jobs_path.write_text(json.dumps(jobs_data))

            with patch("meetcap.export_jobs_path", return_value=jobs_path):
                meetcap.reconcile_export_jobs()
                jobs = meetcap.load_export_jobs(jobs_path)

        self.assertEqual(jobs[0]["status"], "complete")

    def test_reconcile_reenqueues_failed_jobs_exactly_once(self):
        """reconcile re-enqueues failed jobs idempotently — called twice, enqueues once."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            jobs_path = rt / "export_jobs.json"
            txt = str(Path(tmp) / "meeting.txt")
            jobs_data = [{"transcript": txt, "status": "exporting", "updated_at": "2026-01-01T00:00:00"}]
            jobs_path.write_text(json.dumps(jobs_data))

            enqueue_count = []

            def counting_enqueue(t):
                enqueue_count.append(t)

            with patch("meetcap.export_jobs_path", return_value=jobs_path), \
                 patch("meetcap._worker_enqueue", side_effect=counting_enqueue):
                meetcap.reconcile_export_jobs()
                meetcap.reconcile_export_jobs()

        # idempotent: first call changes exporting→failed and enqueues once.
        # second call sees failed status, does not re-enqueue again.
        self.assertEqual(len(enqueue_count), 1)

    def test_restart_during_export_job_resurfaces(self):
        """Simulate subprocess.run blocked → reconcile on fresh process → job resurfaces as queued."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            jobs_path = rt / "export_jobs.json"
            txt = str(Path(tmp) / "meeting.txt")
            # Job was in 'exporting' state when process was killed
            jobs_data = [{"transcript": txt, "status": "exporting", "updated_at": "2026-01-01T00:00:00"}]
            jobs_path.write_text(json.dumps(jobs_data))

            reenqueued = []

            with patch("meetcap.export_jobs_path", return_value=jobs_path), \
                 patch("meetcap._worker_enqueue", side_effect=lambda t: reenqueued.append(t)):
                meetcap.reconcile_export_jobs()

        # Job must resurface after reconcile
        self.assertEqual(len(reenqueued), 1)
        self.assertEqual(reenqueued[0], txt)


class ExportJobWorkerTests(unittest.TestCase):
    """#16 — Non-daemon worker thread transitions jobs through states."""

    def setUp(self):
        self.save_patcher = patch("meetcap.save_state")
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)
        self.notify_patcher = patch("meetcap.notify")
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_worker_transitions_queued_to_complete_on_success(self):
        """Worker processes queued job: success → complete."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            jobs_path = rt / "export_jobs.json"
            txt = str(Path(tmp) / "meeting.txt")

            success_result = MagicMock()
            success_result.returncode = 0
            success_result.stdout = '{"success": true, "outcome": "ok", "stages": {}}'
            success_result.stderr = ""

            with patch("meetcap.export_jobs_path", return_value=jobs_path), \
                 patch("meetcap.subprocess.run", return_value=success_result):
                meetcap.enqueue_export_job(str(txt))
                # Process the job synchronously for test
                meetcap._process_export_job(str(txt), jobs_path)
                jobs = meetcap.load_export_jobs(jobs_path)

        self.assertEqual(jobs[0]["status"], "complete")

    def test_worker_transitions_to_failed_on_nonzero_exit(self):
        """Worker: subprocess returns non-zero → job status is 'failed'."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            jobs_path = rt / "export_jobs.json"
            txt = str(Path(tmp) / "meeting.txt")

            fail_result = MagicMock()
            fail_result.returncode = 1
            fail_result.stdout = ""
            fail_result.stderr = "export error"

            with patch("meetcap.export_jobs_path", return_value=jobs_path), \
                 patch("meetcap.subprocess.run", return_value=fail_result):
                meetcap.enqueue_export_job(str(txt))
                meetcap._process_export_job(str(txt), jobs_path)
                jobs = meetcap.load_export_jobs(jobs_path)

        self.assertEqual(jobs[0]["status"], "failed")


class StatusExportFieldTests(unittest.TestCase):
    """#16 — status socket response gains 'exporting' bool and 'last_export' dict."""

    def setUp(self):
        meetcap.state.is_recording = False
        meetcap.state.is_transcribing = False
        meetcap.state.recording_file = None
        meetcap.state.recording_since = None
        meetcap.state.last_error = None

    def test_status_includes_exporting_field(self):
        """handle_command('status') must include 'exporting' key."""
        status = meetcap.handle_command("status")
        self.assertIn("exporting", status)

    def test_status_includes_last_export_field(self):
        """handle_command('status') must include 'last_export' key."""
        status = meetcap.handle_command("status")
        self.assertIn("last_export", status)

    def test_status_exporting_is_false_when_idle(self):
        """When no export is running, status['exporting'] is False."""
        with patch("meetcap.export_state_is_exporting", return_value=False):
            status = meetcap.handle_command("status")
        self.assertFalse(status["exporting"])

    def test_status_differentiates_transcribing_from_exporting(self):
        """transcribing=True and exporting=False are distinct states in status response."""
        meetcap.state.is_transcribing = True
        with patch("meetcap.export_state_is_exporting", return_value=False):
            status = meetcap.handle_command("status")
        self.assertTrue(status["transcribing"])
        self.assertFalse(status["exporting"])

        meetcap.state.is_transcribing = False
        with patch("meetcap.export_state_is_exporting", return_value=True):
            status = meetcap.handle_command("status")
        self.assertFalse(status["transcribing"])
        self.assertTrue(status["exporting"])


class ExportRetryTests(unittest.TestCase):
    """#16 — export-retry re-enqueues the latest failed job."""

    def setUp(self):
        self.save_patcher = patch("meetcap.save_state")
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)
        self.notify_patcher = patch("meetcap.notify")
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_export_retry_reenqueues_latest_failed_job(self):
        """export-retry socket command re-enqueues the latest failed job."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            jobs_path = rt / "export_jobs.json"
            txt = str(Path(tmp) / "meeting.txt")
            jobs_data = [{"transcript": txt, "status": "failed", "updated_at": "2026-01-01T00:00:00"}]
            jobs_path.write_text(json.dumps(jobs_data))

            reenqueued = []
            with patch("meetcap.export_jobs_path", return_value=jobs_path), \
                 patch("meetcap._worker_enqueue", side_effect=lambda t: reenqueued.append(t)):
                result = meetcap.export_retry_cmd()

        self.assertTrue(result["ok"])
        self.assertEqual(len(reenqueued), 1)
        self.assertEqual(reenqueued[0], txt)

    def test_export_retry_no_failed_jobs_returns_error(self):
        """export-retry with no failed jobs returns ok=False."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            jobs_path = rt / "export_jobs.json"
            jobs_path.write_text("[]")

            with patch("meetcap.export_jobs_path", return_value=jobs_path):
                result = meetcap.export_retry_cmd()

        self.assertFalse(result["ok"])

    def test_handle_command_export_retry_dispatches(self):
        """handle_command('export-retry') calls export_retry_cmd."""
        with patch("meetcap.export_retry_cmd", return_value={"ok": True}) as mock_retry:
            result = meetcap.handle_command("export-retry")
        mock_retry.assert_called_once()
        self.assertTrue(result["ok"])


class ShutdownBoundedJoinTests(unittest.TestCase):
    """#16 — Shutdown joins worker with ≤5s bound; blocked worker leaves reconcilable state."""

    def setUp(self):
        self.save_patcher = patch("meetcap.save_state")
        self.save_patcher.start()
        self.addCleanup(self.save_patcher.stop)
        self.notify_patcher = patch("meetcap.notify")
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

    def test_shutdown_export_worker_does_not_raise(self):
        """shutdown_export_worker() completes without raising even if worker is blocked."""
        block_event = threading.Event()

        def blocked_worker():
            block_event.wait(timeout=10.0)

        t = threading.Thread(target=blocked_worker)
        t.start()
        try:
            # Should return within the bounded join timeout without raising
            meetcap.shutdown_export_worker(t, join_timeout=0.05)
        finally:
            block_event.set()
            t.join(timeout=1.0)

    def test_shutdown_blocked_worker_leaves_exporting_in_state_file(self):
        """If worker is blocked at shutdown, job stays 'exporting' for reconcile to handle."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = _make_tmp_runtime(tmp)
            jobs_path = rt / "export_jobs.json"
            txt = str(Path(tmp) / "meeting.txt")
            jobs_data = [{"transcript": txt, "status": "exporting", "updated_at": "2026-01-01T00:00:00"}]
            jobs_path.write_text(json.dumps(jobs_data))

            block_event = threading.Event()

            def blocked_worker():
                block_event.wait(timeout=10.0)

            t = threading.Thread(target=blocked_worker)
            t.start()
            try:
                meetcap.shutdown_export_worker(t, join_timeout=0.05)
                # After forced timeout, state file still shows 'exporting' (not clobbered)
                with patch("meetcap.export_jobs_path", return_value=jobs_path):
                    jobs = meetcap.load_export_jobs(jobs_path)
            finally:
                block_event.set()
                t.join(timeout=1.0)

        self.assertEqual(jobs[0]["status"], "exporting")


if __name__ == "__main__":
    unittest.main()
