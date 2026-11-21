# Context correctness and efficiency

Run the offline context regression tests:

```sh
source .venv/bin/activate
python -m pytest -q tests/test_context_integrity.py tests/test_wire_context.py \
  tests/test_context_cli.py tests/test_compact_review.py tests/test_archived_context.py
```

No real Codex turns or remote pushes are used in these tests. They exercise the fake worker
and local Git fixtures.

| Test | Expected behaviour |
| --- | --- |
| Preview an assignment | Only approved requirement, contract, skill and exact file resources appear. |
| Explicit upstream file | Verified producer summary and only the selected file; unrelated producer files stay out. |
| Stale or modified context before dispatch | Worker fails before opening Codex. |
| Resume an agent with an unchanged large input | Exact path and matching hash replace repeated inline source. Full saved context retains the bytes. |
| Change that input between runs | New content is supplied; it is never mislabeled as unchanged. |
| Tighten the context budget | Optional previews are referenced/omitted first. Required over-budget context blocks inference. |
| Correct a merged assignment | Only the originally declared, verified archived summary is retained; tampered merge/report evidence blocks it. |
| Review tracked changes | Full diff, current file hash, shared contracts, exact command/exit evidence; no duplicate final text or oversized command log. |

Inspect your current assignment without spending allowance:

```sh
orchestrator cycle context "$SESSION"
orchestrator cycle context "$SESSION" --task ACTUAL_TASK_ID
```

The optional `--full` shows the complete canonical context. After dispatch, the run's
`run.json` contains the exact transmitted prompt, while `context.json` preserves the
longer validated snapshot.

```sh
orchestrator cycle usage "$SESSION" --details
```

Compare `full_vs_compact_prompt_bytes_saved`, `reused_context_files` and the reported
input/cached/output token totals. Byte savings are only a local proxy: actual token use
depends on the model, native history, schema, tool reads and reasoning. Compare equivalent
projects and verification outcomes before claiming lower overall usage.
