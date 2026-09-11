#!/usr/bin/env python3
"""Meetcap — meeting audio capture + transcription daemon.

Daemon controlled via UNIX socket. Talks to rofi for UI.
Records dual-channel WAV (mic=left, system audio=right) via ffmpeg/PipeWire.
Transcribes with faster-whisper (CUDA GPU).
"""

import gc
import os
import sys
import json
import signal
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import doctor

# ── Config ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = BASE_DIR / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
SOCKET_PATH = Path("/tmp/meetcap.sock")
PID_FILE = Path("/tmp/meetcap.pid")
STATE_FILE = Path("/tmp/meetcap_state.json")

WHISPER_MODEL = os.environ.get("MEETCAP_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2")
# Normalize the legacy canonical name to the pinned repo so in-proc fallback
# shares the same HF cache as the router worker (single 1.6G copy, not two).
if WHISPER_MODEL == "large-v3-turbo":
    WHISPER_MODEL = "deepdml/faster-whisper-large-v3-turbo-ct2"
WHISPER_DEVICE = os.environ.get("MEETCAP_DEVICE", "cuda")
WHISPER_COMPUTE = os.environ.get("MEETCAP_COMPUTE", "float16")

# ── State ───────────────────────────────────────────────────────────
class State:
    recording_proc = None
    recording_file = None
    recording_log = None
    is_recording = False
    is_transcribing = False
    last_error = None

state = State()
state_lock = threading.Lock()


def save_state():
    """Write state to JSON file so rofi can read it."""
    data = {
        "recording": state.is_recording,
        "transcribing": state.is_transcribing,
        "last_file": str(state.recording_file) if state.recording_file else None,
        "error": state.last_error,
    }
    STATE_FILE.write_text(json.dumps(data))


def get_audio_sources():
    """Get default mic and system audio monitor from PipeWire."""
    try:
        default_mic = subprocess.check_output(
            ["pactl", "get-default-source"], text=True
        ).strip()
        default_sink = subprocess.check_output(
            ["pactl", "get-default-sink"], text=True
        ).strip()
        return default_mic, default_sink + ".monitor"
    except (OSError, subprocess.SubprocessError):
        return None, None


# ── Recording ────────────────────────────────────────────────────────
def start_recording():
    with state_lock:
        if state.is_recording:
            return {"ok": False, "error": "Already recording"}

        mic_src, sys_src = get_audio_sources()
        if not mic_src or not sys_src:
            return {"ok": False, "error": "No audio sources detected"}

        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        wav_file = RECORDINGS_DIR / f"meeting-{ts}.wav"
        log_file = wav_file.with_suffix(".ffmpeg.log")
        state.recording_file = wav_file
        state.recording_log = log_file

        cmd = [
            "ffmpeg", "-y",
            "-f", "pulse", "-i", mic_src,
            "-f", "pulse", "-i", sys_src,
            "-filter_complex",
            "[0:a]aformat=sample_rates=16000:channel_layouts=mono[mic];"
            "[1:a]aformat=sample_rates=16000:channel_layouts=mono[sys];"
            "[mic][sys]join=inputs=2:channel_layout=stereo[out]",
            "-map", "[out]",
            "-acodec", "pcm_s16le",
            str(wav_file),
        ]

        with log_file.open("wb") as stderr_log:
            state.recording_proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr_log
            )

        # ffmpeg can fail immediately on filter/source errors. Do not lie to the UI.
        time.sleep(0.5)
        if state.recording_proc.poll() is not None:
            error_tail = ""
            try:
                error_tail = "\n".join(log_file.read_text(errors="replace").splitlines()[-8:])
            except OSError:
                pass
            state.last_error = f"ffmpeg exited immediately ({state.recording_proc.returncode})"
            if error_tail:
                state.last_error += f": {error_tail}"
            state.recording_proc = None
            state.is_recording = False
            save_state()
            notify("Meetcap", "❌ Recording failed", state.last_error[:180])
            return {"ok": False, "error": state.last_error, "log": str(log_file)}

        state.is_recording = True
        state.last_error = None
        save_state()

    # Send desktop notification
    notify("Meetcap", "🎙 Recording started", f"mic={mic_src}\nsys={sys_src}")
    print(f"[RECORDING] {wav_file}")
    return {"ok": True, "file": str(wav_file)}


