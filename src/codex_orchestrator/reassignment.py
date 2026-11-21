"""Reviewable assignment changes without editing approved design history."""

from pathlib import Path
from uuid import uuid4

from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import DesignRevision
from codex_orchestrator.design_validation import SUMMARY_KINDS, validate_design
from codex_orchestrator.execution import is_busy


def reassign(state, path, task_id, agent_id, *, new_role=None, model=None):
    check_source(state)
    state.require_current(path)
    if is_busy(state.project):
        raise ValueError(
            "Stop the active project run and wait for it to finish before reassigning."
        )
    revision = state.revisions[-1].model_copy(deep=True)
    design = revision.design
    task = next((t for t in design.tasks if t.id == task_id), None)
    if task is None:
        raise ValueError("Unknown task.")
    if new_role:
        if model is not None and not model.strip():
            raise ValueError("Enter a model ID or use default.")
        agent = next(a for a in design.agents if a.id == task.agent_id).model_copy(deep=True)
        agent.id = "agent-" + uuid4().hex[:8]
        agent.role = new_role.strip()
        if not agent.role:
            raise ValueError("Enter an agent role.")
        design.agents.append(agent)
        revision.agent_models[agent.id] = model
        agent_id = agent.id
    if agent_id not in {a.id for a in design.agents}:
        raise ValueError("Unknown destination agent.")
    if task.agent_id == agent_id:
        return
    task.agent_id = agent_id
    if revision.task_models is not None:
        revision.task_models[task.id] = revision.agent_models[agent_id]
    # Ensure upstream producers declare the recipient of their required handoff.
    by_id = {t.id: t for t in design.tasks}
    for item in task.context:
        if item.kind in SUMMARY_KINDS and (item.required or item.kind == "task_summary"):
            producer = by_id[item.reference]
            if agent_id not in producer.handoff_to:
                producer.handoff_to.append(agent_id)
    used = {t.agent_id for t in design.tasks}
    for stage in design.stages:
        used.update(c.agent_id for c in stage.gate.agent_checks)
        used.update([stage.gate.review_owner, stage.gate.failure_owner])
    design.agents = [a for a in design.agents if a.id in used]
    revision.agent_models = {a.id: revision.agent_models[a.id] for a in design.agents}
    revision.origin = "assignment"
    revision = DesignRevision.model_validate(revision.model_dump())
    validate_design(design, state.source.brief, Path(state.project))
    state.revisions.append(revision)
    state.phase, state.approved_revision = "review", None
    state.save(path)
