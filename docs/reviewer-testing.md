# Mandatory AI review and user permission acceptance

Use a disposable project and remote. Model reviews use your Codex account; automated tests mock
all model calls and pushes. You can test the real browser yourself.

1. Finish worker tests, verify tasks, and accept the stage. Open Dashboard → Git connection and
   Preview accepted stage changes. Commit stays disabled until a current AI review approves.
2. Choose Run independent review. Expect a separate reviewer decision, model attribution,
   findings and Load reviewer input. No commit or push occurs from this action, even with an
   older allow_reviewer_push setting saved. There is no automatic-publishing control.
3. For changes_requested or blocked, expect no commit. Human file confirmation cannot bypass
   this result. Address issues, rerun checks, accept the stage and obtain a new AI review.
4. For approved, confirm the file review and choose Commit reviewed AI changes. Expect a local
   commit and a permission prompt showing AI review, exact SHA, branch and remote. No push yet.
5. Refresh or leave the page without granting permission: expect no remote change. The local
   commit remains available. Refresh must not call a model or publish.
6. Tick I give permission to push this commit to the destination shown above, then click Approve
   and push this commit. Expect only the displayed commit/refspec to be pushed (including its
   prior branch history). Verify the SHA on your remote. No force push, merge or deployment.
7. If authentication fails, the commit remains local. Resolve credentials and deliberately
   retry the permission/push action. Do not rerun workers just to recreate the commit.

Automated tests verify mandatory AI review, failure and stale-evidence rejection, independent
model context, bounded inputs, old automatic-push settings being ignored, a disabled push button
until permission is checked, direct service calls without permission being rejected, and rejection
of permission for a different SHA, branch or remote. Legacy receipts without required review
bindings cannot be pushed through the app; inspect those commits in the terminal.
