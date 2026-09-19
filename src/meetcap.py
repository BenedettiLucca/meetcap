#!/usr/bin/env python3
"""Meetcap — meeting audio capture + transcription daemon.

Daemon controlled via UNIX socket. Talks to rofi for UI.
Records dual-channel WAV (mic=left, system audio=right) via ffmpeg/PipeWire.
Transcribes with faster-whisper (CUDA GPU).
"""

import gc
import os
import queue as _queue_module
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
import runtime_paths
from runtime_paths import pid_is_meetcap

# ── Config ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = BASE_DIR / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
SOCKET_PATH = runtime_paths.socket_path()
PID_FILE = runtime_paths.pid_path()
STATE_FILE = runtime_paths.state_path()
LOG_PATH = runtime_paths.log_path()

SOCKET_TIMEOUT = float(os.environ.get("MEETCAP_SOCKET_TIMEOUT", "2.0"))

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
    recording_since = None  # ISO-8601 BRT timestamp when recording started (#42)

state = State()
state_lock = threading.Lock()


def save_state():
    """Write state to JSON file so rofi can read it."""
    data = {
        "recording": state.is_recording,
        "transcribing": state.is_transcribing,
        "last_file": str(state.recording_file) if state.recording_file else None,
        "error": state.last_error,
        "recording_since": state.recording_since,
    }
    runtime_paths.ensure_runtime_dir(STATE_FILE.parent)
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
def _unique_wav_path(recordings_dir: Path, ts: str) -> Path:
    """Return a WAV path that does not yet exist.

    If ``meeting-<ts>.wav`` already exists, append ``-1``, ``-2``, ... until
    a free slot is found.  Never returns a path that points to an existing file,
    so ffmpeg is never passed ``-y`` against an existing recording (#33).
    """
    candidate = recordings_dir / f"meeting-{ts}.wav"
    if not candidate.exists():
        return candidate
    n = 1
    while True:
        candidate = recordings_dir / f"meeting-{ts}-{n}.wav"
        if not candidate.exists():
            return candidate
        n += 1


def _recording_watcher(proc, wav_path, stop_event: threading.Event):
    """Background thread: poll ffmpeg every 2 s, act if it dies unexpectedly (#11)."""
    while not stop_event.wait(2.0):
        rc = proc.poll()
        if rc is None:
            continue  # still running

        # Process died.  Check whether stop_recording already cleaned up.
        with state_lock:
            if state.recording_proc is not proc:
                # stop_recording already handled it — nothing to do.
                return
            # Unexpected death: clean state.
            state.is_recording = False
            state.recording_proc = None
            state.recording_since = None
            msg = f"ffmpeg died unexpectedly (rc={rc}); file may be truncated: {wav_path.name}"
            state.last_error = msg
            save_state()

        notify("Meetcap", "❌ Recording interrupted", msg[:180])
        print(f"[WATCHER] {msg}")
        return


