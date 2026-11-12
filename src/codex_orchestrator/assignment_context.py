"""Pure assignment context shared by discussion and implementation adapters."""


def task_context(state, task):
    """Build the supplied context from this assignment, without other agents' histories."""
    design = state.revisions[-1].design
    return {
        "agent": next(a.model_dump() for a in design.agents if a.id == task.agent_id),
        "assignment": task.model_dump(),
        "allocation_reason": state.revisions[-1].task_allocation_reasons.get(task.id),
        "requirements": {
            f"R{i}": state.source.brief.requirements[i - 1] for i in task.requirement_ids
        },
        "components": [c.model_dump() for c in design.components if c.id in task.component_ids],
        "interfaces": [
            i.model_dump()
            for i in design.interfaces
            if i.source_component in task.component_ids or i.target_component in task.component_ids
        ],
        "project_constraints": state.source.brief.constraints,
        "project_exclusions": state.source.brief.exclusions,
    }
