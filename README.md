# Codex Project Orchestrator

A Python command-line program for planning frontend, backend, database, and integration work.
All application code is Python. The first milestone is planning: the program saves tasks and
their dependencies for review. Task execution and multiagent coordination come later.

## Stack

- Python 3.11+ for the script.
- Typer for commands and help.
- Pydantic for validated JSON plans.
- Git for checkpoints; pytest and Ruff for development checks.

JSON files store plans for now. A database server, web framework, and agent orchestration library
are not required for this milestone.

## Set up

From this repository's directory, with Python 3.11 or later installed:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
orchestrator --help
```

The equivalent module entry point is `python -m codex_orchestrator`.
On this initialized workspace, `.venv` already exists and has the dependencies installed; start
with `source .venv/bin/activate` in each new terminal session.

## Create a starter plan

```sh
orchestrator plan . "Build an activity planner with saved activities" \
  --name activity-planner --output plans/activity-planner.json
```

Alternatively, put the brief in a UTF-8 file:

```sh
orchestrator plan . --brief-file examples/brief.md \
  --name activity-planner --output plans/activity-planner.json
```

The starter plan is a **generic offline template**. It records the brief but does not analyze it
or inspect project code. Review and adapt the tasks before implementation. The JSON records
`planning_mode: "template"` to make this explicit.

See [`examples/plan.template.json`](examples/plan.template.json) for a complete output example.

## Ask Codex to plan from a real project

Install the [Codex CLI](https://learn.chatgpt.com/docs/cli) if `codex --version` is unavailable,
then sign in. This optional mode needs a working Codex account and network connection:

```sh
codex --version
codex login
orchestrator plan /path/to/your/project "Add activity creation and editing" \
  --provider codex --output plans/activity-feature.json
```

Replace `/path/to/your/project` with an existing project directory. For a new project, create
the directory first. To use this repository as a planning example, use `.` instead.

This performs one Codex planning call. It asks Codex to inspect the project with read-only
operations and return structured tasks; the script validates the response before saving it.
The JSON records `planning_mode: "codex"`. Review the generated assumptions and acceptance criteria.

The adapter uses the officially supported
[`codex exec` interface](https://learn.chatgpt.com/docs/non-interactive-mode), with
`--sandbox read-only`, `--ask-for-approval never`, `--output-schema`, and
`--output-last-message`. The brief is passed through standard input, without a shell.
Authentication and the default model come from the CLI. There are no credentials in this repo.
Codex account usage applies when you select this provider.

Use `--model MODEL_NAME` to choose a model available to your account, and `--timeout 600` to
change the default 300-second timeout. Missing Codex, authentication failures, timeouts, and
invalid responses produce an error; they do not silently fall back to template tasks.

### Why use the CLI from Python?

Official OpenAI documentation currently supports both a
[Python SDK (`openai-codex`)](https://learn.chatgpt.com/docs/codex-sdk) and a TypeScript SDK.
This repository uses Python's standard-library `subprocess` module to call `codex exec` because
this milestone needs just one bounded request. You do not need to write or read JavaScript or
install an SDK to use this code. The Codex adapter can be replaced with the Python SDK later
if persistent sessions or streamed events become necessary.

These interfaces were checked against official documentation on 2026-11-11 and the locally
installed Codex CLI `0.167.0-alpha.3.2`. Local help confirms the flags above. Older installations
may need updating if a flag is unavailable.

## What a plan contains

Each task includes its workstream, prerequisites, description, and acceptance criteria. Plans
reject duplicate IDs, unknown dependencies, self-dependencies, and cycles. The workflow allows
frontend work with mocks to begin once the contract is agreed, while database and backend work
progress along their own dependencies.

The project directory must exist. Output paths are relative to the terminal's current directory,
and missing output directories are created. Existing output files require `--force` to replace.
Only the chosen plan file is written; the command does not implement any tasks.
For Codex mode, the CLI also manages its own authentication/runtime files and temporary response
files. The script requests read-only project access and does not run the proposed implementation.

## Read the Python code

Start with `planner.py` to see the six starter tasks. The rest is split into small modules:

| File | What it does |
| --- | --- |
| `src/codex_orchestrator/planner.py` | Defines the generic offline workflow. |
| `src/codex_orchestrator/models.py` | Defines a task and checks the dependency graph. |
| `src/codex_orchestrator/cli.py` | Reads command-line options and writes the plan. |
| `src/codex_orchestrator/codex.py` | Sends one planning request to the Codex CLI. |
| `tests/` | Checks behavior using Python and pytest. |

The template starts with a contract, then separates database/backend work from frontend work
with mocks. Integration depends on both branches, and verification comes last. This expresses
possible coordination without creating a scheduler or starting worker agents.

## Development

```sh
python -m pytest
ruff check .
ruff format --check .
```

Validation in this workspace: 32 tests passed on Python 3.14.3; Ruff and `pip check` passed.
Tests cover JSON validation, dependency cycles, CLI input/output, output replacement, and Codex
success/failure/timeout handling. A fake CLI process tests the real subprocess and file handoff
without network access. The actual CLI's help was inspected, but a live authenticated AI
planning request has not been run as part of this setup.

## Next increments

1. Add `status` to read a saved plan and show which tasks have their prerequisites satisfied.
2. Add an explicit command to run one selected task and record its result.
3. Add independent workers and Git worktrees once single-task execution is reliable.

Only `plan` is implemented today. The Git history keeps the package scaffold, local planner,
and optional Codex adapter in separate commits so each step is easy to inspect.
