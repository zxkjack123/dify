#!/usr/bin/env bash
set -euo pipefail

# Generate a before/after report (markdown) for ingestion KPIs from a CSV timeseries.
# Usage:
#   ./ingestion-report.sh scripts/monitoring/data/ingestion_timeseries.csv [out_md]

CSV_IN=${1:-"scripts/monitoring/data/ingestion_timeseries.csv"}
OUT_MD=${2:-"scripts/monitoring/data/ingestion_report.md"}

if [ ! -f "$CSV_IN" ]; then
  echo "CSV not found: $CSV_IN" >&2
  exit 1
fi

# Read first and last data rows (skip header)
FIRST_LINE=$(tail -n +2 "$CSV_IN" | head -n 1)
LAST_LINE=$(tail -n 1 "$CSV_IN")

IFS=',' read -r TS1 C1 P1 E1 L1 PA1 CL1 SP1 IX1 SPM1 B1 <<< "$FIRST_LINE"
IFS=',' read -r TS2 C2 P2 E2 L2 PA2 CL2 SP2 IX2 SPM2 B2 <<< "$LAST_LINE"

# Percent and delta helper
pct() { awk -v a="$1" -v b="$2" 'BEGIN{ if (a==0) print 0; else printf "%.2f", ((b-a)/a)*100 }'; }
num() { awk -v a="$1" -v b="$2" 'BEGIN{ printf "%+.2f", (b-a) }'; }

DELTA_C=$(num "$C1" "$C2")
DELTA_P=$(num "$P1" "$P2")
DELTA_E=$(num "$E1" "$E2")
DELTA_L=$(num "$L1" "$L2")
DELTA_PA=$(num "$PA1" "$PA2")
DELTA_CL=$(num "$CL1" "$CL2")
DELTA_SP=$(num "$SP1" "$SP2")
DELTA_IX=$(num "$IX1" "$IX2")
DELTA_SPM=$(num "$SPM1" "$SPM2")
DELTA_B=$(num "$B1" "$B2")

PCT_SPM=$(pct "$SPM1" "$SPM2")

cat > "$OUT_MD" <<EOF
# Ingestion KPIs Report

Window: $TS1 → $TS2

- Segments/min: $SPM1 → $SPM2 ($DELTA_SPM, $PCT_SPM%)
- Completed (30m window): $C1 → $C2 ($DELTA_C)
- In-Progress (30m window): $P1 → $P2 ($DELTA_P)
- Errors (30m window): $E1 → $E2 ($DELTA_E)
- Avg Index Latency (s, 30m): $L1 → $L2 ($DELTA_L)
- Stage Averages (s, 30m, completed docs):
  - parse: $PA1 → $PA2 ($DELTA_PA)
  - clean: $CL1 → $CL2 ($DELTA_CL)
  - split: $SP1 → $SP2 ($DELTA_SP)
  - index: $IX1 → $IX2 ($DELTA_IX)
- Backlog (waiting segments): $B1 → $B2 ($DELTA_B)

## Segments/min over time

Below is a simple bar chart (scaled) for segments/min across the sampling window:

EOF

# Compute max SPM to scale the bar chart
SPM_MAX=$(tail -n +2 "$CSV_IN" | awk -F',' 'BEGIN{max=0} {v=$10+0; if (v>max) max=v} END{ if (max<=0) max=1; print max }')

# Append the chart lines
tail -n +2 "$CSV_IN" | awk -F',' -v max="$SPM_MAX" 'BEGIN{w=30} {v=$10+0; n=int((v/max)*w); if(n<0) n=0; bar=""; for(i=0;i<n;i++) bar=bar "#"; printf("  - %s  %6.2f | %s\n", $1, v, bar)}' >> "$OUT_MD"

cat >> "$OUT_MD" <<EOF
\n## Notes
EOF

# Append existing notes
cat >> "$OUT_MD" <<EOF
Notes:
- "30m" 指以当前时间为止的最近 30 分钟窗口内的统计。
- Segments/min 为最近 10 分钟窗口的近似估算（以 completed_at 计数/10）。
EOF

echo "[ingestion-report] wrote $OUT_MD"