def stop_recording():
    with state_lock:
        if not state.is_recording or not state.recording_proc:
            return {"ok": False, "error": "Not recording"}

        try:
            state.recording_proc.communicate(input=b"q", timeout=5)
        except subprocess.TimeoutExpired:
            state.recording_proc.kill()
            state.recording_proc.wait()

        wav = state.recording_file
        state.is_recording = False
        state.recording_proc = None
        if not wav or not wav.exists() or wav.stat().st_size <= 44:
            state.last_error = f"Recording stopped but WAV was not created or is empty: {wav}"
            save_state()
            notify("Meetcap", "❌ Recording failed", state.last_error[:180])
            return {"ok": False, "error": state.last_error}
        state.last_error = None
        save_state()

    notify("Meetcap", f"⏹ Recording saved", str(wav.name) if wav else "")
    print(f"[STOPPED] {wav}")
    return {"ok": True, "file": str(wav)}


# ── Transcription ────────────────────────────────────────────────────
def format_timestamp(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


ROUTER_TRANSCRIBE_URL = "http://127.0.0.1:8090/v1/audio/transcriptions"


def transcribe_via_router(wav_path):
    """HTTP-first transcription through the local model router.

    The router arbitrates VRAM (swaps out any resident LLM for the whisper
    worker), so meetcap no longer has to win the GPU lottery on its own.
    Returns the .txt path, or raises on any failure (caller falls back in-proc).
    """
    import urllib.request
    import uuid as _uuid

    boundary = _uuid.uuid4().hex
    with open(wav_path, "rb") as f:
        audio = f.read()
    parts = []
    for name, value in [("model", "whisper-turbo"), ("response_format", "json")]:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{wav_path.name}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode() + audio + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        ROUTER_TRANSCRIBE_URL, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=1800) as resp:
        result = json.loads(resp.read())

    lines = [
        "# Meetcap Transcript",
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"File: {wav_path.name}",
        f"Model: {result.get('model', 'whisper-turbo')} (router/cuda/float16)",
        f"Language: {result.get('language', '?')}",
        f"Duration: {result.get('duration', 0):.1f}s",
        "",
        "---",
        "",
    ]
    for seg in result.get("segments", []):
        start = format_timestamp(seg["start"])
        end = format_timestamp(seg["end"])
        lines.append(f"[{start} → {end}] {seg['text'].strip()}")
    transcript = "\n".join(lines)
    txt_path = wav_path.with_suffix(".txt")
    txt_path.write_text(transcript)
    print(f"[DONE via router] {txt_path}")
    return str(txt_path)


