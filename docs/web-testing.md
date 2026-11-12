# Browser interface checks

Start the local interface from the project environment:

```sh
python -m pip install -e '.[web,dev]'
orchestrator web
```

Open http://127.0.0.1:8501 on the same computer.

1. Select **Try sample project**. Expect the Reading list name, complete brief, and a
   new independent local sample folder to appear. This starter deliberately has no
   book-management implementation yet.
2. Select **Create planning session**. Expect a saved Stage 1 session. Switch to
   Technical design before approving it: the interface should ask you to approve Stage 1.
3. Return to Requirements and select **Continue with Codex**. Expect a question or a
   reviewable brief and plan, with the reported response model. Answer any questions.
   Check that the scope excludes accounts, cloud deployment, and external book APIs.
4. Approve the requirements, then open Technical design and create it. Expect a technical
   structure, model recommendations, agent responsibilities, declared context, staged
   tasks, and verification gates with automated and human checks.
5. Review a stage's **Context and handoff** and **Your checks before merge**. These are
   proposed future checks, not claims that the reading-list features were built or tested.
   Required missing context must block design approval.
6. Approve a valid design, then change an agent's model allocation. Expect a new revision
   needing approval. Request a scope-preserving revision and inspect its updated workflow.
7. Refresh the browser or restart the server. Reopen the saved session from the sidebar.
   Expect the saved approvals and revision history to remain, with no new Codex request
   until you explicitly continue or revise.

Use **Check connection** to confirm Codex sign-in. An unavailable CLI should direct you
back to `orchestrator setup`; a failed model request should preserve the saved session
and offer a retry. Credentials and generated sessions are excluded from Git.

Automated interface checks (mocked Codex calls, no model usage):

```sh
python -m pytest tests/test_web.py -q
```

The complete suite also verifies the CLI and shared planning, context, and approval rules:

```sh
python -m pytest -q
ruff check src examples tests
```

## Stage feedback and explanations

Each delivery stage offers Save stage feedback, Approve stage plan, and Revise stage
with AI. Feedback is saved to the design session; requesting a revision sends that
stage's feedback to the architect within the approved requirements. Dependencies may
require related stages to change. Every new design revision needs fresh stage approvals.
Saving change feedback also clears overall design approval. Stage-plan approval does
not certify implementation, tests, or merges.

Highlight ordinary design text with the mouse and choose Explain with AI from the popup.
The explanation should describe the purpose, a beginner-friendly example, and verification,
with model attribution. The expandable explanation box also accepts pasted text.
Explanations use the architect model through the existing read-only Codex connection;
they do not revise the design. Each click makes a model request. Selections are limited
to 4000 characters. Tables use their own selection controls; copy a table passage into
the text box if browser highlighting is unavailable.

## Project home and agent dashboard

1. Choose Home. Expect project cards; open Reading list using Open dashboard.
2. Expect Frontend, Backend, and Database panels showing the assignments declared by
   the current design. Empty areas should say no dedicated tasks. A single owner may
   cover several areas; shared and integration tasks appear in their own list.
3. Choose Open agent workspace or select an assignment. Inspect Context: expect the
   agent's responsibility, skills, boundaries, assigned requirements, declared references,
   and missing-context actions. This is supplied prompt context, not a filesystem sandbox.
4. In Agent chat, ask “Explain your approach and show a small proposed Python example.”
   Expect a read-only response and its model attribution. Reopen the assignment: the
   conversation should persist. Other tasks and design revisions have separate histories.
5. Open Code output. Expect an honest empty state for execution-generated code. If a file
   already exists in that assignment's owned paths, choose it and View source. The preview
   shows existing local source; it does not claim that an agent generated it.
6. Returning to requirements or technical design should preserve the existing approval,
   feedback, revision, and model-allocation controls.

The dashboard does not launch implementation agents or report invented running tasks.
Agent execution, streaming tool activity, generated diffs, and test/merge status remain
future work. Chat requests use the assigned agent model through the existing Codex login.

## Implementation, task moves, and native Codex chat

These checks supersede the earlier planning-only dashboard checks above.
Use a disposable sample project: implementation runs make real edits through your Codex login.

1. Approve the technical design and the stage plan containing your first assignment. In its
   dashboard workspace choose Start implementation. Starting without either approval should
   produce a helpful error and leave the project unchanged.
2. Expect starting/running status, a recorded thread ID, and live Codex messages/tool events.
   Open Exact context sent to Codex: inspect and download the full supplied prompt.
3. Choose Open chat in Codex. Your installed desktop app should open that persistent chat,
   with the assignment context in its initial user message. This native routing is a manual
   integration check; automated tests validate the launcher arguments with a mocked launcher.
4. Try starting another task or reassigning while the run is active: it should be blocked.
   Stop run should reach stopped before the project unlocks. Existing code changes remain.
5. If the design has a single agent, use Reassign this task or create another agent to add
   a role/model. Then drag a task between the two agent lanes, or use the destination selector.
   Expect a new reviewable revision, fresh approvals required, preserved context/run history,
   and updated assignment ownership. Review copied skills and boundaries before approval.
6. Finish a run. Inspect its actual tool output and source files; perform the required manual
   tests. Only then select the verification checkbox and Accept verification. A dependent
   task must remain blocked until prerequisite tasks have accepted verification. Merge/review
   evidence is still a human responsibility; no automatic merge occurs.
7. Reload the page. Expect persisted run history, code files, and supplied context. Use the run
   selector to distinguish historical revisions from current work. Opening Codex adds its own
   configured instructions; our prompt preview shows only the context supplied by this app.

Automated runner tests use local fake Codex subprocesses and make no model requests. They
exercise approval gates, project locks, event persistence, success/failure status, cancellation,
reassignment revisions, and native chat URL construction. Browser interaction and native app
routing should be checked manually using this checklist.

