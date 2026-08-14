import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import doctor
import meetcap


def _run_result(returncode=0, stdout=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


HEALTHY_SOURCES = {"ok": True, "mic": "mic.source", "system": "sink.monitor", "detail": ""}


class PingSocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sock_path = Path(self.tmp.name) / "meetcap.sock"

    def _serve_once(self, server):
        def serve():
            try:
                conn, _ = server.accept()
                conn.recv(4096)
                conn.sendall(b'{"recording": false}')
                conn.close()
            except OSError:
                pass

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        return t

    def test_responsive_server_answers_ping(self):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.sock_path))
        server.listen(1)
        self.addCleanup(server.close)
        self._serve_once(server)
        self.assertTrue(doctor.ping_socket(self.sock_path))

    def test_missing_socket_returns_false(self):
        self.assertFalse(doctor.ping_socket(self.sock_path))

    def test_stale_plain_file_returns_false(self):
        self.sock_path.write_text("not a socket")
        self.assertFalse(doctor.ping_socket(self.sock_path))

    def test_unaccepted_backlog_times_out(self):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.sock_path))
        server.listen(1)
        self.addCleanup(server.close)
        self.assertFalse(doctor.ping_socket(self.sock_path, timeout=0.3))


class ReadPidTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pid_file = Path(self.tmp.name) / "meetcap.pid"

    def test_reads_pid(self):
        self.pid_file.write_text(f"{os.getpid()}\n")
        self.assertEqual(doctor.read_pid(self.pid_file), os.getpid())

    def test_garbage_returns_none(self):
        self.pid_file.write_text("not-a-number")
        self.assertIsNone(doctor.read_pid(self.pid_file))

    def test_missing_file_returns_none(self):
        self.assertIsNone(doctor.read_pid(self.pid_file))


class PidAliveTests(unittest.TestCase):
    def test_own_pid_is_alive(self):
        self.assertTrue(doctor.pid_alive(os.getpid()))

    def test_zero_and_negative_are_dead(self):
        self.assertFalse(doctor.pid_alive(0))
        self.assertFalse(doctor.pid_alive(-1))

    def test_out_of_range_pid_is_dead(self):
        self.assertFalse(doctor.pid_alive(2**31))

    def test_unlikely_pid_is_dead(self):
        self.assertFalse(doctor.pid_alive(4194303))


class CheckBinariesTests(unittest.TestCase):
    def test_known_binary_present(self):
        self.assertEqual(doctor.check_binaries(("sh",)), [])

    def test_missing_binary_reported(self):
        self.assertEqual(doctor.check_binaries(("meetcap-no-such-bin",)), ["meetcap-no-such-bin"])


class CheckServiceTests(unittest.TestCase):
    def test_installed_enabled_active(self):
        results = [_run_result(0, "enabled\n"), _run_result(0, "active\n")]
        with patch("doctor.subprocess.run", side_effect=results):
            state = doctor.check_service("meetcap")
        self.assertEqual(state, {"installed": True, "enabled": True, "active": True})

    def test_not_found_unit(self):
        results = [_run_result(4, "not-found\n"), _run_result(3, "inactive\n")]
        with patch("doctor.subprocess.run", side_effect=results):
            state = doctor.check_service("meetcap")
        self.assertFalse(state["installed"])
        self.assertFalse(state["enabled"])
        self.assertFalse(state["active"])

    def test_systemctl_missing(self):
        with patch("doctor.subprocess.run", side_effect=FileNotFoundError("systemctl")):
            state = doctor.check_service("meetcap")
        self.assertEqual(state, {"installed": False, "enabled": False, "active": False})


class CheckAudioSourcesTests(unittest.TestCase):
    def _patch_pactl(self, mic, sink, listing=None):
        outputs = [_run_result(0, mic + "\n"), _run_result(0, sink + "\n")]
        if listing is not None:
            outputs.append(_run_result(0, listing))
        return patch("doctor.subprocess.run", side_effect=outputs)

    def test_valid_sources(self):
        listing = "0\tmic.source\tpulse\n1\tsink.monitor\tpulse\n"
        with self._patch_pactl("mic.source", "sink", listing):
            result = doctor.check_audio_sources()
        self.assertTrue(result["ok"])
        self.assertEqual(result["mic"], "mic.source")
        self.assertEqual(result["system"], "sink.monitor")

    def test_missing_monitor_is_invalid(self):
        listing = "0\tmic.source\tpulse\n"
        with self._patch_pactl("mic.source", "sink", listing):
            result = doctor.check_audio_sources()
        self.assertFalse(result["ok"])
        self.assertIn("sink.monitor", result["detail"])

    def test_no_default_source(self):
        with self._patch_pactl("", "sink"):
            result = doctor.check_audio_sources()
        self.assertFalse(result["ok"])

    def test_pactl_unavailable(self):
        with patch("doctor.subprocess.run", side_effect=FileNotFoundError("pactl")):
            result = doctor.check_audio_sources()
        self.assertIsNone(result["ok"])

    def test_defaults_ok_when_listing_unavailable(self):
        outputs = [_run_result(0, "mic\n"), _run_result(0, "sink\n"),
                   FileNotFoundError("pactl")]
        with patch("doctor.subprocess.run", side_effect=outputs):
            result = doctor.check_audio_sources()
        self.assertTrue(result["ok"])


