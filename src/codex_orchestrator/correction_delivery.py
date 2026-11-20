"""A scoped delivery round for feedback on work already merged into the base."""

from pathlib import Path
from uuid import uuid4

from codex_orchestrator import git_service as git
from codex_orchestrator import stage_branches as branches
from codex_orchestrator.coordination_store import design_fingerprint, stage_accepted
from codex_orchestrator.cycle_store import ArchivedDelivery, FeedbackRound
from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import (
    AgentCheck,
    ContextReference,
    DeliveryStage,
    DesignRevision,
    DesignSession,
    TechnicalDesign,
    VerificationGate,
    source_digest,
)
from codex_orchestrator.design_validation import (
    ancestors,
    project_path,
    task_graph,
    validate_design,
)
from codex_orchestrator.run_store import latest_runs, run_directory
from codex_orchestrator.session import Answer, DraftPlan, Revision, ScopedTask
from codex_orchestrator.worker_models import WorkerSpec


def verified_merges(state, path, base):
    settings, revision = branches.current(state, path)
    if not settings or settings.base != base:
        raise ValueError("The previous delivery's integration base changed.")
    merged = {}
    project = Path(state.project)
    for stage_id, item in revision.stages.items():
        if not item.merged_commit:
            continue
        if (
            not stage_accepted(state, path, stage_id)
            or item.evidence_digest != git.evidence_digest(state, path, stage_id)
            or not branches.ancestor(project, item.merged_commit, f"refs/heads/{base}")
        ):
            raise ValueError(f"Previous merge evidence is stale for stage {stage_id}.")
        merged[stage_id] = item.merged_commit
    return merged


def archived_commits(cycle, directory, project, base):
    commits = []
    for archived in cycle.archived_deliveries:
        source = directory / archived.session_name
        path = source.with_suffix(".design.json")
        state = DesignSession.load(path)
        check_source(state)
        if (
            Path(state.source_file).resolve() != source.resolve()
            or Path(state.project).resolve() != project.resolve()
            or design_fingerprint(state) != archived.design_digest
            or verified_merges(state, path, base) != archived.merged_stages
        ):
            raise ValueError("Archived delivery evidence changed. Inspect cycle history.")
        commits.extend(archived.merged_stages.values())
    return commits


def _file_reference(name, reason, required=True):
    return ContextReference(
        kind="project_file",
        reference=name,
        reason=reason,
        required=required,
        missing_action="Inspect the integrated source before continuing.",
    )


def _rewrite_context(task, original, active_ids, project):
    context = []
    for ref in task.context:
        if ref.kind != "task_output" or ref.reference in active_ids:
            context.append(ref)
            continue
        producer = original[ref.reference]
        files = [name for name in producer.owned_paths if project_path(project, name).is_file()]
        if ref.required and not files:
            raise ValueError(
                f"Archived context {ref.reference} has no live source files. "
                "Approve a wider plan before correcting this task."
            )
        context.extend(
            _file_reference(name, f"Integrated output of {producer.id}: {ref.reason}", ref.required)
            for name in files
        )
    task.context = context


