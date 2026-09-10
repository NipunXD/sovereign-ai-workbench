#!/usr/bin/env bash
# Block until postgres, qdrant and redis are actually accepting work.
# `docker compose up -d` returns as soon as containers start, which is well
# before Postgres finishes initdb — running migrations then fails confusingly.
set -uo pipefail

TIMEOUT="${INFRA_TIMEOUT:-90}"
deadline=$(( $(date +%s) + TIMEOUT ))

check() {
  local name="$1" cmd="$2"
  while true; do
    if eval "$cmd" >/dev/null 2>&1; then
      printf '  \033[32m*\033[0m %-10s ready\n' "$name"; return 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      printf '  \033[31mx\033[0m %-10s TIMED OUT after %ss\n' "$name" "$TIMEOUT" >&2
      echo "    docker compose logs $name" >&2
      return 1
    fi
    sleep 1
  done
}

PGPORT="${POSTGRES_PORT:-5433}"
QDPORT="${QDRANT_PORT:-6333}"
RDPORT="${REDIS_PORT:-6379}"

rc=0
check postgres "docker exec workbench-postgres pg_isready -U ${POSTGRES_USER:-workbench} -d ${POSTGRES_DB:-workbench}" || rc=1
check qdrant   "curl -sf http://localhost:${QDPORT}/readyz"                                                             || rc=1
check redis    "docker exec workbench-redis redis-cli ping"                                                             || rc=1
exit $rc
