#!/usr/bin/env bash
# One-time setup: create .venv with Python 3.10+ and install the project.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "Python 3.10 or newer is needed. Run: brew install python@3.12" >&2; exit 1; }

if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Creating .venv with $($PY --version)"
  rm -rf .venv
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e .
[ -f .env ] || cp .env.example .env
chmod +x scripts/*.sh "Start DICE Wiki Chat.command" 2>/dev/null || true
echo "Done. Next: .venv/bin/wikichat login, then .venv/bin/wikichat crawl, then scripts/start.sh"
