#!/usr/bin/env python3
"""
Patch SiliconFlow provider inside plugin daemon storage to use a robust
classmethod-based validation call and ensure the model name is quoted.

This script is idempotent and safe to run multiple times. It targets files under:
  /app/storage/cwd/langgenius/siliconflow-*/provider/siliconflow.py

It performs two changes:
1) Replace instance-based validate call with a classmethod call, handling
   both class or instance returned by get_model_instance(ModelType.LLM).
2) Ensure the model argument is quoted: "deepseek-ai/DeepSeek-V3".
"""
from __future__ import annotations

import glob
from pathlib import Path

TARGET_GLOB = "/app/storage/cwd/langgenius/siliconflow-*/provider/siliconflow.py"


def patch_file(fp: Path) -> bool:
    original = fp.read_text()
    text = original

    # Ensure model argument is quoted if unquoted
    text = text.replace(
        "model=deepseek-ai/DeepSeek-V3", 'model="deepseek-ai/DeepSeek-V3"'
    )

    # If already using classmethod dispatch, nothing to do for call pattern
    if "model_cls.validate_credentials(model=\"deepseek-ai/DeepSeek-V3\"" in text:
        changed = text != original
        if changed:
            fp.write_text(text)
        return changed

    # Replace instance-based call with classmethod dispatch block
    # Look for the common pattern lines and replace them.
    needle = (
        "            model_instance = self.get_model_instance(ModelType.LLM)\n"
    )
    if needle in text:
        lines = text.splitlines()
        try:
            idx = lines.index(
                "            model_instance = self.get_model_instance(ModelType.LLM)"
            )
        except ValueError:
            idx = -1

        if idx >= 0:
            # Replace this line and the next up to 2 lines if they match the impl call/comment
            # Build new block (4 lines)
            new_block = [
                "            import inspect",
                "            model_obj = self.get_model_instance(ModelType.LLM)",
                "            model_cls = model_obj if inspect.isclass(model_obj) else type(model_obj)",
                "            model_cls.validate_credentials(model=\"deepseek-ai/DeepSeek-V3\", credentials=credentials)",
            ]

            # Remove up to the next 2 lines if they include comment/call
            # Guard for boundaries
            end = min(len(lines), idx + 3)
            # Replace slice
            lines[idx:end] = new_block
            text = "\n".join(lines) + ("\n" if text.endswith("\n") else "\n")

    if text != original:
        fp.write_text(text)
        return True
    return False


def main() -> int:
    any_changed = False
    for path in glob.glob(TARGET_GLOB):
        fp = Path(path)
        try:
            changed = patch_file(fp)
            print(f"Patched: {fp} -> {changed}")
            any_changed = any_changed or changed
        except Exception as e:
            print(f"Patch failed for {fp}: {e}")
    print("Done. Changes applied:" , any_changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
