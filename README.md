# Codex Project Orchestrator

A Python command-line program for planning frontend, backend, database, and integration work.

## Stack

- Python 3.11+ for the script.
- Typer for commands and help.
- Pydantic for validated JSON plans.
- Git for checkpoints; pytest and Ruff for development checks.

## Set up

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
orchestrator --help
```

The equivalent module entry point is `python -m codex_orchestrator`.

## Development

```sh
python -m pytest
ruff check .
ruff format --check .
```
