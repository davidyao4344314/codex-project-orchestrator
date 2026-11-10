# Project roadmap

## Workflow Analyzer (工作流分析)

Capture a timeline of Codex execution once task execution is available. Show which steps ran,
which actions repeated, and where retries or failures accumulated. Use reported token usage
to flag expensive tasks and investigate repeated work. Keep task, run, and step IDs so the
report can point back to the evidence.

## Task Planner (任务规划)

Stage 1 breaks requirements into tasks, validates dependencies, groups tasks that can become
ready together, and recommends whether several agents would help. Before running tasks in
parallel, account for shared files, interfaces, and other resources. Later, use the plan to
assign a specialist, skills, and a model suited to each task.

## Performance Optimizer (性能优化)

Use analyzer findings to propose specific changes. Examples include consolidating redundant
tool calls, handing agents only the context they need, reusing file reads when the files have
not changed, and running focused checks before slower integration tests. Evaluate each change
against a baseline before making it the default.

## Evaluation Engine (评估引擎)

Run representative tasks before and after an optimization with comparable inputs, model
settings, and starting repository state. Track these measures for each run:

| Measure | Definition |
| --- | --- |
| Task completion time | Elapsed time from start until the task reaches a terminal result. |
| Token consumption | Provider-reported input and output token usage. Mark unavailable usage explicitly. |
| Tool calls | Total calls, with breakdowns by tool and task. |
| Error rate | Failed calls divided by attempted calls, with failed tasks reported separately. |
| Test pass rate | Passed tests divided by executed tests. Report skipped and unrun checks separately. |

Compare efficiency alongside task outcomes and acceptance checks. Keep the baseline, changes,
and measurements available for review.