def start_recording():
    with state_lock:
        # #11: if there's a lingering proc that already died, clean state before checking.
        if state.recording_proc is not None and state.recording_proc.poll() is not None:
            state.recording_proc = None
            state.is_recording = False
            state.recording_since = None
            if not state.last_error:
                state.last_error = "Previous ffmpeg process died unexpectedly"
            save_state()

        if state.is_recording:
            return {"ok": False, "error": "Already recording"}

        mic_src, sys_src = get_audio_sources()
        if not mic_src or not sys_src:
            return {"ok": False, "error": "No audio sources detected"}

        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        wav_file = _unique_wav_path(RECORDINGS_DIR, ts)  # #33: collision-safe
        log_file = wav_file.with_suffix(".ffmpeg.log")
        state.recording_file = wav_file
        state.recording_log = log_file

        # #33: never pass -y against a file that already exists.
        # _unique_wav_path guarantees wav_file doesn't exist, but we keep -y removed
        # to ensure we never silently overwrite; ffmpeg will error instead of clobbering.
        cmd = [
            "ffmpeg",
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
            state.recording_since = None
            save_state()
            notify("Meetcap", "❌ Recording failed", state.last_error[:180])
            return {"ok": False, "error": state.last_error, "log": str(log_file)}

        # #42: stamp when recording truly started (BRT = UTC-3, fixed offset).
        now_brt = datetime.now().astimezone()
        state.recording_since = now_brt.isoformat()
        state.is_recording = True
        state.last_error = None
        save_state()

        # #11: spawn watcher thread to detect unexpected ffmpeg death.
        _stop_watcher = threading.Event()
        state._stop_watcher = _stop_watcher  # keep ref so stop_recording can signal it
        threading.Thread(
            target=_recording_watcher,
            args=(state.recording_proc, wav_file, _stop_watcher),
            daemon=True,
        ).start()

    # Send desktop notification
    notify("Meetcap", "🎙 Recording started", f"mic={mic_src}\nsys={sys_src}")
    print(f"[RECORDING] {wav_file}")
    return {"ok": True, "file": str(wav_file)}



def stop_recording():
    with state_lock:
        # #11: if proc exists but is already dead (watcher may not have fired yet),
        # refuse to pretend we stopped it cleanly.
        if state.recording_proc is not None and state.recording_proc.poll() is not None:
            # Ghost process — watcher will/has cleaned state.  Return informative error.
            proc_rc = state.recording_proc.returncode
            state.recording_proc = None
            state.is_recording = False
            state.recording_since = None
            state.last_error = (
                f"Recording process had already died (rc={proc_rc}); "
                f"file may be truncated: {state.recording_file}"
            )
            save_state()
            return {"ok": False, "error": state.last_error}

        if not state.is_recording or not state.recording_proc:
            return {"ok": False, "error": "Not recording"}

        # Signal watcher to stop before we nullify recording_proc.
        stop_ev = getattr(state, "_stop_watcher", None)
        if stop_ev is not None:
            stop_ev.set()

        try:
            state.recording_proc.communicate(input=b"q", timeout=5)
        except subprocess.TimeoutExpired:
            state.recording_proc.kill()
            state.recording_proc.wait()

        wav = state.recording_file
        state.is_recording = False
        state.recording_proc = None
        state.recording_since = None  # #42: clear since on stop
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
    tmp_path = wav_path.with_suffix(".tmp")
    tmp_path.write_text(transcript)
    os.replace(tmp_path, txt_path)  # atomic: crash can't leave partial txt (#37)
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
            tmp_path = wav_path.with_suffix(".tmp")
            tmp_path.write_text(transcript)
            os.replace(tmp_path, txt_path)
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


def _is_transcript_complete(txt_path: Path) -> bool:
    """Check whether a .txt transcript exists and is complete (#37).

    A transcript is considered incomplete (resumable) if it does not exist,
    is 0 bytes, or does not contain the '---' header separator.
    """
    try:
        if not txt_path.is_file():
            return False
        if txt_path.stat().st_size == 0:
            return False
        content = txt_path.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            if line.strip() == "---":
                return True
        return False
    except OSError:
        return False


def do_transcribe_last():
    """Transcribe the most recent WAV. Called in background thread."""
    with state_lock:
        if state.is_transcribing:
            return {"ok": False, "error": "Already transcribing"}
        wavs = sorted(RECORDINGS_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not wavs:
            return {"ok": False, "error": "No recordings found"}
        # Skip if .txt already exists and is complete (#37)
        wav = None
        for w in wavs:
            if not _is_transcript_complete(w.with_suffix(".txt")):
                wav = w
                break
        if not wav:
            wav = wavs[0]  # re-transcribe latest if all done
        state.is_transcribing = True
        state.last_error = None
        save_state()

    try:
        notify("Meetcap", "📝 Transcribing...", wav.name)
        try:
            # HTTP-first: router arbitrates VRAM with the rest of the local stack
            txt = transcribe_via_router(wav)
        except Exception as e:
            print(f"[WHISPER] Router path failed ({e}); falling back to in-proc")
            txt = transcribe(wav)
        notify("Meetcap", "✅ Transcript done", os.path.basename(txt))
        # #16: Durable export job — enqueue instead of untracked daemon thread
        enqueue_export_job(txt)
        return {"ok": True, "file": txt}
    except Exception as e:
        with state_lock:
            state.last_error = str(e)
        notify("Meetcap", "❌ Transcription failed", str(e))
        return {"ok": False, "error": str(e)}
    finally:
        with state_lock:
            state.is_transcribing = False
            save_state()


def _parse_export_result(stdout: str) -> dict | None:
    """#38: extract the trailing JSON result object from export_to_vault stdout.

    The CLI prints [EXPORT] progress lines before the final JSON blob; walk the
    brace positions until one parses into the result contract.
    """
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            parsed = json.loads(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and ("success" in parsed or "outcome" in parsed):
            return parsed
    return None


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
            payload = _parse_export_result(result.stdout) or {}
            stages = payload.get("stages") or {}
            # #38: exit 0 with failed stages is not a ✅. Stages are the
            # source of truth; "errors" and outcome/success are fallbacks.
            failed_stages = [
                f"{name}: {stage.get('error') or 'failed'}"
                for name, stage in stages.items()
                if isinstance(stage, dict) and not stage.get("ok", True)
            ]
            if not failed_stages:
                failed_stages = [e for e in payload.get("errors", []) if e]
            if payload.get("outcome") == "failed" and not failed_stages:
                failed_stages = ["export outcome: failed"]
            if payload.get("success") is False and not failed_stages:
                failed_stages = ["export reported failure"]
            if failed_stages:
                detail = "; ".join(failed_stages)[:180]
                print(f"[EXPORT] Degraded: {detail}")
                notify("Meetcap", "⚠️ Export degraded", detail)
            else:
                print(f"[EXPORT] Success: {result.stdout.strip()}")
                notify("Meetcap", "✅ Exported to vault", "Check your Meetings folder")
        else:
            # rc!=0: surface stage errors from stdout when stderr is empty.
            payload = _parse_export_result(result.stdout)
            detail = result.stderr.strip()[:100]
            if not detail and payload:
                detail = "; ".join(e for e in payload.get("errors", []) if e)[:100]
            print(f"[EXPORT] Failed: {detail or f'exit {result.returncode}'}")
            notify("Meetcap", "⚠️ Export failed", detail or f"exit {result.returncode}")
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

def _transcription_watchdog(t: threading.Thread, timeout: float = 3600.0) -> None:
    """Watchdog for transcription thread (#15).

    Waits up to `timeout` seconds for the transcription thread to complete.
    If the thread is still alive after the timeout, leaves state intact.
    If the thread died without resetting `is_transcribing`, cleans up stuck state.
    """
    t.join(timeout=timeout)
    if t.is_alive():
        print(f"[WATCHDOG] Transcription thread still alive after timeout ({timeout}s); leaving state intact")
        return

    with state_lock:
        if state.is_transcribing:
            state.is_transcribing = False
            save_state()
            notify("Meetcap", "⚠️ Transcription watchdog triggered", "Thread finished but state was stuck")
            print("[WATCHDOG] Cleared stuck transcribing state (thread finished)")


def transcribe_cmd(watchdog_timeout: float = 3600.0):
    """Handle transcribe command — runs in background."""
    if state.is_recording:
        return {"ok": False, "error": "Stop recording first"}
    t = threading.Thread(target=do_transcribe_last, daemon=True)
    t.start()
    # Start watchdog: if thread dies without resetting is_transcribing, clear it
    threading.Thread(
        target=_transcription_watchdog,
        args=(t, watchdog_timeout),
        daemon=True,
    ).start()
    return {"ok": True, "status": "transcription started"}


def standalone_transcribe_cmd(wav_path_str: str) -> dict:
    """#29: Standalone transcribe — runs transcribe(Path(wav)) in-process, no daemon required.

    Missing file → explicit error, ok=False.
    On success returns {"ok": True, "file": <txt_path>}.
    """
    wav = Path(wav_path_str)
    if not wav.exists():
        return {"ok": False, "error": f"File not found: {wav_path_str}"}
    try:
        notify("Meetcap", "📝 Transcribing (standalone)...", wav.name)
        txt = transcribe(wav)
        notify("Meetcap", "✅ Transcript done", os.path.basename(txt))
        return {"ok": True, "file": txt}
    except Exception as e:
        notify("Meetcap", "❌ Transcription failed", str(e))
        return {"ok": False, "error": str(e)}


# ── Helpers ──────────────────────────────────────────────────────────
def notify(title, body, subtitle=""):
    # #44: notify-send accepts at most 2 positionals (SUMMARY [BODY]); the
    # old code appended `subtitle` as a third positional and every desktop
    # notification failed with rc=1 in silence. libnotify has no subtitle —
    # merge it into the body and use `-a` for app grouping instead.
    full_body = f"{body}\n{subtitle}" if subtitle else body
    cmd = ["notify-send", "-a", "Meetcap", title, full_body]
    try:
        proc = subprocess.run(cmd, timeout=3, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"[NOTIFY] rc={proc.returncode}: {proc.stderr.strip()}")
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[NOTIFY] failed: {e}")



# ── Export Jobs (#16) ────────────────────────────────────────────────
# Minimal durable job model: job state persisted as JSON; worker thread
# (non-daemon) consumes queue; startup reconcile handles incomplete jobs.
#
# Job schema: {"transcript": str, "status": "queued|exporting|complete|failed",
#              "updated_at": ISO-8601}
#
# Ceiling: one job per transcript (dict keyed by transcript path).
# Upgrade trigger: if multiple concurrent exports are needed, convert to a proper
# task queue.

_export_jobs_lock = threading.Lock()
_export_worker_queue: "_queue_module.Queue[str | None] | None" = None



def _get_worker_queue() -> "_queue_module.Queue[str | None]":
    global _export_worker_queue
    if _export_worker_queue is None:
        _export_worker_queue = _queue_module.Queue()
    return _export_worker_queue


def export_jobs_path() -> Path:
    """Return path to the export jobs JSON state file (#16)."""
    return runtime_paths.export_jobs_path()


def load_export_jobs(jobs_path: Path) -> list:
    """Load jobs list from JSON file; returns [] on missing/corrupt file."""
    try:
        if jobs_path.exists():
            return json.loads(jobs_path.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_export_jobs(jobs: list, jobs_path: Path) -> None:
    """Atomically write jobs list to JSON file."""
    tmp = jobs_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs))
    os.replace(tmp, jobs_path)


def _set_job_status(transcript: str, status: str, jobs_path: Path) -> None:
    """Update a job's status in the JSON file (under _export_jobs_lock)."""
    from datetime import timezone
    with _export_jobs_lock:
        jobs = load_export_jobs(jobs_path)
        for job in jobs:
            if job["transcript"] == transcript:
                job["status"] = status
                job["updated_at"] = datetime.now(timezone.utc).isoformat()
                break
        _save_export_jobs(jobs, jobs_path)


def enqueue_export_job(transcript: str) -> None:
    """Add a new queued job unless one already exists and is complete (#16)."""
    from datetime import timezone
    path = export_jobs_path()
    with _export_jobs_lock:
        jobs = load_export_jobs(path)
        existing = {j["transcript"]: j for j in jobs}
        if transcript in existing and existing[transcript]["status"] == "complete":
            return  # complete jobs are never re-exported
        if transcript not in existing:
            jobs.append({
                "transcript": transcript,
                "status": "queued",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            _save_export_jobs(jobs, path)
    _worker_enqueue(transcript)



def _worker_enqueue(transcript: str) -> None:
    """Put transcript onto the worker queue."""
    _get_worker_queue().put(transcript)


def _process_export_job(transcript: str, jobs_path: Path) -> None:
    """Run auto_export for transcript and update job status (#16)."""
    _set_job_status(transcript, "exporting", jobs_path)
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "export_to_vault.py"), transcript],
            capture_output=True, text=True, timeout=600,
            cwd=str(BASE_DIR),
        )
        if result.returncode == 0:
            payload = _parse_export_result(result.stdout) or {}
            stages = payload.get("stages") or {}
            failed_stages = [
                f"{name}: {stage.get('error') or 'failed'}"
                for name, stage in stages.items()
                if isinstance(stage, dict) and not stage.get("ok", True)
            ]
            if not failed_stages:
                failed_stages = [e for e in payload.get("errors", []) if e]
            if payload.get("outcome") == "failed" and not failed_stages:
                failed_stages = ["export outcome: failed"]
            if payload.get("success") is False and not failed_stages:
                failed_stages = ["export reported failure"]
            if failed_stages:
                detail = "; ".join(failed_stages)[:180]
                print(f"[EXPORT] Degraded: {detail}")
                notify("Meetcap", "⚠️ Export degraded", detail)
                _set_job_status(transcript, "failed", jobs_path)
            else:
                print(f"[EXPORT] Success: {result.stdout.strip()}")
                notify("Meetcap", "✅ Exported to vault", "Check your Meetings folder")
                _set_job_status(transcript, "complete", jobs_path)
        else:
            payload = _parse_export_result(result.stdout)
            detail = result.stderr.strip()[:100]
            if not detail and payload:
                detail = "; ".join(e for e in payload.get("errors", []) if e)[:100]
            print(f"[EXPORT] Failed: {detail or f'exit {result.returncode}'}")
            notify("Meetcap", "⚠️ Export failed", detail or f"exit {result.returncode}")
            _set_job_status(transcript, "failed", jobs_path)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[EXPORT] Error: {e}")
        notify("Meetcap", "⚠️ Export error", str(e)[:100])
        _set_job_status(transcript, "failed", jobs_path)


def _export_worker_loop() -> None:
    """Non-daemon worker thread: consume queue and process export jobs (#16)."""
    path = export_jobs_path()
    q = _get_worker_queue()
    while True:
        transcript = q.get()
        if transcript is None:  # sentinel: stop signal
            break
        try:
            _process_export_job(transcript, path)
        except Exception as e:
            print(f"[EXPORT WORKER] Unhandled error for {transcript}: {e}")
            try:
                _set_job_status(transcript, "failed", path)
            except Exception:
                pass
        finally:
            q.task_done()


# Module-level worker thread reference (non-daemon so shutdown waits)
_export_worker_thread: threading.Thread | None = None


def start_export_worker() -> None:
    """Start the export worker thread (non-daemon) if not already running."""
    global _export_worker_thread
    if _export_worker_thread is not None and _export_worker_thread.is_alive():
        return
    t = threading.Thread(target=_export_worker_loop, name="export-worker", daemon=False)
    t.start()
    _export_worker_thread = t


def shutdown_export_worker(worker: threading.Thread | None = None, join_timeout: float = 5.0) -> None:
    """Signal the export worker to stop and join with bounded timeout (#16).

    If the worker is still running after join_timeout, leaves the current job
    in 'exporting' state so startup reconcile can handle it.
    """
    t = worker or _export_worker_thread
    if t is None or not t.is_alive():
        return
    _get_worker_queue().put(None)  # sentinel
    t.join(timeout=join_timeout)
    # If still alive after timeout: job stays 'exporting' — reconcile will handle it.


def reconcile_export_jobs() -> None:
    """On daemon startup: mark stuck jobs as failed and re-enqueue once (#16).

    Jobs in 'exporting' or 'queued' from a previous process become 'failed'
    and are re-enqueued. Idempotent: 'failed' jobs are only re-enqueued on the
    first reconcile (they are re-enqueued by transitioning from exporting/queued).
    """
    from datetime import timezone
    path = export_jobs_path()
    with _export_jobs_lock:
        jobs = load_export_jobs(path)
        to_reenqueue = []
        for job in jobs:
            if job["status"] in ("exporting", "queued"):
                job["status"] = "failed"
                job["updated_at"] = datetime.now(timezone.utc).isoformat()
                to_reenqueue.append(job["transcript"])
        _save_export_jobs(jobs, path)
    for transcript in to_reenqueue:
        _worker_enqueue(transcript)


def export_state_is_exporting() -> bool:
    """Return True if any job is currently in 'exporting' state (#16)."""
    with _export_jobs_lock:
        jobs = load_export_jobs(export_jobs_path())
    return any(j["status"] == "exporting" for j in jobs)


def export_state_last_job() -> dict | None:
    """Return the most-recently-updated job dict, or None (#16)."""
    with _export_jobs_lock:
        jobs = load_export_jobs(export_jobs_path())
    if not jobs:
        return None
    return max(jobs, key=lambda j: j.get("updated_at", ""))


def export_retry_cmd() -> dict:
    """Re-enqueue the latest failed export job (#16). Socket command: export-retry."""
    with _export_jobs_lock:
        jobs = load_export_jobs(export_jobs_path())
    failed = [j for j in jobs if j["status"] == "failed"]
    if not failed:
        return {"ok": False, "error": "No failed export jobs to retry"}
    latest = max(failed, key=lambda j: j.get("updated_at", ""))
    _worker_enqueue(latest["transcript"])
    return {"ok": True, "transcript": latest["transcript"]}


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
            "recording_since": state.recording_since,  # #42: ISO-8601 BRT or None
            "exporting": export_state_is_exporting(),   # #16: bool
            "last_export": export_state_last_job(),     # #16: {transcript, status} or None
        }
    elif cmd == "record":
        return start_recording()
    elif cmd == "stop":
        return stop_recording()
    elif cmd in ("transcribe", "transcribe-last"):
        # #29: bare "transcribe" (no path) and "transcribe-last" both invoke the daemon
        # background transcription of the most-recent WAV.
        return transcribe_cmd()
    elif cmd == "toggle":
        if state.is_recording:
            return stop_recording()
        else:
            return start_recording()
    elif cmd == "list":
        wavs = sorted(RECORDINGS_DIR.glob("*.wav"), reverse=True)
        return {"recordings": [w.name for w in wavs[:20]]}
    elif cmd == "export-retry":
        return export_retry_cmd()
    else:
        return {"ok": False, "error": f"Unknown command: {cmd}"}


