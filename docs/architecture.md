# Architecture and maintenance review

Stage 2 implements the first technical-design workflow: approved requirements become
repository-aware architecture, delivery stages, agent/model/skill/context assignments,
dependency validation, feedback, revisions, and design/stage approvals. Native chat opening,
dragging, and real implementation runs were added afterward; they form an initial execution
layer. Completion of the Stage 2 implementation does not certify manual acceptance of those
later integrations.

## Module boundaries

| Layer | Modules | Responsibility |
| --- | --- | --- |
| Contracts/checkpoints | `models`, `session`, `design_models` | Validate persisted data, atomic replacement, revision history |
| Planning services | `planner`, `interactive`, `design` | Intake, requirements, architecture, clarification and revisions |
| Design rules | `design_validation`, `stage_review`, `reassignment`, `allocation` | Graph/context validation, stage decisions, ownership changes |
| Job assessment | `job_assessment` | Summarize architect difficulty/team decisions without inference on refresh |
| Assignment context | `assignment_context`, `context_builder`, `context_models`, `context_files`, `context_handoffs` | Resolve bounded, versioned task packages without inference or UI imports |
| Read-only Codex adapter | `codex`, `setup`, `explain` | CLI discovery/auth setup, structured responses, explanations |
| Execution coordinator | `coordinator`, `coordination_store`, `coordinator_view` | Explicit dispatch, pause/resume, stage schedules and acceptance checkpoints |
| Worker layer | `worker_models`, `worker_profiles`, `worker_runtime`, `worker_results`, `worker_audit`, `worker_view` | Role-specific execution contracts, reports, observed evidence and review display |
| Execution adapter | `execution` | Start/stop Codex, project writer lock, timeout, prerequisites, verification |
| Run storage | `run_store` | Validated snapshots, bounded cache and log previews, latest-attempt lookup |
| Terminal interface | `cli`, `design_display` | Arguments, prompts and terminal presentation |
| Web interface | `web`, `workflow_view`, `dashboard`, `run_view`, `allocation_view`, `web_widgets` | Navigation, forms, workflow rendering, live run display |
| Browser interactions | `selection`, `task_board` | Small JavaScript components for highlighting and drag/drop |

Keep persistent rules in Python services and contracts. Views should call these services
rather than inventing their own approval or dependency rules. Keep assignment context
independent of either interface. New transports should adapt these services instead of
creating a second workflow implementation. Small browser components use textContent for
untrusted labels; they send IDs back for Python-side validation.

## Findings addressed

- Live output used to read whole growing log files every two seconds. Previews now read
  at most 256 KiB and display up to 60 recent lines; diagnostics are bounded separately.
  Full logs remain on disk. Oversized individual records may be omitted from the preview.
- Polling repeatedly parsed full run files, including long prompts. A bounded 128-entry
  cache now reuses unchanged snapshots, invalidates by file modification time/size, and
  returns copies so callers cannot mutate cached records.
- An older verified success could satisfy prerequisites after a newer failed attempt.
  Only the latest attempt for each task in the current design revision now counts.
  Historical attempts cannot receive new verification acceptance.
- Context building lived inside the Streamlit dashboard. It now has a standalone module
  shared by discussion and execution callers.
- The main web script mixed navigation with a large workflow renderer. Stage/task rendering
  now lives in `workflow_view`; shared presentation helpers live in `web_widgets`.
- Stale stage-review/reassignment callers could overwrite newer saved state. Those updates
  now reject changed snapshots. This optimistic check is not a multi-process transaction.

These changes reduce repeated local work and improve boundaries. They do not establish a
latency, token, or cost advantage over ordinary Codex; that needs the planned evaluation work.

## State and verification rules

Design revisions are historical snapshots. Ownership/model changes create reviewable
revisions; stage approvals are revision-scoped. Old JSON remains readable using defaults
for additive fields. Incompatible contract changes need an explicit schema migration and
fixtures for previous formats. Checkpoints use atomic replacement, not in-place JSON edits.

Model calls and execution require explicit user actions. Browser refreshes and polling must
not trigger inference. Run completion, user verification acceptance, and merge acceptance
are separate concepts. Current verification is user-attested and tied to a task attempt and
design revision; it is not bound to a Git commit or a complete filesystem snapshot. Later
file edits or native Codex follow-ups therefore still require human re-verification.

## Remaining boundaries

- Execution is a single-writer local prototype. Owned paths and prohibitions on commits or
  deployments are prompt instructions inside the project sandbox, not per-file enforcement.
- Parallel execution needs isolated worktrees, a durable supervisor, and explicit integration
  gates. Do not add simultaneous writers to the existing project folder.
- Hard crashes can leave a lock behind. Recovery is currently manual after checking that
  the corresponding process is stopped. Do not automatically delete locks based only on age.
- File checkpoints and optimistic checks suit a local single-user tool. Multi-user or multiple
  server processes need transactional updates and coordinated job ownership.
- Native desktop routing, browser dragging/highlighting, and real-account model behavior
  need manual acceptance. Automated subprocess tests use local fake Codex programs.
- The documented installation is an editable repository checkout. A distributable wheel still
  needs packaging work for sample assets and a user-writable session directory.
- Workflow analysis, token/tool-call metrics, comparative evaluation, verified commits, and
  automatic review/merge gates remain future increments.

