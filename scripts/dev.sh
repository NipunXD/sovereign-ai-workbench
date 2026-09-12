#!/usr/bin/env bash
# Run the API and the frontend together, and make sure a single Ctrl-C takes
# both down rather than orphaning them.
#
# There is no worker process. This script used to start one —
# `arq workbench.workers.worker.WorkerSettings` — against a module that has
# never existed, so every `make dev` opened with a ModuleNotFoundError
# traceback before either real service had said anything. Ingestion runs
# in-process on the API; workbench.workers is an empty package kept only so
# the import-linter contract that forbids the request path from importing it
# stays meaningful for whenever a worker does arrive.
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

( cd apps/web && pnpm dev 2>&1 | sed 's/^/[web]    /' ) &
pids+=($!)

echo
echo "  api  ->  http://localhost:8000/docs"
echo "  web  ->  http://localhost:3000"
echo
wait
