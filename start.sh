#!/bin/sh
set -eu
cd "$(dirname "$0")"
PROJECT_DIR=$(pwd)
WORKER_PID=''
API_PID=''
OLLAMA_PID=''
cleanup() {
  [ -z "$API_PID" ] || kill "$API_PID" 2>/dev/null || true
  [ -z "$WORKER_PID" ] || kill "$WORKER_PID" 2>/dev/null || true
  [ -z "$OLLAMA_PID" ] || kill "$OLLAMA_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
if ! .venv/bin/python -c 'import fastapi,uvicorn,httpx,ddgs,bs4' 2>/dev/null; then .venv/bin/pip install -r requirements.txt; fi
if [ ! -d frontend/dist ]; then (cd frontend && npm ci && npm run build); fi
if curl -fsS http://127.0.0.1:8000/api/auth/status >/dev/null 2>&1; then
  printf 'An app is already running on http://127.0.0.1:8000\n'
  exit 0
fi
LOCAL_RUNTIME="$PROJECT_DIR/../../work/ollama-runtime/ollama"
if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && [ -x "$LOCAL_RUNTIME" ]; then
  mkdir -p data
  OLLAMA_NO_CLOUD=1 OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="$PROJECT_DIR/../../work/ollama-models" "$LOCAL_RUNTIME" serve > data/ollama.log 2>&1 &
  OLLAMA_PID=$!
fi
.venv/bin/python -m backend.worker &
WORKER_PID=$!
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 &
API_PID=$!
printf '\nOrbit is starting at http://127.0.0.1:8000\nPress Ctrl+C to stop.\n\n'
wait "$API_PID"