def client_handler(conn):
    """Handle a single client connection."""
    try:
        conn.settimeout(SOCKET_TIMEOUT)
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


def acquire_single_instance(sock_path=None, timeout=1.0) -> bool:
    """Ensure no live meetcap daemon is running before taking over sock_path.

    Returns True if instance can proceed (no socket or orphan socket unlinked).
    Returns False if a live daemon responded on the socket (do not unlink).
    """
    if sock_path is None:
        sock_path = SOCKET_PATH
    path = Path(sock_path)
    if not path.exists():
        return True

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        s.sendall(b"status\n")
        reply = s.recv(1024)
        if reply:
            return False
    except OSError:
        pass
    finally:
        s.close()

    try:
        path.unlink()
    except OSError:
        pass
    return True


def run_server():
    """UNIX socket server main loop."""
    runtime_paths.ensure_runtime_dir(SOCKET_PATH.parent)
    if not acquire_single_instance(SOCKET_PATH):
        print(f"[DAEMON] Another meetcap daemon is already running on {SOCKET_PATH}", file=sys.stderr)
        sys.exit(1)

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
    runtime_paths.ensure_runtime_dir(PID_FILE.parent)
    PID_FILE.write_text(str(os.getpid()))

    # #16: reconcile stuck jobs from previous run, then start worker
    reconcile_export_jobs()
    start_export_worker()

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
    # #16: bounded join on export worker (≤5s); stuck job stays 'exporting' for reconcile
    shutdown_export_worker(join_timeout=5.0)
    server.close()
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    if PID_FILE.exists():
        PID_FILE.unlink()
    STATE_FILE.unlink(missing_ok=True)
    print("[DAEMON] Stopped")


