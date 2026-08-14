#!/usr/bin/env python3
"""Meetcap doctor — daemon health checks, diagnosis, and recovery helpers.

Checks, in order: socket responsiveness, PID file liveness, systemd user
service state, required binaries, and configured audio sources. Produces a
human-readable diagnosis plus machine-readable findings and a concrete
recovery action for the CLI, rofi, and tests to consume.
"""

import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

SERVICE_NAME = "meetcap"
REQUIRED_BINARIES = ("ffmpeg", "pactl", "socat", "rofi")

# Overall status codes
HEALTHY = "healthy"
MISSING_DEPS = "missing_deps"
WEDGED_DAEMON = "wedged_daemon"
STALE_SOCKET = "stale_socket"
STALE_PID = "stale_pid"
DAEMON_DOWN = "daemon_down"
INVALID_SOURCE = "invalid_source"

_MESSAGES = {
    HEALTHY: "Daemon is healthy",
    MISSING_DEPS: "Required binaries are missing",
    WEDGED_DAEMON: "Daemon process is alive but the socket is unresponsive",
    STALE_SOCKET: "Stale socket file left behind by a dead daemon",
    STALE_PID: "Stale PID file left behind by a dead daemon",
    DAEMON_DOWN: "Daemon is not running",
    INVALID_SOURCE: "Configured audio sources are not available",
}


# ── Individual checks ────────────────────────────────────────────────
def ping_socket(socket_path, timeout=1.0):
    """Return True if the daemon socket accepts connections and answers."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(socket_path))
        s.sendall(b"status\n")
        return bool(s.recv(4096))
    except OSError:
        return False
    finally:
        s.close()


def read_pid(pid_file):
    """Return the PID recorded in pid_file, or None if unreadable."""
    try:
        return int(Path(pid_file).read_text().strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid):
    """Return True if the given PID belongs to a live process."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Process exists but is owned by someone else.
    except (OverflowError, OSError):
        return False


def check_binaries(names=REQUIRED_BINARIES):
    """Return the list of required binaries that are not on PATH."""
    return [name for name in names if shutil.which(name) is None]


