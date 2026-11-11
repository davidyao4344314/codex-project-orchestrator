"""Complete terminal presentation of an architect's proposed workflow."""

from graphlib import TopologicalSorter
from pathlib import Path

import typer

from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.design_validation import (
    context_blockers,
    project_path,
    skill_status,
    task_graph,
)
from codex_orchestrator.interactive import model_label


def lines(label: str, items: list[str]) -> None:
    if items:
        typer.echo(label)
        for item in items:
            typer.echo(f"  - {item}")


def show_design(state: DesignSession) -> None:
    typer.echo(f"\nTechnical design — {state.phase}")
    typer.echo(f"Approved Stage 1 revision: {state.source_revision}")
    lines(
        "Approved requirements",
        [
            f"R{number}: {requirement}"
            for number, requirement in enumerate(state.source.brief.requirements, start=1)
        ],
    )
    lines("Approved scope exclusions", state.source.brief.exclusions)
    lines("Approved constraints", state.source.brief.constraints)
    if not state.revisions:
        typer.echo("The architect has not produced a complete design yet.")
        if state.pending_question:
            typer.echo(f"Pending question: {state.pending_question}")
        lines("Validation feedback for the next request", state.validation_feedback)
        return
    revision = state.revisions[-1]
    design = revision.design
    project = Path(state.project)
    typer.echo(f"\nDesign revision {len(state.revisions)}")
    source_model = model_label(revision.requested_model, revision.reported_model)
    typer.echo(f"Architect response model: {source_model}")
    if revision.origin == "model_allocation":
        typer.echo("Agent model choices were revised locally using the existing architect design.")
    else:
        typer.echo(f"Architect call time: {revision.elapsed_seconds:.1f}s")
    typer.echo(design.overview)
    lines("\nRepository findings", design.repository_findings)
    typer.echo("\nComponent structure")
    for component in design.components:
        typer.echo(f"  {component.id} — {component.name}: {component.responsibility}")
        lines("    Paths", component.paths)
    typer.echo("\nInterfaces")
    for interface in design.interfaces:
        typer.echo(
            f"  {interface.id}: {interface.source_component} -> "
            f"{interface.target_component or 'external caller/user'}"
        )
        lines("    Inputs", interface.inputs)
        lines("    Outputs", interface.outputs)
        lines("    Errors", interface.errors)
    lines("\nData flow", design.data_flow)
    lines("\nStorage design", design.storage)
    for technology in design.technologies:
        status = "existing" if technology.already_used else "proposed"
        typer.echo(f"Technology: {technology.name} ({status}) — {technology.reason}")
    lines("\nDesign exclusions", design.exclusions)
    lines("\nAssumptions", design.assumptions)
    typer.echo("\nAgent allocation")
    for agent in design.agents:
        selected = revision.agent_models[agent.id] or "Codex configured default"
        typer.echo(f"  {agent.id} — {agent.role}")
        typer.echo(f"    Responsibility: {agent.responsibility}")
        typer.echo(f"    Selected model: {selected}")
        typer.echo(f"    Recommended capability: {agent.model_capability} — {agent.model_reason}")
        lines("    Boundaries", agent.exclusions)
        assigned = [
            f"{task.id} in stage {task.stage_id}"
            for task in design.tasks
            if task.agent_id == agent.id
        ]
        lines("    Assigned tasks", assigned)
        for skill in agent.skills:
            typer.echo(f"    Skill: {skill.name} — {skill_status(skill.location, project)}")
            if skill.location:
                typer.echo(f"      Location: {skill.location}")
    typer.echo(f"\nParallel strategy: {design.parallel_strategy}")
    task_order = list(TopologicalSorter(task_graph(design)).static_order())
    by_task = {task.id: task for task in design.tasks}
    by_stage = {stage.id: stage for stage in design.stages}
    stage_order = TopologicalSorter({stage.id: set(stage.depends_on) for stage in design.stages})
    stage_order.prepare()
    wave = 0
    while stage_order.is_active():
        ready = stage_order.get_ready()
        wave += 1
        typer.echo(f"\nStage dependency group {wave}: {', '.join(ready)}")
        for stage_id in ready:
            stage = by_stage[stage_id]
            typer.echo(f"\nStage {stage.id} — {stage.title}")
            typer.echo(f"  Goal: {stage.goal}")
            if stage.depends_on:
                typer.echo(f"  Starts after verified merge of: {', '.join(stage.depends_on)}")
            lines("  Stage deliverables", stage.deliverables)
            for task_id in task_order:
                task = by_task[task_id]
                if task.stage_id != stage.id:
                    continue
                typer.echo(f"\n  Task {task.id} — {task.title} [agent {task.agent_id}]")
                typer.echo(f"    {task.description}")
                typer.echo(
                    f"    Requirements: {', '.join(f'R{n}' for n in task.requirement_ids)}; "
                    f"components: {', '.join(task.component_ids)}; difficulty: {task.difficulty}"
                )
                lines("    Task prerequisites", task.depends_on)
                lines("    Scope", task.scope)
                lines("    Owned files", task.owned_paths)
                if not task.owned_paths:
                    typer.echo("    File changes: read-only task")
                lines("    Explicit exclusions", task.exclusions)
                typer.echo(
                    "    Context inputs (shared approved brief and task instructions also apply)"
                )
                for item in task.context:
                    availability = "supplied by design"
                    if item.kind == "project_file":
                        availability = (
                            "file found"
                            if project_path(project, item.reference).is_file()
                            else "missing"
                        )
                    elif item.kind == "task_output":
                        availability = "pending upstream task"
                    requirement = "required" if item.required else "optional"
                    typer.echo(
                        f"      {item.kind} {item.reference} [{requirement}, {availability}] "
                        f"— {item.reason}"
                    )
                    typer.echo(f"        If missing: {item.missing_action}")
                lines("    Deliverables", task.deliverables)
                lines("    Acceptance criteria", task.acceptance_criteria)
                lines("    Planned automated checks", task.automated_checks)
                lines("    Planned human checks", task.human_checks)
                typer.echo(f"    Handoff to {', '.join(task.handoff_to)}: {task.handoff_summary}")
                lines("    Escalate when", task.escalation)
            typer.echo(f"\n  Verification gate for stage {stage.id} — PLANNED")
            typer.echo("    Verify the combined stage changes before merging.")
            for check in stage.gate.agent_checks:
                typer.echo(
                    f"    Agent {check.agent_id} checks: {check.check}\n"
                    f"      Expected: {check.expected_result}"
                )
            lines("    Required user tests before merge", stage.gate.user_checks)
            lines("    Stage acceptance criteria", stage.gate.acceptance_criteria)
            typer.echo(
                f"    Review owner: {stage.gate.review_owner}; "
                f"failure owner: {stage.gate.failure_owner}"
            )
            lines("    Completion evidence to report", stage.gate.required_evidence)
            typer.echo(
                "    Merge requires the tested commit, actual check commands/results, "
                "stage review, "
                "and all required user checks. Changed commits require fresh verification."
            )
        stage_order.done(*ready)
    lines("\nRequired context blockers", context_blockers(design, project))
    typer.echo(
        "\nThese are proposed assignments and checks. Plan approval records your design decision."
    )