# ── CLI ──────────────────────────────────────────────────────────────
def _resolve_socket():
    # ponytail: no /tmp glob fallback — cross-user socket discovery defeats #18 isolation
    return SOCKET_PATH


def _send(cmd, timeout=SOCKET_TIMEOUT, sock_path=None):
    """Send a command to the daemon and return the raw response, or None."""
    s = None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        target = sock_path or _resolve_socket()
        s.connect(str(target))
        s.sendall((cmd + "\n").encode())
        data = s.recv(4096).decode().strip()
        return data
    except (socket.timeout, TimeoutError):
        raise TimeoutError("Timed out waiting for daemon response")
    except OSError:
        return None
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


def send_command(cmd, sock_path=None):
    """Send a command to the daemon and return the response."""
    target = sock_path or _resolve_socket()
    if not target.exists():
        print("Meetcap daemon is not running", file=sys.stderr)
        sys.exit(1)
    try:
        data = _send(cmd, sock_path=target)
    except TimeoutError:
        raise
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
    try:
        daemon_status = _send("status")
    except TimeoutError:
        daemon_status = None
    svc = doctor.check_service()
    pid = doctor.read_pid(PID_FILE)
    print(f"Daemon: running (pid {pid})")
    if daemon_status:
        print(f"State: {daemon_status}")
    mode = "systemd user service" if svc["active"] else "manual"
    print(f"Managed via: {mode}")


