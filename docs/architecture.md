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
| Planning optimization | `planning_context`, `scope_outline`, `compact_planning`, `model_catalog`, `model_allocation` | Bounded repository map, local scope outlines, combined drafts and difficulty-based model allocation |
| Design rules | `design_validation`, `stage_review`, `reassignment`, `allocation` | Graph/context validation, stage decisions, ownership changes |
| Job assessment | `job_assessment` | Summarize architect difficulty/team decisions without inference on refresh |
| Assignment context | `assignment_context`, `context_builder`, `context_models`, `context_files`, `context_handoffs` | Resolve bounded, versioned task packages without inference or UI imports |
| Read-only Codex adapter | `codex`, `setup`, `explain` | CLI discovery/auth setup, structured responses, explanations |
| Conversation management | `session_manager`, `usage_report` | Scoped thread bindings, turn receipts, read-only usage aggregation |
| Execution coordinator | `coordinator`, `coordination_store`, `coordinator_view` | Explicit dispatch, pause/resume, stage schedules and acceptance checkpoints |
| Worker layer | `worker_models`, `worker_profiles`, `worker_runtime`, `worker_results`, `worker_audit`, `worker_view` | Role-specific execution contracts, reports, observed evidence and review display |
| Execution adapter | `execution` | Start/stop Codex, project writer lock, timeout, prerequisites, verification |
| Run storage | `run_store` | Validated snapshots, bounded cache and log previews, latest-attempt lookup |
| Terminal interface | `cli`, `design_display` | Arguments, prompts and terminal presentation |
| Interactive cycle navigation | `cycle_navigation`, `cycle_cli` | Saved project selection and explicit state-dependent terminal actions |
| GitHub connection | `github_connection`, `github_cli` | Local Git origin inspection, gh authentication/access check, explicit setup |
| GitHub PR delivery | `github_api`, `github_pr`, `pr_description`, `github_pr_cli` | Approved PR creation, CI checks, exact-head merge and recovery |
| Branch allocation | `branch_allocation`, `agent_worktrees`, `stage_branches` | Graph-derived agent isolation, audited local integration and stage merge ancestry |
| Web interface | `web`, `workflow_view`, `dashboard`, `run_view`, `allocation_view`, `web_widgets` | Navigation, forms, workflow rendering, live run display |
| Browser interactions | `selection`, `task_board` | Small JavaScript components for highlighting and drag/drop |

Keep persistent rules in Python services and contracts. Views should call these services
rather than inventing their own approval or dependency rules. Keep assignment context
independent of either interface. New transports should adapt these services instead of
creating a second workflow implementation. Small browser components use textContent for
untrusted labels; they send IDs back for Python-side validation.

The terminal's `cycle start` menu reads the active cycle and recovery snapshot on
each iteration. It delegates to existing planning, feature delivery, feedback,
usage and PR commands. Stage selection prefers the active unmerged stage, then a
prerequisite-ready stage. Local-only merges use a clean, verified fast-forward
after explicit user consent and the same merge-recording gate. Menu inspection
and saving do not start inference or automatically replay failed operations.

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
are separate concepts. Task verification is tied to the latest attempt and audited source
hashes, not a complete filesystem snapshot. Stage verification composes the ordered before
and after snapshots, accepts a successor's edit only within its approved ownership and
dependencies, and compares the last observed state with the current source. A new worker's
before/spec/after hashes are included in publication evidence. Intervening external edits
and stale final outputs require re-verification.

## Remaining boundaries

- Execution is a single-writer local prototype. Owned paths and prohibitions on commits or
  deployments are prompt instructions inside the project sandbox, not per-file enforcement.
- Agent worktrees now isolate independent writers. Parallel execution still needs a
  durable multi-worker supervisor and coordinated integration gates. Dispatch remains
  serialized by the project lease.
- Hard crashes can leave a lock behind. Structured worker leases support explicit, process-checked
  recovery; legacy/ambiguous and Git/review locks still need manual inspection. Never release by age.
- File checkpoints and optimistic checks suit a local single-user tool. Multi-user or multiple
  server processes need transactional updates and coordinated job ownership.
- Native desktop routing, browser dragging/highlighting, and real-account model behavior
  need manual acceptance. Automated subprocess tests use local fake Codex programs.
- The documented installation is an editable repository checkout. A distributable wheel still
  needs packaging work for sample assets and a user-writable session directory.
- Detailed workflow analysis and controlled comparative evaluation remain future increments.
  Managed turns record token, tool and timing observations; GitHub PR merges are explicit.

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

## Adaptive Git branch allocation

