#!/usr/bin/env python3
"""Meetcap — system audio + mic recorder with Whisper transcription.

Tray icon app for Hyprland/Wayland (Arch Linux, CachyOS).
Records dual-channel WAV (mic=left, system audio=right) via ffmpeg/PipeWire.
Transcribes with faster-whisper (CUDA GPU).
"""

import os
import sys
import json
import signal
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

# ── Config ──────────────────────────────────────────────────────────
RECORDINGS_DIR = Path.home() / "Projects" / "meetcap" / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

WHISPER_MODEL = os.environ.get("MEETCAP_MODEL", "large-v3")
WHISPER_DEVICE = os.environ.get("MEETCAP_DEVICE", "cuda")
WHISPER_COMPUTE = os.environ.get("MEETCAP_COMPUTE", "float16")

# ── State ───────────────────────────────────────────────────────────
class State:
    recording_proc = None
    recording_file = None
    is_recording = False

state = State()


def get_audio_sources():
    """Get default mic source and system audio monitor source from PipeWire."""
    try:
        default_mic = subprocess.check_output(
            ["pactl", "get-default-source"], text=True
        ).strip()
        default_sink = subprocess.check_output(
            ["pactl", "get-default-sink"], text=True
        ).strip()
        monitor = default_sink + ".monitor"
        return default_mic, monitor
    except Exception as e:
        print(f"Error getting audio sources: {e}")
        return None, None


# ── Recording ────────────────────────────────────────────────────────
def start_recording():
    if state.is_recording:
        return False

    mic_src, sys_src = get_audio_sources()
    if not mic_src or not sys_src:
        print("ERROR: Could not detect audio sources")
        return False

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    wav_file = RECORDINGS_DIR / f"meeting-{ts}.wav"
    state.recording_file = wav_file

    # ffmpeg: dual-channel capture
    # -i 0 = mic (left), -i 1 = system audio monitor (right)
    # merge into stereo WAV: channel 0 = mic, channel 1 = system
    cmd = [
        "ffmpeg", "-y",
        "-f", "pulse", "-i", mic_src,          # input 0: mic
        "-f", "pulse", "-i", sys_src,           # input 1: system audio
        "-filter_complex",
        "[0:a]aformat=sample_rates=16000|channel_layouts=mono[mic];"
        "[1:a]aformat=sample_rates=16000|channel_layouts=mono[sys];"
        "[mic][sys]join=inputs=2:channel_layout=stereo[out]",
        "-map", "[out]",
        "-acodec", "pcm_s16le",
        str(wav_file),
    ]

    print(f"Recording to {wav_file}")
    print(f"  Mic: {mic_src}")
    print(f"  System: {sys_src}")

    state.recording_proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    state.is_recording = True
    return True


def stop_recording():
    if not state.is_recording or not state.recording_proc:
        return None

    # Send 'q' to ffmpeg for clean exit
    try:
        state.recording_proc.communicate(input=b"q", timeout=5)
    except subprocess.TimeoutExpired:
        state.recording_proc.kill()
        state.recording_proc.wait()

    state.is_recording = False
    wav = state.recording_file
    state.recording_proc = None
    state.recording_file = None
    print(f"Recording saved: {wav}")
    return wav


# ── Transcription ────────────────────────────────────────────────────
def transcribe(wav_path):
    """Transcribe WAV file with faster-whisper. Returns transcript text."""
    from faster_whisper import WhisperModel

    print(f"Loading Whisper model '{WHISPER_MODEL}' on {WHISPER_DEVICE}...")
    model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)

    print(f"Transcribing {wav_path}...")
    segments, info = model.transcribe(
        str(wav_path),
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    print(f"Detected language: {info.language} (prob: {info.language_probability:.2f})")

    lines = []
    lines.append(f"# Meetcap Transcript")
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"File: {wav_path.name}")
    lines.append(f"Language: {info.language} ({info.language_probability:.1%})")
    lines.append(f"Duration: {info.duration:.1f}s")
    lines.append("")
    lines.append("---")
    lines.append("")

    for seg in segments:
        start = format_timestamp(seg.start)
        end = format_timestamp(seg.end)
        lines.append(f"[{start} → {end}] {seg.text.strip()}")

    transcript = "\n".join(lines)

    # Save .txt alongside the wav
    txt_path = wav_path.with_suffix(".txt")
    txt_path.write_text(transcript)
    print(f"Transcript saved: {txt_path}")

    return txt_path


