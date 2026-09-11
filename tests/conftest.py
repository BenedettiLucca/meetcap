"""Fixtures and integration helpers for Meetcap daemon testing."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Optional, Union

import pytest

from tests.fixtures.synthetic import create_synthetic_wav, generate_synthetic_wav_bytes

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def wait_for_socket(
    sock_path: Union[Path, str],
    timeout: float = 5.0,
    poll_interval: float = 0.05,
    probe_cmd: Optional[str] = "status",
) -> bool:
    """Wait until UNIX socket exists and is responsive to connection/probe."""
    sock_path = Path(sock_path)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if sock_path.exists():
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.5)
            try:
                s.connect(str(sock_path))
                if probe_cmd is not None:
                    s.sendall((probe_cmd.strip() + "\n").encode("utf-8"))
                    data = s.recv(1024)
                    if data:
                        return True
                else:
                    return True
            except OSError:
                pass
            finally:
                s.close()
        time.sleep(poll_interval)
    return False


def send_socket_command(
    sock_path: Union[Path, str],
    cmd: str,
    timeout: float = 5.0,
) -> Union[Dict[str, Any], str]:
    """Connect to daemon UNIX socket, send command line, and parse response."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        s.sendall((cmd.strip() + "\n").encode("utf-8"))
        chunks = []
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8").strip()
        if not raw:
            raise RuntimeError(f"Empty response from daemon for command '{cmd}'")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    finally:
        s.close()


def stop_process(proc: subprocess.Popen, timeout: float = 3.0) -> int:
    """Gracefully terminate a subprocess via SIGTERM, falling back to SIGKILL."""
    if proc.poll() is not None:
        return proc.returncode
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except OSError:
            pass
    return proc.returncode


@dataclass
class DaemonProcess:
    """Encapsulates a running daemon subprocess with helper methods."""
    proc: subprocess.Popen
    runtime_dir: Path
    sock_path: Path
    pid_path: Path
    log_path: Path

    @property
    def is_alive(self) -> bool:
        return self.proc.poll() is None

    @property
    def returncode(self) -> Optional[int]:
        return self.proc.poll()

    def send(self, cmd: str, timeout: float = 5.0) -> Union[Dict[str, Any], str]:
        return send_socket_command(self.sock_path, cmd, timeout=timeout)

    def stop(self, timeout: float = 3.0) -> int:
        return stop_process(self.proc, timeout=timeout)

    def kill(self, timeout: float = 2.0) -> int:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=timeout)
        return self.proc.returncode

    def read_logs(self) -> str:
        if self.log_path.exists():
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        return ""


@pytest.fixture
def wait_socket() -> Callable[..., bool]:
    """Helper fixture to wait for socket availability."""
    return wait_for_socket


@pytest.fixture
def daemon_client() -> Callable[[Union[Path, str], str, float], Union[Dict[str, Any], str]]:
    """Helper fixture to send commands to a UNIX socket."""
    return send_socket_command


@pytest.fixture
def spawn_daemon(tmp_path: Path) -> Generator[Callable[..., DaemonProcess], None, None]:
    """Fixture providing a factory to spawn real daemon subprocesses with guaranteed teardown."""
    managed_daemons: list[DaemonProcess] = []
    opened_files: list[Any] = []

    def _spawn(
        runtime_dir: Optional[Path] = None,
        extra_env: Optional[Dict[str, str]] = None,
        wait: bool = True,
        timeout: float = 5.0,
    ) -> DaemonProcess:
        rt_dir = runtime_dir or (tmp_path / "runtime")
        rt_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(rt_dir, 0o700)
        except OSError:
            pass

        log_path = rt_dir / f"daemon_subproc_{len(managed_daemons)}.log"
        log_file = log_path.open("w+", encoding="utf-8")
        opened_files.append(log_file)

        env = os.environ.copy()
        env["MEETCAP_RUNTIME_DIR"] = str(rt_dir)
        env["PYTHONUNBUFFERED"] = "1"
        if extra_env:
            env.update(extra_env)

        proc = subprocess.Popen(
            [sys.executable, str(SRC_DIR / "meetcap.py"), "daemon"],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_file,
            stderr=log_file,
            text=True,
        )

        daemon = DaemonProcess(
            proc=proc,
            runtime_dir=rt_dir,
            sock_path=rt_dir / "meetcap.sock",
            pid_path=rt_dir / "meetcap.pid",
            log_path=log_path,
        )
        managed_daemons.append(daemon)

        if wait:
            ready = wait_for_socket(daemon.sock_path, timeout=timeout)
            if not ready:
                if daemon.is_alive:
                    daemon.stop(timeout=1.0)
                raise TimeoutError(
                    f"Daemon did not become ready on {daemon.sock_path} within {timeout}s.\n"
                    f"Subprocess exit code: {daemon.returncode}\n"
                    f"Subprocess logs:\n{daemon.read_logs()}"
                )

        return daemon

    yield _spawn

    for d in managed_daemons:
        d.stop(timeout=3.0)
    for f in opened_files:
        try:
            f.close()
        except Exception:
            pass


@pytest.fixture
def synthetic_recording(request) -> Generator[Path, None, None]:
    """Temporary synthetic WAV in RECORDINGS_DIR, safely cleaned up after test."""
    recordings_dir = REPO_ROOT / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"meeting-synthetic-test-{os.getpid()}-{int(time.time() * 1000)}.wav"
    synthetic_path = recordings_dir / file_name
    create_synthetic_wav(synthetic_path, duration_ms=200)
    try:
        yield synthetic_path
    finally:
        if synthetic_path.exists():
            synthetic_path.unlink()
