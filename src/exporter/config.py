import os
from pathlib import Path
from datetime import timedelta, timezone

# ── Config ──────────────────────────────────────────────────────────
VAULT = os.environ.get("OBSIDIAN_VAULT_PATH", "/home/lucca/HD2/vault")
MEETINGS_DIR = Path(VAULT) / "Meetings"
TASKS_DIR = Path(VAULT) / "Tasks"
TASKS_ARCHIVE_DIR = TASKS_DIR / "Archive"

BRT = timezone(timedelta(hours=-3))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
LLM_MODEL = os.environ.get("MEETCAP_LLM_MODEL", "deepseek/deepseek-v4-flash")

SUMMARY_MAX_TOKENS = 2048
TASK_SUGGESTIONS_MAX_TOKENS = 1400
SUMMARY_TEMPERATURE = 1.0
TASK_SUGGESTIONS_TEMPERATURE = 0.0

SUMMARY_SINGLE_PASS_MAX_CHARS = 15000
SUMMARY_CHUNK_MAX_CHARS = 30000
SUMMARY_MERGE_MAX_CHARS = 22000
