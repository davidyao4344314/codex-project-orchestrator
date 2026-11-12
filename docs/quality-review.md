# Code quality and structure review

Reviewed 13 November 2026, after the worker implementation.

The current structure is suitable for continued single-user development. Contracts, planning
rules, context selection, process execution, evidence reconciliation and Streamlit views have
separate modules. All six worker profiles reuse one execution harness. A separate framework or
large rewrite is not needed for the next increment.

The most consequential defects were in the evidence used to accept work. The following fixes
are included in this review.

| Finding | Impact before the fix | Change |
| --- | --- | --- |
| Shell command normalization lost arguments and quotation semantics | A passed command could be counted as evidence for a different reported command | Require the exact command string and observed exit code |
| Only initial unreadable owned files blocked completion | A worker could leave an unreadable output and still receive completed status | Check assigned inputs/outputs in both initial and final snapshots |
| Audit file replacement and growth were not handled safely | A substituted FIFO could block an audit; a growing file could exceed its remaining read budget | Use nonblocking/no-follow descriptors, validate regular-file identity and read stability, and cap reads by the remaining budget |
| Verification bound the report but not the full specification or final snapshot | Altered watched paths or saved hashes could bypass stale-output rejection | Record and check specification/final-snapshot digests; check the specification before launching Codex |

Regression coverage is in tests/test_worker_quality.py. It exercises shell arguments, quoting
and wrappers; post-run unreadable outputs; a FIFO replacement; growth before/during reads;
modified specification/final snapshots; rejection before subprocess launch; and compatibility
with older records. The existing fake-worker integration tests also verify that new runs record
both additional digests across every worker profile.

Validation: 161 tests passed with the web extra installed; Ruff lint and format checks passed.
The tests use local fake workers and do not spend model usage. Real-account execution and native
chat routing still need the manual acceptance described in worker-testing.md.

## Maintenance priorities

1. Preserve the current contracts/services/views separation. Add workflow rules to services and
   call them from both interfaces. The largest UI modules, web.py and dashboard.py, are the next
   candidates for focused extraction when their forms or navigation change.
2. Before parallel execution, add isolated worktrees and a durable job supervisor. The present
   background threads, project lock and local JSON checkpoints deliberately serialize writers;
   hard-crash recovery remains manual. Do not add parallel writers to this harness.
3. Before multiple users or server processes, introduce transactional state updates. Atomic file
   replacement and stale-snapshot checks do not make a multi-process transaction.
4. Extend type annotations through execution and planning services as those modules change.
   Persisted structures already have validated models; context metadata still uses dictionaries,
   so a typed assignment contract would help when that format next evolves.

## Practical limits

Command evidence proves that a recorded command exited, not that its tests adequately cover the
requirement. Exact matching can conservatively block a report that reformats an equivalent
command; the worker should copy the actual event command. Human task/stage acceptance remains
necessary. Verification is performed at acceptance time, not continuously after later edits.

Workspace audits observe bounded net changes, not every write. Ignored files, reverted writes,
outside-project activity and directory substitution races are not completely covered. Owned
paths are checked after execution within the CLI sandbox, rather than enforced per file.
Artifact hashes detect inconsistencies; they do not prevent an actor with write access from
altering run.json and its hashes together. Legacy runs retain their original, weaker checks.

This review does not establish a token, cost or latency improvement over ordinary Codex. Those
claims require the planned workflow analysis and comparative evaluation work.