def check_service(name=SERVICE_NAME):
    """Report systemd user-service state: installed/enabled/active."""
    result = {"installed": False, "enabled": False, "active": False}
    try:
        enabled = subprocess.run(
            ["systemctl", "--user", "is-enabled", name],
            capture_output=True, text=True, timeout=5,
        )
        active = subprocess.run(
            ["systemctl", "--user", "is-active", name],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return result
    # `is-enabled` exits 4 when the unit file does not exist.
    if enabled.returncode != 4:
        result["installed"] = True
    result["enabled"] = enabled.returncode == 0
    result["active"] = active.stdout.strip() == "active"
    return result


def check_audio_sources():
    """Validate the default mic and system monitor sources via pactl."""
    result = {"ok": None, "mic": None, "system": None, "detail": ""}
    try:
        mic = subprocess.run(
            ["pactl", "get-default-source"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        sink = subprocess.run(
            ["pactl", "get-default-sink"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        result["detail"] = f"pactl unavailable: {e}"
        return result
    if not mic or not sink:
        result["ok"] = False
        result["detail"] = "No default source/sink configured in PipeWire"
        return result
    monitor = sink + ".monitor"
    result["mic"] = mic
    result["system"] = monitor
    try:
        listing = subprocess.run(
            ["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        # Defaults exist but the listing is unavailable; assume valid.
        result["ok"] = True
        return result
    names = set()
    for line in listing.splitlines():
        cols = line.split("\t")
        if len(cols) > 1:
            names.add(cols[1].strip())
    missing = [n for n in (mic, monitor) if n not in names]
    if missing:
        result["ok"] = False
        result["detail"] = "Sources not present: " + ", ".join(missing)
    else:
        result["ok"] = True
    return result


# ── Diagnosis ────────────────────────────────────────────────────────
def _classify(missing, socket_exists, socket_ok, pid, pid_ok, sources):
    if missing:
        return MISSING_DEPS
    if socket_ok:
        if sources.get("ok") is False:
            return INVALID_SOURCE
        return HEALTHY
    if pid_ok:
        return WEDGED_DAEMON
    if socket_exists:
        return STALE_SOCKET
    if pid is not None:
        return STALE_PID
    return DAEMON_DOWN


def _action(status, svc):
    via_service = svc["installed"]
    if status == MISSING_DEPS:
        return "Install the missing binaries, then re-run: meetcap.sh doctor"
    if status == INVALID_SOURCE:
        return "Check PipeWire sources: pactl list short sources"
    if status == WEDGED_DAEMON:
        if via_service:
            return "systemctl --user restart meetcap"
        return "meetcap.sh restart"
    if status in (STALE_SOCKET, STALE_PID, DAEMON_DOWN):
        if via_service:
            return "systemctl --user start meetcap"
        return "meetcap.sh start (or: meetcap.sh install-service for autostart)"
    return ""


def diagnose(socket_path, pid_file, *, binaries=REQUIRED_BINARIES,
             service=SERVICE_NAME, sources=None):
    """Run all checks and return a diagnosis dict with a recovery action."""
    socket_exists = Path(socket_path).exists()
    socket_ok = ping_socket(socket_path) if socket_exists else False
    pid = read_pid(pid_file)
    pid_ok = pid_alive(pid)
    missing = check_binaries(binaries)
    svc = check_service(service)
    if sources is None:
        sources = check_audio_sources()

    status = _classify(missing, socket_exists, socket_ok, pid, pid_ok, sources)
    message = _MESSAGES[status]
    if status == MISSING_DEPS:
        message += ": " + ", ".join(missing)
    elif status == INVALID_SOURCE and sources.get("detail"):
        message += " — " + sources["detail"]

    return {
        "status": status,
        "message": message,
        "action": _action(status, svc),
        "checks": {
            "socket": {"exists": socket_exists, "responsive": socket_ok},
            "pid": {"file_exists": Path(pid_file).exists(), "pid": pid, "alive": pid_ok},
            "service": svc,
            "binaries": {"missing": missing},
            "audio_sources": sources,
        },
    }


def format_report(diagnosis):
    """Render a diagnosis dict as a human-readable multi-line report."""
    lines = [f"Meetcap doctor: {diagnosis['status']}", diagnosis["message"]]
    if diagnosis["action"]:
        lines.append(f"Next action: {diagnosis['action']}")
    checks = diagnosis["checks"]
    sock = checks["socket"]
    lines.append(f"  socket: exists={sock['exists']} responsive={sock['responsive']}")
    pidc = checks["pid"]
    lines.append(
        f"  pid file: exists={pidc['file_exists']} pid={pidc['pid']} alive={pidc['alive']}"
    )
    svc = checks["service"]
    lines.append(
        f"  service: installed={svc['installed']} enabled={svc['enabled']} active={svc['active']}"
    )
    lines.append(f"  missing binaries: {', '.join(checks['binaries']['missing']) or 'none'}")
    src = checks["audio_sources"]
    if src.get("ok") is None:
        state = "unknown"
    else:
        state = "ok" if src["ok"] else "invalid"
    lines.append(f"  audio sources: {state}" + (f" ({src.get('detail')})" if src.get("detail") else ""))
    return "\n".join(lines)


# ── Recovery helpers ─────────────────────────────────────────────────
def clean_stale_files(socket_path, pid_file):
    """Remove stale socket/PID files. Safe to call when the daemon is down."""
    removed = []
    for p in (socket_path, pid_file):
        try:
            Path(p).unlink()
            removed.append(str(p))
        except OSError:
            pass
    return removed


def stop_pid(pid, timeout=5.0):
    """SIGTERM a PID and wait; escalate to SIGKILL after timeout."""
    if not pid_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return not pid_alive(pid)


def service_start(name=SERVICE_NAME):
    try:
        return subprocess.run(
            ["systemctl", "--user", "start", name], capture_output=True, timeout=15
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def service_restart(name=SERVICE_NAME):
    try:
        return subprocess.run(
            ["systemctl", "--user", "restart", name], capture_output=True, timeout=15
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install_service(unit_path, dest_dir=None):
    """Copy the unit file into the user systemd dir, reload, enable --now."""
    src = Path(unit_path)
    if not src.exists():
        raise FileNotFoundError(f"Unit file not found: {src}")
    dest_dir = Path(dest_dir) if dest_dir else Path.home() / ".config" / "systemd" / "user"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=15)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", src.stem], capture_output=True, timeout=15
    )
    return dest
