#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "Missing .env. Copy .env.example first." >&2
  exit 1
fi

set -a
source "$ROOT_DIR/.env"
set +a

mkdir -p "$ROOT_DIR/runtime"

if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  python3 -m venv "$ROOT_DIR/.venv"
fi

source "$ROOT_DIR/.venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -e .

exec python -m uvicorn remote_sandbox_framework.app:create_app \
  --factory \
  --host "${REMOTE_SANDBOX_HOST:-0.0.0.0}" \
  --port "${REMOTE_SANDBOX_PORT:-8787}"
