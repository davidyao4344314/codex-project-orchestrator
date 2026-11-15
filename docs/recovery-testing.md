# Recovery and resume acceptance

Use a disposable project. Tests mock model calls or use local fake workers; real retries/reviews
use your account. You can perform browser checks yourself.

1. Save an unanswered intake question, close/reopen the app, choose Resume project. Expect the
   saved question and Requirements page; no model call until you answer or explicitly continue.
   Repeat for an architect question, requirements/design review and approved design.
2. During a live worker, open Resume project. Expect active work and no recovery/second writer.
   Stop through the owning app normally if possible; confirm partial code remains inspectable.
3. In a disposable run with persisted worker metadata, stop the app and its worker/process group.
   On reopening, inspect files/processes. Expect recoverable only when both owner and worker/group
   are absent. Confirm your inspection and Recover interrupted worker. The run becomes stopped,
   verification stays false and the lock clears; source files and earlier logs remain intact.
4. Retry explicitly. Inspect Exact context sent to Codex: recovery_brief should identify the prior
   attempt, its error/summary and historical commands, plus current owned-input hashes. Expect a
   new run ID and all required tests rerun. No old conversation is blindly resumed.
5. Complete and verify a task, then edit its audited source. Resume project must flag stale output
   and recommend inspecting/rerunning, rather than treating the earlier test pass as current.
6. Interrupt a review. After inspecting any orphan process/lock manually, resume: unfinished AI
   decisions stay blocked. Only an explicit new review can create fresh AI approval.
7. Prepare a local reviewed commit but do not push (or simulate failed authentication). Reopen and
   Resume project: expect existing commit and fresh push-permission guidance, no duplicate commit
   or automatic push. Change origin/HEAD/evidence and expect a publication-needs-review warning.
8. For a managed stage already pushed, expect merge/refresh/record-merge guidance. Once stages are
   merged, final acceptance is recommended rather than launching more work.
9. Legacy/partial lock text or an incomplete worker PID must stay blocked with manual-inspection
   instructions. Do not expect age-based cleanup. Never remove a lock until all writers are stopped.

Automated coverage checks live processes, stale and replacement leases, explicit confirmation,
missing locks, bounded retry history, secret filtering, preservation of partial files, stale source,
publication/destination checks and navigation across fresh Streamlit sessions. Recovery never kills
processes, reverts edits, runs inference, changes branches or grants push permission automatically.
