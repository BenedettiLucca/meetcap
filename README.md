# 🎙️ Meetcap

**Local-first meeting audio capture, transcription, and AI-powered note export for Obsidian.**

Meetcap records dual-channel audio (microphone + system audio) from any meeting app on Linux, transcribes it locally with GPU-accelerated [faster-whisper](https://github.com/SYSTRAN/faster-whisper), then generates structured Obsidian notes with AI summaries and task suggestions.

No cloud recording. No third-party transcription APIs. Your audio never leaves your machine.

---

## ✨ Features

- **Dual-channel recording** — mic on the left channel, system audio on the right, via PipeWire/ffmpeg
- **GPU transcription** — faster-whisper with CUDA (large-v3-turbo by default, configurable)
- **AI meeting summary** — structured notes with Summary, Key Points, Action Items, and Risks sections, via OpenRouter
- **Smart chunking** — long meetings (1h+) are split into chunks, summarized individually, then hierarchically merged
- **Task suggestions** — compares the transcript against your Obsidian daily task list and surfaces matched + new tasks
- **Obsidian vault export** — notes are written directly to your vault with frontmatter, ready to search and link
- **Daemon + rofi UI** — runs as a systemd service, controlled via a rofi menu bound to a keybind

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
- **Never hardcode API keys** — use env vars or `~/.hermes/.env`
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

1. **Daemon** listens on `/tmp/meetcap.sock` for commands (`record`, `stop`, `transcribe`)
2. **ffmpeg** captures dual-channel WAV from PipeWire sources (default mic + system monitor)
3. **faster-whisper** transcribes the WAV locally with GPU acceleration
4. **export_to_vault.py** kicks in automatically after transcription

### Export pipeline

The exporter (`src/exporter/`) is a standalone module that transforms a raw transcript into a rich Obsidian note:

| Module | Responsibility |
|--------|---------------|
| `transcript_parser.py` | Parse meetcap `.txt` transcripts, extract metadata (date, duration, model, language) |
| `summarizer.py` | Generate AI summary with chunking for long meetings (split → summarize chunks → hierarchical merge) |
| `task_extractor.py` | Compare transcript against Obsidian daily tasks, produce matched + new task suggestions |
| `vault_exporter.py` | Assemble final Obsidian note with frontmatter, summary, task suggestions, and full transcript |
| `llm_client.py` | OpenRouter API client (curl-based, no SDK dependency) |
| `prompts.py` | All LLM prompts (summary, task suggestions, JSON repair) |
| `config.py` | Paths, model config, token limits |

The exporter can also be used standalone:

```bash
python3 export_to_vault.py /path/to/transcript.txt --title "Custom Title"
```

---

## 🔗 Hermes Agent Integration

Meetcap was built to work with [Hermes Agent](https://hermes-agent.nousresearch.com) — an autonomous AI agent framework. The integration creates a full **capture → transcribe → summarize → distribute** pipeline:

### How they connect

1. **Hermes reads the OpenRouter key** from `~/.hermes/.env` — meetcap's `llm_client.py` falls back to this file if `OPENROUTER_API_KEY` isn't set in the environment
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
- **rofi** (for the UI menu — optional, you can also send commands directly)

### Python dependencies

```bash
pip install -r requirements.txt
```

For development and tests:

```bash
pip install -r requirements-dev.txt
```

The only hard dependency is `faster-whisper`. The exporter uses stdlib + `curl` (no additional Python packages).

### API keys

- **OpenRouter API key** — for AI summary + task suggestions. Set `OPENROUTER_API_KEY` in your environment or `~/.hermes/.env`.
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

```bash
# Required: OpenRouter for AI summaries
export OPENROUTER_API_KEY="sk-or-..."

# Optional: model overrides
export MEETCAP_MODEL="large-v3-turbo"    # whisper model
export MEETCAP_DEVICE="cuda"             # or "cpu"
export MEETCAP_COMPUTE="float16"         # or "float32", "int8"
export MEETCAP_LLM_MODEL="qwen/qwen3.7-flash"

# Optional: Obsidian vault path (defaults to ~/vault)
export OBSIDIAN_VAULT_PATH="/path/to/your/vault"
```

### 3. Run as a systemd service (recommended)

Edit `meetcap.service` to match your paths and CUDA library paths:

```ini
[Unit]
Description=Meetcap Meeting Recorder Daemon
After=pipewire.service

[Service]
Type=simple
Environment=MEETCAP_MODEL=large-v3-turbo
Environment=MEETCAP_DEVICE=cuda
Environment=MEETCAP_COMPUTE=float16
Environment=MEETCAP_LLM_MODEL=qwen/qwen3.7-flash
ExecStart=/path/to/.venv/bin/python /path/to/src/meetcap.py daemon
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

The CLI is service-aware: when the systemd user service is installed, `start`/`restart` go through `systemctl --user`; otherwise the daemon is spawned manually (logged to `/tmp/meetcap-daemon.log`).

```bash
./meetcap.sh status    # ping + pid + state + how the daemon is managed
./meetcap.sh start     # start via service (or manual spawn), waits for the socket
./meetcap.sh restart   # restart via service (or stop + clean + respawn)
./meetcap.sh doctor    # diagnose: healthy / stale socket / stale PID / wedged daemon
                        #   / missing service / missing deps / invalid audio source
./meetcap.sh doctor --fix   # clean stale socket/PID files, then re-diagnose
./meetcap.sh doctor --json  # machine-readable diagnosis
```

The rofi menu also self-heals: if the daemon does not respond, it offers 🔄 Restart daemon or 🏥 Run doctor instead of dead-ending.

### 4. Bind rofi menu to a key (Hyprland example)

```ini
# ~/.config/hypr/hyprland.conf
bind = SUPER, M, exec, /path/to/rofi-meetcap.sh
```

The rofi menu adapts to state:

| State | Options |
|-------|---------|
| Idle | 🎙 Start Recording · 📝 Transcribe Last · 📂 Open Recordings |
| Recording | ⏹ Stop Recording · 📝 Transcribe Last · 📂 Open Recordings |
| Transcribing | 🎙 Start Recording (queued) · 📂 Open Recordings · ⏳ Transcribing... |

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

Tests cover all core business logic with mocked LLM calls (no API key needed for testing):

- `task_extractor`: pending task extraction, conservative matching, suggestion rendering, payload normalization
- `transcript_parser`: chunking of long transcripts (order preservation, character limits)
- `summarizer`: multi-round chunk pipeline (split → reduce → merge) with mocked OpenRouter
- `vault_exporter`: note rendering (task suggestions positioned before transcript)
- `llm_client`: transport/HTTP/response failure classification, retry decisions (httpx mocked)
- `doctor`: health classification (stale socket/PID, wedged daemon, missing deps, invalid source)
- `meetcap` daemon: command handling, recording state, socket client (OS-level calls mocked)

---

## 📁 Project structure

```
meetcap/
├── src/
│   ├── meetcap.py              # Daemon: recording + transcription + socket server
│   └── exporter/               # Export pipeline (transcript → Obsidian note)
│       ├── config.py           # Paths, model config, token limits
│       ├── prompts.py          # All LLM prompts
│       ├── llm_client.py       # OpenRouter API client (curl-based)
│       ├── transcript_parser.py # Transcript parsing + chunking
│       ├── summarizer.py       # AI summary generation with hierarchical merge
│       ├── task_extractor.py   # Daily task matching + suggestion generation
│       └── vault_exporter.py   # Final note assembly + vault write
├── tests/
│   └── test_export_to_vault.py # 8 tests covering all exporter modules
├── docs/
│   └── feature-ideas/          # Roadmap docs (entity resolution, retention, etc.)
├── export_to_vault.py          # CLI entry point for standalone export
├── meetcap.sh                  # CLI wrapper for the daemon
├── meetcap.service             # systemd service file
├── rofi-meetcap.sh             # rofi menu script
└── requirements.txt            # Python deps (just faster-whisper)
```

---

## ⚙️ Configuration reference

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `OPENROUTER_API_KEY` | (from `~/.hermes/.env`) | OpenRouter API key for LLM calls |
| `MEETCAP_MODEL` | `large-v3-turbo` | Whisper model size |
| `MEETCAP_DEVICE` | `cuda` | Compute device (`cuda`, `cpu`) |
| `MEETCAP_COMPUTE` | `float16` | Compute type (`float16`, `float32`, `int8`) |
| `MEETCAP_LLM_MODEL` | `qwen/qwen3.7-flash` | LLM model for summary + tasks |
| `OBSIDIAN_VAULT_PATH` | `~/vault` | Path to your Obsidian vault |

---

## 📝 Generated note format

Each meeting produces an Obsidian note with YAML frontmatter:

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
---

# Meeting — 2026-05-20 (29 min)

## 📌 Summary
...

## 🔑 Key Points
- ...

## ✅ Action Items
- [ ] ...

## ⚠️ Open Questions / Risks
- ...

## 🧩 Sugestões de Tarefas
### ✅ Já estavam programadas e apareceram na reunião
- [ ] ...

### 🆕 Novas tarefas sugeridas pela reunião
- [ ] ...

## 🔎 Claims & Evidence
- **Claim:** ...
  - Why it matters: ...
  - Evidence: 00:12, 00:18
  - Confidence: high

## Name Corrections
- `TechKeyon` -> `Project Tachyon` (high confidence, alias)

## 🚩 QA Flags
- Coverage score: 0.42
- Needs review: yes
- Missing decisions: 1
- Action item gaps: 2
- Attribution risks: 0
- Unsupported claims: 1

## 🗺️ Room Manifest
- Authority mix: decision-heavy
- Freshness: same-day
- Best used for: task carry-over, wiki update
- Decisions: 2 · Open questions: 1 · Actions: 3

## 📝 Transcrição Completa
[00:00 → 00:05] ...
```

### Export sidecar artifacts

Each export also writes machine-readable sidecars under `Meetings/.meetcap/<note>/`:

- `evidence.json` — schema `meetcap.evidence/1`: verified claims with real transcript timestamps, dropped-claim count, model metadata
- `corrections.json` — schema `meetcap.corrections/1`: entity-resolver audit trail (surface, canonical, confidence, rule)
- `verification.json` / `verification.md` — schema `meetcap.verification/1`: QA pass results (coverage score, decision/action gaps, attribution risks, unsupported claims)
- `room_manifest.json` — schema `meetcap.room-manifest/1`: routing metadata for downstream agents (authority mix, freshness, decisions, open questions, actions, missing proof, downstream lanes)

The entity resolver builds its canonical vocabulary from `vault/wiki/entities/` and `vault/wiki/concepts/` slugs, an optional `docs/glossary.txt` (one term per line, or `alias = Canonical`) or `docs/glossary.json` (`{"terms": [...], "aliases": {...}}`), plus explicit participant names. Only high-confidence corrections rewrite derived surfaces (summary + task suggestions); medium-confidence matches are flagged, never applied. The raw transcript is never modified.

### QA verification pass

After building the note, an advisory QA pass (`note_verifier`) judges it against the timestamped transcript: coverage score (0–1), missing decisions, action-item gaps, speaker attribution risks, and unsupported claims — each pointing back to the timestamps where the gap occurred. It never blocks or rewrites the export; risky notes get a compact `## 🚩 QA Flags` block and full details land in `verification.json`/`verification.md`. Disable with `MEETCAP_EXPORT_QA=0`.

### Room manifest

Each export also emits a routing manifest (`room_manifest.json` + a compact `## 🗺️ Room Manifest` note block) for downstream agents (Hermes). It reuses the evidence claims and QA verification instead of re-deriving them: authority mix (decision-heavy / discussion-heavy / mixed), freshness (same-day / aging / stale-follow-up), decisions, open questions, checkbox actions, missing proof, and suggested downstream lanes (`daily-tasks`, `wiki`, `content`, `client-followup`, `reference-only`). Low-confidence material (poor QA coverage and no decisions) routes to `reference-only` only. Disable with `MEETCAP_EXPORT_MANIFEST=0`.

---

## 🛣️ Roadmap

Feature ideas are tracked in [`docs/feature-ideas/`](docs/feature-ideas/):

- **Canonical entity resolution** — resolve speaker names and project references in transcripts
- **Evidence-linked exports** — link summary points back to transcript timestamps
- **Project room exporter** — group meeting notes by project/client
- **Recording retention policy** — auto-cleanup old audio files while keeping transcripts

---

## 📄 License

MIT
