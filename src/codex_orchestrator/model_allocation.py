"""Deterministic model allocation using a palette the user approved from Codex."""

from pathlib import Path

from codex_orchestrator.design_models import DesignRevision, TechnicalDesign
from codex_orchestrator.session import ModelPolicy

TIERS = ("routine", "standard", "strong_reasoning")
DIFFICULTY = {"routine": 0, "moderate": 1, "complex": 2}


def automatic_models(design: TechnicalDesign, policy: ModelPolicy):
    """Pick a model for each task; keep each agent's strongest model as its default."""
    agents, tasks, reasons = {}, {}, []
    for agent in design.agents:
        assignments = [t for t in design.tasks if t.agent_id == agent.id]
        capability = TIERS.index(agent.model_capability)
        hardest = max((DIFFICULTY[t.difficulty] for t in assignments), default=capability)
        # If the architect's recommendation exceeds ALL task ratings, preserve the
        # additional reasoning requirement instead of under-allocating the whole agent.
        floor = capability if capability > hardest else 0
        agents[agent.id] = getattr(policy, TIERS[max(capability, hardest)])
        for task in assignments:
            tier = TIERS[max(DIFFICULTY[task.difficulty], floor)]
            model = getattr(policy, tier)
            tasks[task.id] = model
            reasons.append(f"{task.id}: {tier} → {model or 'Codex default'}")
    return agents, tasks, "Automatic model allocation from approved palette: " + "; ".join(reasons)


def revision_models(state, design):
    if state.auto_model_allocation and state.model_policy:
        agents, tasks, rationale = automatic_models(design, state.model_policy)
        return {"agent_models": agents, "task_models": tasks, "allocation_rationale": rationale}
    previous = state.revisions[-1].agent_models if state.revisions else {}
    return {
        "agent_models": {
            agent.id: previous.get(agent.id, state.default_agent_model) for agent in design.agents
        }
    }


def apply_automatic_models(state, path, *, keep_architect=False):
    from codex_orchestrator import git_service, stage_branches
    from codex_orchestrator.design import check_source
    from codex_orchestrator.run_store import runs

    with git_service.writer(Path(state.project)):
        source = check_source(state)
        state.require_current(path)
        if not source.model_policy:
            raise ValueError("Configure a model palette with orchestrator cycle models first.")
        if runs(path) or stage_branches.load(path):
            raise ValueError(
                "Delivery has started. Keep the approved models and use the palette in a new cycle."
            )
        if any(r.origin == "correction" for r in state.revisions):
            raise ValueError("Scoped corrections retain their approved task models.")
        state.model_policy = source.model_policy
        state.auto_model_allocation = True
        state.models_configured = True
        if state.phase == "draft" and not keep_architect:
            state.architect_model = source.model_policy.strong_reasoning
        if state.revisions and state.phase in {"review", "approved"}:
            current = state.revisions[-1]
            agents, tasks, rationale = automatic_models(current.design, source.model_policy)
            if current.agent_models != agents or current.task_models != tasks:
                new = DesignRevision.model_validate(
                    {
                        **current.model_dump(),
                        "agent_models": agents,
                        "task_models": tasks,
                        "origin": "model_allocation",
                        "elapsed_seconds": 0,
                        "allocation_rationale": rationale,
                    }
                )
                state.revisions.append(new)
                state.phase, state.approved_revision = "review", None
        state.save(path)
