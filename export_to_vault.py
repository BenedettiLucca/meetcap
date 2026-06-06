#!/usr/bin/env python3
"""
Meetcap → Obsidian vault exporter with AI-powered summary.

Called automatically after transcription completes.
Reads the meetcap .txt transcript, generates a structured Obsidian note
with full transcript + AI summary + task suggestions, saves to vault.

Can also be called standalone:
  python3 export_to_vault.py /path/to/transcript.txt [--title "Custom Title"]
"""

import json
import sys
from pathlib import Path

# Add src to path so we can import exporter
sys.path.insert(0, str(Path(__file__).parent / "src"))

from exporter.vault_exporter import export_note

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export meetcap transcript to Obsidian vault with AI summary")
    parser.add_argument("transcript", help="Path to meetcap .txt transcript")
    parser.add_argument("--title", help="Custom note title", default=None)
    args = parser.parse_args()

    result = export_note(Path(args.transcript), args.title)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("success") else 1)
