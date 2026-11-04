#!/usr/bin/env bash
# Start DICE Wiki Chat: activate the venv, make sure Ollama runs, start the server, open the browser.
# Usage: scripts/start.sh            (opens the browser)
#        NO_BROWSER=1 scripts/start.sh
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

env_value() {  # read KEY from .env without sourcing the file
  [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true
}

PORT="${WIKICHAT_PORT:-$(env_value WIKICHAT_PORT)}"; PORT="${PORT:-8000}"
LLM_URL="${WIKICHAT_LLM_BASE_URL:-$(env_value WIKICHAT_LLM_BASE_URL)}"
URL="http://127.0.0.1:${PORT}"

if [ ! -x .venv/bin/wikichat ]; then
  echo "No virtual environment found. Run: scripts/setup.sh" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Start Ollama when the project is set up to use it.
if [ "$LLM_URL" = "http://localhost:11434/v1" ]; then
  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "Starting Ollama..."
    if command -v brew >/dev/null 2>&1 && brew list ollama >/dev/null 2>&1; then
      brew services start ollama >/dev/null 2>&1 || true
    elif command -v ollama >/dev/null 2>&1; then
      nohup ollama serve >/tmp/ollama.log 2>&1 &
    else
      echo "Ollama is not installed. Run: brew install ollama" >&2
    fi
    for _ in $(seq 1 20); do
      curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
      sleep 1
    done
  fi
fi

open_browser() { [ "${NO_BROWSER:-0}" = "1" ] || open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || true; }

# Already running? Then just open it.
if curl -sf "$URL/api/status" >/dev/null 2>&1; then
  echo "Already running at $URL"
  open_browser
  exit 0
fi

# Open the browser as soon as the server answers.
( for _ in $(seq 1 30); do
    curl -sf "$URL/api/status" >/dev/null 2>&1 && { open_browser; break; }
    sleep 1
  done ) &

echo "Starting DICE Wiki Chat at $URL (Ctrl + C to stop)"
exec wikichat serve --port "$PORT"
