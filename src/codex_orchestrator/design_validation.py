"""Structural validation for the proposed workflow, before user approval."""

from graphlib import CycleError, TopologicalSorter
from pathlib import Path, PurePosixPath

from codex_orchestrator.codex import CodexError
from codex_orchestrator.design_models import TechnicalDesign
from codex_orchestrator.session import Brief


def ancestors(graph: dict[str, set[str]]) -> dict[str, set[str]]:
    try:
        order = TopologicalSorter(graph).static_order()
        result = {}
        for node in order:
            result[node] = set(graph[node])
            for parent in graph[node]:
                result[node].update(result[parent])
        return result
    except CycleError as exc:
        raise CodexError("Workflow dependencies contain a cycle.") from exc


def task_graph(design: TechnicalDesign) -> dict[str, set[str]]:
    """Stage prerequisites include completion of all tasks in upstream stages."""
    stage_tasks = {
        stage.id: {task.id for task in design.tasks if task.stage_id == stage.id}
        for stage in design.stages
    }
    stages = {stage.id: stage for stage in design.stages}
    return {
        task.id: set(task.depends_on).union(
            *(stage_tasks[parent] for parent in stages[task.stage_id].depends_on)
        )
        for task in design.tasks
    }


def project_path(project: Path, reference: str) -> Path:
    path = PurePosixPath(reference)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or "\\" in reference
        or ":" in reference
        or any(character in reference for character in "*?[]")
    ):
        raise CodexError(f"Use an exact repository-relative file path: {reference}")
    resolved = (project / reference).resolve()
    if not resolved.is_relative_to(project.resolve()):
        raise CodexError(f"Path leaves the project through a link: {reference}")
    return resolved


SUMMARY_KINDS = {"task_output", "task_summary"}


def upstream_parts(reference: str) -> tuple[str, str]:
    producer, separator, name = reference.rpartition(":")
    if not separator or not producer or not name:
        raise CodexError(
            "Upstream file context must use producer-task-id:repository-relative-file."
        )
    return producer, name


