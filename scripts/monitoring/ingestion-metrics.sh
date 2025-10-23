#!/usr/bin/env bash

set -euo pipefail

# Simple Dify ingestion metrics snapshot
# - Docs completed/in-progress/errors in last 30m
# - Avg indexing latency (s) in last 30m
# - Avg per-stage durations (parse/clean/split/index) in last 30m
# - Segments per minute in last 10m
# - Waiting segments backlog

DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR=${DOCKER_DIR:-"$DIR/../../docker"}
COMPOSE_FILES=("-f" "docker-compose.yaml" "-f" "docker-compose.override.yaml")

cd "$DOCKER_DIR"

run_psql() {
  local sql="$1"
  # Use printf to safely pass SQL through sh -lc and psql -c
  local cmd="export PGPASSWORD=\${DB_PASSWORD:-difyai123456}; psql -U \${DB_USERNAME:-postgres} -d \${DB_DATABASE:-dify} -t -A -c \"${sql}\""
  docker compose "${COMPOSE_FILES[@]}" exec -T db sh -lc "$cmd"
}

echo "== Documents status (last 30m) =="
run_psql "SELECT COUNT(*) FILTER (WHERE indexing_status='completed') AS completed, COUNT(*) FILTER (WHERE indexing_status IN ('indexing','splitting','cleaning','parsing','waiting')) AS inprogress, COUNT(*) FILTER (WHERE indexing_status='error') AS error FROM documents WHERE updated_at > NOW() - INTERVAL '30 minutes';" | awk -F'|' '{printf("completed=%s in_progress=%s error=%s\n", $1,$2,$3)}'

echo
echo "== Avg indexing latency (s) (last 30m) =="
run_psql "SELECT COALESCE(ROUND(AVG(indexing_latency)::numeric,2),0) FROM documents WHERE indexing_status='completed' AND completed_at > NOW() - INTERVAL '30 minutes';"

echo
echo "== Avg per-stage durations (s) (last 30m, completed docs) =="
run_psql "SELECT COALESCE(ROUND(AVG(EXTRACT(EPOCH FROM parsing_completed_at - processing_started_at))::numeric,2),0) AS parse_s, COALESCE(ROUND(AVG(EXTRACT(EPOCH FROM cleaning_completed_at - parsing_completed_at))::numeric,2),0) AS clean_s, COALESCE(ROUND(AVG(EXTRACT(EPOCH FROM splitting_completed_at - cleaning_completed_at))::numeric,2),0) AS split_s, COALESCE(ROUND(AVG(indexing_latency)::numeric,2),0) AS index_s FROM documents WHERE indexing_status='completed' AND completed_at > NOW() - INTERVAL '30 minutes';" | awk -F'|' '{printf("parse=%s clean=%s split=%s index=%s\n", $1,$2,$3,$4)}'

echo
echo "== Segments per minute (last 10m) =="
run_psql "SELECT COALESCE(ROUND(COUNT(*)/10.0,2),0) FROM document_segments WHERE completed_at > NOW() - INTERVAL '10 minutes';"

echo
echo "== Waiting segments backlog =="
run_psql "SELECT COUNT(*) FROM document_segments WHERE status='waiting';"

echo
echo "== Note =="
echo "- Run with: DOCKER_DIR=/path/to/dify/docker $(basename "$0")"
echo "- Requires docker compose access and the db service running."
