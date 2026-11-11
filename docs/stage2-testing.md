# Stage 2 manual checks

Use the PyCharm terminal from the repository directory with the Python environment activated.
Replace the example session names below with the path printed by your own Stage 1 run.
Live architect requests use the Codex account you signed in with.

## 1. Check the approval prerequisite

Start from a saved Stage 1 session that is still gathering information:

```sh
orchestrator design plans/activity-session.json
```

Expected result: a message asks you to approve Stage 1 first. It gives the resume command,
and the architect has not been called.

## 2. Create a small design

Resume Stage 1, finish its plan, and choose approve. Then run:

```sh
orchestrator design plans/activity-session.json
```

Choose the architect and initial agent models, or press Enter to use the displayed defaults.
Answer any material technical questions. For a recursion counter, check that the design
fits a small Python terminal program and respects your requested stopping behavior.

Expected result: the terminal shows the technical structure and complete workflow before
asking for approval. The architect response model appears when the CLI reports it. The
Stage 1 JSON retains its approved revision.

## 3. Inspect responsibilities and context

For each agent, locate its role, responsibility, boundaries, selected model, and capability
recommendation. For each task, locate the owning agent and stage, owned files, inputs,
deliverables, handoff recipients, and escalation conditions.

Expected result: you can identify who owns every task. Required and optional context are
labelled, with an action for missing inputs. Shared-file writes have ordering dependencies.
Task outputs used as required context have an upstream producer.

Skill entries with an existing referenced SKILL.md show that the file was found. Others
show that setup or verification is needed.

## 4. Inspect every stage's merge gate

Check each stage's planned agent checks and expected results, required user tests,
acceptance criteria, review/failure owners, and completion evidence.

Expected result: the report ties verification to each stage, including the combined changes
before merge. It requests a tested commit and actual commands/results. The report labels
these checks as planned. Required user checks are part of the future merge gate.

For a sufficiently complex request, expect multiple useful delivery stages. A small feature
may reasonably have one stage with a verification gate.

## 5. Change model allocation

At review, enter `models`. Keep the architect choice and change an agent model to an ID
available to your account.

Expected result: the selected agent model changes in a new design revision. The original
architect response attribution is retained. This local allocation edit makes no model request.
Approval is required for the new revision.

## 6. Save, resume, revise, and approve

Choose `save`, then repeat the design command. Choose `revise` and request a scope-preserving
change, such as making termination checks explicit. Finally choose `approve`.

Expected result: the same design resumes, your revision request is saved, and a new architect
revision appears. Approval records the current revision. Prior revisions stay in the JSON.
If a provider call fails, the saved answers and prior revisions remain available for retry.

A missing required project file prevents approval. Resolve that reference or revise the
design so an upstream investigation supplies the needed context.

## 7. Display the saved design

```sh
orchestrator design plans/activity-session.json --show
git status --short
```

Expected result: the full saved workflow appears with its approval status. No architect
request is made and the design file is not rewritten. Default session files under
`plans/` remain absent from Git's untracked-file list.

## 8. Check source changes

After saving a technical design, revise and reapprove Stage 1. Repeat the original design
command.

Expected result: the old design cannot be approved against the changed requirements.
Start a new design with `--output plans/activity-v2.design.json`. The previous design
remains available on disk.

## Automated checks

```sh
python -m pytest -q
ruff check .
ruff format --check .
```

The suite checks legacy Stage 1 behavior, design validation, per-stage prerequisites,
context and ownership conflicts, approval/revision behavior, model attribution, recovery,
and the subprocess handoff.