def _scoped_design(state, path, target, note, merged, stage_id):
    old = state.revisions[-1]
    design = old.design.model_copy(deep=True)
    project = Path(state.project)
    original = {task.id: task for task in design.tasks}
    predecessors = ancestors(task_graph(design))
    remaining = [t.model_copy(deep=True) for t in design.tasks if t.stage_id not in merged]
    correction = target.model_copy(deep=True)
    correction.stage_id, correction.depends_on = stage_id, []
    correction.title = f"Correct: {target.title}"
    correction.description = (
        f"Address only this feedback within the original assignment: {note}. "
        f"Preserve the original contract: {target.description}"
    )
    correction.acceptance_criteria.append(f"User correction satisfied: {note}")
    correction.human_checks.append(f"Confirm correction: {note}")
    # Previous merged implementation is never redeployed just to validate a correction.
    # Re-check affected merged consumers using their original tests, read-only.
    regression = []
    latest = latest_runs(path, len(state.revisions))
    owned = {project_path(project, name) for name in target.owned_paths}
    for other in original.values():
        if other.id == target.id or other.stage_id not in merged:
            continue
        audit = list(other.owned_paths)
        run = latest.get(other.id)
        if run:
            spec = run_directory(path) / run.id / "worker_spec.json"
            if spec.exists():
                audit.extend(WorkerSpec.load(spec).audit_paths)
        if target.id not in predecessors[other.id] and not owned.intersection(
            project_path(project, name) for name in audit
        ):
            continue
        check = other.model_copy(deep=True)
        check.id = f"{stage_id}-check-{other.id}"
        check.title = f"Regression check: {other.title}"
        check.description = (
            "Re-run the original checks on corrected integrated source; do not edit."
        )
        check.scope = ["Read-only regression verification"]
        check.stage_id, check.owned_paths, check.depends_on = stage_id, [], [target.id]
        check.deliverables = ["Observed regression test results"]
        check.handoff_to = ["user"]
        check.context = [r for r in check.context if r.kind != "task_output"]
        check.context.extend(
            _file_reference(name, "Existing integration input for regression check", False)
            for name in dict.fromkeys(audit)
            if project_path(project, name).is_file()
        )
        check.context.append(
            ContextReference(
                kind="task_output",
                reference=target.id,
                reason="Verify the corrected output",
                required=True,
                missing_action="Verify the correction first.",
            )
        )
        if check.agent_id not in correction.handoff_to:
            correction.handoff_to.append(check.agent_id)
        regression.append(check)
    tasks = [correction, *regression, *remaining]
    active_ids = {t.id for t in tasks}
    brief = state.source.brief.model_copy(deep=True)
    brief.requirements.append(f"Targeted correction: {note}")
    correction.requirement_ids.append(len(brief.requirements))
    correction.context.append(
        ContextReference(
            kind="requirement",
            reference=f"R{len(brief.requirements)}",
            reason="Explicit user correction",
            required=True,
            missing_action="Ask the user.",
        )
    )
    for task in tasks:
        task.depends_on = [dep for dep in task.depends_on if dep in active_ids]
        _rewrite_context(task, original, active_ids, project)
        # Keep every requirement referenced by retained assignments, including context.
        task.requirement_ids = sorted(
            set(task.requirement_ids)
            | {int(ref.reference[1:]) for ref in task.context if ref.kind == "requirement"}
        )
    required = sorted({i for t in tasks for i in t.requirement_ids})
    mapping = {old_id: new_id for new_id, old_id in enumerate(required, 1)}
    brief.requirements = [brief.requirements[i - 1] for i in required]
    for task in tasks:
        task.requirement_ids = [mapping[i] for i in task.requirement_ids]
        for ref in task.context:
            if ref.kind == "requirement":
                ref.reference = f"R{mapping[int(ref.reference[1:])]}"
        if not task.context:
            task.context.append(
                ContextReference(
                    kind="requirement",
                    reference=f"R{task.requirement_ids[0]}",
                    reason="Retained approved requirement",
                    required=True,
                    missing_action="Ask the user.",
                )
            )
    gate_tasks = [correction, *regression]
    correction_stage = DeliveryStage(
        id=stage_id,
        title="Targeted correction",
        goal=note,
        depends_on=[],
        deliverables=correction.deliverables + ["Correction and regression evidence"],
        gate=VerificationGate(
            agent_checks=[
                AgentCheck(agent_id=t.agent_id, check=check, expected_result="Pass")
                for t in gate_tasks
                for check in t.automated_checks
            ]
            + [
                AgentCheck(
                    agent_id=correction.agent_id,
                    check=f"Verify correction: {note}",
                    expected_result="Pass",
                )
            ],
            user_checks=[check for t in gate_tasks for check in t.human_checks],
            acceptance_criteria=correction.acceptance_criteria,
            review_owner="user",
            failure_owner=correction.agent_id,
            required_evidence=["Worker checks, regression results, and human acceptance"],
        ),
    )
    pending_stages = [s for s in design.stages if s.id not in merged]
    pending_ids = {s.id for s in pending_stages}
    for stage in pending_stages:
        stage.depends_on = [stage_id, *(i for i in stage.depends_on if i in pending_ids)]
    design.stages = [correction_stage, *pending_stages]
    design.tasks = tasks
    used = {t.agent_id for t in tasks}
    for stage in design.stages:
        used.update([stage.gate.failure_owner, *(c.agent_id for c in stage.gate.agent_checks)])
        if stage.gate.review_owner != "user":
            used.add(stage.gate.review_owner)
    design.agents = [a for a in design.agents if a.id in used]
    for task in tasks:
        task.handoff_to = [a for a in task.handoff_to if a in used or a == "user"] or ["user"]
    design.overview = f"Scoped correction to merged work: {note}. Preserve completed delivery."
    design.parallel_strategy = (
        "Correct the assigned files, verify affected outputs, then continue any unfinished stages."
    )
    design = TechnicalDesign.model_validate(design.model_dump())
    validate_design(design, brief, project)
    return brief, design, {a.id: old.agent_models[a.id] for a in design.agents}


