"""Summarize the planner's job assessment without another model request."""

from dataclasses import dataclass

from codex_orchestrator.design_models import AgentPreference, DesignSession


@dataclass(frozen=True)
class JobAssessment:
    complexity: str
    task_count: int
    stage_count: int
    roster: list[AgentPreference]
    reasoning: str
    source_revision: int
    tasks: list[dict[str, str]]


def assess_job(state: DesignSession) -> JobAssessment:
    """Keep the latest architect recommendation separate from manual team overrides."""
    if not state.revisions:
        raise ValueError("Create a technical design before assessing the job.")
    index = next(
        (
            i
            for i in reversed(range(len(state.revisions)))
            if state.revisions[i].origin in {"architect", "compact"}
        ),
        0,
    )
    revision = state.revisions[index]
    design = revision.design
    levels = {"routine": 0, "moderate": 1, "complex": 2}
    complexity = max((t.difficulty for t in design.tasks), key=levels.__getitem__)
    agents = {a.id: a for a in design.agents}
    return JobAssessment(
        complexity=complexity,
        task_count=len(design.tasks),
        stage_count=len(design.stages),
        roster=[
            AgentPreference(id=a.id, role=a.role, model=revision.agent_models[a.id])
            for a in design.agents
        ],
        reasoning=design.parallel_strategy,
        source_revision=index + 1,
        tasks=[
            {
                "Task": t.title,
                "Difficulty": t.difficulty,
                "Agent": agents[t.agent_id].role,
                "Model capability": agents[t.agent_id].model_capability,
                "Reason": agents[t.agent_id].model_reason,
            }
            for t in design.tasks
        ],
    )
