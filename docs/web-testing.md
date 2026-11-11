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
