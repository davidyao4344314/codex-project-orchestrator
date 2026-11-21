# Planning efficiency evaluation

## Workflow

```text
Idea → requirements interview (routine model; no repository tools)
         ↓
      scope draft (local Python in outline mode; standard AI planner optional)
         ↓ user approves requirements
      architect (bounded repository map; focused inspection; strong model)
         ↓ user approves design and stage boundaries
      task model allocation → worker → verification → independent review
         ↓ human approval
      existing commit/push/merge checkpoints → feedback → next cycle
```

For small eligible work, the compact route produces a combined plan/design in one model turn
instead of generating a separate architect turn. For larger well-defined features,
`outline` is the preferred experiment. Existing auto and standard routes remain available.

## Recorded baseline (Git branch management feature)

| Role | Application prompt bytes | Input tokens | Cached input (subset) | Output tokens |
| --- | ---: | ---: | ---: | ---: |
| Requirements (2 turns) | 3,846 | 163,758 | 110,720 | 2,625 |
| Planner (1 turn) | 4,654 | 133,978 | 99,328 | 3,405 |
| Architect (1 turn) | 18,909 | 224,645 | 174,208 | 11,785 |
| Total | 27,409 | 522,381 | 384,256 | 17,815 |

The Codex logs showed five internal model requests in the planner and six in the architect.
This explains why input token totals greatly exceeded the submitted prompt size. These are
observed CLI token counters, not account usage charges.

An offline capture of the new architect prompt using this same approved brief and project
inventory (80 paths, marked partial) measured 22,396 bytes on the standard route, including
the retained detailed plan and the added map, and 11,561 bytes on the outline route.
Compared with the recorded 18,909-byte original architect prompt, the outline is about
39% smaller. Standard's larger application payload is a deliberate tradeoff for fewer
exploratory reads; whether it saves end-to-end tokens must be measured live.

## What changes

- With the same two intake turns, outline mode reduces four managed planning turns to three
  (25% fewer), eliminating the whole standalone planner call. This is a deterministic control
  flow improvement, not a claim of 25% lower tokens or account usage.
- The repository map scans bounded path metadata only, avoiding repeated directory listings;
  requirements and standard planning tell the model not to inspect the repository.
- Architect exploration targets are guidance, not enforced provider token limits. The prompt
  byte cap is enforced locally without dropping approved constraints.
- A configured model palette picks role-specific models and difficulty-based task models.
- New usage receipts include observed unique tool and command completions. Old counts remain
  unknown; no retrospective tool count is invented.

## Controlled manual comparison

1. Use two clean project clones at the same commit, the same scope and answers, and the same
   approved palette. Create one cycle with `--route standard` and one with `--route outline`.
2. Only run each through approved technical design. Do not start implementation merely to
   measure planning. Verify that both cover the same requirements and exclusions with clear
   file ownership, dependencies, checks and review gates.
3. Record `orchestrator cycle usage SESSION --details` for each run. Compare total input,
   cached input subset, output, elapsed seconds, observed tool calls and design completeness.
   Repeat only when the cost is justified; different cache states affect the result.
4. Separately compare a normal Codex session given the same feature and acceptance criteria.
   Account usage depends on model and caching; don't estimate billing from raw totals alone.

Automated regression tests exercise local inventory limits, secret-path and symlink omission,
prompt budget, no-inference outline creation, model palette defaults, role boundaries and
tool-count deduplication without spending Codex usage.