## How to extend it

1. Add or evolve a validated contract, preserving old saved sessions or supplying a migration.
2. Implement the rule in a service with focused tests for failure and resume behavior.
3. Reuse the service from the CLI/web interface. Keep rendering separate from execution.
4. For execution changes, test cancellation, project locking, failure events, prerequisite
   acceptance, and preservation of run history with fake subprocesses.
5. Update the manual checklist only for behavior requiring a browser, desktop app, or account.

Install the complete development environment with `python -m pip install -e '.[dev,web]'`.
Run `python -m pytest -q`, `ruff check src tests examples`, and `ruff format --check src tests examples`.
Web tests intentionally skip without the web extra, so install it when changing the interface.

## AI allocation extension

`allocation` produces a separate, fingerprinted proposal for a user-selected roster. The
AI supplies agent metadata, task owners/reasons, and gate owners; Python assembles those into
a copy of the existing design. It cannot add tasks or alter their scope, dependency graph,
context references, or check definitions. Validation requires exact coverage and meaningful
responsibility for every selected agent, and preserves required human review. Applying the
proposal creates a new revision and clears approval. `allocation_view` only renders sidebar
settings and proposal review. Roster preferences have defaults so older sessions still load.

## Coordinator boundary

The coordinator reads current run snapshots and derives a stage schedule; observation has
no execution side effects. Explicit dispatch calls the existing runner with assignment_context.
It does not implement a second Codex transport. Checkpoints are versioned and bound to the
full design revision fingerprint, with acceptance tied to latest verified run IDs. Pauses and
acceptance persist, but crash supervision of subprocesses remains the runner's existing limit.

The coordinator stops at verification waits rather than treating turn completion as success.
Task verification is user-attested; stage acceptance records combined-change review and human
checks. The runner also checks prerequisite stage acceptance, preventing a separate Start
implementation button from bypassing that boundary. No merging or independent AI reviewer is
implemented in this increment. Dispatch, retry, and resume are explicit; polling never starts
inference. Existing file checkpoints still target one local user, not concurrent server writers.

## Context builder boundary

`assignment_context` selects approved requirement/contract metadata. `context_builder` resolves
that manifest plus declared files, owned source, local skills, applicable project instructions,
and verified prerequisite outputs. `context_models` owns limits/resource statuses/package shape;
`context_files` handles bounded safe local reads; `context_handoffs` restricts run-summary access
to declared predecessors and recipients. `context_view` renders snapshots and explicit previews.

Packages retain reasons, required/optional status, hashes, source run IDs, warnings and blockers.
Required resources are prioritized and duplicate resolved files are read once per build. File
reads, handoff/event tails, resource counts and wire-package bytes are bounded. Required content
that is missing, excluded, truncated or over budget prevents execution. Discussion can include
missing-resource descriptions to clarify the plan, while over-budget metadata blocks inference.
Same-assignment discussion history is limited to 12 messages and 16 KiB; omitted messages are
counted. Retry prompts include at most three prior-run metadata records, not full transcripts.

The execution adapter rebuilds canonical context under the writer lock, irrespective of a
caller-provided preview. It saves a run-local context.json and its digest before starting Codex.
The worker captures a bounded final message for future handoff; older runs use bounded event-tail
fallback. Only latest completed/user-verified outputs in the current design revision are usable.
Producer source files are current workspace contents, not a Git snapshot of the earlier run.

Context limits are additive session settings with defaults for older sessions. Changing limits
only affects future packages, not design scope or existing run snapshots. Package hashes identify
supplied bytes; they are not a filesystem lock, commit certificate, or token-usage measurement.
Skill lookup supports local SKILL.md files under the project, ~/.codex/skills, ~/.agents/skills,
and ~/.codex/plugins. Remote skill resources and recursive skill-resource bundling are not yet
implemented. Context exclusion heuristics do not guarantee removal of every possible secret.
The Codex sandbox and its own instruction loading remain separate from package selection.

## Worker boundary

The six profiles share the existing Codex subprocess harness. `worker_profiles` derives a contract
from the context snapshot, normalizes ownership and selects read-only for no-write assignments.
`worker_models` defines the structured report schema. The CLI receives output-schema and
output-last-message paths; reports are validated locally before a new-contract run can complete.
Legacy runs stay readable with additive defaults and do not gain invented evidence.

`worker_audit` snapshots owned/context paths plus Git-visible dirt and observes final changes.
It preserves pre-existing edits, excludes the run artifact directory, and bounds hashing/diffs.
`worker_results` matches planned check IDs to actual completed command events and exit codes,
validates identity/scope/coverage, and preserves required manual checks. `worker_runtime` prepares
artifacts and finalizes reports; `worker_view` renders them without invoking a model. Verification
checks report integrity and watched source hashes at acceptance time. Later edits still require
manual re-verification; old accepted runs are not continuously supervised or invalidated.

A valid JSON report or zero exit code does not establish semantic correctness or test adequacy.
The user gate remains. Ownership is detected post-run, not isolated per file; audits are bounded
and cannot exhaustively capture ignored/reverted/outside-project writes. Read-only workers use
the CLI read-only sandbox. This worker layer does not add an independent reviewer, parallel
execution, automatic merges, or destructive rollback. See worker-testing.md for acceptance and
recovery checks, artifact names, exact audit limits and the all-profile sample.
