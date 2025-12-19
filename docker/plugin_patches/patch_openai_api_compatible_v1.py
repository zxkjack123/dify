"""Patch the OpenAI-API-compatible plugin to avoid /v1/v1 base URLs.

Why this exists:
- langgenius/openai_api_compatible 0.0.22 builds OpenAI base_url as:
    base = credentials["openai_api_base"].rstrip("/")
    base_url = base + "/v1"
  If users enter a base that already includes /v1, it becomes /v1/v1.

This patch makes the logic accept either form:
- https://host            -> https://host/v1
- https://host/v1         -> https://host/v1

It is idempotent and safe to run repeatedly.
"""

from __future__ import annotations

import glob
from pathlib import Path


def _patch_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")

    # If already patched, do nothing.
    if "Avoid generating https://host/v1/v1" in text or "openai_api_base.endswith(\"/v1\")" in text:
        return False

    old = (
        "        if credentials.get(\"openai_api_base\"):\n"
        "            openai_api_base = credentials[\"openai_api_base\"].rstrip(\"/\")\n"
        "            credentials_kwargs[\"base_url\"] = openai_api_base + \"/v1\"\n"
    )

    new = (
        "        if credentials.get(\"openai_api_base\"):\n"
        "            openai_api_base = credentials[\"openai_api_base\"].rstrip(\"/\")\n"
        "            # Accept both:\n"
        "            # - https://host\n"
        "            # - https://host/v1\n"
        "            # Avoid generating https://host/v1/v1\n"
        "            credentials_kwargs[\"base_url\"] = (\n"
        "                openai_api_base if openai_api_base.endswith(\"/v1\") else openai_api_base + \"/v1\"\n"
        "            )\n"
    )

    if old not in text:
        # Try a slightly more flexible fallback (some plugin builds may differ in whitespace).
        if "credentials_kwargs[\"base_url\"] = openai_api_base + \"/v1\"" not in text:
            return False
        text = text.replace(
            "credentials_kwargs[\"base_url\"] = openai_api_base + \"/v1\"",
            (
                "# Accept both:\n"
                "            # - https://host\n"
                "            # - https://host/v1\n"
                "            # Avoid generating https://host/v1/v1\n"
                "            credentials_kwargs[\"base_url\"] = (\n"
                "                openai_api_base if openai_api_base.endswith(\"/v1\") else openai_api_base + \"/v1\"\n"
                "            )"
            ),
        )
    else:
        text = text.replace(old, new)

    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    patterns = [
        "/app/storage/cwd/langgenius/openai_api_compatible-*/models/common_openai.py",
        # Some installs may use different extraction roots; keep an extra glob for safety.
        "/app/storage/**/openai_api_compatible-*/models/common_openai.py",
    ]

    touched = 0
    for pat in patterns:
        for p in glob.glob(pat, recursive=True):
            path = Path(p)
            if not path.is_file():
                continue
            if _patch_file(path):
                print(f"[plugin_patch][openai_api_compatible] patched: {path}")
                touched += 1

    if touched == 0:
        print("[plugin_patch][openai_api_compatible] no changes (already patched or not found)")


if __name__ == "__main__":
    main()
