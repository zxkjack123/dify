#!/usr/bin/env bash
set -euo pipefail

# Simple sampling tool for Dify dataset indexing throughput
# Usage: embedding_throughput_sample.sh [duration_seconds] [interval_seconds]
# Defaults: duration=600s (10 minutes), interval=60s (1 minute)

DURATION="${1:-600}"
INTERVAL="${2:-60}"

if ! [[ "$DURATION" =~ ^[0-9]+$ ]] || ! [[ "$INTERVAL" =~ ^[0-9]+$ ]]; then
  echo "duration_seconds and interval_seconds must be integers" >&2
  exit 1
fi

LOOPS=$(( DURATION / INTERVAL ))
if (( LOOPS < 1 )); then
  LOOPS=1
fi

# Container names (adjust if your compose project name differs)
PLUGIN_DAEMON_CONTAINER="docker-plugin_daemon-1"
REDIS_CONTAINER="docker-redis-1"

echo "Starting sampling for ${DURATION}s (every ${INTERVAL}s) at $(date)" >&2
for i in $(seq 1 "$LOOPS"); do
  TS=$(date +%H:%M:%S)

  # Embedding requests in the last interval
  EMB=$(docker logs "$PLUGIN_DAEMON_CONTAINER" --since "${INTERVAL}s" 2>/dev/null | wc -l || echo 0)

  # Redis dataset queue length
  QLEN=$(docker exec "$REDIS_CONTAINER" redis-cli -n 0 LLEN dataset 2>/dev/null || echo -1)

  # Dataset worker CPU snapshot average and top
  docker stats --no-stream --format "{{.Name}} {{.CPUPerc}}" \
    | awk '/docker-dataset_worker-/{gsub("%","",$2); print $1, $2}' > /tmp/_cpu.tmp || true

  AVG="0.00"; TOP="n/a"
  if [[ -s /tmp/_cpu.tmp ]]; then
    AVG=$(awk '{s+=$2;c++} END{if(c>0) printf "%.2f", s/c; else print 0}' /tmp/_cpu.tmp)
    TOP=$(sort -k2 -nr /tmp/_cpu.tmp | head -1 | awk '{printf "%s:%s%%", $1, $2}')
  fi

  echo "$TS | embeds_last_${INTERVAL}s=$EMB | redis.dataset.llen=$QLEN | dataset_worker.cpu_avg=${AVG}% | top=${TOP}"
  sleep "$INTERVAL"
done

echo "Completed at $(date)" >&2
#!/usr/bin/env bash
set -euo pipefail
DURATION_SECONDS=${1:-180}
SAMPLE_INTERVAL=${2:-10}
PLUGIN_CONTAINER=${PLUGIN_CONTAINER:-docker-plugin_daemon-1}
REDIS_CONTAINER=${REDIS_CONTAINER:-docker-redis-1}
WORKER_PREFIX=${WORKER_PREFIX:-docker-dataset_worker-}

START_TS=$(date -u +%s)
END_TS=$((START_TS + DURATION_SECONDS))
# Create a unique temp dir; mktemp requires Xs pattern
TMPDIR=$(mktemp -d "/tmp/throughput.$START_TS.XXXXXX")
REPLICAS=$(docker ps --format "{{.Names}}" | grep -E "^${WORKER_PREFIX}[0-9]+$" | sort || true)
if [ -z "${REPLICAS:-}" ]; then
  REPLICAS="${WORKER_PREFIX}1 ${WORKER_PREFIX}2 ${WORKER_PREFIX}3 ${WORKER_PREFIX}4 ${WORKER_PREFIX}5"
fi

for r in $REPLICAS; do :> "$TMPDIR/${r}.cpu"; done
:> "$TMPDIR/queue_trend.txt"
:> "$TMPDIR/embedding_rates.txt"

# T0 queue length
QLEN_0=$(docker exec "$REDIS_CONTAINER" redis-cli LLEN dataset 2>/dev/null || echo 0)
echo "0s $QLEN_0" >> "$TMPDIR/queue_trend.txt"