Stage delivery uses a revision-bound feature branch starting from the selected local
integration base. The terminal defaults to the checked-out base when enabling a new
cycle's stage branches; an established cycle retains its recorded base.

For each approved stage, the allocator identifies independent tasks owned by different
agents with disjoint declared inputs and outputs. Combined stage checks, dependent
writers and read-only verification use the shared stage worktree. A first eligible
attempt with compatible tracked inputs gets an individual local agent branch and a
worktree under the project's sibling `.orchestrator-worktrees` directory. If the task
needs uncommitted stage inputs, it uses the shared stage instead.

The worker must pass its structured checks and workspace ownership audit before the
orchestrator creates a local draft commit on its agent branch. Integration checks the
stage HEAD, clean index, original input hashes, changed-file set and draft output
hashes, then applies the audited patch to the uncommitted stage tree. Final stage
checks observe the combined files. The stage still requires independent AI review and
explicit user approval before its publication commit or push. Agent branches are local
working history and never pushed by this workflow.

Failed or interrupted isolated attempts retain their worktree for inspection. Explicit
retry runs the affected task on the shared stage with rebuilt context; unrelated
accepted tasks are retained. Worktrees and draft branches remain available locally.
Execution is serialized in this version, with audited isolation as the foundation for
later parallel scheduling.

## GitHub PR delivery

PR creation is bound to the current pushed stage publication receipt, independent
review and user approval. The GitHub origin fetch and push destinations must identify
the same repository. The published stage must contain exactly the reviewed commit
and its approved paths against the recorded stage base. Local PR checkpoints are
revision- and stage-bound; creation discovers a matching existing PR before making
a new one. The generated description is previewed and must match the text approved
for publication. It reuses existing test records and requires no additional inference.

The merge operation reads remote PR identity, head and base hashes, draft status,
review decision, reported checks and GitHub merge state. The terminal obtains
permission for the exact repository, PR number, base and approved head. The service
rechecks all conditions before the GitHub REST merge request with the head SHA
guard. It requests merge-commit semantics to preserve reviewed ancestry and relies
on GitHub's branch rules for server-side enforcement. A changed base blocks a new
merge request; the REST operation pins the head SHA rather than providing an atomic
base-hash precondition.

The merge SHA is checkpointed before local synchronization. A retry observes a
server-confirmed merge and resumes fast-forward fetch/merge and existing stage
acceptance checks. Cycle history records the PR number, base, approved head and
merge SHA. Local and non-GitHub Git delivery remains available.

## Compact planning and model allocation

Requirements gathering and the standard planner receive explicit no-exploration instructions.
The standard plan describes delivery outcomes, dependencies and acceptance criteria rather than
inventing exact file paths or detailed tests. The architect owns technical discovery. It starts
with a local filename-only inventory, ranked by goal terms, capped at 80 paths, 6 KiB of path
metadata, 1,200 directory entries and six levels of directory descent. Excluded private and
generated paths and symlinks are not included. A partial map is labeled incomplete; it is not
evidence of file content or existence at a later time. Compact planning uses the same map.
Inventory changes invalidate architect thread reuse. The architect receives a soft exploration
target (six files, four inspection calls and 16 KiB per file), and all these planning routes
have a hard 64 KiB application-prompt limit. Oversized inputs fail before inference; approved
requirements are never silently truncated. The Codex harness can still inject instructions
and perform tool work; the soft target is not provider quota enforcement.

An explicitly selected `outline` route drafts the scope approval locally from the complete
brief, with a placeholder covering every requirement. This uses no separate planner
inference. It deliberately defers task count, dependencies, models, skills, ownership and
technical checks to the architect. It retains the existing plan and design approvals.
Existing saved plans, compact routing and standard AI planning remain supported.

`cycle plan` uses the local outline and allows the architect to inspect a proposed brief
without marking it approved. The combined review presents the brief and technical design;
one explicit approval records both revisions. The draft-only source check is confined to
planning. Delivery and publication still use the strict approved-source check. If approval
is interrupted after saving the requirements checkpoint, the technical design stays
unapproved until the operator resumes and approves it.

New architect and compact designs bind combined stage checks to a task assigned to
the check owner after all other stage writers. The stable stage/check label is included
in the task check description, and stage verification requires matching command evidence.
Older saved designs retain their existing task checks and historical approvals.

Independent review includes the recorded human stage observation and fingerprints it.
An unchanged completed review is reused, including negative decisions. Explicit
`cycle feature --retry-review` retries review only after feature acceptance.
Usage reports include uncached input where both input and cached counts are available.

