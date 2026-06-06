# Review Notes — meetcap

**Date:** 2026-05-31
**Status:** No remote (local repo), local review only
**Stack:** Python / FFmpeg / Whisper
**Size:** ~969 lines Python, 10 source files

## Good
- Systemd service file for auto-start
- Export to vault functionality
- Clean gitignore

## Improvement Opportunities
- Recording files (`recordings/`) contain .wav and .txt transcripts locally — ensure these don't grow unbounded (no cleanup mechanism visible)
- `__pycache__` present but properly gitignored
- Exporter logic now lives in `src/exporter/`; keep wrapper and module layout documented so future changes don't drift