def _spawn_manual_daemon():
    doctor.clean_stale_files(SOCKET_PATH, PID_FILE)
    log_path = LOG_PATH
    runtime_paths.ensure_runtime_dir(log_path.parent)
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


def stop_daemon():
    """Stop running meetcap daemon safely (guarding PID reuse)."""
    pid = doctor.read_pid(PID_FILE)
    if pid and not pid_is_meetcap(pid):
        print(f"[STOP] Stale PID {pid} is not meetcap, cleaning up without killing")
        doctor.clean_stale_files(SOCKET_PATH, PID_FILE)
        return True
    stopped = doctor.stop_pid(pid)
    doctor.clean_stale_files(SOCKET_PATH, PID_FILE)
    return stopped


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
        if pid and not pid_is_meetcap(pid):
            print(f"[RESTART] Stale PID {pid} is not meetcap, cleaning up")
            doctor.clean_stale_files(None, PID_FILE)
        else:
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
        print("Usage: meetcap.py <daemon|doctor|status|start|restart|install-service|record|stop|toggle|transcribe[<path.wav>]|transcribe-last|list>")
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
    elif cmd == "stop-daemon":
        stop_daemon()
    elif cmd == "install-service":
        install_service_cmd()
    elif cmd == "transcribe":
        # #29: with a path arg → standalone (no daemon needed)
        #      with no args → send 'transcribe-last' to daemon
        extra = sys.argv[2:]
        if len(extra) > 1:
            print(
                f"Error: 'transcribe' takes at most one argument (path.wav), got {len(extra)}",
                file=sys.stderr,
            )
            sys.exit(1)
        if extra:
            result = standalone_transcribe_cmd(extra[0])
            print(json.dumps(result))
            if not result.get("ok"):
                sys.exit(1)
        else:
            # No path — send to daemon as transcribe-last (backward compat)
            try:
                response = send_command("transcribe-last")
                print(response)
            except TimeoutError as e:
                print(f"Error communicating with daemon: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        # Client mode — send command to daemon
        try:
            response = send_command(cmd)
            print(response)
        except TimeoutError as e:
            print(f"Error communicating with daemon: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
