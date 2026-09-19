"""Integration tests for Meetcap daemon lifecycle and UNIX socket IPC (#41).

Exercising REAL subprocesses and REAL UNIX sockets with temporary runtime directories.
No physical audio hardware or PipeWire devices required.
"""

import sys
from pathlib import Path




def test_daemon_lifecycle_status_and_clean_shutdown(spawn_daemon, tmp_path):
    """Scenario 1: daemon starts up, answers `status` via socket, and shuts down cleanly.

    Validates:
    - Subprocess spawns and creates listening UNIX domain socket.
    - Status query over socket returns valid JSON payload.
    - Process PID matches PID file content.
    - SIGTERM triggers clean shutdown without orphan socket or PID file.
    """
    rt_dir = tmp_path / "rt_clean"
    daemon = spawn_daemon(runtime_dir=rt_dir, wait=True)

    # 1. Verify files exist while running
    assert daemon.sock_path.exists(), "Socket file must exist while daemon is running"
    assert daemon.pid_path.exists(), "PID file must exist while daemon is running"
    assert int(daemon.pid_path.read_text().strip()) == daemon.proc.pid

    # 2. Query status via socket
    status = daemon.send("status")
    assert isinstance(status, dict), f"Expected JSON response from daemon, got: {status}"
    assert status.get("recording") is False
    assert status.get("transcribing") is False
    assert "last_file" in status
    assert "error" in status

    # 3. Clean shutdown via SIGTERM
    exit_code = daemon.stop(timeout=3.0)
    assert exit_code == 0, f"Daemon should exit with code 0 on SIGTERM, got {exit_code}"

    # 4. Verify no orphan files remain
    assert not daemon.sock_path.exists(), "Socket file must be unlinked on clean shutdown"
    assert not daemon.pid_path.exists(), "PID file must be unlinked on clean shutdown"


def test_second_instance_preserves_live_daemon_socket(spawn_daemon, tmp_path):
    """Scenario 2: second instance in same runtime dir detects live daemon and exits without unlinking socket (#28).

    Validates:
    - Instance 1 starts and acquires socket.
    - Instance 2 attempts to run in the same MEETCAP_RUNTIME_DIR.
    - Instance 2 detects the live instance, refuses to overwrite/unlink socket, and exits with code 1.
    - Instance 1 continues operating uninterrupted and answers status.
    - Socket file was not unlinked or corrupted by Instance 2.
    """
    rt_dir = tmp_path / "rt_single_instance"
    daemon1 = spawn_daemon(runtime_dir=rt_dir, wait=True)
    orig_pid = daemon1.proc.pid

    # Spawn second instance in the same runtime dir (without waiting, since it must exit immediately)
    daemon2 = spawn_daemon(runtime_dir=rt_dir, wait=False)

    # Instance 2 must terminate quickly with exit code 1
    exit_code2 = daemon2.proc.wait(timeout=5.0)
    assert exit_code2 == 1, f"Second instance must exit with code 1, got {exit_code2}"

    logs2 = daemon2.read_logs()
    assert "Another meetcap daemon is already running" in logs2

    # Verify Instance 1's socket still exists and was not unlinked
    assert daemon1.sock_path.exists(), "Socket file must NOT be unlinked by rejected second instance"

    # Verify Instance 1 is still responsive and healthy
    status1 = daemon1.send("status")
    assert isinstance(status1, dict)
    assert status1.get("recording") is False

    # Verify PID file still contains Instance 1's PID
    assert int(daemon1.pid_path.read_text().strip()) == orig_pid

    # Clean shutdown of Instance 1
    daemon1.stop()
    assert not daemon1.sock_path.exists()
    assert not daemon1.pid_path.exists()


