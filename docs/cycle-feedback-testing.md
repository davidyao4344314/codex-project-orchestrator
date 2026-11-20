# Terminal feedback rounds acceptance

Use a disposable project with independent frontend and backend tasks, or an existing tested
stage containing a frontend assignment. All commands use the actual cycle session filename
and stage/task IDs displayed in the saved design.

1. Run and verify tasks normally. Before committing the stage, run
   `orchestrator cycle feedback SESSION --kind improve --task FRONTEND_TASK`.
   Enter a small frontend-only adjustment. Expect a timestamped record showing the same cycle,
   assigned agent, exact owned paths and prior attempt. No agent starts just by recording feedback.
2. Run `orchestrator cycle deliver SESSION status --stage STAGE`. Expect the affected task
   blocked for targeted feedback and untouched independent tasks still verified.
3. Run `orchestrator cycle deliver SESSION retry --stage STAGE`. Expect only the selected
   assignment to run, with the old scope and the feedback check. Inspect its run.json,
   context.json and worker_spec.json. Verify the result and redo combined stage acceptance.
4. Repeat with another adjustment before committing. Expect a new timestamped round, not
   a new cycle or a full project replan. A correction to an already-executed dependency
   or overlapping audited file must be blocked for a wider scope decision.
5. After all earlier stages are merged and the final stage has passed checks, record
   `orchestrator cycle feedback SESSION --kind goal-achieved --stage FINAL_STAGE`.
   Enter your final acceptance note. Expect a fresh AI review to be needed (older review
   fingerprints are stale). Run review, commit, then push with explicit permission.
6. Inspect the final Git commit message: it must include cycle number, feedback, UTC timestamp
   and acceptance ID. A verification-only stage gets a metadata-only commit. Declining push
   permission leaves it local. Merge and record the merge, then finish the cycle.
7. Run `orchestrator cycle history SESSION` to inspect feedback rounds, outcome, timestamps and
   commit SHAs. Starting the next cycle must retain this history and ask for a new goal.

## Four delivery regression checks

| Check | Action | Expected result |
| --- | --- | --- |
| Branch gate | In a new cycle, approve the design/stage but try `cycle deliver SESSION run --stage STAGE` before enable/prepare. | Nonzero exit and branch-setup guidance; no worker runs on main. After enable/prepare, status is ready. |
| Merged feedback | Merge an implementation stage, then request an improvement on its task during later testing. | New correction session and stage ID, same cycle number; archived commits stay unchanged. Approve the scoped design, enable/approve/prepare, then retry. Only the requested task writes files; affected merged tasks are read-only regression checks. |
| Shared file | Plan two dependent, ordered assignments owning the same file; run and verify both before accepting the stage. | Combined stage acceptance/review can proceed against the final file. Editing that file manually between attempts or after verification blocks acceptance/publication. |
| Failure | In a disposable task with a reproducible failed check, dispatch or retry. Run `echo $?` immediately afterward. | Status failed/blocked, diagnostic and evidence folder, exit code 1 (130 if stopped). The next stage is blocked until the task passes and is verified. |

Use the [README terminal walkthrough](../README.md) for the actual setup and end-to-end
commands. The merged-feedback command prints the new session path and correction stage ID
to use from that point onward. Check `cycle history` through the original session too;
the final cycle should contain both original and corrective commit SHAs.

Automated tests use fake workers, fake reviewer calls and mocked publication. Real model
behavior, manual application checks and Git hosting integration are still user acceptance tasks.
