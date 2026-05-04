#!/usr/bin/env bash
# Meetcap launcher
cd "$(dirname "$0")"
exec .venv/bin/python src/meetcap.py "$@"