def test_orphan_socket_recovery_after_sigkill(spawn_daemon, tmp_path):
    """Scenario 3: daemon killed with SIGKILL leaves orphan socket; new daemon unlinks and binds successfully.

    Validates:
    - Daemon 1 is forcefully killed with SIGKILL, leaving orphan socket and PID files behind.
    - New daemon (Instance 2) in the same runtime directory detects orphan socket.
    - Instance 2 unlinks the stale socket and binds successfully.
    - Instance 2 answers commands and updates PID file.
    - Instance 2 shuts down cleanly.
    """
    rt_dir = tmp_path / "rt_orphan"
    daemon1 = spawn_daemon(runtime_dir=rt_dir, wait=True)
    pid1 = daemon1.proc.pid

    # Kill daemon 1 with SIGKILL (preventing signal handlers / cleanup)
    daemon1.kill()
    assert daemon1.sock_path.exists(), "Socket file should remain orphaned on SIGKILL"
    assert daemon1.pid_path.exists(), "PID file should remain orphaned on SIGKILL"

    # Verify daemon 1 is not running
    assert not daemon1.is_alive

    # Spawn daemon 2 in the same runtime directory; it must recover from orphan socket
    daemon2 = spawn_daemon(runtime_dir=rt_dir, wait=True, timeout=5.0)
    pid2 = daemon2.proc.pid
    assert pid2 != pid1

    # Daemon 2 successfully bound and answers queries
    status2 = daemon2.send("status")
    assert isinstance(status2, dict)
    assert status2.get("recording") is False

    # PID file should now contain daemon 2's PID
    assert int(daemon2.pid_path.read_text().strip()) == pid2

    # Clean shutdown of daemon 2
    daemon2.stop()
    assert not daemon2.sock_path.exists(), "Socket must be removed after clean shutdown of daemon 2"
    assert not daemon2.pid_path.exists(), "PID file must be removed after clean shutdown of daemon 2"


def test_list_command_graceful_degradation_without_audio_hardware(spawn_daemon, tmp_path):
    """Scenario 4: `list` responds without depending on ffmpeg/PipeWire (daemon degrades gracefully).

    Validates:
    - Daemon operates in a restricted environment where audio tools/PipeWire are unavailable.
    - `list` returns valid JSON recordings list without throwing errors.
    - Attempting `record` returns a clean error without crashing or wedging the daemon.
    - Daemon remains responsive for subsequent commands and terminates cleanly.
    """
    rt_dir = tmp_path / "rt_degraded"
    bin_dir = tmp_path / "dummy_bin"
    bin_dir.mkdir()
    # Symlink only the python executable to dummy bin directory so ffmpeg/pactl are absent from PATH
    python_link = bin_dir / "python3"
    python_link.symlink_to(sys.executable)

    degraded_env = {
        "PATH": str(bin_dir),
        "PIPEWIRE_RUNTIME_DIR": "/dev/null/pw",
        "PIPEWIRE_REMOTE": "nonexistent",
        "PULSE_SERVER": "/dev/null/pulse",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }

    daemon = spawn_daemon(runtime_dir=rt_dir, extra_env=degraded_env, wait=True)

    # 1. `list` command responds properly with a recordings list
    list_resp = daemon.send("list")
    assert isinstance(list_resp, dict), f"Expected dict, got {list_resp}"
    assert "recordings" in list_resp
    assert isinstance(list_resp["recordings"], list)

    # 2. `record` command gracefully reports error instead of crashing
    rec_resp = daemon.send("record")
    assert isinstance(rec_resp, dict)
    assert rec_resp.get("ok") is False
    assert "No audio sources" in rec_resp.get("error", "")

    # 3. Daemon is still responsive after failed record
    status_resp = daemon.send("status")
    assert status_resp.get("recording") is False

    daemon.stop()
    assert daemon.returncode == 0


def test_list_command_reflects_synthetic_recordings(spawn_daemon, tmp_path, synthetic_recording):
    """Scenario 4 (supplement): `list` correctly enumerates WAV files from disk without audio hardware."""
    rt_dir = tmp_path / "rt_list_synthetic"
    daemon = spawn_daemon(runtime_dir=rt_dir, wait=True)

    list_resp = daemon.send("list")
    assert isinstance(list_resp, dict)
    assert "recordings" in list_resp
    assert synthetic_recording.name in list_resp["recordings"]

    daemon.stop()
    assert daemon.returncode == 0
