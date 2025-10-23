#!/usr/bin/env bash

set -euo pipefail

# Repeatedly print Dify ingestion metrics to observe trends over time.
# Usage:
#   DOCKER_DIR=/path/to/dify/docker ./ingestion-watch.sh [interval_seconds] [iterations]
# Examples:
#   ./ingestion-watch.sh                # default: every 15s, infinite
#   ./ingestion-watch.sh 10             # every 10s, infinite
#   ./ingestion-watch.sh 5 24           # every 5s, 24 times (~2 minutes)
#   ./ingestion-watch.sh 300 6          # every 5m, 6 times (~30 minutes)

INTERVAL=${1:-15}
ITERATIONS=${2:-0}  # 0 = infinite

DIR="$(cd "$(dirname "$0")" && pwd)"

COUNT=0

# Ensure we have a baseline CSV sample at t0 (won't duplicate header)
"$DIR/ingestion-sample.sh" "$DIR/data/ingestion_timeseries.csv" >/dev/null || true

while [ "$ITERATIONS" -eq 0 ] || [ "$COUNT" -lt "$ITERATIONS" ]; do
  echo "\n==== $(date -u '+%Y-%m-%d %H:%M:%S UTC') ===="
  if ! "$DIR/ingestion-metrics.sh"; then
    echo "[warn] ingestion-metrics.sh failed (iteration $COUNT)" >&2
  fi
  echo "----------------------------------------"
  # Append a CSV sample for this iteration as well
  "$DIR/ingestion-sample.sh" "$DIR/data/ingestion_timeseries.csv" >/dev/null || true
  sleep "$INTERVAL"
  COUNT=$((COUNT+1))
done

# If invoked with SCHEDULED_RUN=1 and INTERVAL/ITERATIONS set (e.g., 300 6),
# also write CSV samples and emit a report at the end.
if [ "${SCHEDULED_RUN:-0}" = "1" ]; then
  # Re-run sampling once to ensure we have end point in CSV
  "$DIR/ingestion-sample.sh" "$DIR/data/ingestion_timeseries.csv" >/dev/null || true
  "$DIR/ingestion-report.sh" "$DIR/data/ingestion_timeseries.csv" "$DIR/data/ingestion_report.md"
fi