def transcribe(wav_path):
    from faster_whisper import WhisperModel

    def attempt_transcribe(model_name, device, compute_type):
        print(f"[WHISPER] Loading {model_name} on {device}/{compute_type}...")
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        try:
            print(f"[WHISPER] Transcribing {wav_path}...")
            segments, info = model.transcribe(
                str(wav_path),
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
            )

            lines = [
                f"# Meetcap Transcript",
                f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"File: {wav_path.name}",
                f"Model: {model_name} ({device}/{compute_type})",
                f"Language: {info.language} ({info.language_probability:.1%})",
                f"Duration: {info.duration:.1f}s",
                "",
                "---",
                "",
            ]

            for seg in segments:
                start = format_timestamp(seg.start)
                end = format_timestamp(seg.end)
                lines.append(f"[{start} → {end}] {seg.text.strip()}")

            transcript = "\n".join(lines)
            txt_path = wav_path.with_suffix(".txt")
            txt_path.write_text(transcript)
            print(f"[DONE] {txt_path}")
            return str(txt_path)
        finally:
            del model
            gc.collect()

    attempts = []
    for candidate in [
        (WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE),
        ("deepdml/faster-whisper-large-v3-turbo-ct2", "cuda", "float16"),
        ("medium", "cuda", "float16"),
        ("medium", "cpu", "int8"),
    ]:
        if candidate not in attempts:
            attempts.append(candidate)

    last_error = None
    for idx, (model_name, device, compute_type) in enumerate(attempts, start=1):
        try:
            return attempt_transcribe(model_name, device, compute_type)
        except Exception as e:
            last_error = e
            error_text = str(e).lower()
            retryable_cuda_error = device == "cuda" and any(
                token in error_text
                for token in ["out of memory", "cuda failed", "cublas", "cudnn", "cuda"]
            )
            print(f"[WHISPER] Attempt {idx}/{len(attempts)} failed: {e}")
            if retryable_cuda_error and idx < len(attempts):
                print("[WHISPER] Retrying with safer fallback model/device...")
                continue
            raise

    raise last_error


def do_transcribe_last():
    """Transcribe the most recent WAV. Called in background thread."""
    with state_lock:
        if state.is_transcribing:
            return {"ok": False, "error": "Already transcribing"}
        wavs = sorted(RECORDINGS_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not wavs:
            return {"ok": False, "error": "No recordings found"}
        # Skip if .txt already exists
        wav = None
        for w in wavs:
            if not w.with_suffix(".txt").exists():
                wav = w
                break
        if not wav:
            wav = wavs[0]  # re-transcribe latest if all done
        state.is_transcribing = True
        state.last_error = None
        save_state()

    notify("Meetcap", "📝 Transcribing...", wav.name)

    try:
        try:
            # HTTP-first: router arbitrates VRAM with the rest of the local stack
            txt = transcribe_via_router(wav)
        except Exception as e:
            print(f"[WHISPER] Router path failed ({e}); falling back to in-proc")
            txt = transcribe(wav)
        notify("Meetcap", "✅ Transcript done", os.path.basename(txt))
        # Auto-export to Obsidian vault with AI summary
        threading.Thread(target=auto_export, args=(txt,), daemon=True).start()
        with state_lock:
            state.is_transcribing = False
            save_state()
        return {"ok": True, "file": txt}
    except Exception as e:
        with state_lock:
            state.is_transcribing = False
            state.last_error = str(e)
            save_state()
        notify("Meetcap", "❌ Transcription failed", str(e))
        return {"ok": False, "error": str(e)}


def auto_export(txt_path: str):
    """Run export_to_vault.py in a subprocess after transcription."""
    try:
        print(f"[EXPORT] Auto-exporting {txt_path} to vault...")
        notify("Meetcap", "📋 Generating summary...", "Exporting to Obsidian")
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "export_to_vault.py"), txt_path],
            capture_output=True, text=True, timeout=600,
            cwd=str(BASE_DIR),
        )
        if result.returncode == 0:
            print(f"[EXPORT] Success: {result.stdout.strip()}")
            notify("Meetcap", "✅ Exported to vault", "Check your Meetings folder")
        else:
            print(f"[EXPORT] Failed: {result.stderr.strip()}")
            notify("Meetcap", "⚠️ Export failed", result.stderr.strip()[:100])
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[EXPORT] Error: {e}")
        notify("Meetcap", "⚠️ Export error", str(e)[:100])


def _reset_transcribing_on_fail():
    """Clear stuck transcribing state and notify."""
    with state_lock:
        state.is_transcribing = False
        save_state()
    notify("Meetcap", "❌ Transcription failed", "Check logs for details")
    print("[WATCHDOG] Cleared stuck transcribing state")

