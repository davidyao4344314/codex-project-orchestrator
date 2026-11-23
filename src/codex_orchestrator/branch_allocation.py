"""Conservative branch allocation derived from the approved task graph."""

from pathlib import PurePosixPath

from codex_orchestrator.design_validation import ancestors, task_graph, upstream_parts


def _reads(task):
    return {
        str(
            PurePosixPath(
                upstream_parts(ref.reference)[1] if ref.kind == "upstream_file" else ref.reference
            )
        )
        for ref in task.context
        if ref.kind in {"project_file", "upstream_file"}
    }


def _owns(task):
    return {str(PurePosixPath(path)) for path in task.owned_paths}


def isolated_tasks(design, stage_id):
    """Isolate independent writers with different agents and disjoint inputs/outputs."""
    tasks = [task for task in design.tasks if task.stage_id == stage_id and task.owned_paths]
    predecessors = ancestors(task_graph(design))
    roots = [
        task for task in tasks if not any(other.id in predecessors[task.id] for other in tasks)
    ]
    # A task interacting with any independent writer should stay on the shared branch.
    eligible = [
        task
        for task in roots
        # Combined checks must observe the final integrated stage.
        if not any(check.startswith(f"[{stage_id}:G") for check in task.automated_checks)
        and not any(
            task.id != other.id
            and (_owns(task) & (_owns(other) | _reads(other)) or _reads(task) & _owns(other))
            for other in roots
        )
    ]
    return (
        {task.id for task in eligible} if len({task.agent_id for task in eligible}) > 1 else set()
    )
