import os
from pathlib import Path


def runtime_dir() -> Path:
    """Return user-isolated runtime directory path according to precedence order.

    Precedence:
    1. MEETCAP_RUNTIME_DIR environment variable
    2. $XDG_RUNTIME_DIR/meetcap
    3. /run/user/<uid>/meetcap
    """
    meetcap_rt = os.environ.get("MEETCAP_RUNTIME_DIR")
    if meetcap_rt:
        path = Path(meetcap_rt)
    else:
        xdg_rt = os.environ.get("XDG_RUNTIME_DIR")
        if xdg_rt:
            path = Path(xdg_rt) / "meetcap"
        else:
            uid = os.getuid() if hasattr(os, "getuid") else 1000
            path = Path(f"/run/user/{uid}/meetcap")

    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    except OSError:
        pass
    return path


def ensure_runtime_dir(path: Path | None = None) -> Path:
    """Ensure directory exists with 0700 permissions."""
    target = path if path is not None else runtime_dir()
    try:
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass
    except OSError:
        pass
    return target


def socket_path() -> Path:
    """Return path to meetcap UNIX domain socket."""
    return runtime_dir() / "meetcap.sock"


def pid_path() -> Path:
    """Return path to meetcap daemon PID file."""
    return runtime_dir() / "meetcap.pid"


def state_path() -> Path:
    """Return path to meetcap state JSON file."""
    return runtime_dir() / "meetcap_state.json"


def log_path() -> Path:
    """Return path to meetcap daemon log file."""
    return runtime_dir() / "meetcap-daemon.log"


def export_jobs_path() -> Path:
    """Return path to meetcap export jobs state JSON file (#16)."""
    return runtime_dir() / "meetcap_export_jobs.json"


def pid_is_meetcap(pid: int | str | None) -> bool:
    """Return True only if process exists and /proc/<pid>/cmdline contains \"meetcap.py\"."""
    if pid is None:
        return False
    try:
        pid_int = int(pid)
    except (ValueError, TypeError):
        return False
    if pid_int <= 0:
        return False
    try:
        cmdline_file = Path(f"/proc/{pid_int}/cmdline")
        if not cmdline_file.exists():
            return False
        raw = cmdline_file.read_bytes()
        content = raw.decode("utf-8", errors="replace")
        return "meetcap.py" in content
    except OSError:
        return False


RUNTIME_DIR = runtime_dir()
SOCKET_PATH = socket_path()
PID_PATH = pid_path()
STATE_PATH = state_path()
LOG_PATH = log_path()