def transcribe_cmd():
    """Handle transcribe command — runs in background."""
    if state.is_recording:
        return {"ok": False, "error": "Stop recording first"}
    t = threading.Thread(target=do_transcribe_last, daemon=True)
    t.start()
    # Start watchdog: if thread dies without resetting is_transcribing, clear it
    def watchdog():
        t.join(timeout=3600)
        with state_lock:
            if state.is_transcribing:
                state.is_transcribing = False
                save_state()
                notify("Meetcap", "⚠️ Transcription watchdog triggered", "Thread finished but state was stuck")
    threading.Thread(target=watchdog, daemon=True).start()
    return {"ok": True, "status": "transcription started"}


# ── Helpers ──────────────────────────────────────────────────────────
def notify(title, body, subtitle=""):
    try:
        cmd = ["notify-send", title, body]
        if subtitle:
            cmd.append(subtitle)
        subprocess.run(cmd, timeout=3, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


# ── Socket Server ────────────────────────────────────────────────────
def handle_command(cmd):
    """Process a command string and return a JSON response."""
    cmd = cmd.strip().lower()

    if cmd == "status":
        return {
            "recording": state.is_recording,
            "transcribing": state.is_transcribing,
            "last_file": str(state.recording_file) if state.recording_file else None,
            "error": state.last_error,
        }
    elif cmd == "record":
        return start_recording()
    elif cmd == "stop":
        return stop_recording()
    elif cmd == "transcribe":
        return transcribe_cmd()
    elif cmd == "toggle":
        if state.is_recording:
            return stop_recording()
        else:
            return start_recording()
    elif cmd == "list":
        wavs = sorted(RECORDINGS_DIR.glob("*.wav"), reverse=True)
        return {"recordings": [w.name for w in wavs[:20]]}
    else:
        return {"ok": False, "error": f"Unknown command: {cmd}"}


def client_handler(conn):
    """Handle a single client connection."""
    try:
        data = conn.recv(4096).decode().strip()
        if data:
            response = handle_command(data)
            conn.sendall((json.dumps(response) + "\n").encode())
    except (OSError, UnicodeDecodeError) as e:
        try:
            conn.sendall((json.dumps({"ok": False, "error": str(e)}) + "\n").encode())
        except OSError:
            pass
    finally:
        conn.close()


def run_server():
    """UNIX socket server main loop."""
    # Clean stale socket
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    server.listen(5)
    server.settimeout(1.0)  # Allow checking for shutdown

    print(f"[DAEMON] Listening on {SOCKET_PATH}")

    running = True

    def shutdown(signum=None, frame=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Write PID file
    PID_FILE.write_text(str(os.getpid()))

    while running:
        try:
            conn, _ = server.accept()
            threading.Thread(target=client_handler, args=(conn,), daemon=True).start()
        except socket.timeout:
            continue
        except OSError:
            break

    # Cleanup
    if state.is_recording:
        stop_recording()
    server.close()
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    if PID_FILE.exists():
        PID_FILE.unlink()
    STATE_FILE.unlink(missing_ok=True)
    print("[DAEMON] Stopped")


# ── CLI ──────────────────────────────────────────────────────────────
def _send(cmd):
    """Send a command to the daemon and return the raw response, or None."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(SOCKET_PATH))
        s.sendall((cmd + "\n").encode())
        data = s.recv(4096).decode().strip()
        s.close()
        return data
    except OSError:
        return None


def send_command(cmd):
    """Send a command to the daemon and return the response."""
    if not SOCKET_PATH.exists():
        print("Meetcap daemon is not running", file=sys.stderr)
        sys.exit(1)
    data = _send(cmd)
    if data is None:
        print("Error communicating with daemon", file=sys.stderr)
        sys.exit(1)
    return data


# ── Bootstrap / doctor CLI ───────────────────────────────────────────
def wait_for_socket(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if doctor.ping_socket(SOCKET_PATH):
            return True
        time.sleep(0.2)
    return False


def doctor_cmd(args):
    diagnosis = doctor.diagnose(SOCKET_PATH, PID_FILE)
    if "--fix" in args and diagnosis["status"] in (doctor.STALE_SOCKET, doctor.STALE_PID):
        for removed in doctor.clean_stale_files(SOCKET_PATH, PID_FILE):
            print(f"[DOCTOR] Removed stale file: {removed}")
        diagnosis = doctor.diagnose(SOCKET_PATH, PID_FILE)
    if "--json" in args:
        print(json.dumps(diagnosis, indent=2))
    else:
        print(doctor.format_report(diagnosis))
    sys.exit(0 if diagnosis["status"] == doctor.HEALTHY else 1)


def status_cmd():
    if not doctor.ping_socket(SOCKET_PATH):
        diagnosis = doctor.diagnose(SOCKET_PATH, PID_FILE)
        print(doctor.format_report(diagnosis))
        sys.exit(1)
    daemon_status = _send("status")
    svc = doctor.check_service()
    pid = doctor.read_pid(PID_FILE)
    print(f"Daemon: running (pid {pid})")
    if daemon_status:
        print(f"State: {daemon_status}")
    mode = "systemd user service" if svc["active"] else "manual"
    print(f"Managed via: {mode}")


def _spawn_manual_daemon():
    doctor.clean_stale_files(SOCKET_PATH, PID_FILE)
    log_path = Path("/tmp/meetcap-daemon.log")
    with log_path.open("ab") as log:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "daemon"],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True,
        )
    print(f"[START] Daemon launched in background (log: {log_path})")


def start_cmd():
    if doctor.ping_socket(SOCKET_PATH):
        print("Daemon already running")
        return
    svc = doctor.check_service()
    if svc["installed"]:
        if doctor.service_start():
            print("[START] systemctl --user start meetcap")
        else:
            print("[START] systemctl start failed; run: meetcap.sh doctor", file=sys.stderr)
            sys.exit(1)
    else:
        _spawn_manual_daemon()
    if wait_for_socket():
        print("[START] Daemon is up")
    else:
        print("[START] Daemon did not come up; run: meetcap.sh doctor", file=sys.stderr)
        sys.exit(1)


def restart_cmd():
    svc = doctor.check_service()
    if svc["installed"]:
        if doctor.service_restart():
            print("[RESTART] systemctl --user restart meetcap")
        else:
            print("[RESTART] systemctl restart failed; run: meetcap.sh doctor", file=sys.stderr)
            sys.exit(1)
    else:
        pid = doctor.read_pid(PID_FILE)
        if doctor.stop_pid(pid):
            print("[RESTART] Stopped old daemon")
        doctor.clean_stale_files(SOCKET_PATH, PID_FILE)
        _spawn_manual_daemon()
    if wait_for_socket():
        print("[RESTART] Daemon is up")
    else:
        print("[RESTART] Daemon did not come up; run: meetcap.sh doctor", file=sys.stderr)
        sys.exit(1)


def install_service_cmd():
    unit = BASE_DIR / "meetcap.service"
    try:
        dest = doctor.install_service(unit)
    except (OSError, shutil.Error) as e:
        print(f"[INSTALL] Failed to install service: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[INSTALL] Installed {dest}")
    print("[INSTALL] Enabled + started via systemctl --user enable --now meetcap")


def main():
    if len(sys.argv) < 2:
        print("Usage: meetcap.py <daemon|doctor|status|start|restart|install-service|record|stop|toggle|transcribe|list>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "daemon":
        print("Meetcap daemon starting...")
        print(f"  Recordings: {RECORDINGS_DIR}")
        print(f"  Whisper: {WHISPER_MODEL} on {WHISPER_DEVICE}")
        run_server()
    elif cmd == "doctor":
        doctor_cmd(sys.argv[2:])
    elif cmd == "status":
        status_cmd()
    elif cmd == "start":
        start_cmd()
    elif cmd == "restart":
        restart_cmd()
    elif cmd == "install-service":
        install_service_cmd()
    else:
        # Client mode — send command to daemon
        response = send_command(cmd)
        print(response)


if __name__ == "__main__":
    main()
