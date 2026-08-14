import json
import socket
import subprocess
import sys
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

    def test_unknown_command_returns_error(self):
        result = meetcap.handle_command("bogus")
        self.assertFalse(result["ok"])
        self.assertIn("Unknown command", result["error"])


class StatusCommandTests(unittest.TestCase):
    def test_status_reports_state_fields(self):
        meetcap.state.recording_file = Path("/tmp/fake.wav")
        status = meetcap.handle_command("status")
        self.assertEqual(
            status,
            {
                "recording": meetcap.state.is_recording,
                "transcribing": meetcap.state.is_transcribing,
                "last_file": "/tmp/fake.wav",
                "error": meetcap.state.last_error,
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


if __name__ == "__main__":
    unittest.main()
