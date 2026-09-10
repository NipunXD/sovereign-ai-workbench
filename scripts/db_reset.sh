#!/usr/bin/env bash
# Drop and rebuild the database. Destructive by design; asks first.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DB="${POSTGRES_DB:-workbench}"
USER="${POSTGRES_USER:-workbench}"

if [ "${FORCE:-0}" != "1" ]; then
  read -r -p "This deletes ALL data in '$DB' (documents, audit log, users). Continue? [y/N] " reply
  case "$reply" in [yY]*) ;; *) echo "aborted"; exit 1 ;; esac
fi

docker exec -i workbench-postgres psql -U "$USER" -d postgres \
  -c "DROP DATABASE IF EXISTS $DB WITH (FORCE);" -c "CREATE DATABASE $DB OWNER $USER;"

# The vector store must be dropped in step with the relational data, or
# retrieval will surface chunks whose documents no longer exist.
curl -sf -X DELETE "http://localhost:${QDRANT_PORT:-6333}/collections/chunks__bge_m3__1024" >/dev/null 2>&1 || true
rm -rf data/runtime/blobs data/runtime/page_images data/runtime/artifacts data/runtime/workspaces

( cd apps/api && uv run alembic upgrade head )
echo "database reset. run 'make seed' to recreate roles and demo users."
