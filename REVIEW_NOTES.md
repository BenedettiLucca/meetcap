## Weekly Review — 2026-05-17

### Snapshot
- Local git repo with **no origin remote configured**.
- Tiny Python + shell + systemd utility repo.
- README is effectively one line, so setup/usage knowledge is trapped in the author’s head.
- No tests surfaced.

### Actionable opportunities
1. **Write the real README.**
   - Capture install, dependencies, service setup, where recordings land, and how to run it manually before enabling the service.
2. **Add a smoke-test command.**
   - Even a basic `--help` / dry-run / config validation command would make service debugging less blind.
3. **Separate ops assets from app logic.**
   - `meetcap.service`, `meetcap.sh`, `rofi-meetcap.sh`, and `src/` are all top-level. A `systemd/` or `deploy/` folder would reduce clutter.

### Verdict
- Biggest risk here is not code quality, it’s operational ambiguity. One bad machine migration and this becomes archaeology.

---

# Weekly Review Notes — 2026-05-10


## Inventory
- Project: `meetcap`
- Path: `/home/lucca/Projects/meetcap`
- Git repo: yes
- Remote: `none`
- Ownership: `local-only`
- Tests detected: no
- GitHub workflows: none
- Dependency files: none found

### Pygount snapshot
- Python: 275 LOC across 1 files
- Bash: 34 LOC across 2 files
- Systemd: 11 LOC across 1 files

## Improvement Opportunities
1. Add smoke tests for the Python capture flow and the systemd/service scripts; today there is no regression safety net.
2. Add an explicit dependency/bootstrap manifest so a fresh machine can reproduce the setup without guesswork.
3. Document the operational path (start, stop, logs, transcript location) in the README for faster incident recovery.

## Signals from the Scan
- No high-signal TODO/FIXME/HACK markers surfaced in the filtered scan.
