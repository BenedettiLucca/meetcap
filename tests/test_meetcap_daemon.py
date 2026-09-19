import contextlib
import io
import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import meetcap


class AudioSourceTests(unittest.TestCase):
    def test_returns_mic_and_sink_monitor(self):
        with patch(
            "meetcap.subprocess.check_output", side_effect=["mic.source\n", "sink.output\n"]
        ):
            self.assertEqual(
                meetcap.get_audio_sources(), ("mic.source", "sink.output.monitor")
            )

    def test_missing_pactl_returns_none(self):
        with patch("meetcap.subprocess.check_output", side_effect=FileNotFoundError("pactl")):
            self.assertEqual(meetcap.get_audio_sources(), (None, None))

    def test_pactl_failure_returns_none(self):
        with patch(
            "meetcap.subprocess.check_output",
            side_effect=subprocess.CalledProcessError(1, "pactl"),
        ):
            self.assertEqual(meetcap.get_audio_sources(), (None, None))

    def test_unexpected_os_error_returns_none(self):
        with patch("meetcap.subprocess.check_output", side_effect=OSError("boom")):
            self.assertEqual(meetcap.get_audio_sources(), (None, None))


class RecordingCommandTests(unittest.TestCase):
    def setUp(self):
        self.state_patcher = patch("meetcap.save_state")
        self.state_patcher.start()
        self.addCleanup(self.state_patcher.stop)
        meetcap.state.is_recording = False
        meetcap.state.recording_proc = None
        meetcap.state.recording_file = None
        meetcap.state.recording_since = None

    def test_record_without_audio_sources_fails(self):
        with patch("meetcap.get_audio_sources", return_value=(None, None)):
            result = meetcap.handle_command("record")
        self.assertFalse(result["ok"])
        self.assertIn("No audio sources", result["error"])

    def test_record_while_recording_fails(self):
        meetcap.state.is_recording = True
        result = meetcap.handle_command("record")
        self.assertFalse(result["ok"])

    def test_stop_when_not_recording_fails(self):
        result = meetcap.handle_command("stop")
        self.assertFalse(result["ok"])
        self.assertIn("Not recording", result["error"])

    def test_toggle_starts_when_idle(self):
        with patch("meetcap.start_recording", return_value={"ok": True}) as mock_start:
            result = meetcap.handle_command("toggle")
        mock_start.assert_called_once()
        self.assertTrue(result["ok"])

    def test_toggle_stops_when_recording(self):
        meetcap.state.is_recording = True
        with patch("meetcap.stop_recording", return_value={"ok": True}) as mock_stop:
            result = meetcap.handle_command("toggle")
        mock_stop.assert_called_once()
        self.assertTrue(result["ok"])

    def test_transcribe_while_recording_fails(self):
        meetcap.state.is_recording = True
        result = meetcap.handle_command("transcribe")
        self.assertFalse(result["ok"])
        self.assertIn("Stop recording first", result["error"])

    def test_transcribe_dispatches_when_idle(self):
        with patch("meetcap.transcribe_cmd", return_value={"ok": True, "status": "started"}) as mock_cmd:
            result = meetcap.handle_command("transcribe")
        mock_cmd.assert_called_once()
        self.assertTrue(result["ok"])

    def test_unknown_command_returns_error(self):
        result = meetcap.handle_command("bogus")
        self.assertFalse(result["ok"])
        self.assertIn("Unknown command", result["error"])


class StatusCommandTests(unittest.TestCase):
    def test_status_reports_state_fields(self):
        meetcap.state.recording_file = Path("/tmp/fake.wav")
        meetcap.state.recording_since = None
        status = meetcap.handle_command("status")
        self.assertEqual(
            status,
            {
                "recording": meetcap.state.is_recording,
                "transcribing": meetcap.state.is_transcribing,
                "last_file": "/tmp/fake.wav",
                "error": meetcap.state.last_error,
                "recording_since": None,
            },
        )

    def test_list_returns_recent_recordings(self):
        with patch.object(meetcap, "RECORDINGS_DIR") as recordings:
            recordings.glob.return_value = [
                Path(f"meeting-{i}.wav") for i in range(25, 0, -1)
            ]
            result = meetcap.handle_command("list")
        self.assertEqual(len(result["recordings"]), 20)
        self.assertIn("meeting-25.wav", result["recordings"])


