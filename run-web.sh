#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
exec gunicorn --workers 2 --bind "${BIND:-0.0.0.0:10000}" app:app