Intake returns a route recommendation as part of its existing structured response. The
auto route has a local requirement-count guard. A compact call returns a complete
technical design (or a clarification/standard-route reason) and Python projects the
existing `ScopedTask` fields into the Stage 1 plan. It validates both the full design
contract and compact limits: one agent, one stage, at most three non-complex tasks,
two components and six owned files; no database/storage or multi-domain writers.
The full-route recommendation is conservative; local checks are structural, not a
semantic proof that the brief is simple. A rejected compact result saves a fallback
for this draft and requires explicit resume rather than spending another model call.

The candidate design is saved beside the planning checkpoint under the plan revision
number and digest. After Stage 1 approval, `prepare_design` checks project/source
identity, digest, task projection and design validation. It loads the candidate as
a reviewable Stage 2 revision, not an approval. The same stage review, worker scope,
verification, independent review and publication gates are required as for standard
designs. Existing sessions without a candidate retain their normal workflow.

`model_catalog` opens a bounded local Codex app-server stdio connection, initializes
and requests only `model/list`. It does not read auth files, start a model turn or save
credentials. The user confirms a palette of offered exact model IDs; model-family
preferences are heuristics, not a guarantee of availability or cost. Unknown tiers
use the configured default, and explicit choices can override the suggestion.
`model_allocation` maps task difficulty to the palette and respects additional
architect capability recommendations when task ratings understate the agent's needs.
The revision contains both agent defaults and exact per-task choices; execution,
context and displays use the latter. Changing the model invalidates session reuse.

The optional task-model map is included in the design fingerprint when present.
For legacy revisions, omitting its null value from the digest preserves saved approvals,
branches, publication receipts and archival identities. Applying automatic allocation
to a prior unstarted design creates a new reviewable revision, and started deliveries
or scoped corrections cannot be automatically reallocated. Manual model/roster
choices take precedence. Scoped correction drafts copy approved task models, and
independent review uses the chosen standard/strong tier unless explicitly overridden.

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

The explicit terminal `cycle feature` flow advances the same coordinator through ready tasks
and verifies only structured completed attempts with current file hashes and matching
observed checks. It records the verification source separately; required task-level human
checks are presented at feature acceptance. Read-only prerequisites with human checks
still require manual verification. Failures stop the loop and retries require explicit
operator action. The terminal displays the feature's files, test results and combined gate
before collecting actual user evidence. Independent review and the existing reviewed-content
fingerprint bind the final publication. One publication confirmation authorizes the reviewed
commit and, if configured, its stage-branch push. A local-only receipt cannot authorize a
remote push. Integration merges remain explicit and recorded, with a local-only merge
requiring a recorded user approval.

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
counted. Retry prompts include the latest relevant recovery summary and up to three observed
commands, prioritizing failures, not full transcripts.

New designs select prerequisite summaries and exact producer-owned files separately using
`task_summary` and `upstream_file`. The validator checks the producer, dependency, recipient,
file ownership and required-summary pairing. Legacy `task_output` remains readable and
continues supplying all producer-owned files. A merged correction may retain an archived
summary only when the original assignment declared it, the same agent is its recipient,
and the original design and merge evidence are still valid in this cycle. Structured
handoffs derive a compact summary from the verified report digest, not an arbitrary cached
message or log. Branch eligibility and pending correction checks apply to handoffs.

The execution adapter rebuilds canonical context under the writer lock, irrespective of a
caller-provided preview. It saves a run-local context.json and its digest before starting Codex.
The prompt also has an independent digest. Before subprocess launch, the adapter checks
the package/prompt identities and current source hashes, including applicable instructions
and configured skills. Saved context/prompt identities and audited project source hashes
are checked again during verification/publication.
Older handoff runs use bounded event-tail fallback. Only eligible latest verified outputs
or explicitly verified merged archive handoffs are usable.
Producer source files are current workspace contents, not a Git snapshot of the earlier run.

`wire_context` projects the full snapshot into a compact worker payload: the worker spec
owns checks and acceptance criteria, stage-wide peer metadata is not sent to implementation
tasks, and empty/duplicate resource metadata is omitted. Same-thread prior context is
validated by its saved run and digest. Included local source files of at least 8 KiB use
exact path/hash references even on fresh turns. Required references must be read and checked
before edits; optional references are read as needed. Instructions, skills and required
handoffs remain inline. The outgoing prompt is capped at 64 KiB independently of the
canonical snapshot allowance (or the lower configured package limit). Optional preview
content is removed first when needed. The complete canonical
snapshot and exact transmitted prompt remain available for review.

Context limits are additive session settings with defaults for older sessions. Changing limits
only affects future packages, not design scope or existing run snapshots. Package hashes identify
supplied bytes; they are not a filesystem lock, commit certificate, or token-usage measurement.
Skill lookup supports local SKILL.md files under the project, ~/.codex/skills, ~/.agents/skills,
and ~/.codex/plugins. Remote skill resources and recursive skill-resource bundling are not yet
implemented. Context exclusion heuristics do not guarantee removal of every possible secret.
The Codex sandbox and its own instruction loading remain separate from package selection.

