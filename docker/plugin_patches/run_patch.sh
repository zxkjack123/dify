#!/usr/bin/env sh
set -eu

echo "[plugin_patch] Waiting for SiliconFlow plugin files to exist..."
# Wait up to ~120s for plugin daemon to download/extract plugin into storage
ATTEMPTS=60
SLEEP_SECS=2
FOUND=0
while [ $ATTEMPTS -gt 0 ]; do
  if ls /app/storage/cwd/langgenius/siliconflow-*/provider/siliconflow.py >/dev/null 2>&1; then
    FOUND=1
    break
  fi
  ATTEMPTS=$((ATTEMPTS-1))
  sleep $SLEEP_SECS
done

if [ "$FOUND" -eq 1 ]; then
  echo "[plugin_patch] SiliconFlow provider located. Applying patch..."
else
  echo "[plugin_patch] SiliconFlow provider not found after wait; attempting patch anyway (idempotent)."
fi

python /patches/patch_siliconflow_provider.py || true
echo "[plugin_patch] Patch run complete."
