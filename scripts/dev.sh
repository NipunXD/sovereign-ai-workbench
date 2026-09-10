#!/usr/bin/env bash
# Run the API, the background worker and the frontend together, and make sure a
# single Ctrl-C takes all three down rather than orphaning them.
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pids=()
cleanup() {
  trap - INT TERM EXIT
  echo
  echo "shutting down..."
  for pid in "${pids[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null
  done
  wait 2>/dev/null
}
trap cleanup INT TERM EXIT

if ! curl -sf "${OLLAMA_BASE_URL:-http://localhost:11434}/api/version" >/dev/null 2>&1; then
  echo "warning: Ollama is not reachable. The API will start, but chat will fail."
  echo "         Start it with 'ollama serve', or set WORKBENCH_PROVIDER=mock."
fi

( cd apps/api && uv run uvicorn workbench.main:app --reload --port 8000 2>&1 | sed 's/^/[api]    /' ) &
pids+=($!)

( uv run arq workbench.workers.worker.WorkerSettings 2>&1 | sed 's/^/[worker] /' ) &
pids+=($!)

( cd apps/web && pnpm dev 2>&1 | sed 's/^/[web]    /' ) &
pids+=($!)

echo
echo "  api  ->  http://localhost:8000/docs"
echo "  web  ->  http://localhost:3000"
echo
wait