def validate_design(design: TechnicalDesign, brief: Brief, project: Path) -> None:
    errors = []
    for label, items in (
        ("agent", design.agents),
        ("stage", design.stages),
        ("component", design.components),
        ("interface", design.interfaces),
    ):
        if len({item.id for item in items}) != len(items):
            errors.append(f"Duplicate {label} IDs")
    agents = {agent.id for agent in design.agents}
    if "user" in agents:
        errors.append("The agent ID user is reserved for the human recipient")
    stages = {stage.id for stage in design.stages}
    components = {component.id for component in design.components}
    interfaces = {interface.id for interface in design.interfaces}
    tasks = {task.id: task for task in design.tasks}
    requirements = set(range(1, len(brief.requirements) + 1))
    covered = {number for task in design.tasks for number in task.requirement_ids}
    if covered != requirements:
        errors.append(
            f"Requirement coverage mismatch: missing {sorted(requirements - covered)}, "
            f"unknown {sorted(covered - requirements)}"
        )
    used_agents = set()
    for interface in design.interfaces:
        if interface.source_component not in components or (
            interface.target_component is not None and interface.target_component not in components
        ):
            errors.append(f"Interface {interface.id} references an unknown component")
    for stage in design.stages:
        if stage.id in stage.depends_on or set(stage.depends_on) - stages:
            errors.append(f"Stage {stage.id} has invalid prerequisites")
        if not any(task.stage_id == stage.id for task in design.tasks):
            errors.append(f"Stage {stage.id} has no tasks")
        gate = stage.gate
        owners = [gate.failure_owner, *(check.agent_id for check in gate.agent_checks)]
        used_agents.update(owners)
        if set(owners) - agents or gate.review_owner not in agents | {"user"}:
            errors.append(f"Stage {stage.id} has an unknown verification owner")
        if gate.review_owner != "user":
            used_agents.add(gate.review_owner)
    for task in design.tasks:
        used_agents.add(task.agent_id)
        if task.agent_id not in agents or task.stage_id not in stages:
            errors.append(f"Task {task.id} references an unknown agent or stage")
        if set(task.component_ids) - components:
            errors.append(f"Task {task.id} references an unknown component")
        if set(task.handoff_to) - (agents | {"user"}):
            errors.append(f"Task {task.id} has an unknown handoff recipient")
        for item in task.context:
            known = {
                "requirement": {f"R{number}" for number in requirements},
                "component": components,
                "interface": interfaces,
                "task_output": set(tasks),
                "task_summary": set(tasks),
            }
            if item.kind in known and item.reference not in known[item.kind]:
                errors.append(f"Task {task.id} has an unknown {item.kind} context reference")
            if item.kind == "project_file":
                project_path(project, item.reference)
            if item.kind == "archived_summary":
                name, separator, producer = item.reference.partition(":")
                if (
                    not separator
                    or not producer
                    or Path(name).name != name
                    or not name.endswith(".json")
                ):
                    errors.append(f"Task {task.id} has an invalid archived handoff reference")
            if item.kind == "upstream_file":
                producer_id, name = upstream_parts(item.reference)
                producer = tasks.get(producer_id)
                resolved = project_path(project, name)
                if producer is None or resolved not in {
                    project_path(project, p) for p in producer.owned_paths
                }:
                    errors.append(
                        f"Task {task.id} requests a file not owned by producer {producer_id}"
                    )
                if not any(
                    ref.kind in SUMMARY_KINDS
                    and ref.reference == producer_id
                    and (ref.required or not item.required)
                    for ref in task.context
                ):
                    errors.append(f"Task {task.id} needs a matching handoff from {producer_id}")
        for path in task.owned_paths:
            resolved = project_path(project, path)
            if resolved.is_dir() or path.endswith("/"):
                errors.append(f"Task {task.id} must own exact files, not directory {path}")
    if agents - used_agents:
        errors.append(
            f"Agents have no task or verification responsibility: {sorted(agents - used_agents)}"
        )
    if errors:
        raise CodexError("Invalid technical design. " + "; ".join(errors))
    stage_ancestors = ancestors({stage.id: set(stage.depends_on) for stage in design.stages})
    for task in design.tasks:
        for parent in task.depends_on:
            producer_stage = tasks[parent].stage_id
            if (
                producer_stage != task.stage_id
                and producer_stage not in stage_ancestors[task.stage_id]
            ):
                raise CodexError(f"Stage {task.stage_id} must depend on stage {producer_stage}")
    predecessors = ancestors(task_graph(design))
    writers: dict[Path, list[str]] = {}
    for task in design.tasks:
        for path in task.owned_paths:
            writers.setdefault(project_path(project, path), []).append(task.id)
        for item in task.context:
            if item.kind in {"task_summary", "upstream_file"} or (
                item.kind == "task_output" and item.required
            ):
                producer_id = (
                    upstream_parts(item.reference)[0]
                    if item.kind == "upstream_file"
                    else item.reference
                )
                if producer_id not in predecessors[task.id]:
                    raise CodexError(
                        f"Task {task.id} needs a dependency on context producer {producer_id}"
                    )
                producer = tasks[producer_id]
                if task.agent_id != producer.agent_id and task.agent_id not in producer.handoff_to:
                    raise CodexError(
                        f"Task {producer.id} must hand off its result to agent {task.agent_id}"
                    )
    for path, owners in writers.items():
        unique = list(dict.fromkeys(owners))
        for index, first in enumerate(unique):
            for second in unique[index + 1 :]:
                if first not in predecessors[second] and second not in predecessors[first]:
                    raise CodexError(
                        f"Unordered writes to {path.relative_to(project.resolve())}: "
                        f"{first} and {second}. Add dependencies or assign separate files."
                    )


def context_blockers(
    design: TechnicalDesign, project: Path, *, task_ids: set[str] | None = None
) -> list[str]:
    blockers = []
    for task in design.tasks:
        if task_ids is not None and task.id not in task_ids:
            continue
        for item in task.context:
            if item.kind == "project_file" and item.required:
                if not project_path(project, item.reference).is_file():
                    blockers.append(
                        f"{task.id} needs missing file {item.reference}. {item.missing_action}"
                    )
    return blockers


def skill_status(location: str | None, project: Path) -> str:
    if location is None:
        return "setup or verification needed"
    path = Path(location).expanduser()
    if not path.is_absolute():
        path = project / path
    if path.name == "SKILL.md" and path.is_file():
        return "skill file found"
    return "setup or verification needed"
