#!/bin/sh
set -eu

PATH="/opt/homebrew/opt/node@24/bin:$PATH"
export PATH

cleanup() {
  trap - INT TERM EXIT
  kill "$api_pid" "$web_pid" 2>/dev/null || true
  wait "$api_pid" "$web_pid" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

uv run uvicorn lethe_control.app:create_app --factory --host 127.0.0.1 --port 8000 &
api_pid=$!
pnpm --dir web dev --host 127.0.0.1 &
web_pid=$!

wait "$api_pid" "$web_pid"
