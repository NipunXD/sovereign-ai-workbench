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
# retrieval surfaces chunks whose documents no longer exist. The collection name
# encodes the embedding model, so it is discovered rather than hardcoded — a
# stale literal here silently leaves every previous ingest in the index.
QDRANT="http://localhost:${QDRANT_PORT:-6333}"
for collection in $(curl -sf "$QDRANT/collections" 2>/dev/null \
    | python3 -c "import json,sys;print(' '.join(c['name'] for c in json.load(sys.stdin)['result']['collections'] if c['name'].startswith('chunks__')))" 2>/dev/null); do
  curl -sf -X DELETE "$QDRANT/collections/$collection" >/dev/null 2>&1 && echo "dropped vector collection $collection"
done
rm -rf data/runtime/blobs data/runtime/page_images data/runtime/artifacts data/runtime/workspaces

( cd apps/api && uv run alembic upgrade head )
echo "database reset. run 'make seed' to recreate roles and demo users."
