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
- Hard crashes can leave a lock behind. Structured worker leases support explicit, process-checked
  recovery; legacy/ambiguous and Git/review locks still need manual inspection. Never release by age.
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
implementation button from bypassing that boundary. The coordinator does not merge. Independent review is an explicit publication action in the
review services, rather than a side effect of dispatch or polling. Dispatch, retry, and resume are explicit; polling never starts
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
checks report integrity and watched source hashes at acceptance time. New runs also bind the
full worker specification and final workspace snapshot to digests in run.json; contract checks
run before launching the subprocess as well. Existing records without those additive digests
retain their earlier verification behavior. These hashes detect inconsistent artifacts, not
malicious changes by an actor able to rewrite the run record itself. Later edits still require
manual re-verification; old accepted runs are not continuously supervised or invalidated.

Check evidence requires the exact recorded command string: shell wrappers, positional arguments,
and quoting can change behavior and are not normalized away. File observations open descriptors
with nonblocking/no-follow flags and reject special files or files changed while being read.
Reads respect the remaining snapshot byte budget, including discarded race reads. An unreadable
assigned input/output before or after a run blocks completion. These checks complement the CLI
sandbox; they do not implement a complete filesystem race or isolation boundary.

A valid JSON report or zero exit code does not establish semantic correctness or test adequacy.
The user gate remains. Ownership is detected post-run, not isolated per file; audits are bounded
and cannot exhaustively capture ignored/reverted/outside-project writes. Read-only workers use
the CLI read-only sandbox. The worker harness does not itself review or publish; the separate review/publication services
provide that gate. It does not add parallel execution, automatic merges or destructive rollback. See worker-testing.md for acceptance and
recovery checks, artifact names, exact audit limits and the all-profile sample.

See quality-review.md for the worker review findings, regression coverage, and maintenance priorities.


## Git publication boundary

`git_service` owns repository initialization, credential-free origin connection, accepted-stage
previews, scoped commits and explicit pushes. `git_view` renders the dashboard panel; it does
not call a model. Publication rechecks latest structured worker verification and stage acceptance.
Git writes acquire the same project lock as execution. Preview fingerprints bind selected bytes,
design revision, HEAD, branch and push destination; unrelated staged changes block committing.
Paths are literal Git pathspecs. A saved publication receipt binds commit SHA, design, branch,
remote and paths for retry; push checks those before a normal explicit refspec push.

Git authentication stays with the user's credential helper/SSH tooling. Noninteractive subprocesses
have bounded captured output and a timeout; errors omit raw output that might expose URLs with
credentials. The service does not create hosted repositories, force push, merge, or grant workers
independent Git access. Local JSON checkpoints remain single-user storage, not a durable Git job
queue. External editors and Git hooks can still modify files independently of the application lock.


## Independent review and publishing gate

The sequence is mandatory AI approval, local commit, then explicit user permission before push.
`review_store.require_reviewer` enforces a current independent decision for selected changes.
Human confirmation cannot override a blocked or missing AI review. The commit receipt binds both
the AI reviewer ID/digest and any separate user file-review record, plus attempt/evidence hashes.

`review_agent` calls the existing read-only Codex adapter in a separate ephemeral turn. A pending
blocked record supersedes an older review before inference, so errors cannot preserve an old
approval. Inputs are bounded to 256 KiB and checked after inference. Stage tests and human stage
acceptance still apply. The reviewer never commits or pushes; legacy combined-publication requests
are explicitly rejected. Legacy automatic-push policy fields load but are not honored.

`git_view` shows the AI review, local commit SHA, branch and remote, then requires a checkbox and
an explicit Approve and push action. `PushPermission` binds that request to the displayed commit
and destination. The service rejects a missing or mismatched permission, then rechecks AI approval,
evidence, source, HEAD and destination under the shared writer lock. Permission is passed for this
request rather than stored as a standing grant. A retry also needs a deliberate user push action.
Polling never reviews or publishes. No automatic merge or deployment is implemented.

Checkpoint fields are additive. Old receipts missing required AI-review bindings remain readable
but fail closed at publication. Hashes enforce application consistency, not isolation from actors
who can rewrite local code/state. External editors and Git hooks remain outside the application
lock. Durable supervision, transactional storage and quantitative review evaluation remain future work.


