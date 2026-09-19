#!/usr/bin/env bash
# Meetcap CLI — wraps the python daemon/client
cd "$(dirname "$0")" || exit
set -a
# shellcheck disable=SC1091
[ -f .env ] && . ./.env
set +a
exec .venv/bin/python src/meetcap.py "$@"
