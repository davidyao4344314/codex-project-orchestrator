# Planning optimization acceptance tests

Offline tests (no Codex model usage):

```sh
source .venv/bin/activate
python -m pytest -q tests/test_compact_planning.py tests/test_model_catalog.py \
  tests/test_model_allocation.py
```

## Test a simple project

In the orchestrator directory, use a new disposable project:

```sh
mkdir -p plans
PROJECT="$(mktemp -d "$PWD/plans/counter-smoke.XXXXXX")"
orchestrator cycle new "$PROJECT" --goal "Build a Python recursion counter from 0 to 5 and test it."
```

Set `SESSION` to the actual printed session path. Use the catalog palette if available:

```sh
orchestrator cycle models "$SESSION"
orchestrator resume "$SESSION"
orchestrator design "$SESSION"
orchestrator cycle usage "$SESSION"
```

Answer only essential questions. A simple well-defined counter should use the compact
route. Expect `Compact planning: drafting the tasks and technical design together` and,
after approving the plan, `Using the saved compact design; no additional architect call
is needed.` Inspect and approve the design separately. Usage should show requirements
and compact-planner turns, not a separate architect turn. If the auto recommendation is
standard, a new small test can request `--route compact` explicitly.

## Test the full route and model allocation

| Check | Expected |
| --- | --- |
| Plan a notes app with SQLite, backend and interface | Standard planner and architect; no dropped database tasks. |
| Discover models with `cycle models SESSION` | Exact model IDs and proposed routine/standard/strong mapping. Declining leaves the session unchanged. |
| Read saved palette with `cycle models SESSION --show` | No model discovery/inference; displays confirmed settings. |
| Apply the palette to an unstarted design with `design SESSION --auto-models` | New reviewable revision when models differ; no inference needed to allocate an existing complete design. |
| Inspect task models in the design | Routine, moderate and complex assignments get different models when the palette has distinct choices, even for one agent. |
| Change a model manually in design review | Explicit per-agent choice overrides automatic task routing, with renewed approval. |
| Try automatic reallocation after delivery started | Blocked; existing branch/test evidence is preserved. |

Inspect `cycle usage SESSION --details` to confirm the model requested per worker turn
and compare the model groups. Do not assume a smaller prompt or model always saves
allowance: compare equivalent repositories, requirements, tests and quality. Model
availability and real-account performance need manual acceptance.