## Sequential stage branches

`stage_branches` stores explicit, revision-bound branch provenance in a per-design checkpoint.
The revision key includes both revision number and content fingerprint, so identical copied
revisions cannot reuse earlier branch metadata. Names include design-path identity to avoid
collisions between designs. Setup is explicit for existing projects; no branch history is invented.
A shared project lock serializes setup, switching, base refresh, merge checkpoints and execution.

`Run.branch` is additive. Once branching is enabled, `valid_runs` prevents earlier unbound runs
from satisfying scheduling or stage acceptance. Execution checks the required branch before and
under the writer lock, records it in the run, and checks it again at worker start/completion.
Git review/publication enforces the same stage branch. Retry preserves dirty work on that branch;
switching requires a clean tree. Only one unmerged stage is prepared per revision at a time.

A dependent stage needs accepted upstream runs plus a recorded merge whose exact approved,
user-pushed SHA is an ancestor of the configured local base. The checkpoint also binds upstream
attempt/evidence hashes and requires that the stage branch has no newer unreviewed commits.
Current inputs/outputs are checked at merge recording. Later base edits are not automatically
certified. Refresh is an explicit fetch and fast-forward synchronization, never a reset or an
automatic stage merge. Squash/rebase merges fail closed because SHA ancestry cannot prove them.

No-change stages still use the reviewer and user-permission gates, but use the existing HEAD
rather than an empty commit. Old checkpoints remain intact on new revisions. Multi-user locking,
parallel worktrees, provider-specific PR status and squash-merge evidence remain future work.


## Recovery and resume

`project_activity` supplies the shared lock path, structured token/owner/start identity and
conservative PID/process-group observations. New Git/review writers use token-matched release;
workers persist their child PID/start metadata before writing the prompt. Old records have
additive defaults. Unknown metadata is a blocker, not evidence that work stopped.

`recovery` derives read-only navigation guidance from planning/design checkpoints, current runs,
branch state, verified artifact/source hashes, AI review and publication receipts. Git publication
validation is shared with the actual push gate but never grants permission or publishes. Resume
navigation and inspection invoke no inference, branch switch, retry, review acceptance or push.
A local commit awaiting permission is recognized rather than recreated.

Explicit reconciliation of an interrupted worker rechecks the lease, live owner, child/group and
run state under the local execution guard, uses an exclusive lease when none remains, records
stopped/unverified status and releases only the matching lease. Files and history are untouched.
No process is killed by recovery. An active/unreconciled run blocks new attempts, including after
a missing lock. Git/review operations, legacy locks and incomplete worker launch identity remain
manual: without reliable child identity the service fails closed.

`recovery_brief` builds at most 16 KiB of same-task, same-revision retry context from bounded
history and current context-resource hashes. Historical commands/violations are labeled as such;
all required tests remain mandatory. Secrets recognizable by existing heuristics are excluded.
`recovery_view` opens the derived view and exposes explicit recovery/navigation controls.

Process observations are local and conservative, with stable UTC/C-locale owner start strings.
They are not a durable supervisor, distributed transaction or exhaustive external-writer detector.
The gap before child metadata is checkpointed and detached children require manual inspection.
Existing human stage acceptance, AI review, branch provenance and fresh per-push permission remain.

## Development cycle layer

`cycle_store` adds a per-project history manifest beside the first planning session. Each cycle
references its own session filename, so design revisions, agent runs, branch identities and
publication receipts remain independent. Importing a legacy session is explicit; its start time
is estimated from the session timestamp and marked imported.

`cycle_manager` serializes creation and completion with the shared project writer lock.
Completion requires an approved design, accepted stages, matching recorded merge evidence,
a clean integration base containing the merged commits, and explicit changes/feedback.
Starting the next cycle requires the completed predecessor and its integrated commits.
The terminal commands and worker/publication gates reject further work on historical cycles.
Existing unregistered sessions remain usable. Checkpoints are local JSON, not a transactional
database; an interrupted creation can leave an unattached session file for manual inspection.

The next session carries bounded prior-cycle context as reference data for intake/planning.
The user supplies a fresh goal; existing approved scope and review gates continue to apply.
The initial interface is `cycle_cli`, with no Streamlit changes. Performance evaluation,
longer-term memory selection and automatic workflow optimization are later work.