class NotifyTests(unittest.TestCase):
    def test_notify_swallows_subprocess_failures(self):
        with patch(
            "meetcap.subprocess.run", side_effect=subprocess.TimeoutExpired("notify-send", 3)
        ):
            meetcap.notify("Meetcap", "title", "body")

    def test_notify_swallows_missing_binary(self):
        with patch("meetcap.subprocess.run", side_effect=FileNotFoundError("notify-send")):
            meetcap.notify("Meetcap", "title", "body")

    # #44 regression: notify-send accepts at most 2 positionals; the old code
    # appended `subtitle` as a third positional and every notification failed
    # with rc=1 ("Invalid number of options.") in silence.
    def test_notify_sends_exactly_two_positionals(self):
        with patch("meetcap.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stderr="")
            meetcap.notify("Meetcap", "Recording started", "mic=foo")
        argv = run.call_args.args[0]
        # -a value, title, merged body — exactly 2 positionals after options.
        self.assertEqual(
            argv, ["notify-send", "-a", "Meetcap", "Meetcap", "Recording started\nmic=foo"]
        )

    def test_notify_subtitle_is_merged_into_body(self):
        with patch("meetcap.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stderr="")
            meetcap.notify("Meetcap", "Recording started", "mic=foo\nsys=bar")
        argv = run.call_args.args[0]
        self.assertEqual(
            argv,
            [
                "notify-send",
                "-a",
                "Meetcap",
                "Meetcap",
                "Recording started\nmic=foo\nsys=bar",
            ],
        )

    def test_notify_without_subtitle_keeps_body_untouched(self):
        with patch("meetcap.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stderr="")
            meetcap.notify("Meetcap", "plain body")
        argv = run.call_args.args[0]
        self.assertEqual(argv, ["notify-send", "-a", "Meetcap", "Meetcap", "plain body"])

    def test_notify_logs_nonzero_returncode(self):
        with patch("meetcap.subprocess.run") as run:
            run.return_value = Mock(returncode=1, stderr="Invalid number of options.")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                meetcap.notify("Meetcap", "title", "body")
        self.assertIn("[NOTIFY] rc=1", out.getvalue())


class ClientHandlerTests(unittest.TestCase):
    def test_dispatches_command_and_returns_json(self):
        server, client = socket.socketpair()
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        client.sendall(b"status\n")
        meetcap.client_handler(server)
        response = client.recv(4096).decode()
        self.assertEqual(json.loads(response)["recording"], meetcap.state.is_recording)

    def test_socket_error_returns_error_json(self):
        conn = Mock()
        conn.recv.side_effect = OSError("broken pipe")
        conn.sendall = Mock()
        meetcap.client_handler(conn)
        sent = conn.sendall.call_args[0][0]
        self.assertIn("broken pipe", json.loads(sent.decode())["error"])

    def test_sendall_failure_is_ignored(self):
        conn = Mock()
        conn.recv.side_effect = OSError("boom")
        conn.sendall.side_effect = OSError("unreachable")
        meetcap.client_handler(conn)
        conn.close.assert_called_once()


class SendCommandTests(unittest.TestCase):
    def test_missing_socket_exits(self):
        with patch("meetcap.SOCKET_PATH") as socket_path:
            socket_path.exists.return_value = False
            with self.assertRaises(SystemExit):
                meetcap.send_command("status")

    def test_communication_error_exits(self):
        with patch("meetcap.SOCKET_PATH") as socket_path:
            socket_path.exists.return_value = True
            with patch(
                "meetcap.socket.socket",
                side_effect=OSError("permission denied"),
            ):
                with self.assertRaises(SystemExit):
                    meetcap.send_command("status")


class AutoExportTests(unittest.TestCase):
    def test_export_failure_is_reported_not_raised(self):
        with patch("meetcap.notify"):
            with patch(
                "meetcap.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="python", timeout=600),
            ):
                meetcap.auto_export("/tmp/does-not-matter.txt")


class AutoExportOutcomeTests(unittest.TestCase):
    """#38: exit 0 alone is not success — stage errors in the export payload
    must surface in the notification instead of a false ✅."""

    @staticmethod
    def _payload(outcome="ok", stages=None):
        return json.dumps({
            "success": outcome != "failed",
            "outcome": outcome,
            "stages": stages or {},
        })

    def _run(self, returncode=0, stdout="", stderr=""):
        proc = subprocess.CompletedProcess(
            args=["export"], returncode=returncode, stdout=stdout, stderr=stderr
        )
        with patch("meetcap.notify") as notify, patch("meetcap.subprocess.run", return_value=proc):
            meetcap.auto_export("/tmp/whatever.txt")
        return notify

    def test_stage_errors_surface_despite_exit_zero(self):
        stdout = (
            "[EXPORT] Generating AI summary...\n"
            "[EXPORT] Saved to /vault/Meetings/x.md\n"
            + self._payload(
                outcome="failed",
                stages={
                    "summary": {"ok": False, "error": "summary generation failed: no API key"},
                    "tasks": {"ok": True, "error": None},
                },
            )
        )
        notify = self._run(stdout=stdout)
        everything = " ".join(str(call.args) for call in notify.call_args_list)
        self.assertNotIn("✅", everything)
        self.assertIn("⚠️", everything)
        self.assertIn("summary generation failed", everything)

    def test_clean_payload_keeps_success_notification(self):
        stdout = "[EXPORT] Saved\n" + self._payload(outcome="ok", stages={
            "summary": {"ok": True, "error": None},
            "tasks": {"ok": True, "error": None},
        })
        notify = self._run(stdout=stdout)
        self.assertTrue(any("✅" in str(call.args) for call in notify.call_args_list))

    def test_non_json_stdout_falls_back_to_exit_code(self):
        notify = self._run(stdout="[EXPORT] Saved\nno json here")
        self.assertTrue(any("✅" in str(call.args) for call in notify.call_args_list))

    def test_json_tail_extraction(self):
        stdout = "[EXPORT] a\n[EXPORT] b\n" + self._payload(
            outcome="degraded",
            stages={
                "summary": {"ok": True, "error": None},
                "tasks": {"ok": False, "error": "task suggestion generation failed: timeout"},
            },
        )
        parsed = meetcap._parse_export_result(stdout)
        self.assertEqual(parsed["outcome"], "degraded")
        self.assertIn("timeout", parsed["stages"]["tasks"]["error"])


class SingleInstanceTests(unittest.TestCase):
    def test_acquire_single_instance_no_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock = Path(tmp) / "test.sock"
            self.assertTrue(meetcap.acquire_single_instance(sock))

    def test_acquire_single_instance_orphan_socket_unlinked(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock = Path(tmp) / "test.sock"
            sock.touch()
            self.assertTrue(sock.exists())
            self.assertTrue(meetcap.acquire_single_instance(sock))
            self.assertFalse(sock.exists())

    def test_acquire_single_instance_live_socket_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock = Path(tmp) / "test.sock"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(server.close)
            server.bind(str(sock))
            server.listen(1)

            def serve():
                try:
                    conn, _ = server.accept()
                    conn.sendall(b'{"recording": false}\n')
                    conn.close()
                except OSError:
                    pass

            threading.Thread(target=serve, daemon=True).start()
            self.assertFalse(meetcap.acquire_single_instance(sock))
            self.assertTrue(sock.exists())

    def test_run_server_exits_when_already_running(self):
        with patch("meetcap.acquire_single_instance", return_value=False), \
             patch("meetcap.runtime_paths.ensure_runtime_dir"):
            with self.assertRaises(SystemExit) as ctx:
                meetcap.run_server()
            self.assertEqual(ctx.exception.code, 1)


class PidGuardLifecycleTests(unittest.TestCase):
    def test_restart_stale_pid_cleans_file_without_killing(self):
        svc = {"installed": False, "enabled": False, "active": False}
        with patch("meetcap.doctor.check_service", return_value=svc), \
             patch("meetcap.doctor.read_pid", return_value=9999), \
             patch("meetcap.pid_is_meetcap", return_value=False), \
             patch("meetcap.doctor.stop_pid") as mock_stop, \
             patch("meetcap.doctor.clean_stale_files") as mock_clean, \
             patch("meetcap._spawn_manual_daemon") as mock_spawn, \
             patch("meetcap.wait_for_socket", return_value=True), \
             patch("builtins.print"):
            meetcap.restart_cmd()
        mock_stop.assert_not_called()
        mock_clean.assert_any_call(None, meetcap.PID_FILE)
        mock_spawn.assert_called_once()

    def test_restart_meetcap_pid_calls_stop_pid(self):
        svc = {"installed": False, "enabled": False, "active": False}
        with patch("meetcap.doctor.check_service", return_value=svc), \
             patch("meetcap.doctor.read_pid", return_value=9999), \
             patch("meetcap.pid_is_meetcap", return_value=True), \
             patch("meetcap.doctor.stop_pid", return_value=True) as mock_stop, \
             patch("meetcap.doctor.clean_stale_files"), \
             patch("meetcap._spawn_manual_daemon"), \
             patch("meetcap.wait_for_socket", return_value=True), \
             patch("builtins.print"):
            meetcap.restart_cmd()
        mock_stop.assert_called_once_with(9999)

    def test_stop_daemon_stale_pid_cleans_without_killing(self):
        with patch("meetcap.doctor.read_pid", return_value=9999), \
             patch("meetcap.pid_is_meetcap", return_value=False), \
             patch("meetcap.doctor.stop_pid") as mock_stop, \
             patch("meetcap.doctor.clean_stale_files") as mock_clean, \
             patch("builtins.print"):
            self.assertTrue(meetcap.stop_daemon())
        mock_stop.assert_not_called()
        mock_clean.assert_called_once_with(meetcap.SOCKET_PATH, meetcap.PID_FILE)

    def test_stop_daemon_meetcap_pid_calls_stop_pid(self):
        with patch("meetcap.doctor.read_pid", return_value=9999), \
             patch("meetcap.pid_is_meetcap", return_value=True), \
             patch("meetcap.doctor.stop_pid", return_value=True) as mock_stop, \
             patch("meetcap.doctor.clean_stale_files") as mock_clean:
            self.assertTrue(meetcap.stop_daemon())
        mock_stop.assert_called_once_with(9999)
        mock_clean.assert_called_once_with(meetcap.SOCKET_PATH, meetcap.PID_FILE)


class SocketTimeoutTests(unittest.TestCase):
    def test_client_handler_sets_timeout_on_conn(self):
        conn = Mock()
        conn.recv.return_value = b""
        meetcap.client_handler(conn)
        conn.settimeout.assert_called_once_with(meetcap.SOCKET_TIMEOUT)

    def test_send_command_timeout_raises_timeout_error(self):
        with patch("meetcap.SOCKET_PATH") as mock_sock, \
             patch("meetcap._send", side_effect=TimeoutError("timed out")):
            mock_sock.exists.return_value = True
            with self.assertRaises(TimeoutError):
                meetcap.send_command("status")


if __name__ == "__main__":
    unittest.main()
