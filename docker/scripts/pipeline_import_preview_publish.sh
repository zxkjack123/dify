#!/usr/bin/env bash
set -euo pipefail

# Import and (optionally) publish a RAG pipeline template via Dify API.
# Usage:
#   pipeline_import_preview_publish.sh <RAG_YAML_PATH> [--publish] [--endpoint BASE_URL] [--api-key KEY]
# Defaults:
#   BASE_URL: http://localhost:3000
#   KEY: requires Dify Console API key with sufficient permissions.

RAG_FILE=${1:-}
shift || true

PUBLISH=0
BASE_URL=${DIFY_BASE_URL:-http://localhost:3000}
API_KEY=${DIFY_API_KEY:-}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --publish)
      PUBLISH=1
      shift
      ;;
    --endpoint)
      BASE_URL="$2"; shift 2
      ;;
    --api-key)
      API_KEY="$2"; shift 2
      ;;
    *)
      echo "Unknown arg: $1" >&2; exit 1
      ;;
  esac
done

if [[ -z "$RAG_FILE" || ! -f "$RAG_FILE" ]]; then
  echo "RAG template file not found: $RAG_FILE" >&2
  exit 1
fi

if [[ -z "$API_KEY" ]]; then
  echo "Set DIFY_API_KEY or pass --api-key." >&2
  exit 1
fi

echo "Importing: $RAG_FILE"
PIPELINE_JSON=$(curl -sS -X POST "$BASE_URL/console/api/rag/pipelines/import" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@$RAG_FILE")

echo "$PIPELINE_JSON" | jq . >/dev/null || { echo "Invalid response:"; echo "$PIPELINE_JSON"; exit 1; }

PIPELINE_ID=$(echo "$PIPELINE_JSON" | jq -r '.id // .data.id')
if [[ -z "$PIPELINE_ID" || "$PIPELINE_ID" == "null" ]]; then
  echo "Failed to parse pipeline id from response:" >&2
  echo "$PIPELINE_JSON"
  exit 1
fi

echo "Imported Pipeline ID: $PIPELINE_ID"

if [[ "$PUBLISH" -eq 1 ]]; then
  echo "Publishing pipeline $PIPELINE_ID"
  PUB_JSON=$(curl -sS -X POST "$BASE_URL/console/api/rag/pipelines/$PIPELINE_ID/publish" \
    -H "Authorization: Bearer $API_KEY")
  echo "$PUB_JSON" | jq . >/dev/null || { echo "Invalid publish response:"; echo "$PUB_JSON"; exit 1; }
  echo "Published."
else
  echo "Preview only. Use --publish to publish."
fi
