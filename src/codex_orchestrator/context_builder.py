"""Resolve a minimal assignment package without inference or directory crawling."""

from pathlib import Path
from typing import Literal

from codex_orchestrator.assignment_context import task_context
from codex_orchestrator.codex import CodexError
from codex_orchestrator.context_files import read_file, resolve_file
from codex_orchestrator.context_handoffs import archived_handoff_resource, handoff_resource
from codex_orchestrator.context_models import ContextLimits, ContextPackage, ContextResource
from codex_orchestrator.coordination_store import design_fingerprint
from codex_orchestrator.design_models import Assignment, DesignSession
from codex_orchestrator.design_validation import SUMMARY_KINDS, project_path, upstream_parts


def build_context(
    state: DesignSession,
    task: Assignment,
    path: Path | None = None,
    *,
    purpose: Literal["discussion", "execution"] = "execution",
    limits: ContextLimits | None = None,
) -> ContextPackage:
    if not state.revisions:
        raise ValueError("Create a technical design first.")
    current = next((t for t in state.revisions[-1].design.tasks if t.id == task.id), None)
    if current is None or current != task:
        raise ValueError("Build context from the current design's assignment.")
    limits = limits or state.context_limits
    metadata = task_context(state, task)
    design = state.revisions[-1].design
    metadata["project_goal"] = state.source.brief.goal
    metadata["stage"] = next(s.model_dump() for s in design.stages if s.id == task.stage_id)
    metadata["owned_paths"] = task.owned_paths
    if purpose == "execution" and path is not None:
        from codex_orchestrator.cycle_feedback import task_feedback

        feedback = task_feedback(state, path, task.id)
        if feedback:
            metadata["user_feedback"] = feedback.model_dump(mode="json")
    package = ContextPackage(
        design_revision=len(state.revisions),
        design_fingerprint=design_fingerprint(state),
        task_id=task.id,
        agent_id=task.agent_id,
        requested_model=state.revisions[-1].model_for_task(task),
        purpose=purpose,
        metadata=metadata,
        limits=limits,
    )
    resources = []
    for item in task.context:
        if item.kind == "project_file":
            resources.append(
                ContextResource(
                    kind="project_file",
                    reference=item.reference,
                    required=item.required,
                    reason=item.reason,
                    missing_action=item.missing_action,
                    status="pending",
                )
            )
    selected = {}
    project = Path(state.project)
    producers = {t.id: t for t in design.tasks}
    for reference in task.context:
        if reference.kind == "upstream_file":
            producer, name = upstream_parts(reference.reference)
            choices = selected.setdefault(producer, [])
            upstream = producers.get(producer)
            matching = any(
                r.kind in SUMMARY_KINDS
                and r.reference == producer
                and (r.required or not reference.required)
                for r in task.context
            )
            if (
                upstream is None
                or not matching
                or project_path(project, name)
                not in {project_path(project, p) for p in upstream.owned_paths}
            ):
                resources.append(
                    ContextResource(
                        kind="upstream_file",
                        reference=name,
                        required=reference.required,
                        reason="File is not owned by the declared handoff producer.",
                        missing_action="Correct the approved assignment context.",
                        status="excluded",
                    )
                )
            else:
                choices.append((name, reference))
    for reference in task.context:
        if reference.kind == "archived_summary":
            resources.append(archived_handoff_resource(state, task, reference, path, limits))
            continue
        if reference.kind not in SUMMARY_KINDS:
            continue
        handoff = handoff_resource(state, task, reference, path, limits)
        resources.append(handoff)
        if handoff.status == "included":
            producer = next(t for t in design.tasks if t.id == reference.reference)
            chosen = selected.get(producer.id)
            if chosen is None:
                chosen = (
                    [(p, reference) for p in producer.owned_paths]
                    if reference.kind == "task_output"
                    else []
                )
            resources.extend(
                ContextResource(
                    kind="upstream_file",
                    reference=p,
                    required=selection.required,
                    missing_action="Restore the producer's output or rerun and verify it.",
                    reason=f"Current source handed off by {producer.id}: {selection.reason}",
                    source_run_id=handoff.source_run_id,
                    status="pending",
                )
                for p, selection in chosen
            )
    resources.extend(
        ContextResource(
            kind="owned_file", reference=p, reason="Current source in owned scope", status="pending"
        )
        for p in task.owned_paths
    )
    agent = next(a for a in design.agents if a.id == task.agent_id)
    for skill in agent.skills:
        resources.append(
            ContextResource(
                kind="skill",
                reference=skill.location or skill.name,
                required=bool(skill.location),
                reason=f"Assigned skill: {skill.name}",
                status="pending" if skill.location else "unconfigured",
                missing_action="Configure this skill's SKILL.md, or revise the assignment.",
            )
        )
    instruction_refs = {"AGENTS.md"}
    for resource in resources:
        if resource.kind in {"task_output", "skill"}:
            continue
        try:
            resolved = resolve_file(Path(state.project), resource)
            relative = resolved.relative_to(Path(state.project).resolve())
        except (ValueError, OSError, CodexError):
            continue
        instruction_refs.update(str(parent / "AGENTS.md") for parent in relative.parents)
    for reference in sorted(instruction_refs):
        if (Path(state.project) / reference).is_file():
            resources.append(
                ContextResource(
                    kind="instruction",
                    reference=reference,
                    required=True,
                    reason="Coding instructions for the selected file scope",
                    status="pending",
                )
            )
    _resolve_resources(package, Path(state.project), resources)
    return package


def _resolve_resources(package, project, resources):
    # Required inputs have priority over optional source previews.
    resources.sort(key=lambda item: not item.required)
    unique = {}
    for item in resources:
        if item.status == "excluded":
            key = (item.kind, item.reference, "excluded")
        elif item.kind == "task_output" or item.status == "unconfigured":
            key = (item.kind, item.reference)
        else:
            try:
                key = str(resolve_file(project, item))
            except (OSError, ValueError, CodexError):
                key = (item.kind, item.reference)
        if key in unique:
            existing = unique[key]
            existing.required |= item.required
            alias = f"{item.kind}: {item.reference}"
            if item.source_run_id and existing.source_run_id not in {None, item.source_run_id}:
                alias += f" (source run {item.source_run_id})"
            existing.source_run_id = existing.source_run_id or item.source_run_id
            existing.aliases.append(alias)
        else:
            unique[key] = item
    for index, item in enumerate(unique.values()):
        if index >= package.limits.max_resources:
            item.status = "omitted"
        elif item.status == "pending" and item.kind != "task_output":
            item = read_file(project, item, package.limits)
        package.resources.append(item)
        if package.size_bytes() > package.limits.max_package_bytes:
            item.content, item.sha256, item.status = None, None, "omitted"
        if item.status != "included":
            message = f"{item.kind} {item.reference}: {item.status}. {item.missing_action}"
            # Missing owned files are expected before implementation creates them.
            if item.kind == "owned_file" and item.status == "missing":
                continue
            (package.blockers if item.required else package.warnings).append(message)
    if package.size_bytes() > package.limits.max_package_bytes:
        package.blockers.append(
            "Assignment metadata exceeds the package budget; narrow the assignment."
        )


def update_context_limits(state: DesignSession, path: Path, limits: ContextLimits) -> None:
    from codex_orchestrator.design import check_source

    check_source(state)
    state.require_current(path)
    if max(limits.max_file_bytes, limits.max_handoff_bytes) > limits.max_package_bytes:
        raise ValueError("File and handoff limits must fit inside the package budget.")
    state.context_limits = limits
    state.save(path)