The terminal `cycle context` command inspects or explicitly updates limits without inference.
It includes a fresh worker prompt estimate and the owned-file count. The `cycle benchmark
SESSION RUN_ID` command measures a saved worker prompt against a fresh compact replay
without inference. Workers are instructed to prefer concise pytest diagnostics and targeted
tests, retaining full logs locally where necessary. These are model instructions, not an
enforced output limit on Codex tools.
The usage report records full-versus-compacted prompt bytes and repeated source references;
these figures measure only the application payload and not the CLI schema, cached history
or provider token billing. Independent review shares task definitions once, uses diffs and
verified current-file references for tracked files, includes untracked file content, and
caps command output excerpts while retaining exact commands and exit codes.

## Persistent Codex sessions and usage

`session_manager` stores thread IDs and scope fingerprints in a sidecar per planning/design
checkpoint. The scope includes the resolved checkpoint and project, role, model, design
boundary, and Codex home selection. Worker slots separate the logical agent, workstream and
write/verification mode. The approved design fingerprint and revision prevent a changed
allocation or design from inheriting old conversation permissions. Threads rotate after eight
successful turns, and new cycles/correction deliveries have independent checkpoints.

Begin consumes the prior binding under a short file lock and creates a unique turn receipt.
Only a successfully completed, validated turn with a matching CLI thread event can publish
a new binding. A crash, missing ID, mismatch, or failed turn leaves no reusable binding;
there is no hidden replay. A superseded claim cannot restore an older binding. This uses
POSIX file locking and the existing project-wide writer lock for workers; it is still a
local single-user application, not a transactional multi-user planner. Native Codex's
own history retention and external chat edits are outside this registry.

The CLI adapter uses an explicit UUID with `exec resume`, retains the sandbox and schema on
every call, and never selects the last arbitrary chat. Intake/architect follow-ups hash the
unchanged answer prefix (and intake's saved brief) before sending an increment. A changed
prefix or scope results in a complete new prompt. Workers always rebuild canonical context,
current file hashes and scopes; old history is reference data, not authority. For a same-thread
retry, duplicate historical command output and handoff text are omitted. Review remains
ephemeral and independent.

Managed turns persist observed usage from JSONL completion events, byte length of the
supplied prompt, timing, role/task/stage and session IDs. Missing counters stay null.
New completed turns also count distinct recognized tool/command completion IDs without
retaining tool arguments or output. Historical receipts without counts remain unknown.
`usage_report` aggregates planning/design sidecars across the selected cycle's active and
archived deliveries. It is not an account-billing estimate or a complete trace of ad-hoc
calls. Separate cached/reasoning counters are not added to total input/output. The optional
`ORCHESTRATOR_FRESH_SESSIONS=1` switch provides a fresh-session comparison baseline;
controlled performance evaluation is still required.

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

## Feedback rounds within a cycle

`cycle_feedback` records the two user decisions against the current design fingerprint.
An improve decision binds a tested task, prior attempt, agent, workstream and exact owned files.
The coordinator blocks old verification and gives the selected task priority; execution cannot
dispatch another assignment while the correction is outstanding. The new context package
contains the feedback record and WorkerSpec adds a focused check and human acceptance item.
Original checks still apply; ownership and the prior design remain unchanged. Unrelated task
evidence remains intact. Already-executed dependency or audited-path overlaps block the narrow
retry and require a broader approved plan rather than implicit invalidation.

For a merged assignment, `correction_delivery` creates an independent reviewable delivery
checkpoint within the same cycle. It archives merged stage evidence and ancestry, preserves
the target's agent/model/file ownership, retains unfinished tasks and creates read-only
regression assignments for affected merged consumers. History resolves both old and current
session names, but only the current delivery can dispatch. Dirty or committed-unmerged work
blocks the transition. Archive evidence is checked again at cycle completion. New cycles and
correction delivery rounds require explicit branch setup before any worker can run.

Goal acceptance binds the final stage evidence after other stages have recorded merges.
It changes the review fingerprint and is sent to the independent reviewer. Final commit
preparation appends the note, cycle number, timestamp and acceptance ID, even for a no-change
verification stage. Completion checks the merged commit contains the acceptance record.
No review, retry or publication runs merely because feedback is recorded. Legacy cycles retain
their original completion behavior until feedback rounds are enabled; new cycles enable them.
Feedback rounds are local immutable history entries, not a new approval or push authority.