class DiagnoseTests(unittest.TestCase):
    """Matrix over the classification logic; all OS probes are patched out."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sock_path = Path(self.tmp.name) / "meetcap.sock"
        self.pid_file = Path(self.tmp.name) / "meetcap.pid"
        self.svc_installed = {"installed": True, "enabled": True, "active": True}

    def _diagnose(self, *, ping=False, alive=False, missing=(), svc=None,
                  sources=HEALTHY_SOURCES):
        if svc is None:
            svc = self.svc_installed
        with patch("doctor.ping_socket", return_value=ping), \
             patch("doctor.pid_alive", return_value=alive), \
             patch("doctor.check_binaries", return_value=list(missing)), \
             patch("doctor.check_service", return_value=svc), \
             patch("doctor.check_audio_sources", return_value=sources):
            return doctor.diagnose(self.sock_path, self.pid_file)

    def test_healthy(self):
        self.sock_path.touch()
        d = self._diagnose(ping=True)
        self.assertEqual(d["status"], doctor.HEALTHY)
        self.assertEqual(d["action"], "")

    def test_invalid_source(self):
        self.sock_path.touch()
        bad = {"ok": False, "mic": None, "system": None,
               "detail": "Sources not present: x"}
        d = self._diagnose(ping=True, sources=bad)
        self.assertEqual(d["status"], doctor.INVALID_SOURCE)
        self.assertIn("pactl list short sources", d["action"])

    def test_wedged_daemon(self):
        self.sock_path.touch()
        self.pid_file.write_text(str(os.getpid()))
        d = self._diagnose(ping=False, alive=True)
        self.assertEqual(d["status"], doctor.WEDGED_DAEMON)
        self.assertEqual(d["action"], "systemctl --user restart meetcap")

    def test_wedged_daemon_manual_action_without_service(self):
        self.sock_path.touch()
        self.pid_file.write_text(str(os.getpid()))
        d = self._diagnose(
            ping=False, alive=True,
            svc={"installed": False, "enabled": False, "active": False},
        )
        self.assertEqual(d["status"], doctor.WEDGED_DAEMON)
        self.assertEqual(d["action"], "meetcap.sh restart")

    def test_stale_socket(self):
        self.sock_path.touch()
        d = self._diagnose()
        self.assertEqual(d["status"], doctor.STALE_SOCKET)
        self.assertIn("systemctl --user start meetcap", d["action"])

    def test_stale_pid(self):
        self.pid_file.write_text("4194303")
        d = self._diagnose()
        self.assertEqual(d["status"], doctor.STALE_PID)

    def test_daemon_down(self):
        d = self._diagnose()
        self.assertEqual(d["status"], doctor.DAEMON_DOWN)
        self.assertIn("systemctl --user start meetcap", d["action"])

    def test_daemon_down_without_service_suggests_manual_start(self):
        d = self._diagnose(svc={"installed": False, "enabled": False, "active": False})
        self.assertEqual(d["status"], doctor.DAEMON_DOWN)
        self.assertIn("meetcap.sh start", d["action"])
        self.assertIn("install-service", d["action"])

    def test_missing_deps_takes_priority_over_healthy_socket(self):
        self.sock_path.touch()
        d = self._diagnose(ping=True, missing=["ffmpeg", "socat"])
        self.assertEqual(d["status"], doctor.MISSING_DEPS)
        self.assertIn("ffmpeg", d["message"])
        self.assertIn("socat", d["message"])

    def test_checks_section_reports_raw_findings(self):
        self.pid_file.write_text("123")
        d = self._diagnose()
        checks = d["checks"]
        self.assertEqual(checks["pid"]["pid"], 123)
        self.assertFalse(checks["socket"]["exists"])
        self.assertEqual(checks["binaries"]["missing"], [])
        self.assertEqual(checks["service"], self.svc_installed)


class CleanStaleFilesTests(unittest.TestCase):
    def test_removes_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock = Path(tmp) / "s"
            pid = Path(tmp) / "p"
            sock.touch()
            pid.touch()
            removed = doctor.clean_stale_files(sock, pid)
            self.assertFalse(sock.exists())
            self.assertFalse(pid.exists())
        self.assertEqual(sorted(Path(r).name for r in removed), ["p", "s"])

    def test_missing_files_are_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            removed = doctor.clean_stale_files(Path(tmp) / "s", Path(tmp) / "p")
        self.assertEqual(removed, [])


class StopPidTests(unittest.TestCase):
    def test_dead_pid_is_noop(self):
        with patch("doctor.pid_alive", return_value=False):
            self.assertTrue(doctor.stop_pid(4194303))

    def test_sigterm_stops_pid(self):
        with patch("doctor.pid_alive", side_effect=[True, False]), \
             patch("doctor.os.kill") as kill:
            self.assertTrue(doctor.stop_pid(1, timeout=0.3))
        kill.assert_called_once_with(1, 15)  # SIGTERM


class FormatReportTests(unittest.TestCase):
    def _diagnosis(self, status=doctor.DAEMON_DOWN):
        return {
            "status": status,
            "message": "Daemon is not running",
            "action": "meetcap.sh start",
            "checks": {
                "socket": {"exists": False, "responsive": False},
                "pid": {"file_exists": False, "pid": None, "alive": False},
                "service": {"installed": False, "enabled": False, "active": False},
                "binaries": {"missing": []},
                "audio_sources": dict(HEALTHY_SOURCES),
            },
        }

    def test_report_contains_key_lines(self):
        report = doctor.format_report(self._diagnosis())
        self.assertIn("daemon_down", report)
        self.assertIn("Daemon is not running", report)
        self.assertIn("Next action: meetcap.sh start", report)
        self.assertIn("missing binaries: none", report)
        self.assertIn("audio sources: ok", report)

    def test_invalid_sources_rendered(self):
        d = self._diagnosis()
        d["checks"]["audio_sources"] = {"ok": False, "mic": None, "system": None,
                                        "detail": "Sources not present: x"}
        report = doctor.format_report(d)
        self.assertIn("audio sources: invalid", report)
        self.assertIn("Sources not present: x", report)


class BootstrapCliTests(unittest.TestCase):
    @staticmethod
    def _empty_checks():
        return {
            "socket": {"exists": False, "responsive": False},
            "pid": {"file_exists": False, "pid": None, "alive": False},
            "service": {"installed": False, "enabled": False, "active": False},
            "binaries": {"missing": []},
            "audio_sources": dict(HEALTHY_SOURCES),
        }

    def test_doctor_json_output_exits_zero_when_healthy(self):
        diagnosis = {
            "status": doctor.HEALTHY, "message": "Daemon is healthy", "action": "",
            "checks": {
                "socket": {"exists": True, "responsive": True},
                "pid": {"file_exists": True, "pid": 1, "alive": True},
                "service": {"installed": True, "enabled": True, "active": True},
                "binaries": {"missing": []},
                "audio_sources": dict(HEALTHY_SOURCES),
            },
        }
        printed = []
        with patch("meetcap.doctor.diagnose", return_value=diagnosis), \
             patch("builtins.print", side_effect=printed.append):
            try:
                meetcap.doctor_cmd(["--json"])
            except SystemExit as e:
                self.assertEqual(e.code, 0)
        payload = json.loads(printed[0])
        self.assertEqual(payload["status"], "healthy")

    def test_doctor_fix_cleans_stale_files_then_rechecks(self):
        stale = {"status": doctor.STALE_SOCKET, "message": "stale", "action": "a",
                 "checks": self._empty_checks()}
        after = {"status": doctor.DAEMON_DOWN, "message": "down", "action": "a",
                 "checks": self._empty_checks()}
        with patch("meetcap.doctor.diagnose", side_effect=[stale, after]), \
             patch("meetcap.doctor.clean_stale_files",
                   return_value=["/tmp/meetcap.sock"]) as clean, \
             patch("builtins.print"):
            with self.assertRaises(SystemExit) as ctx:
                meetcap.doctor_cmd(["--fix"])
        clean.assert_called_once()
        self.assertEqual(ctx.exception.code, 1)

    def test_doctor_noop_fix_when_healthy(self):
        diagnosis = {"status": doctor.HEALTHY, "message": "ok", "action": "",
                     "checks": self._empty_checks()}
        with patch("meetcap.doctor.diagnose", return_value=diagnosis), \
             patch("meetcap.doctor.clean_stale_files") as clean, \
             patch("builtins.print"):
            try:
                meetcap.doctor_cmd(["--fix"])
            except SystemExit as e:
                self.assertEqual(e.code, 0)
        clean.assert_not_called()

    def test_start_when_already_running(self):
        with patch("meetcap.doctor.ping_socket", return_value=True), \
             patch("builtins.print") as pr:
            meetcap.start_cmd()
        pr.assert_any_call("Daemon already running")

    def test_start_via_service(self):
        svc = {"installed": True, "enabled": True, "active": False}
        with patch("meetcap.doctor.ping_socket", return_value=False), \
             patch("meetcap.doctor.check_service", return_value=svc), \
             patch("meetcap.doctor.service_start", return_value=True) as start, \
             patch("meetcap.wait_for_socket", return_value=True), \
             patch("builtins.print"):
            meetcap.start_cmd()
        start.assert_called_once()

    def test_start_service_failure_exits(self):
        svc = {"installed": True, "enabled": True, "active": False}
        with patch("meetcap.doctor.ping_socket", return_value=False), \
             patch("meetcap.doctor.check_service", return_value=svc), \
             patch("meetcap.doctor.service_start", return_value=False):
            with self.assertRaises(SystemExit):
                meetcap.start_cmd()

    def test_restart_without_service_spawns_manual(self):
        svc = {"installed": False, "enabled": False, "active": False}
        with patch("meetcap.doctor.check_service", return_value=svc), \
             patch("meetcap.doctor.read_pid", return_value=None), \
             patch("meetcap.doctor.stop_pid", return_value=True) as stop, \
             patch("meetcap.doctor.clean_stale_files", return_value=[]), \
             patch("meetcap._spawn_manual_daemon") as spawn, \
             patch("meetcap.wait_for_socket", return_value=True), \
             patch("builtins.print"):
            meetcap.restart_cmd()
        stop.assert_called_once()
        spawn.assert_called_once()

    def test_restart_via_service(self):
        svc = {"installed": True, "enabled": True, "active": True}
        with patch("meetcap.doctor.check_service", return_value=svc), \
             patch("meetcap.doctor.service_restart", return_value=True) as restart, \
             patch("meetcap.wait_for_socket", return_value=True), \
             patch("builtins.print"):
            meetcap.restart_cmd()
        restart.assert_called_once()

    def test_status_unhealthy_exits_with_doctor_report(self):
        diagnosis = {
            "status": doctor.DAEMON_DOWN, "message": "Daemon is not running",
            "action": "meetcap.sh start",
            "checks": {
                "socket": {"exists": False, "responsive": False},
                "pid": {"file_exists": False, "pid": None, "alive": False},
                "service": {"installed": False, "enabled": False, "active": False},
                "binaries": {"missing": []},
                "audio_sources": dict(HEALTHY_SOURCES),
            },
        }
        with patch("meetcap.doctor.ping_socket", return_value=False), \
             patch("meetcap.doctor.diagnose", return_value=diagnosis), \
             patch("builtins.print"):
            with self.assertRaises(SystemExit):
                meetcap.status_cmd()

    def test_status_healthy_prints_state(self):
        svc = {"installed": True, "enabled": True, "active": True}
        with patch("meetcap.doctor.ping_socket", return_value=True), \
             patch("meetcap.doctor.check_service", return_value=svc), \
             patch("meetcap.doctor.read_pid", return_value=42), \
             patch("meetcap._send", return_value='{"recording": false}'), \
             patch("builtins.print") as pr:
            meetcap.status_cmd()
        pr.assert_any_call("Daemon: running (pid 42)")
        pr.assert_any_call('State: {"recording": false}')
        pr.assert_any_call("Managed via: systemd user service")

    def test_install_service_cmd(self):
        with patch("meetcap.doctor.install_service",
                   return_value=Path("/dest/meetcap.service")) as inst, \
             patch("builtins.print"):
            meetcap.install_service_cmd()
        inst.assert_called_once()

    def test_spawn_manual_daemon_detaches(self):
        with patch("meetcap.doctor.clean_stale_files", return_value=[]), \
             patch("meetcap.subprocess.Popen") as popen, \
             patch("builtins.print"):
            meetcap._spawn_manual_daemon()
        self.assertTrue(popen.call_args.kwargs.get("start_new_session"))


if __name__ == "__main__":
    unittest.main()
