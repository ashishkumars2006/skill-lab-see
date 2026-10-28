#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! python -c 'import flask' >/dev/null 2>&1; then
  echo "Flask is missing. Run: python -m pip install -r requirements.txt" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI is missing. Install Docker Engine/Desktop before starting the judge." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is unavailable. Start Docker and ensure this user can access it." >&2
  exit 1
fi

mkdir -p instance
python worker.py &
WORKER_PID=$!
cleanup() {
  kill "$WORKER_PID" 2>/dev/null || true
  wait "$WORKER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "CodeForge is starting at http://127.0.0.1:${PORT:-5000}"
echo "The judge worker will pull python:3.12-slim and gcc:13 if needed."
python app.py
