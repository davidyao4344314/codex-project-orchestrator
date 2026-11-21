# Persistent-session acceptance tests

The quick offline checks do not use your Codex allowance:

```sh
source .venv/bin/activate
python -m pytest -q tests/test_session_manager.py tests/test_persistent_planning.py \
  tests/test_persistent_workers.py tests/test_usage_report.py
```

For manual testing, use the disposable notes project in the README. Use the actual
session and stage IDs printed by the orchestrator; avoid running against a real project
until these checks pass.

| Test | Expected result |
| --- | --- |
| Answer a second requirements question | Terminal says resuming the first requirements ID, sends the new answer, and prints observed usage if supplied. |
| Save and reopen during clarification | Same eligible requirements/architect ID resumes despite restarting the terminal. |
| Move from intake to planning or architecture | Different thread ID; earlier approved data is supplied, not the other role's full conversation. |
| Complete two tasks for the same backend agent in the same approved design | Second run says resuming the first backend ID; fresh current files and ownership are supplied. |
| Run a frontend or database assignment | Different thread ID even if the logical agent also handles backend tasks. |
| Run read-only verification | Separate read-only thread; no worker write permissions are carried over. |
| Fail a resumed run in a disposable project | Failure is recorded; next explicitly requested retry starts fresh. No extra automatic model call. |
| Run independent AI review | A fresh read-only ephemeral call; no implementation chat reused. Normal push permission is still required. |

After the calls, inspect local records:

```sh
orchestrator cycle sessions "$SESSION"
orchestrator cycle usage "$SESSION" --details
```

Expect per-role bindings and turn records with `thread_id`, `requested_thread_id` (null
for fresh turns), `status`, `prompt_bytes` and token usage if reported. Previously saved
plans are readable but acquire a binding only on their first new successful managed turn.

For a small controlled comparison, use two equivalent disposable copies of the same
starting repository, the same goal, answers, model settings and acceptance checks. In
the baseline terminal set `export ORCHESTRATOR_FRESH_SESSIONS=1`; leave it unset in the
persistent-session terminal. Compare measured input, cached input and output, elapsed
time, failures and quality. Stop if resources are exhausted; neither mode is guaranteed
to save usage. Missing observed counters and untracked/ad-hoc calls mean these reports
are not a substitute for the account usage dashboard.
