# Worker agent acceptance checklist

The worker implementation is ready for manual review. Automated tests use fake Codex
subprocesses; this checklist exercises your real Codex login and makes real project edits.
Use disposable projects/data. Workers run sequentially and require approved plans.

## Start the supplied project

1. Open the local planner at http://127.0.0.1:8501. Choose New project, then **Try worker
   test project**. Expect a separate Git repository under plans/samples/worker-smoke-…,
   the Worker smoke name, and a notes-app brief. No inference should start yet.
2. In the copied folder, prepare its environment using the starter README:

   ```sh
   python -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

3. Create the session, continue planning, review/approve requirements, and create the technical
   design. The brief explicitly requests database, backend, frontend, integration, general,
   and read-only verification assignments. Check their workstream labels and owned files.
   Logical agents may be reused; the number of processes remains one at a time.
4. Inspect AI team settings and each model. Use default or an accessible model ID. Review any
   proposed allocation before applying it, then approve the resulting design and stage plans.
5. On Dashboard, choose the first stage in Execution coordinator and Run next ready task.
   Inspect task and verification opens its workspace. Worker profile should match its task;
   assignments with no owned files should select verification and a read-only sandbox.

## Review every worker type

| Worker | Expected code/output | What you should check |
| --- | --- | --- |
| Database | storage.py and focused temporary-SQLite tests | Inserts/listing work; fresh connections retain notes; parameterized SQL; no personal DB touched |
| Backend | service.py and validation tests | Blank/whitespace titles fail; valid titles are trimmed; service calls the approved storage API |
| Frontend | app.py and focused AppTest checks | Add form, useful empty/errors, newest-first list; manually try the browser flow |
| Integration | tests/test_integration.py | Full service/storage contract works with disposable data and existing interfaces |
| General | Updated README | Setup, run, storage location, and test commands match the actual finished project |
| Verification | Structured findings/check report, no code edits | Uses read-only; executes planned commands, records actual results, leaves source unchanged |

The planner decides the precise file names and stage graph. Review that design before running;
these names describe the supplied test brief. Do not approve unexpected extra features.

## Check each completed run

1. Expect starting → running → completed, then **verification pending**. A successful CLI turn
   alone must not mark the job verified or start a dependent task.
2. In Worker report, check its task/agent/profile, summary, reported file changes, check outcomes,
   handoff, and manual checklist. Context package used for this run should match that task/model.
3. Select Load worker evidence. Expect observed command strings, exit codes/output, matching
   check IDs, observed changed-file paths, and bounded diffs for readable owned text files.
   Compare these with the report and actual source. An exit code of zero is not proof that
   the command adequately tested the requirement; inspect the test itself.
4. Use Open chat in Codex to review its actual conversation if needed. Native routing is a
   manual integration check. Source previews show current files; saved artifacts show that run.
5. Perform the required human checks. Only then tick the task verification checkbox and select
   Accept verification. Return to the coordinator to dispatch the next ready task.
6. After all stage tasks are verified, review combined changes and enter real review/test
   evidence before accepting the stage. Dependent stages must stay blocked before that gate.
   No worker or coordinator should automatically commit, merge, push, or deploy.

## Browser checks for the finished notes app

Run `python -m streamlit run app.py` in the copied project's environment.

- An empty database shows a helpful empty state.
- A blank/whitespace title shows validation feedback and creates no note.
- Add two disposable notes; the newest appears first with the expected title/body.
- Close/reopen the browser and stop/restart the app: notes remain in data/notes.sqlite3.
- Run `python -m unittest discover -s tests -v`; inspect focused storage, service, screen,
  and integration coverage. Test databases should be temporary rather than your real notes file.
- Follow the final README from a fresh terminal and confirm the commands work.

## Failure and recovery checks

| Check | Expected result |
| --- | --- |
| Pause/stop an active disposable run | Process stops, partial edits remain visible, project unlocks, verification stays pending |
| Resume coordinator | Clears pause only; dispatch/retry still requires a separate explicit click |
| Missing required context/skill | Worker is not launched; preview explains what needs resolving |
| Failed automated check | Worker fails; downstream work and verification acceptance stay blocked |
| Report says passed without matching command evidence | Runner blocks the result; inspect violations and retry after fixing the cause |
| Unreported or out-of-scope file edits | Runner blocks verification and shows observed paths; changes remain for inspection |
| Invalid/missing JSON report | Run fails with diagnostics; it does not become a usable verified handoff |
| Worker reports an unavailable runtime/input | Blocked outcome and reason; provide the missing input and explicitly retry |
| Edit an audited input/output after worker completion | Accept verification rejects stale output; rerun against the changed source |
| Refresh/reopen a run | No new job; report, context, logs, and prior attempts remain available |

Unsupported pass claims, malformed reports, unauthorized edits, timeout handling, and all six
profiles are covered automatically with fake workers. You do not need to deliberately prompt a
real worker to violate scope just to test those cases. Review pause/retry and ordinary failure
behavior in the disposable sample.

## Artifacts and limits

Each run saves context.json, worker_spec.json, result_schema.json, result.raw.json,
worker_result.json (when validated), worker_evidence.json, workspace_before.json,
workspace_after.json, events.jsonl, stderr.log, and run.json beside the design. Normal generated
sessions/logs are under ignored plans/. They are not pushed to Git.

The audit observes net changes to owned files and Git-visible changed/untracked files. It
compares against the starting dirty state so pre-existing edits are preserved. Ignored files,
changes reverted before the final snapshot, and writes outside the project are not exhaustively
audited. Outside a Git root, only declared owned/context paths can be checked. Writable workers
retain the project-wide workspace-write sandbox: ownership violations are detected afterward,
not prevented per file, and no automatic rollback occurs.

Audit budgets are 2,000 paths, 4 MiB/file and 64 MiB of hashing per snapshot, 24 KiB text/file,
and 96 KiB total diff preview. Command evidence retains the latest 200 completed command events
with bounded output previews. The JSON result limit is 256 KiB. Large/special owned files may
require manual investigation; raw Codex logs remain local. Existing pre-contract runs retain
legacy user-attested verification rather than acquiring fabricated structured evidence.

This completes the worker layer. Independent stage-wide AI review, automatic regression grading,
parallel worktrees, durable crash supervision, and merge automation are separate increments.
