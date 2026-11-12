"""Resolve a minimal assignment package without inference or directory crawling."""

from pathlib import Path
from typing import Literal

from codex_orchestrator.assignment_context import task_context
from codex_orchestrator.codex import CodexError
from codex_orchestrator.context_files import read_file, resolve_file
from codex_orchestrator.context_models import ContextLimits, ContextPackage, ContextResource
from codex_orchestrator.coordination_store import design_fingerprint
from codex_orchestrator.design_models import Assignment, DesignSession


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
    limits = limits or ContextLimits()
    metadata = task_context(state, task)
    design = state.revisions[-1].design
    metadata["project_goal"] = state.source.brief.goal
    metadata["stage"] = next(s.model_dump() for s in design.stages if s.id == task.stage_id)
    metadata["owned_paths"] = task.owned_paths
    package = ContextPackage(
        design_revision=len(state.revisions),
        design_fingerprint=design_fingerprint(state),
        task_id=task.id,
        agent_id=task.agent_id,
        requested_model=state.revisions[-1].agent_models[task.agent_id],
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
    if (Path(state.project) / "AGENTS.md").is_file():
        resources.append(
            ContextResource(
                kind="instruction",
                reference="AGENTS.md",
                reason="Project-root coding instructions",
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
        if item.status == "unconfigured":
            key = ("unconfigured", item.reference)
        else:
            try:
                key = str(resolve_file(project, item))
            except (OSError, ValueError, CodexError):
                key = (item.kind, item.reference)
        if key in unique:
            existing = unique[key]
            existing.required |= item.required
            existing.aliases.append(f"{item.kind}: {item.reference}")
        else:
            unique[key] = item
    for index, item in enumerate(unique.values()):
        if index >= package.limits.max_resources:
            item.status = "omitted"
        elif item.status == "pending":
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
