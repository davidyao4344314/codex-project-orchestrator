# Project maintenance

This is a Python local Codex planner with a Streamlit interface and an initial single-task runner.
Read docs/architecture.md when changing workflow boundaries or execution behavior.

- Keep validated contracts and workflow rules separate from CLI and Streamlit rendering.
- Reuse assignment_context.py for supplied task context; it must not import a UI framework.
- Preserve existing JSON sessions and revision history. Add defaults for compatible fields;
  document and test migrations for incompatible changes. Use SessionData checkpoints.
- Trigger inference and implementation only from explicit actions, never page reruns or polling.
- Distinguish turn completion, latest-attempt verification, and human review/merge acceptance.
- Keep project execution serialized. Parallel writers require isolated worktrees and new tests.
- Keep live log reads bounded. Never read an entire growing log merely to display its tail.
- Automated tests must mock model calls or use local fake subprocesses; do not spend account
  usage or open native application windows in the test suite.
- Install development extras with python -m pip install -e '.[dev,web]'. Verify changes with
  python -m pytest -q, ruff check src tests examples, and ruff format --check src tests examples.
- Keep auth files, .env files, generated sessions, and run logs out of Git.