def create_correction(source, state, path, target, old_run, note, history_file, history, cycle):
    """Called under the project writer lease; never rewrite or push historical work."""
    project = Path(state.project)
    settings, revision = branches.current(state, path)
    base = settings.base
    branches.clean(project)
    archived_commits(cycle, source.parent, project, base)
    merged = verified_merges(state, path, base)
    if target.stage_id not in merged:
        raise ValueError("Record the target stage merge before creating a correction.")
    head = git.git(project, "rev-parse", f"refs/heads/{base}")
    active = git.git(project, "symbolic-ref", "--short", "HEAD")
    # A clean uncommitted verification branch can be parked safely. Never discard
    # another stage's dirty work or unmerged commits/publication receipts.
    safe_branches = {base}
    for stage_id, item in revision.stages.items():
        if stage_id in merged:
            continue
        tip = git.git(project, "rev-parse", f"refs/heads/{item.branch}")
        if tip != item.start_commit:
            raise ValueError("Finish the unmerged stage commit before starting a correction.")
        if tip == head:
            safe_branches.add(item.branch)
    if active not in safe_branches:
        raise ValueError("Return to the clean integration base before creating a correction.")
    token = uuid4().hex[:10]
    stage_id = f"correction-{token}"
    # Repeated feedback must not grow the filename on every delivery round.
    original_name = (
        cycle.archived_deliveries[0].session_name if cycle.archived_deliveries else source.name
    )
    new_source = source.with_name(f"{Path(original_name).stem}-correction-{token}.json")
    new_path = new_source.with_suffix(".design.json")
    brief, design, models = _scoped_design(state, path, target, note, merged, stage_id)
    plan = DraftPlan(
        assumptions=design.assumptions,
        tasks=[
            ScopedTask.model_validate(t.model_dump(include=set(ScopedTask.model_fields)))
            for t in design.tasks
        ],
        agent_strategy="single" if len(models) == 1 else "multiple",
        agent_rationale="Preserve approved owners; only the targeted task changes merged code.",
    )
    source_revision = Revision(brief=brief, plan=plan)
    session = check_source(state).model_copy(deep=True)
    session.brief, session.revisions, session.approved_revision = brief, [source_revision], 1
    session.answers = [Answer(prompt="Targeted correction", response=note)]
    session.previous_cycle_context = f"Same cycle {cycle.number}; previous delivery: {source.name}."
    new_state = DesignSession(
        project=state.project,
        source_file=str(new_source),
        source_revision=1,
        source=source_revision,
        source_digest=source_digest(source_revision),
        architect_model=state.architect_model,
        default_agent_model=state.default_agent_model,
        models_configured=True,
        context_limits=state.context_limits,
        phase="review",
        revisions=[
            DesignRevision(
                origin="correction",
                design=design,
                agent_models=models,
                elapsed_seconds=0,
            )
        ],
    )
    record = FeedbackRound(
        kind="improve",
        revision=1,
        design_digest=design_fingerprint(new_state),
        stage_id=stage_id,
        note=note,
        task_id=target.id,
        agent_id=target.agent_id,
        workstream=str(target.workstream),
        owned_paths=target.owned_paths,
        prior_run_id=old_run.id,
        session_name=new_source.name,
    )
    if active != base:
        git.git(project, "switch", base)
    session.save(new_source, new=True)
    new_state.save(new_path, new=True)
    cycle.archived_deliveries.append(
        ArchivedDelivery(
            session_name=source.name,
            design_digest=design_fingerprint(state),
            merged_stages=merged,
        )
    )
    cycle.session_name = new_source.name
    cycle.feedback_enabled = True
    cycle.feedback_rounds.append(record)
    history.save(history_file)
    return record
