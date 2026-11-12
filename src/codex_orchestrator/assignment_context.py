"""Pure assignment context shared by discussion and implementation adapters."""


def task_context(state, task):
    """Build the supplied context from this assignment, without other agents' histories."""
    design = state.revisions[-1].design
    requirement_ids = set(task.requirement_ids)
    component_ids = set(task.component_ids)
    interface_ids = set()
    for item in task.context:
        if item.kind == "requirement":
            if not item.reference.startswith("R") or not item.reference[1:].isdigit():
                raise ValueError(f"Unknown requirement context: {item.reference}")
            requirement_ids.add(int(item.reference[1:]))
        elif item.kind == "component":
            component_ids.add(item.reference)
        elif item.kind == "interface":
            interface_ids.add(item.reference)
    if not requirement_ids <= set(range(1, len(state.source.brief.requirements) + 1)):
        raise ValueError("Assignment references unknown requirements.")
    if not component_ids <= {c.id for c in design.components}:
        raise ValueError("Assignment references unknown components.")
    if not interface_ids <= {i.id for i in design.interfaces}:
        raise ValueError("Assignment references unknown interfaces.")
    return {
        "agent": next(a.model_dump() for a in design.agents if a.id == task.agent_id),
        "assignment": task.model_dump(),
        "allocation_reason": state.revisions[-1].task_allocation_reasons.get(task.id),
        "requirements": {
            f"R{i}": state.source.brief.requirements[i - 1] for i in sorted(requirement_ids)
        },
        "components": [c.model_dump() for c in design.components if c.id in component_ids],
        "interfaces": [
            i.model_dump()
            for i in design.interfaces
            if i.id in interface_ids
            or i.source_component in component_ids
            or i.target_component in component_ids
        ],
        "project_constraints": state.source.brief.constraints,
        "project_exclusions": state.source.brief.exclusions,
    }