def format_timestamp(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def transcribe_last():
    """Find and transcribe the most recent WAV in recordings dir."""
    wavs = sorted(RECORDINGS_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not wavs:
        print("No recordings found")
        return None
    return transcribe(wavs[0])


# ── Tray Icon ────────────────────────────────────────────────────────
def create_icon(state_str="idle"):
    """Generate a simple tray icon. 32x32 colored circle."""
    size = 32
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    colors = {"idle": "#888888", "recording": "#FF0000", "transcribing": "#FFAA00"}
    color = colors.get(state_str, "#888888")

    # filled circle
    margin = 4
    draw.ellipse([margin, margin, size - margin, size - margin], fill=color)

    # mic symbol (simple) for idle, dot for recording, pen for transcribing
    if state_str == "recording":
        draw.ellipse([12, 12, 20, 20], fill="#FFFFFF")
    elif state_str == "transcribing":
        draw.rectangle([14, 8, 18, 24], fill="#FFFFFF")

    return img


def update_tray(icon):
    """Update icon appearance based on state."""
    if state.is_recording:
        icon.icon = create_icon("recording")
        icon.title = "Meetcap — Recording..."
    else:
        icon.icon = create_icon("idle")
        icon.title = "Meetcap"


def on_record(icon, item):
    if state.is_recording:
        wav = stop_recording()
        update_tray(icon)
        if wav:
            icon.notify(f"Saved: {wav.name}", title="Meetcap")
    else:
        ok = start_recording()
        update_tray(icon)
        if ok:
            icon.notify("Recording started", title="Meetcap")
        else:
            icon.notify("Failed to start recording", title="Meetcap")


def on_transcribe(icon, item):
    if state.is_recording:
        icon.notify("Stop recording first!", title="Meetcap")
        return

    icon.icon = create_icon("transcribing")
    icon.title = "Meetcap — Transcribing..."
    icon.notify("Transcribing last recording...", title="Meetcap")

    def do_transcribe():
        try:
            txt = transcribe_last()
            update_tray(icon)
            if txt:
                icon.notify(f"Done: {txt.name}", title="Meetcap")
            else:
                icon.notify("No recordings found", title="Meetcap")
        except Exception as e:
            update_tray(icon)
            icon.notify(f"Error: {e}", title="Meetcap")
            print(f"Transcription error: {e}")

    threading.Thread(target=do_transcribe, daemon=True).start()


def on_open(icon, item):
    subprocess.Popen(["xdg-open", str(RECORDINGS_DIR)])


def on_quit(icon, item):
    if state.is_recording:
        stop_recording()
    icon.stop()


def build_menu():
    return pystray.Menu(
        pystray.MenuItem("🎙 Record / ⏹ Stop", on_record),
        pystray.MenuItem("📝 Transcribe Last", on_transcribe),
        pystray.MenuItem("📂 Open Recordings", on_open),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )


def main():
    icon = pystray.Icon("meetcap", create_icon("idle"), "Meetcap", build_menu())

    # Handle SIGTERM gracefully
    def sig_handler(signum, frame):
        if state.is_recording:
            stop_recording()
        icon.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    print("Meetcap tray icon starting...")
    print(f"Recordings: {RECORDINGS_DIR}")
    print(f"Whisper model: {WHISPER_MODEL} on {WHISPER_DEVICE}")
    icon.run()


if __name__ == "__main__":
    main()
