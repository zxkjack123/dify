#!/usr/bin/env bash
set -euo pipefail

# Re-import knowledge templates into pipeline_customized_templates.
# Requirements:
# - Docker stack running (db and compose project in ./docker)
# - Template YAMLs at docker/knowledge-templates/
# - SQL helper at docker/sql/insert_custom_templates.sql

ROOT_DIR="$(cd "$(dirname "$0")"/../.. && pwd)"
cd "$ROOT_DIR/docker"

# Ensure files exist
if [[ ! -f knowledge-templates/file-parentchild-external-embedding-hybrid.rag.yml ]]; then
  echo "Missing: docker/knowledge-templates/file-parentchild-external-embedding-hybrid.rag.yml" >&2
  exit 1
fi
if [[ ! -f knowledge-templates/file-general-external-embedding-hybrid.rag.yml ]]; then
  echo "Missing: docker/knowledge-templates/file-general-external-embedding-hybrid.rag.yml" >&2
  exit 1
fi
if [[ ! -f sql/insert_custom_templates.sql ]]; then
  echo "Missing: docker/sql/insert_custom_templates.sql" >&2
  exit 1
fi

# Copy YAML and SQL into db container
echo "→ Copying YAMLs and SQL into db container..."
docker compose cp knowledge-templates/file-parentchild-external-embedding-hybrid.rag.yml db:/tmp/file1.rag.yml
docker compose cp knowledge-templates/file-general-external-embedding-hybrid.rag.yml db:/tmp/file2.rag.yml
docker compose cp sql/insert_custom_templates.sql db:/tmp/insert_custom_templates.sql

# Execute SQL
echo "→ Executing SQL..."
docker compose exec -T db bash -lc "psql -U postgres -d dify -f /tmp/insert_custom_templates.sql"

# Show results
echo "→ Current customized templates:"
docker compose exec -T db psql -U postgres -d dify -c "SELECT name, language, position FROM pipeline_customized_templates ORDER BY position;"