sample_cpu() {
  docker stats --no-stream --format "{{.Name}}\t{{.CPUPerc}}" 2>/dev/null |
  awk -v tmp="$TMPDIR" -v prefix="$WORKER_PREFIX" 'BEGIN{FS="\t"} $1 ~ ("^"prefix"[0-9]+$") { gsub(/%/, "", $2); gsub(/ /, "", $2); if ($2 != "") printf "%s\t%s\n", $1, $2 }' |
  while IFS=$'\t' read -r name cpu; do echo "$cpu" >> "$TMPDIR/${name}.cpu"; done
}

rate_minute() {
  docker logs --since 60s "$PLUGIN_CONTAINER" 2>&1 | egrep -i "(embedding|num_tokens|/v1/embeddings|/embeddings)" | wc -l || echo 0
}

# Main loop
while true; do
  NOW=$(date -u +%s)
  [ "$NOW" -ge "$END_TS" ] && break
  ELAPSED=$((NOW - START_TS))

  sample_cpu

  if [ "$ELAPSED" -ge 60 ] && [ ! -f "$TMPDIR/m1" ]; then
    rate_minute >> "$TMPDIR/embedding_rates.txt" || echo 0 >> "$TMPDIR/embedding_rates.txt"
    QLEN_60=$(docker exec "$REDIS_CONTAINER" redis-cli LLEN dataset 2>/dev/null || echo 0)
    echo "60s $QLEN_60" >> "$TMPDIR/queue_trend.txt"
    :> "$TMPDIR/m1"
  fi
  if [ "$ELAPSED" -ge 120 ] && [ ! -f "$TMPDIR/m2" ]; then
    rate_minute >> "$TMPDIR/embedding_rates.txt" || echo 0 >> "$TMPDIR/embedding_rates.txt"
    QLEN_120=$(docker exec "$REDIS_CONTAINER" redis-cli LLEN dataset 2>/dev/null || echo 0)
    echo "120s $QLEN_120" >> "$TMPDIR/queue_trend.txt"
    :> "$TMPDIR/m2"
  fi
  sleep "$SAMPLE_INTERVAL"
done

# Final minute and queue at end
(rate_minute || echo 0) >> "$TMPDIR/embedding_rates.txt"
QLEN_END=$(docker exec "$REDIS_CONTAINER" redis-cli LLEN dataset 2>/dev/null || echo 0)
echo "${DURATION_SECONDS}s $QLEN_END" >> "$TMPDIR/queue_trend.txt"

pct() { awk 'NF' "$1" | sort -n | awk -v p="$2" 'BEGIN{c=0} {a[++c]=$1} END{ if(c==0){print "NA"; exit} idx=int((p/100.0)*c); if((p/100.0)*c>idx) idx++; if(idx<1) idx=1; print a[idx] }'; }
avg() { awk 'NF{ s+=$1; c++ } END{ if(c==0) print "NA"; else printf "%.2f\n", s/c }' "$1"; }
minv() { awk 'NF' "$1" | sort -n | head -n1; }
maxv() { awk 'NF' "$1" | sort -n | tail -n1; }

# Report
echo "=== THROUGHPUT REPORT ($TMPDIR) ==="
echo "Per-replica CPU percentiles (%) over ~$(printf %.0f "$(echo "$DURATION_SECONDS/$SAMPLE_INTERVAL" | bc -l)") samples"
printf "%s\n" "Replica	count	min	p50	p95	max	avg"
for r in $REPLICAS; do
  f="$TMPDIR/${r}.cpu"; cnt=$(wc -l < "$f" | tr -d " ")
  if [ "$cnt" -eq 0 ]; then printf "%s\t0\tNA\tNA\tNA\tNA\tNA\n" "$r"; continue; fi
  p50=$(pct "$f" 50); p95=$(pct "$f" 95); mn=$(minv "$f"); mx=$(maxv "$f"); av=$(avg "$f")
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$r" "$cnt" "$mn" "$p50" "$p95" "$mx" "$av"
done

echo
echo "Embedding request rate per minute (lines/min)"
awk '{printf "min%d %s\n", NR, $1}' "$TMPDIR/embedding_rates.txt" || true

echo
echo "Queue length trend (dataset LLEN at ticks)"
cat "$TMPDIR/queue_trend.txt" || true

echo
echo "Final docker stats snapshot"
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" | egrep "docker-(api|worker|dataset_worker|plugin_daemon|db|redis|weaviate)" || true
