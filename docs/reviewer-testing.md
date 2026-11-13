# Reviewer and gated publication acceptance

Use a disposable project and remote. Model reviews use your Codex account; automated tests mock
all model calls and pushes. No native browser automation is required.

1. Finish a worker stage, review actual tests/manual behavior, verify its tasks and accept the
   stage. Open Dashboard → Git connection → Preview accepted stage changes.
2. Expect human approval by default. Run independent review should be available; Review and
   publish if approved should be absent. Refreshing must not start inference or publish.
3. Choose Run independent review. Expect a separate review with requested/reported model,
   decision, findings and any additional human checks. Load reviewer input should show selected
   files, full diff, requirements, scope and command evidence. This action alone must not commit.
4. For human approval, inspect the preview and model findings, tick the review confirmation and
   commit. The saved receipt must identify that user approval, and push remains a separate action.
5. To test agent publishing, create an orchestrator/ development branch in Review and publishing
   settings. Select a model, enable reviewer-approved commit and push, and save. Preview again
   because branch changes invalidate the previous preview.
6. Choose Review and publish if approved. If approved, expect a local commit of selected files
   and an ordinary push of its displayed SHA to that development branch. Verify on your remote.
   If changes_requested or blocked, expect findings and no commit/push. There is no automatic merge.
7. Disable reviewer publishing and save: the combined action should disappear. A reviewer-based
   unpushed receipt must not push while the policy is disabled. Human approval remains available.

Automated tests cover:

- Missing, blocked, superseded or stale approval prevents commits.
- Main/master cannot opt into reviewer publication; destination changes invalidate policy use.
- Code or evidence changed during/after review invalidates approval; post-commit evidence changes
  prevent pushing the receipt.
- Required human checks cannot coexist with reviewer approval.
- A reviewer error leaves a blocked attempt, releases the writer lock and does not publish.
- A user can approve independently after reviewing a model's objections; stage prerequisites
  and structured worker verification remain required.
- Oversized inputs block inference instead of silently omitting content.
- Dashboard refresh does not call a model or publish.

If push fails, inspect the existing local commit and authentication, then retry using Push
committed AI changes. Do not rerun the worker merely to recreate the same commit. A stale review,
changed source, newer attempt or revoked policy needs new review instead. Older publication
receipts without approval must be inspected in the terminal; the app does not fabricate approval.