## AI team sidebar and task allocation

1. Open a project with a complete technical design. Expand the sidebar if it is collapsed.
   Expect AI team, the current design's agent count, and Number of AI agents (1–8).
2. Choose two agents. Set roles such as Python developer and Test specialist, then choose
   each model or leave default. Changing these settings should make no model request.
3. Select Suggest task assignments. Expect a planner response with model attribution,
   a complete task mapping with reasons, and verification owners in the full details.
   If there is too little work for the chosen count, unused agents should be rejected;
   choose fewer agents or give them meaningful verification roles.
4. Inspect the proposed team and select Apply suggested team. Expect one new design
   revision, the exact chosen agent count/models, unchanged task scope and dependencies,
   and fresh design/stage approval required. No implementation should start automatically.
5. Open an assigned task: check its model, skills, boundaries, context, and assignment reason.
   Reopen the project: settings and applied ownership should persist.
6. An active project run must block new allocation requests and application. A proposal
   made before another design revision must be rejected instead of overwriting new work.
   Required human stage review must remain in place.

This feature chooses logical agent responsibilities. Concurrent writers remain unsupported.
Automated sidebar tests mock the planner and verify explicit requests and review-before-apply.

## Automatic assessment defaults

Open an existing technical design and inspect Automatic job assessment in the sidebar.
Expect task/stage counts, overall difficulty (the highest task difficulty), recommended team
size, and Why this team with per-task capability reasons. Planner recommendation should be
selected by default, with team settings read-only. Switch to Custom team to edit the count,
roles, or models and use the existing proposal/review flow. Refreshing should not call Codex.
Manual team revisions should preserve the architect's original recommendation. A newly
requested architect revision should replace it with that revision's assessment.

## Stage 3 coordinator manual acceptance

Use a disposable sample project. These controls perform real Codex work and edit its files.

1. Approve the current technical design and the first stage's plan. On Dashboard, choose
   that delivery stage in Execution coordinator. Expect ready tasks and queued dependencies.
2. Select Run next ready task once. Expect exactly one assignment to start using its assigned
   agent/model/context. Refresh: no second task should start. Inspect task and verification
   should open the matching workspace with output, code, and its Codex chat button.
3. When the task completes, run its required manual checks and accept task verification in
   the workspace. Return to Dashboard: the next eligible task should become ready. Dispatch
   it explicitly. Until acceptance, the coordinator should report awaiting verification.
4. Pause during a disposable run. Expect a saved paused state and a stop request. Wait for
   the process to stop. Resume should only clear the pause; Retry failed or stopped task is
   a separate explicit action. Edits already made should remain available to the new attempt.
5. After all stage tasks have accepted verification, inspect the required stage evidence and
   human-check list. Enter actual review/test evidence, check the confirmation, and accept
   stage verification. Expect accepted status; this does not merge the code.
6. Select a dependent stage. Its dispatch must stay blocked before prerequisite stage
   acceptance. Once its prerequisites and its own plan/design approvals are satisfied,
   it may dispatch a ready task. The individual-task Start implementation control must also
   enforce prerequisite stage acceptance.
7. A failed/stopped run must block ordinary dispatch and offer explicit retry. Reloading
   should retain history and pause/acceptance checkpoints. New design revisions must not
   inherit the old coordinator's acceptance or verification records.

Automated tests use fake runs/subprocesses and mocked model calls. They check scheduling,
verification waits, pause/resume, retry, cross-stage gates, revision isolation, read-only
refreshes, task-workspace navigation, and stage evidence persistence. Real account behavior
and native chat routing remain manual checks.

## Stage 3 context builder manual acceptance

1. Open an assignment's workspace and Context tab. Select Build context preview. Expect the
   approved requirements/contracts, scope, owned paths, checks, skill references, and only
   relevant selected source files. Inspect a file: expect its contents, status, hash and reason.
   No model request or implementation should start. Download context package should return JSON.
2. In a disposable project with a declared required input, temporarily rename that input.
   Rebuild the preview: expect a missing blocker. Dispatch must fail before a worker starts.
   Restore the input and rebuild to clear it. Missing optional inputs should only warn.
3. Inspect configured skills. A readable SKILL.md in the project or configured local skill roots
   should appear. A missing configured skill should block; a null location should show unconfigured.
   Credential files/binary files/outside-project source links should not be supplied automatically.
4. If a required file is too large, expect truncated/omitted status and a blocker. Increase
   Context budgets or narrow the declared input. Save budgets, then rebuild: expect the new limits
   to persist without inference, starting work, or altering an earlier run's saved package.
5. For an assignment with required task_output context, build a preview before its producer
   is verified: expect pending context. Run and verify the producer, then rebuild the consumer's
   preview. Expect the final handoff summary, verified source run ID, and produced source files.
   Failed/stopped/unverified runs and unrelated agent conversations must not appear as handoffs.
6. Dispatch a ready assignment. Inspect Context package used for this run and the exact prompt.
   Expect a saved package/digest for the inputs used at dispatch. Rebuild the live preview after
   changing a disposable source file: the live hash should change; the earlier snapshot should not.
7. Send a plan-discussion message. It should use a discussion package and describe missing
   prerequisites rather than claim they exist. Long same-task history is bounded and records
   omission counts in the request; other tasks' chat history is excluded.

Defaults: 128 KiB package, 24 KiB/file, 16 KiB/handoff, 32 resources. UI budgets are explicitly
saved; previews are explicitly rebuilt. Snapshot inspection is explicitly loaded so polling
avoids repeatedly resolving source files. Full code and raw logs remain local. Context filtering
only controls the supplied package; it does not change the Codex sandbox or native instructions.
