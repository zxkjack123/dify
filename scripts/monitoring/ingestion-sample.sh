#!/usr/bin/env bash
set -euo pipefail

# Sample ingestion KPIs in CSV form.
# Columns:
# ts_utc,completed_30m,in_progress_30m,error_30m,avg_index_latency_s_30m,parse_s_30m,clean_s_30m,split_s_30m,index_s_30m,seg_per_min_10m,backlog_waiting_segments
# Usage:
#   DOCKER_DIR=/path/to/dify/docker ./ingestion-sample.sh [csv_out_path]

DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR=${DOCKER_DIR:-"$DIR/../../docker"}
COMPOSE_FILES=("-f" "docker-compose.yaml" "-f" "docker-compose.override.yaml")

CSV_OUT=${1:-"$DIR/data/ingestion_timeseries.csv"}
# Ensure CSV_OUT is absolute and its directory exists before changing directories
CSV_DIR="$(dirname "$CSV_OUT")"
mkdir -p "$CSV_DIR"
CSV_OUT="$(cd "$CSV_DIR" && pwd)/$(basename "$CSV_OUT")"

cd "$DOCKER_DIR"

run_psql() {
  local sql="$1"
  # -A (unaligned), -t (tuples only), -F '|' (field separator) for predictable parsing
  local cmd="export PGPASSWORD=\${DB_PASSWORD:-difyai123456}; psql -U \${DB_USERNAME:-postgres} -d \${DB_DATABASE:-dify} -t -A -F '|' -c \"${sql}\""
  docker compose "${COMPOSE_FILES[@]}" exec -T db sh -lc "$cmd"
}

# Query metrics
IFS='|' read -r COMPLETED_30M INPROG_30M ERROR_30M <<< "$(run_psql "SELECT COUNT(*) FILTER (WHERE indexing_status='completed') AS completed, COUNT(*) FILTER (WHERE indexing_status IN ('indexing','splitting','cleaning','parsing','waiting')) AS inprogress, COUNT(*) FILTER (WHERE indexing_status='error') AS error FROM documents WHERE updated_at > NOW() - INTERVAL '30 minutes';")"
AVG_INDEX_LAT_30M=$(run_psql "SELECT COALESCE(ROUND(AVG(indexing_latency)::numeric,2),0) FROM documents WHERE indexing_status='completed' AND completed_at > NOW() - INTERVAL '30 minutes';")
IFS='|' read -r PARSE_S CLEAN_S SPLIT_S INDEX_S <<< "$(run_psql "SELECT COALESCE(ROUND(AVG(EXTRACT(EPOCH FROM parsing_completed_at - processing_started_at))::numeric,2),0), COALESCE(ROUND(AVG(EXTRACT(EPOCH FROM cleaning_completed_at - parsing_completed_at))::numeric,2),0), COALESCE(ROUND(AVG(EXTRACT(EPOCH FROM splitting_completed_at - cleaning_completed_at))::numeric,2),0), COALESCE(ROUND(AVG(indexing_latency)::numeric,2),0) FROM documents WHERE indexing_status='completed' AND completed_at > NOW() - INTERVAL '30 minutes';")"
SEG_PER_MIN_10M=$(run_psql "SELECT COALESCE(ROUND(COUNT(*)/10.0,2),0) FROM document_segments WHERE completed_at > NOW() - INTERVAL '10 minutes';")
BACKLOG_WAIT=$(run_psql "SELECT COUNT(*) FROM document_segments WHERE status='waiting';")

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Write header if file empty or missing
if [ ! -s "$CSV_OUT" ]; then
  echo "ts_utc,completed_30m,in_progress_30m,error_30m,avg_index_latency_s_30m,parse_s_30m,clean_s_30m,split_s_30m,index_s_30m,seg_per_min_10m,backlog_waiting_segments" >> "$CSV_OUT"
fi

# Append CSV row
printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n" \
  "$TS" "$COMPLETED_30M" "$INPROG_30M" "$ERROR_30M" "$AVG_INDEX_LAT_30M" "$PARSE_S" "$CLEAN_S" "$SPLIT_S" "$INDEX_S" "$SEG_PER_MIN_10M" "$BACKLOG_WAIT" >> "$CSV_OUT"

echo "[ingestion-sample] appended row at $TS -> $CSV_OUT"