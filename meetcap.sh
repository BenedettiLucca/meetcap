#!/usr/bin/env bash
# Meetcap CLI — wraps the python daemon/client
cd "$(dirname "$0")"
# Load repo .env into the environment (does not override existing vars)
set -a; [ -f .env ] && . ./.env; set +a
exec .venv/bin/python src/meetcap.py "$@"
