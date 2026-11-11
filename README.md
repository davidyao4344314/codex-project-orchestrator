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

In PyCharm, open this repository folder as a project and select the existing `.venv/bin/python`
as its Python interpreter. Run `python -m codex_orchestrator --help` in the PyCharm terminal.

Run `orchestrator setup` to check Codex installation and sign-in. Interactive planning also
runs this check after saving your initial idea. The program searches PATH, common install
locations, and the Codex CLI bundled with ChatGPT on macOS. To select a specific executable,
set `CODEX_EXECUTABLE` to its full path in your terminal environment.

If Codex is missing, setup offers to install `@openai/codex` with npm after confirmation. If
npm is unavailable or you prefer another method, it shows the official installation guide
and accepts the path to an installed executable. If sign-in is needed, it offers to open
Codex's browser login and verifies the result before continuing. Existing sign-ins are reused.
Codex handles credential storage. Setup keeps credentials out of the project files.

If you postpone setup, resume your saved session with `orchestrator resume` and its JSON path.
Setup is also available as a separate command so you can check it before starting a session.

## Local browser interface

Install the optional interface and start it from the repository directory:

```sh
python -m pip install -e '.[web]'
orchestrator web
```

Open [the local planner](http://127.0.0.1:8501) on this computer. The server binds to loopback. The browser uses
the same session files, Codex connection, validation, and approval actions as the terminal.
Start a session, answer questions, approve Stage 1, then switch to Technical design.

The “Try sample project” button copies a reading-list starter into an isolated Git repository
under the ignored `plans/samples/` directory and fills in its brief. Create the session and
choose Continue with Codex. The sample asks for a Python/Streamlit/SQLite reading tracker
with book entry, status filters, and persistent storage.

Use Check connection in the sidebar to inspect Codex sign-in. The browser can open Codex's
sign-in flow if necessary. For CLI installation, run `orchestrator setup` in the terminal.
Model requests are triggered by explicit actions. Reloading a saved plan makes no model request.

The workflow remains at planning and technical-design approval. Stage checks are displayed
as planned verification gates. Generated implementation and merge execution are later work.

## Stage 1: interactive planning

With Codex CLI installed and signed in, start a session from your terminal:

```sh
orchestrator plan . --interactive -o plans/activity-session.json
```

Pass the directory of the project you want to plan for in place of `.`. You can also pass
an initial brief as an argument or with `--brief-file`. If you omit `-o`, the program creates
a unique session file under `plans/`.

1. Describe the feature. The intake model asks about gaps that affect scope or success criteria.
   Type `:plan` at an intake question to proceed with the information you have.
2. The planning model reads the brief and inspects the project. It can ask a clarification
   question before drafting. The plan links tasks to requirements and suggests a specialist
   role, difficulty, dependencies, scope, and automated and human checks for each task.
3. Review the dependency groups and the agent recommendation. Tasks in the same group have
   their dependencies satisfied at the same time. Whether they can run concurrently also
   depends on whether their changes overlap.
4. Choose `approve`, `revise`, or `save`. Approval records the current revision. A revision
   request opens a draft and keeps the previous plan in the session history.

Use `:save` at a question or resume after an interrupted call:

```sh
orchestrator resume plans/activity-session.json
```

The session JSON stores the conversation, structured brief, revisions, and approval. The
interactive workflow ends with the saved plan. It uses read-only Codex planning calls.
Set `--intake-model` and `--model` to choose intake and planning models separately, or
omit them to use your Codex configuration. `--timeout` applies per model call.

On first use, the terminal asks for the intake and planning model IDs that you have not
specified through flags. Press Enter to use the Codex configuration. Enter a model ID
available to your account to select it explicitly. `--planning-model` is an alias for `--model`.
At a question, type `:models` to change these choices. At the plan review prompt, enter
`models`. You can also override them when resuming:

```sh
orchestrator resume plans/activity-session.json --intake-model MODEL_ID --planning-model MODEL_ID
```

Use `default` in the selector or resume options to return to the Codex configuration. Choices
are saved per session. The terminal displays the requested model before each call and the
model reported by the CLI after a successful response. Some CLI versions omit model metadata.
In that case, the display explicitly says the model was unreported and shows the request
setting. Each response stores its stage and requested/reported model names, and each plan
revision keeps its own model attribution even after you change future model choices. Older
session files remain readable and display unknown model attribution where it was not recorded.

## Stage 2: technical design and staged agent workflow

Finish and approve a Stage 1 session, then pass its saved path to the architect:

```sh
orchestrator design plans/activity-session.json
```

The default output is beside the source, such as `plans/activity-session.design.json`.
The same command reopens it and resumes a saved question or draft. Use `--output` to create
a separate design, `--model` / `--architect-model` to choose the architect, and
`--agent-model` to set the initial model choice for proposed agents. Enter `default` to
use Codex's configuration. The architect initially inherits the Stage 1 planning model.

The full review displays the approved requirements and exclusions, repository findings,
components, interfaces, data flow, storage, technology choices, and staged delivery workflow.
Each assignment includes the responsible agent, scope, exact owned files, required and optional
context, model recommendation, selected model, skills, deliverables, checks, escalation,
and handoff. Skill availability is reported from the presence of a named `SKILL.md` file.
A missing or unverified skill remains a setup item.

Each delivery stage has a verification gate. It specifies agent checks with expected results,
required user tests, acceptance criteria, review and failure owners, and completion evidence.
The merge policy calls for actual check commands/results against the tested commit, review of
the combined stage changes, and any required user acceptance before merging. Dependent stages
start after their prerequisites' verified merges. These are planned gates for the future
execution runner. Stage 2 approval records approval of the design.

At review, choose `approve`, `revise`, `models`, or `save`. The model menu lets you change the
architect for future requests and the selected model for each proposed agent. Allocation
changes create a fresh reviewable revision. At an architect question, use `:models` to change
request defaults or `:save` to exit.

Display a saved design without a model request:

```sh
orchestrator design plans/activity-session.json --show
```

Designs retain the approved Stage 1 revision and a fingerprint of its snapshot. If Stage 1
is revised, approve its new revision and start a new design with a different `--output` path.
The old design stays available as a historical artifact. Required project-file context must
exist before approval. Invalid reference IDs, requirement coverage gaps, dependency cycles,
unordered writes to a shared declared file, and missing dependencies on required upstream
context are rejected. Validation feedback is saved for the next architect attempt.

These structural checks operate on the declared design. Semantic scope decisions still need
review, and worker permissions and verified merges belong to the later execution runner.
The architect uses the existing read-only Codex adapter. Sessions under `plans/` are ignored by Git.

See [Stage 2 manual checks](docs/stage2-testing.md) for a walkthrough and expected results.

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

Use `codex login status` to check your sign-in. If you need to sign in, run `codex login` and
complete the ChatGPT browser flow. Keep Codex's credential store outside the repository.
Git ignores `auth.json`, `.env` files, and generated plans under `plans/`. If you choose an
output path elsewhere, review the saved brief and session before staging it. Codex can read
project files during planning, so keep secrets out of the brief and use a project folder that
you are comfortable sharing with your signed-in Codex service.

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
| `src/codex_orchestrator/codex.py` | Sends a request and validates structured Codex output. |
| `src/codex_orchestrator/interactive.py` | Runs the terminal intake, planning, and review flow. |
| `src/codex_orchestrator/session.py` | Defines briefs and revisions and saves resumable checkpoints. |
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

Tests cover JSON validation, dependency cycles, CLI input/output, and Codex failures. The
interactive tests simulate Codex replies to exercise clarification, revision, approval, and
resuming after a failed model call.

## Next increments

1. Add `status` to read a saved plan and show which tasks have their prerequisites satisfied.
2. Add an explicit command to run one selected task and record its result.
3. Add independent workers and Git worktrees once single-task execution is reliable.

See the [project roadmap](docs/roadmap.md) for the Workflow Analyzer, Task Planner,
Performance Optimizer, and Evaluation Engine.

## Project dashboard

Home lists saved projects. Each project's Dashboard groups planned assignments into
Frontend, Backend, and Database, with shared/integration work listed separately.
Open an assignment to inspect its supplied context, skills, boundaries, model, and
saved read-only agent conversation. Agents can explain their plan and propose code;
implementation execution is a later stage. Code output also offers a read-only preview
of existing source within that assignment's owned paths. Conversations stay in ignored
local session files and are separated by assignment and design revision.

See [browser testing instructions](docs/web-testing.md) for expected results.
