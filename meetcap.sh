#!/usr/bin/env bash
# Meetcap CLI — wraps the python daemon/client
cd "$(dirname "$0")"
exec .venv/bin/python src/meetcap.py "$@"
