"""Patch the OpenAI-API-compatible plugin for WxiAI gpt-5* compatibility.

Why this exists:
- Some OpenAI-compatible gateways (notably WxiAI) return HTTP 400 for gpt-5* if
  `max_tokens` is provided (they require `max_completion_tokens`).
- The upstream OpenAI-compatible SDK validation uses `max_tokens`, so Dify's
  "Credentials validation" fails even though the credentials are correct.

What this patch does (idempotent):
1) In the plugin implementation `models/llm/llm.py`, translate
   model_parameters max_tokens -> max_completion_tokens for gpt-5*.
2) Add/override validate_credentials to use max_completion_tokens for gpt-5*.
"""

from __future__ import annotations

import glob
from pathlib import Path


TOKEN_COMPAT_MARKER = (
    "# WxiAI (and some OpenAI-compatible gateways) require `max_completion_tokens`"
)


def _ensure_import(text: str, *, needle: str, insert_after: str) -> str:
    if needle in text:
        return text
    if insert_after not in text:
        return text
    return text.replace(insert_after, insert_after + needle, 1)


def _patch_llm_py(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    # Only patch the expected plugin file.
    if "class OpenAILargeLanguageModel(OAICompatLargeLanguageModel):" not in text:
        return False

    # If marker is present and validate_credentials exists, we consider it patched.
    if TOKEN_COMPAT_MARKER in text and "def validate_credentials(" in text:
        return False

    # --- imports ---
    text = _ensure_import(
        text,
        needle="from dify_plugin.entities.model.llm import LLMMode\n",
        insert_after="from dify_plugin.entities.model.llm import LLMResult\n",
    )
    text = _ensure_import(
        text,
        needle="from urllib.parse import urljoin\n\n",
        insert_after="from typing import List\n",
    )
    # Keep imports simple; add json/requests/errors if missing.
    if "import json\n" not in text:
        text = text.replace(
            "from urllib.parse import urljoin\n\n",
            "from urllib.parse import urljoin\n\nimport json\n",
            1,
        )
    if "import requests\n" not in text:
        text = text.replace(
            "import json\n",
            "import json\nimport requests\n",
            1,
        )
    if "from dify_plugin.errors.model import CredentialsValidateFailedError\n" not in text:
        # Put it after requests import to avoid splitting stdlib/third-party too much.
        text = text.replace(
            "import requests\n",
            "import requests\nfrom dify_plugin.errors.model import CredentialsValidateFailedError\n",
            1,
        )

    # --- _invoke token compatibility ---
    if TOKEN_COMPAT_MARKER not in text:
        anchor = (
            "        enable_thinking = model_parameters.pop(\"enable_thinking\", None)\n"
            "        if enable_thinking is not None:\n"
            "            model_parameters[\"chat_template_kwargs\"] = {\"enable_thinking\": bool(enable_thinking)}\n"
        )
        if anchor in text:
            insertion = (
                anchor
                + "\n"
                + "        "
                + TOKEN_COMPAT_MARKER
                + "\n"
                + "        # for newer model families (e.g. gpt-5*). They may return HTTP 400 if `max_tokens`\n"
                + "        # is provided.\n"
                + "        if (\n"
                + "            isinstance(model, str)\n"
                + "            and model.lower().startswith(\"gpt-5\")\n"
                + "            and \"max_tokens\" in model_parameters\n"
                + "            and \"max_completion_tokens\" not in model_parameters\n"
                + "        ):\n"
                + "            model_parameters[\"max_completion_tokens\"] = model_parameters.pop(\"max_tokens\")\n"
            )
            text = text.replace(anchor, insertion, 1)

    # --- validate_credentials override ---
    if "def validate_credentials(" not in text:
        # Insert near end of class (append is safest).
        if not text.endswith("\n"):
            text += "\n"
        text += (
            "\n"
            "    def validate_credentials(self, model: str, credentials: dict) -> None:\n"
            "        \"\"\"Validate model credentials.\n\n"
            "        The upstream `OAICompatLargeLanguageModel.validate_credentials` uses `max_tokens`.\n"
            "        Some providers return HTTP 400 for gpt-5* when `max_tokens` is present, but succeed\n"
            "        with `max_completion_tokens`. We implement a compatible validation here.\n"
            "        \"\"\"\n\n"
            "        try:\n"
            "            headers = {\"Content-Type\": \"application/json\"}\n\n"
            "            api_key = credentials.get(\"api_key\")\n"
            "            if api_key:\n"
            "                headers[\"Authorization\"] = f\"Bearer {api_key}\"\n\n"
            "            endpoint_url = credentials[\"endpoint_url\"]\n"
            "            if not endpoint_url.endswith(\"/\"):\n"
            "                endpoint_url += \"/\"\n\n"
            "            completion_type = LLMMode.value_of(credentials[\"mode\"])\n\n"
            "            data: dict = {\"model\": credentials.get(\"endpoint_model_name\", model)}\n\n"
            "            if completion_type is LLMMode.CHAT:\n"
            "                data[\"messages\"] = [{\"role\": \"user\", \"content\": \"ping\"}]\n"
            "                endpoint_url = urljoin(endpoint_url, \"chat/completions\")\n"
            "            elif completion_type is LLMMode.COMPLETION:\n"
            "                data[\"prompt\"] = \"ping\"\n"
            "                endpoint_url = urljoin(endpoint_url, \"completions\")\n"
            "            else:\n"
            "                raise ValueError(\"Unsupported completion type for model configuration.\")\n\n"
            "            if isinstance(model, str) and model.lower().startswith(\"gpt-5\"):\n"
            "                data[\"max_completion_tokens\"] = 16\n"
            "            else:\n"
            "                data[\"max_tokens\"] = 5\n\n"
            "            response = requests.post(endpoint_url, headers=headers, json=data, timeout=(10, 60))\n\n"
            "            if response.status_code != 200:\n"
            "                raise CredentialsValidateFailedError(\n"
            "                    f\"Credentials validation failed with status code {response.status_code} \"\n"
            "                    f\"and response body {response.text}\"\n"
            "                )\n\n"
            "            try:\n"
            "                json_result = response.json()\n"
            "            except json.JSONDecodeError:\n"
            "                raise CredentialsValidateFailedError(\n"
            "                    f\"Credentials validation failed: JSON decode error, response body {response.text}\"\n"
            "                ) from None\n\n"
            "            if completion_type is LLMMode.CHAT and json_result.get(\"object\", \"\") == \"\":\n"
            "                json_result[\"object\"] = \"chat.completion\"\n"
            "            elif completion_type is LLMMode.COMPLETION and json_result.get(\"object\", \"\") == \"\":\n"
            "                json_result[\"object\"] = \"text_completion\"\n\n"
            "            if completion_type is LLMMode.CHAT and json_result.get(\"object\") != \"chat.completion\":\n"
            "                raise CredentialsValidateFailedError(\n"
            "                    \"Credentials validation failed: invalid response object, must be 'chat.completion', \"\n"
            "                    f\"response body {response.text}\"\n"
            "                )\n"
            "            elif completion_type is LLMMode.COMPLETION and json_result.get(\"object\") != \"text_completion\":\n"
            "                raise CredentialsValidateFailedError(\n"
            "                    \"Credentials validation failed: invalid response object, must be 'text_completion', \"\n"
            "                    f\"response body {response.text}\"\n"
            "                )\n\n"
            "        except CredentialsValidateFailedError:\n"
            "            raise\n"
            "        except Exception as ex:\n"
            "            raise CredentialsValidateFailedError(\n"
            "                f\"An error occurred during credentials validation: {ex!s}\"\n"
            "            ) from ex\n"
        )

    if text == original:
        return False

    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    patterns = [
        "/app/storage/cwd/langgenius/openai_api_compatible-*/models/llm/llm.py",
        "/app/storage/**/openai_api_compatible-*/models/llm/llm.py",
    ]

    touched = 0
    for pat in patterns:
        for p in glob.glob(pat, recursive=True):
            path = Path(p)
            if not path.is_file():
                continue
            if _patch_llm_py(path):
                print(f"[plugin_patch][openai_api_compatible] patched: {path}")
                touched += 1

    if touched == 0:
        print(
            "[plugin_patch][openai_api_compatible] no changes (already patched or not found)"
        )


if __name__ == "__main__":
    main()
