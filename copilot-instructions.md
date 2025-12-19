# Copilot Instructions

## Dify CLI usage

- In this workspace, **`dify-dev` / `dify` mean the Dify CLI installed directly on the host machine**, not a Docker container command.
- When you need to run Dify workflows or apps from the command line, prefer using the local CLI (e.g. `dify-dev ...`) rather than `docker compose exec` or similar Docker-based invocations.
- Assume the CLI is available on the system `PATH` and should be executed from the project root (`/home/gw/opt/dify`) unless the user specifies another directory.

## Python environment

- This workspace has a dedicated Python virtual environment at **`.venv/` in the repo root** (`/home/gw/opt/dify/.venv`).
- When running Python scripts or tools for this repo, prefer using this environment (e.g. `source .venv/bin/activate` or `.venv/bin/python ...`).
- Avoid creating new virtualenvs unless explicitly requested; reuse `.venv` for Python-based tooling and testing.
