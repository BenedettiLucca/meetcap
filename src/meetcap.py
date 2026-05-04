#!/usr/bin/env python3
"""Meetcap — meeting audio capture + transcription daemon.

Daemon controlled via UNIX socket. Talks to rofi for UI.
Records dual-channel WAV (mic=left, system audio=right) via ffmpeg/PipeWire.
Transcribes with faster-whisper (CUDA GPU).
"""

import os
import sys
import json
import signal
import socket
import subprocess
import threading
from datetime import datetime
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────
BASE_DIR = Path.home() / "Projects" / "meetcap"
RECORDINGS_DIR = BASE_DIR / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
SOCKET_PATH = Path("/tmp/meetcap.sock")
PID_FILE = Path("/tmp/meetcap.pid")
STATE_FILE = Path("/tmp/meetcap_state.json")

WHISPER_MODEL = os.environ.get("MEETCAP_MODEL", "large-v3")
WHISPER_DEVICE = os.environ.get("MEETCAP_DEVICE", "cuda")
WHISPER_COMPUTE = os.environ.get("MEETCAP_COMPUTE", "float16")

# ── State ───────────────────────────────────────────────────────────
class State:
    recording_proc = None
    recording_file = None
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
    except Exception as e:
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
        state.recording_file = wav_file

        cmd = [
            "ffmpeg", "-y",
            "-f", "pulse", "-i", mic_src,
            "-f", "pulse", "-i", sys_src,
            "-filter_complex",
            "[0:a]aformat=sample_rates=16000|channel_layouts=mono[mic];"
            "[1:a]aformat=sample_rates=16000|channel_layouts=mono[sys];"
            "[mic][sys]join=inputs=2:channel_layout=stereo[out]",
            "-map", "[out]",
            "-acodec", "pcm_s16le",
            str(wav_file),
        ]

        state.recording_proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
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
        state.last_error = None
        save_state()

    notify("Meetcap", f"⏹ Recording saved", str(wav.name) if wav else "")
    print(f"[STOPPED] {wav}")
    return {"ok": True, "file": str(wav)}


# ── Transcription ────────────────────────────────────────────────────
def format_timestamp(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def transcribe(wav_path):
    from faster_whisper import WhisperModel

    print(f"[WHISPER] Loading {WHISPER_MODEL} on {WHISPER_DEVICE}...")
    model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)

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
        txt = transcribe(wav)
        notify("Meetcap", "✅ Transcript done", os.path.basename(txt))
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


def transcribe_cmd():
    """Handle transcribe command — runs in background."""
    if state.is_recording:
        return {"ok": False, "error": "Stop recording first"}
    threading.Thread(target=do_transcribe_last, daemon=True).start()
    return {"ok": True, "status": "transcription started"}


# ── Helpers ──────────────────────────────────────────────────────────
def notify(title, body, subtitle=""):
    try:
        cmd = ["notify-send", title, body]
        if subtitle:
            cmd.append(subtitle)
        subprocess.run(cmd, timeout=3, capture_output=True)
    except Exception:
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
    except Exception as e:
        try:
            conn.sendall((json.dumps({"ok": False, "error": str(e)}) + "\n").encode())
        except Exception:
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
def send_command(cmd):
    """Send a command to the daemon and return the response."""
    if not SOCKET_PATH.exists():
        print("Meetcap daemon is not running", file=sys.stderr)
        sys.exit(1)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(SOCKET_PATH))
        s.sendall((cmd + "\n").encode())
        data = s.recv(4096).decode().strip()
        s.close()
        return data
    except Exception as e:
        print(f"Error communicating with daemon: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: meetcap.py <daemon|status|record|stop|toggle|transcribe|list>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "daemon":
        print("Meetcap daemon starting...")
        print(f"  Recordings: {RECORDINGS_DIR}")
        print(f"  Whisper: {WHISPER_MODEL} on {WHISPER_DEVICE}")
        run_server()
    else:
        # Client mode — send command to daemon
        response = send_command(cmd)
        print(response)


if __name__ == "__main__":
    main()
