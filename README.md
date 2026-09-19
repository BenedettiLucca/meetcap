# 🎙️ Meetcap

**Local-first meeting audio capture, transcription, and AI-powered note export for Obsidian.**

Meetcap records dual-channel audio (microphone + system audio) from any meeting app on Linux, transcribes it locally with GPU-accelerated [faster-whisper](https://github.com/SYSTRAN/faster-whisper), then generates structured Obsidian notes with AI summaries and task suggestions.

No cloud recording. No third-party transcription APIs. Your audio never leaves your machine.

---

## ✨ Features

- **Dual-channel recording** — mic on the left channel, system audio on the right, via PipeWire/ffmpeg with disk space preflight (`MEETCAP_MIN_FREE_GB`) and collision-safe naming
- **GPU transcription** — faster-whisper with CUDA (large-v3-turbo by default, configurable), aggregating pauses < 0.5s into natural sentence/turn spans
- **Resilient timestamp parsing** — handles timestamps in `MM:SS` (supporting total minutes > 59) and `H:MM:SS` without truncation
- **AI meeting summary** — structured notes with Summary (`SUMMARY_TEMPERATURE = 0.3`), Key Points, Action Items (with explicit dictated owner/deadline markers), Open Questions / Risks, and Decisions, via OpenRouter
- **Bounded concurrent export** — parallelized chunk summarization and claims extraction controlled by `MEETCAP_EXPORT_MAX_CONCURRENCY` (1–4 workers)
- **Grounded Evidence & Decisions** — mechanical exact/fuzzy claim grounding in `evidence.json`, plus verified/candidate/unresolved decision tracking in notes
- **Advisory QA verification** — full-meeting chunked QA evaluating coverage score, decision gaps, action item gaps (triggering `needs_human_review`), unsupported claims, and clear speaker attribution status (`not_assessable`, no diarization)
- **Canonical entity resolution** — vault wiki & glossary canonical entity matching; `alias` and `fuzzy` rules auto-apply while `substring` matches are audit-only in `corrections.json`
- **Local-first task suggestions** — anti-rewording against summary action items and local deduplication; your Obsidian daily task context never leaves your machine
- **Durable export jobs** — background export queue with retries, status tracking (`exporting`, `last_export`), and explicit outcome reporting (`ok`, `degraded`, `failed`)
- **Opt-in audio retention** — automatically purges old raw WAV recordings (`MEETCAP_RETENTION_DAYS`) only after verifying a complete transcript exists
- **Obsidian vault export** — atomic `.partial` note and sidecar writing with frontmatter health metadata, collision-safe suffixes
- **Desktop controls & indicators** — service-aware daemon, rofi menu with error notifications (rofi is optional), Waybar module (blank-when-idle, recording/transcribing states, CSS blink with `--blink-fallback`), and clean `notify-send` alerts (`-a Meetcap`, ≤ 2 positional arguments)

---

## 🔒 Privacy & Security

Meetcap is designed for **sensitive meeting material**. A public repo is only safe if the repository contains **code only** — not real recordings, transcripts, or exported notes.

### What is kept out of git by default

- `recordings/` — raw `.wav`, `.txt`, and ffmpeg logs
- `.env`, `*.env`, `.env.*` — API keys and local config
- `REVIEW_NOTES.md` — local review artifacts
- `dist/`, `build/`, `__pycache__/`, `.venv/` — generated files

### Public-repo hygiene rules

- **Never commit raw meeting artifacts** (`.wav`, transcript `.txt`, exported meeting `.md` notes)
- **Never commit real client/project examples** into docs or fixtures
- **Never hardcode API keys** — use env vars or the repo `.env` (gitignored)
- Before pushing, run a quick audit:
  - `git ls-files | grep -iE "\\.env|recordings|transcript|meeting-.*\\.txt|\\.wav"`
  - `git ls-files -z | xargs -0 grep -rlP '(api[_-]?key|secret|password|token)\\s*[=:]\\s*["\\x27]?[a-zA-Z0-9]{20,}'`

If you use Meetcap for real work calls, treat the **local machine** and **Obsidian vault** as the trust boundary. The GitHub repo should contain implementation only.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│                  meetcap daemon                  │
│              (systemd service + UNIX socket)     │
│                                                  │
│   ┌──────────┐     ┌──────────────┐             │
│   │  rofi UI │────▶│  UNIX socket │             │
│   │  (keybind)│     └──────┬───────┘             │
│   └──────────┘             │                     │
│                            ▼                     │
│   ┌──────────────────────────────────┐          │
│   │     Audio Recording (ffmpeg)      │          │
│   │  mic (PipeWire) ──▶ left channel  │          │
│   │  system audio   ──▶ right channel │          │
│   └──────────────┬───────────────────┘          │
│                  ▼                               │
│   ┌──────────────────────────────────┐          │
│   │  faster-whisper (CUDA/float16)    │          │
│   │  .wav ──▶ transcript .txt         │          │
│   └──────────────┬───────────────────┘          │
│                  ▼                               │
│   ┌──────────────────────────────────┐          │
│   │       export_to_vault.py          │          │
│   │                                   │          │
│   │  transcript ──▶ AI summary        │          │
│   │             ──▶ task suggestions  │          │
│   │             ──▶ Obsidian .md      │          │
│   └──────────────────────────────────┘          │
└─────────────────────────────────────────────────┘
         │                          │
         ▼                          ▼
   ┌──────────┐              ┌──────────────┐
   │ Obsidian │              │  OpenRouter  │
   │  vault   │              │  (LLM API)   │
   └──────────┘              └──────────────┘
```

### Recording pipeline

1. **Daemon** listens on a user-isolated UNIX socket (`$XDG_RUNTIME_DIR/meetcap/meetcap.sock`, mode 0700) with socket timeouts, single-instance `flock` protection, and desktop alerts via `notify-send -a Meetcap` (strictly ≤ 2 positional arguments)
2. **Preflight check** validates available disk space against `MEETCAP_MIN_FREE_GB` (default 2.0 GB) before capture starts
3. **ffmpeg** captures dual-channel WAV from PipeWire sources (default mic + system monitor) under process supervision, monotonic elapsed tracking, and collision-safe naming (`-n`, `-1`, `-2` suffix)
4. **faster-whisper** transcribes the WAV locally with GPU acceleration, aggregating pauses < 0.5s into natural sentence/turn spans (`src/transcript_segments.py`)
5. **Durable export queue** manages background export jobs with retries, status reporting (`exporting`, `last_export`), and startup reconciliation in `$XDG_RUNTIME_DIR/meetcap/export_jobs.json`
6. **export_to_vault.py** executes the export pipeline, publishing the note and sidecars atomically, and reporting explicit stage health and outcome (`ok`, `degraded`, `failed`)

### Export pipeline

The exporter (`src/exporter/`) is a modular pipeline that transforms a raw transcript into a rich Obsidian note and machine-readable sidecars:

| Module | Responsibility |
|--------|---------------|
| `transcript_parser.py` | Parse meetcap `.txt` transcripts, extract metadata, handle `MM:SS` (minutes > 59) and `H:MM:SS`, and split long meetings into character-budgeted chunks |
| `transcript_segments.py` | Shared transcript writer: aggregate raw whisper segments across pauses < 0.5s into turn/sentence spans, format `[MM:SS → MM:SS] text` lines |
| `summarizer.py` | Generate AI summary with bounded parallel chunking (`MEETCAP_EXPORT_MAX_CONCURRENCY`), hierarchical merge, and `SUMMARY_TEMPERATURE = 0.3` |
| `task_extractor.py` | Compare transcript against Obsidian daily tasks locally (daily context stays on-device), enforce anti-rewording rules, and locally deduplicate against summary action items |
| `entity_resolver.py` | Resolve canonical entities from vault wiki & glossary; `alias` and `fuzzy` rules auto-apply while `substring` matches are audit-only in `corrections.json` |
| `claim_extractor.py` | Extract key verbatim claims and mechanically ground them against transcript segments (`exact` vs `fuzzy` match methods in `evidence.json`) |
| `note_verifier.py` | Advisory QA pass chunked across the full meeting: weighted coverage score, decision gaps, action item gaps (triggering `needs_human_review`), and speaker attribution (`not_assessable`) |
| `room_manifest.py` | Meeting room manifest routing metadata (`meetcap.room-manifest/1`) and grounded `## Decisões` section (`verified`, `candidate`, `unresolved`) |
| `vault_exporter.py` | Orchestrate bounded concurrent stages (summary + claims in parallel), assemble note with escaped frontmatter health metadata, and atomically write note & sidecars |
| `llm_client.py` | OpenRouter API client via `httpx` with timeout budgets and structured output retry specialization (never retries transport/auth/5xx errors without schema) |
| `prompts.py` | System and user prompts with explicit dictated owner/deadline markers (`(owner unspecified)` / `(deadline unspecified)`) |
| `config.py` | Paths, model configuration, temperatures, concurrency limits, and feature toggles |

### Export outcomes & stage health

The export process evaluates stage health (`summary`, `tasks`, `claims`, `qa`, `manifest`, `artifacts`) and produces an overall outcome:
- **`ok`**: All stages completed successfully.
- **`degraded`**: Core deliverables succeeded, but advisory stages degraded (e.g. QA verification failed or room manifest could not be built).
- **`failed`**: The summary failed or a required artifact could not be written. The CLI exits with code != 0.

The daemon inspects the outcome and issues desktop alerts:
- `"✅ Exported to vault"` on clean completion
- `"⚠️ Export degraded"` when secondary stages fail
- `"⚠️ Export failed"` when core export fails

### Standalone CLI usage

Both transcription and vault export can be run standalone without a background daemon:

```bash
# Standalone transcription (runs in-process directly on the WAV)
python3 src/meetcap.py transcribe /path/to/meeting.wav
# Or via wrapper:
./meetcap.sh transcribe /path/to/meeting.wav

# Standalone vault export (reads .txt transcript, runs AI pipeline, writes to vault)
python3 export_to_vault.py /path/to/transcript.txt --title "Custom Title"
```

To instruct a running daemon to transcribe the most recent recording instead, use:

```bash
./meetcap.sh transcribe-last
# Or bare transcribe (no path argument):
./meetcap.sh transcribe
```

---

## 🔗 Hermes Agent Integration

Meetcap was built to work with [Hermes Agent](https://hermes-agent.nousresearch.com) — an autonomous AI agent framework. The integration creates a full **capture → transcribe → summarize → distribute** pipeline:

### How they connect

1. **Dedicated credentials** — Meetcap loads its OpenRouter key from the repo `.env` (or environment), with a safe allowlist read (extracting only `OPENROUTER_API_KEY`, without shell/eval)
2. **Hermes cron jobs can process transcripts** — scheduled jobs (e.g., a nightly "Meeting Notes Processor") can call `export_to_vault.py` on new recordings
3. **Hermes skills consume the notes** — Obsidian skills (`obsidian-core`, `obsidian-tasks`) can read meeting notes, extract action items, and update daily task lists
4. **The vault note footer** marks notes as `Gerado automaticamente pelo Meetcap + Hermes`, creating an audit trail

### Example: Hermes cron job for meeting processing

```yaml
# Every evening at 22:00, export any new unprocessed transcripts
schedule: "0 22 * * *"
prompt: |
  Check ~/Projects/meetcap/recordings/ for .txt transcripts
  that don't have a corresponding .md in the Obsidian vault Meetings/ dir.
  For each unprocessed transcript, run export_to_vault.py.
  Report what was processed.
```

---

## 📦 Requirements

### System dependencies

- **Linux** with **PipeWire** (for audio capture)
- **NVIDIA GPU** with CUDA (for transcription — CPU mode works but is much slower)
- **ffmpeg** (for audio recording)
- **pactl** (PipeWire/PulseAudio CLI — for source detection)
- **socat** (for the UNIX socket client)
- **rofi** (optional UI menu — if missing, `doctor` reports `optional-missing` without degrading system health)
- **libnotify / notify-send** (desktop notifications, called with `-a Meetcap` and ≤ 2 positional arguments)

### Python dependencies

```bash
pip install -r requirements.txt
```

Production dependencies are `faster-whisper` and `httpx`.

For development, testing, and static analysis:

```bash
pip install -r requirements-dev.txt
```

The CI workflow runs on GitHub Actions across Python 3.11, 3.12, and 3.13 with 520+ tests, accompanied by a `static` job running `ruff`, `compileall`, `shellcheck`, and `pip-audit`.

### API keys

- **OpenRouter API key** — for AI summary, tasks, claims, QA, and manifest. Set `OPENROUTER_API_KEY` in your environment or in the repo `.env` (loaded with a strict allowlist via `src/process_env.py`, no eval/shell).
- The LLM model defaults to `qwen/qwen3.7-flash` but is configurable via `MEETCAP_LLM_MODEL`.

---

## 🚀 Setup

### 1. Clone and install

```bash
git clone https://github.com/BenedettiLucca/meetcap.git
cd meetcap
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### 2. Configure environment

Credentials and configuration can be set as environment variables or placed in the repo `.env` (read by both the CLI wrapper and the systemd unit):

```bash
# Required: OpenRouter for AI summaries (in env or repo .env)
export OPENROUTER_API_KEY="sk-or-..."

# Optional: model overrides
export MEETCAP_MODEL="large-v3-turbo"    # whisper model
export MEETCAP_DEVICE="cuda"             # or "cpu"
export MEETCAP_COMPUTE="float16"         # or "float32", "int8"
export MEETCAP_LLM_MODEL="qwen/qwen3.7-flash"

# Optional: Obsidian vault path (defaults to ~/vault)
export OBSIDIAN_VAULT_PATH="/path/to/your/vault"

# Optional: Retention / Disk budget for raw WAV recordings (#36)
# Purge WAVs older than N days if transcript is complete (default 0 = disabled)
export MEETCAP_RETENTION_DAYS="0"
# Minimum free disk space in GB required to start recording (default 2.0)
export MEETCAP_MIN_FREE_GB="2.0"

# Optional: Export concurrency for chunk summarization and claims extraction (default 2, max 4)
export MEETCAP_EXPORT_MAX_CONCURRENCY="2"

# Optional: custom runtime directory (defaults to $XDG_RUNTIME_DIR/meetcap)
# export MEETCAP_RUNTIME_DIR="/run/user/1000/meetcap"
```

### 3. Run as a systemd service (recommended)

The provided `meetcap.service` is portable across users using `%h` specifiers (no hardcoded personal paths) and loads the repo `.env`:

```ini
[Unit]
Description=Meetcap Meeting Recorder Daemon
After=pipewire.service

[Service]
Type=simple
WorkingDirectory=%h/Projects/meetcap
Environment=LD_LIBRARY_PATH=%h/Projects/meetcap/.venv/lib/python3.11/site-packages/nvidia/cublas/lib:%h/Projects/meetcap/.venv/lib/python3.11/site-packages/nvidia/cuda_nvrtc/lib
Environment=MEETCAP_MODEL=large-v3-turbo
Environment=MEETCAP_DEVICE=cuda
Environment=MEETCAP_COMPUTE=float16
Environment=MEETCAP_LLM_MODEL=qwen/qwen3.7-flash
Environment=OBSIDIAN_VAULT_PATH=%h/HD2/vault
EnvironmentFile=-%h/Projects/meetcap/.env
ExecStart=%h/Projects/meetcap/.venv/bin/python %h/Projects/meetcap/src/meetcap.py daemon
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

Install (service-aware):

```bash
# Preferred: installs the unit and enables it via systemctl --user
./meetcap.sh install-service

# Or manually:
cp meetcap.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now meetcap
```

### Daemon lifecycle & troubleshooting

Runtime artifacts (socket, PID, state, and logs) are isolated per user under `MEETCAP_RUNTIME_DIR` > `$XDG_RUNTIME_DIR/meetcap` > `/run/user/<uid>/meetcap` with `0700` permissions.

The CLI is service-aware: when the systemd user service is installed, `start`/`restart` go through `systemctl --user`; otherwise the daemon is spawned manually (logged to `$XDG_RUNTIME_DIR/meetcap/meetcap-daemon.log`). Socket operations enforce timeouts (#19) and single-instance protection via file locking (`flock`) (#28) prevents split-brain daemons. Recording is actively supervised (#11): a background watcher monitors ffmpeg liveness, cleans state, records `last_error`, and sends desktop alerts on unexpected failure.

Durable export jobs are queued in `$XDG_RUNTIME_DIR/meetcap/export_jobs.json` with bounded retries (#16). The daemon tracks export lifecycle, exposing `"exporting": bool` and `"last_export": {"transcript": ..., "status": ...}` in its status payload, and automatically reconciles pending jobs on startup.

```bash
./meetcap.sh status    # ping + pid + state (including recording_since, exporting, last_export)
./meetcap.sh start     # start via service (or manual spawn), waits for the socket
./meetcap.sh restart   # restart via service (or stop + clean + respawn)
./meetcap.sh doctor    # diagnose: healthy / stale socket / stale PID / wedged daemon
                        #   / missing service / missing deps (rofi is optional-missing)
./meetcap.sh doctor --fix   # clean stale socket/PID files, then re-diagnose
./meetcap.sh doctor --json  # machine-readable diagnosis
```

The rofi menu also self-heals: if the daemon does not respond, it offers 🔄 Restart daemon or 🏥 Run doctor instead of dead-ending.

### 4. Desktop integration (Rofi & Waybar)

#### Rofi menu
Bind to a hotkey (Hyprland example):

```ini
# ~/.config/hypr/hyprland.conf
bind = SUPER, M, exec, /path/to/rofi-meetcap.sh
```

The rofi menu adapts to state and surfaces daemon errors via desktop notifications (`notify-send -a Meetcap`):

| State | Options |
|-------|---------|
| Idle | 🎙 Start Recording · 📝 Transcribe Last · 📂 Open Recordings |
| Recording | ⏹ Stop & Save · 🗑 Discard Recording · 📂 Open Recordings |
| Transcribing | 🎙 Start Recording · 📂 Open Recordings · ⏳ Transcribing... |

#### Waybar indicator
`waybar-meetcap.py` queries daemon status over the UNIX socket and emits a JSON block for Waybar:
- **Blank when idle**: outputs empty text and tooltip when idle or unreachable, preventing "ghost tooltips" over invisible modules.
- **Recording state**: displays `🎙 MM:SS` (or `H:MM:SS`) with CSS class `recording`. Blinking is driven by CSS animation keyframes; the `--blink-fallback` flag alternates `🔴`/`⚪` glyphs for setups without CSS blink.
- **Transcribing state**: displays `📝` with CSS class `transcribing`.

See `docs/examples/waybar-meetcap.jsonc` for a complete Waybar configuration snippet and CSS styles.

### 5. Use standalone (no daemon)

```bash
# Record manually
ffmpeg -f pulse -i "default_input" -f pulse -i "default_output.monitor" \
  -filter_complex "[0:a][1:a]amerge=inputs=2" \
  -ac 2 recordings/meeting.wav

# Transcribe
.venv/bin/python src/meetcap.py transcribe recordings/meeting.wav

# Export to Obsidian
.venv/bin/python export_to_vault.py recordings/meeting.txt
```

---

## 🧪 Testing

```bash
.venv/bin/python -m pytest tests/ -v
```

The test suite includes 520+ tests (523 tests in main suite) covering core business logic, edge cases, and real lifecycle/IPC daemon integration:

- `task_extractor`: daily task matching (local context only), anti-rewording against summary action items, and local deduplication
- `transcript_parser` & `transcript_segments`: chunking under character budgets, pause aggregation (<0.5s into sentence/turn spans), and resilient timestamp parsing (`MM:SS` with minutes > 59 and `H:MM:SS`)
- `summarizer`: bounded parallel chunking (`MEETCAP_EXPORT_MAX_CONCURRENCY`), hierarchical merge, and `SUMMARY_TEMPERATURE = 0.3`
- `entity_resolver`: canonical entity resolution from vault wiki & glossary; `alias` and `fuzzy` rules auto-applied while `substring` matches are audit-only
- `claim_extractor`: verbatim claim extraction and mechanical grounding against transcript segments (`exact` vs `fuzzy` match methods in `evidence.json`)
- `note_verifier`: full-meeting chunked QA verification, length-weighted coverage scoring, decision gaps, action item gaps (triggering `needs_human_review`), and speaker attribution (`not_assessable`, no diarization)
- `room_manifest`: room routing manifest and grounded decisions section (`verified` with timestamps, `candidate`, `unresolved` with `[unverified]`)
- `vault_exporter`: stages tracking, outcome evaluation (`ok`, `degraded`, `failed`), atomic staged `.partial` writes, and escaped YAML frontmatter with health metadata
- `llm_client`: `httpx` client with connection timeouts, failure classification, and retry without `response_format` restricted strictly to structured output failures
- `retention`: opt-in `MEETCAP_RETENTION_DAYS` purging only WAVs with complete transcripts, and `MEETCAP_MIN_FREE_GB` preflight space checks
- `doctor`: health classification (stale socket/PID, wedged daemon, missing deps, and rofi as `optional-missing`)
- `meetcap` daemon & integration: socket timeouts, single-instance `flock`, ffmpeg supervision, durable export jobs queue and retries, standalone `transcribe <wav>` vs daemon `transcribe-last`, and desktop alerts via `notify-send -a Meetcap`

---

## 📁 Project structure

```
meetcap/
├── src/
│   ├── meetcap.py              # Daemon: recording + transcription + socket server + export worker
│   ├── runtime_paths.py        # User-isolated runtime paths (socket, PID, state, log)
│   ├── process_env.py          # Safe environment loader with strict allowlist
│   ├── transcript_segments.py  # Segment aggregation (<0.5s) + timestamp parser/formatter
│   └── exporter/               # Export pipeline (transcript → Obsidian note + sidecars)
│       ├── config.py           # Paths, model config, temperatures, concurrency limits
│       ├── prompts.py          # LLM prompts with dictated owner/deadline markers
│       ├── llm_client.py       # OpenRouter API client (httpx, structured output retry)
│       ├── transcript_parser.py # Transcript parsing + chunking (MM:SS >59m, H:MM:SS)
│       ├── summarizer.py       # AI summary with bounded parallel chunking (T=0.3)
│       ├── task_extractor.py   # Daily task matching, anti-rewording + local dedup
│       ├── entity_resolver.py  # Canonical entity resolution (alias/fuzzy, substring audit-only)
│       ├── claim_extractor.py  # Verbatim claim extraction & exact/fuzzy grounding
│       ├── note_verifier.py    # Advisory chunked QA verifier & mechanical grounding
│       ├── room_manifest.py    # Meeting room manifest & grounded decisions block
│       └── vault_exporter.py   # Final note assembly, frontmatter health, atomic write
├── tests/                      # 520+ tests (unit + lifecycle/IPC integration)
├── docs/
│   ├── examples/               # Desktop indicator configs (waybar-meetcap.jsonc)
│   ├── feature-ideas/          # Architectural reference & roadmap docs
│   └── plans/                  # Sprint implementation plans and baselines
├── export_to_vault.py          # CLI entry point for standalone export
├── meetcap.sh                  # CLI wrapper for the daemon
├── meetcap.service             # Portable systemd user service file (%h)
├── rofi-meetcap.sh             # rofi menu script with error notifications
├── waybar-meetcap.py           # Waybar indicator (blank-when-idle, recording, transcribing)
└── requirements.txt            # Python dependencies (faster-whisper, httpx)
```

---

## ⚙️ Configuration reference

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `OPENROUTER_API_KEY` | (from repo `.env`) | OpenRouter API key for LLM calls |
| `MEETCAP_MODEL` | `large-v3-turbo` | Whisper model size |
| `MEETCAP_DEVICE` | `cuda` | Compute device (`cuda`, `cpu`) |
| `MEETCAP_COMPUTE` | `float16` | Compute type (`float16`, `float32`, `int8`) |
| `MEETCAP_LLM_MODEL` | `qwen/qwen3.7-flash` | LLM model for summary + tasks |
| `OBSIDIAN_VAULT_PATH` | `~/vault` | Path to your Obsidian vault |
| `MEETCAP_RUNTIME_DIR` | `$XDG_RUNTIME_DIR/meetcap` | User-isolated directory for socket, PID, state, and logs |
| `MEETCAP_RETENTION_DAYS` | `0` | Opt-in retention: purge WAVs older than N days with complete transcripts (`0` = disabled) |
| `MEETCAP_MIN_FREE_GB` | `2.0` | Minimum free disk space in GB required to start recording |
| `MEETCAP_EXPORT_MAX_CONCURRENCY` | `2` | Max concurrent worker threads for chunk summarization and claims extraction (1–4) |
| `MEETCAP_SOCKET_TIMEOUT` | `2.0` | Socket read/write timeout in seconds |
| `MEETCAP_EXPORT_QA` | `1` | Enable/disable advisory QA verification pass (`0` to disable) |
| `MEETCAP_EXPORT_MANIFEST` | `1` | Enable/disable room routing manifest generation (`0` to disable) |

---

## 📝 Generated note format

Each meeting produces an Obsidian note with deterministically escaped YAML frontmatter:

```markdown
---
title: "Meeting — 2026-05-20 (29 min)"
date: 2026-05-20
time: 16:03
duration: "29 min"
tags: [meeting, meeting-notes, meetcap]
model: "large-v3-turbo (cuda/float16)"
language: "pt (99.9%)"
created: "2026-05-20 16:35"
qa_needs_review: false
qa_coverage_score: 0.85
outcome: "ok"
qa_audited: "post-entity-resolution"
entity_corrections_count: 1
---

# Meeting — 2026-05-20 (29 min)

## 📌 Summary
...

## 🔑 Key Points
- ...

## ✅ Action Items
- [ ] Finalizar contrato da API (owner: Alice) (deadline: sexta-feira)
- [ ] Revisar cobertura de testes (owner unspecified) (deadline unspecified)

## ⚠️ Open Questions / Risks
- ...

## 🧩 Sugestões de Tarefas
### ✅ Já estavam programadas e apareceram na reunião
- [ ] ...

### 🆕 Novas tarefas sugeridas pela reunião
- [ ] ...

## Decisões
- Migrar pipeline para PipeWire amerge (at 04:12)
- Manter modelo whisper local em float16
- Adotar nova política de retenção [unverified]

## 🔎 Claims & Evidence
- **Claim:** Migração para PipeWire reduziu latência de captura
  - Why it matters: Valida estabilidade do serviço de gravação
  - Evidence: 04:12, 04:18
  - Confidence: high

## Name Corrections
- `TechKeyon` -> `Project Tachyon` (high confidence, alias)

## 🚩 QA Flags
- Coverage score: 0.85
- Needs review: no
- Missing decisions: 0
- Action item gaps: 0
- Speaker attribution: not_assessable:no-diarization
- Unsupported claims: 0

## 🗺️ Room Manifest
- Authority mix: decision-heavy
- Freshness: same-day
- Best used for: task carry-over, wiki update
- Decisions: 2 · Open questions: 1 · Actions: 2

## 📝 Transcrição Completa
[00:00 → 00:05] ...
```

### Export sidecar artifacts

Each export also writes machine-readable sidecars under `Meetings/.meetcap/<meeting-stem>/`:

- `evidence.json` — schema `meetcap.evidence/1`: verbatim claims grounded against transcript segments, with `match_method` (`exact` or `fuzzy`), verified timestamps, dropped-claim count, and model metadata
- `corrections.json` — schema `meetcap.corrections/1`: entity-resolver audit trail (surface, canonical, confidence, rule). High-confidence `alias` and `fuzzy` matches auto-apply to derived text (summary and task suggestions); `substring` matches are kept **audit-only** in this sidecar to avoid shrinking valid multi-word names
- `verification.json` / `verification.md` — schema `meetcap.verification/1`: advisory QA pass results chunked across the full meeting (length-weighted coverage score, decision gaps, action item gaps triggering `needs_human_review`, unsupported claims, and `speaker_attribution: "not_assessable:no-diarization"`)
- `room_manifest.json` — schema `meetcap.room-manifest/1`: routing metadata for downstream agents (authority mix, freshness, decisions, open questions, actions, missing proof, downstream lanes)

The entity resolver builds its canonical vocabulary from `vault/wiki/entities/` and `vault/wiki/concepts/` slugs, an optional `docs/glossary.txt` (one term per line, or `alias = Canonical`) or `docs/glossary.json` (`{"terms": [...], "aliases": {...}}`), plus explicit participant names. The raw transcript is never modified.

Exports are atomic, collision-safe, and durable: notes and sidecars are written via `.partial` staging files before atomic rename (`os.replace`). Name collisions generate numeric suffixes (`-1`, `-2`) without overwriting existing notes. Durable export jobs in `export_jobs.json` track retries and status (`exporting`, `last_export`), while stage health determines the outcome (`ok`, `degraded`, `failed`), exiting with code != 0 on failure.

### QA verification pass

After building the note, an advisory QA pass (`note_verifier`) evaluates the note against the timestamped transcript: coverage score (0–1), missing decisions, action-item gaps, speaker attribution risks, and unsupported claims. Long transcripts are processed in chunks and aggregated deterministically (weighted coverage average and union of gaps). Timestamps are mechanically grounded against real segments—ungrounded or out-of-bounds timestamps are never presented as verified evidence. If `action_item_gaps` or other gaps are detected, `needs_human_review` is set to `true`. Speaker attribution is explicitly marked `not_assessable` because Meetcap does not implement diarization. Risky notes receive a compact `## 🚩 QA Flags` block and full details land in `verification.json`/`verification.md`. Disable with `MEETCAP_EXPORT_QA=0`.

### Room manifest & decisions

Each export also emits a routing manifest (`room_manifest.json` + a compact `## 🗺️ Room Manifest` note block) and a grounded `## Decisões` section for downstream agents (Hermes). It reuses the evidence claims and QA verification instead of re-deriving them: authority mix (decision-heavy / discussion-heavy / mixed), freshness (same-day / aging / stale-follow-up), decisions, open questions, checkbox actions, missing proof, and suggested downstream lanes (`daily-tasks`, `wiki`, `content`, `client-followup`, `reference-only`). Decisions are classified into `verified` (rendered with grounded timestamps), `candidate` (rendered without timestamps), or `unresolved` (rendered with `[unverified]`). Disable manifest generation with `MEETCAP_EXPORT_MANIFEST=0`.

---

## 🛣️ Roadmap

### Delivered in Integrity Epic (Sprint 5)

- **Canonical entity resolution** — vault wiki & glossary matching (alias/fuzzy auto-applied, substring audit-only)
- **Evidence-linked exports** — verbatim claim extraction with exact/fuzzy mechanical grounding
- **Advisory QA pass** — full-meeting chunked verification with mechanical timestamp grounding and `action_item_gaps` triggering human review
- **Room manifest & grounded decisions** — routing metadata and decision classification (`verified`, `candidate`, `unresolved`)
- **Durable export queue & health contract** — background retries, startup reconciliation, and `ok`/`degraded`/`failed` outcome handling
- **Recording retention & disk preflight** — opt-in cleanup of old WAVs (`MEETCAP_RETENTION_DAYS`) requiring complete transcripts, and `MEETCAP_MIN_FREE_GB` preflight space check
- **Bounded export concurrency** — parallel chunk summarization and claims extraction (`MEETCAP_EXPORT_MAX_CONCURRENCY`)
- **Desktop indicators & IPC reliability** — blank-when-idle Waybar indicator with CSS blink (`--blink-fallback`), rofi optional support, and safe `notify-send` alerts

### Future Roadmap

- **Speaker diarization** — multi-speaker voice separation and identification (future milestone; speaker attribution is currently not performed and is marked `not_assessable` in QA verification)
- **Project room bundling** — optional organization of meeting notes and sidecars under per-project vault directories (`vault/Projects/<project>/Meetings/`)

---

## 📄 License

MIT
