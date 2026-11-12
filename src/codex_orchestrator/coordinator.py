"""Explicit, resumable scheduling for a single delivery stage at a time."""

from pathlib import Path
from typing import Literal

from codex_orchestrator import execution
from codex_orchestrator.assignment_context import task_context
from codex_orchestrator.coordination_store import (
    StageAcceptance,
    check_stage_prerequisites,
    checkpoint_path,
    load_coordination,
    stage_accepted,
)
from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.design_validation import context_blockers, task_graph, validate_design
from codex_orchestrator.run_store import Run, latest_runs
from codex_orchestrator.session import SessionData
from codex_orchestrator.stage_review import current_review


class ScheduledTask(SessionData):
    id: str
    title: str
    agent_id: str
    status: Literal[
        "ready", "queued", "running", "awaiting_verification", "verified", "failed", "stopped"
    ]
    dependencies: list[str]
    run_id: str | None = None


class StageSchedule(SessionData):
    stage_id: str
    status: Literal[
        "ready",
        "running",
        "paused",
        "blocked",
        "awaiting_verification",
        "awaiting_stage_acceptance",
        "accepted",
    ]
    reason: str | None = None
    tasks: list[ScheduledTask]


def _current(path: Path) -> DesignSession:
    state = DesignSession.load(path)
    check_source(state)
    if not state.revisions:
        raise ValueError("Complete the technical design first.")
    validate_design(state.revisions[-1].design, state.source.brief, Path(state.project))
    return state


def stage_schedule(path: Path, stage_id: str) -> StageSchedule:
    """Read-only: observing progress never starts, retries, or verifies a run."""
    state = _current(path)
    design = state.revisions[-1].design
    if stage_id not in {s.id for s in design.stages}:
        raise ValueError("Unknown delivery stage.")
    latest = latest_runs(path, len(state.revisions))
    verified = {i for i, r in latest.items() if r.status == "completed" and r.verified}
    graph = task_graph(design)
    tasks = []
    for task in design.tasks:
        if task.stage_id != stage_id:
            continue
        run = latest.get(task.id)
        if run and run.status in {"starting", "running"}:
            status = "running"
        elif run and run.status in {"failed", "stopped"}:
            status = run.status
        elif run and run.status == "completed":
            status = "verified" if run.verified else "awaiting_verification"
        else:
            status = "ready" if graph[task.id] <= verified else "queued"
        tasks.append(
            ScheduledTask(
                id=task.id,
                title=task.title,
                agent_id=task.agent_id,
                status=status,
                dependencies=sorted(graph[task.id]),
                run_id=run.id if run else None,
            )
        )

    def result(status, reason=None):
        return StageSchedule(stage_id=stage_id, status=status, reason=reason, tasks=tasks)

    if load_coordination(state, path).paused:
        return result("paused", "Resume the coordinator to dispatch another task.")
    if any(t.status == "running" for t in tasks) or execution.is_busy(state.project):
        return result("running", "Wait for the active project run to finish or pause it.")
    if state.phase != "approved":
        return result("blocked", "Approve the current technical design first.")
    review = current_review(state, stage_id)
    if not review or not review.approved:
        return result("blocked", "Approve this stage's plan first.")
    try:
        check_stage_prerequisites(state, path, stage_id)
    except ValueError as exc:
        return result("blocked", str(exc))
    if stage_accepted(state, path, stage_id):
        return result("accepted")
    if any(t.status in {"failed", "stopped"} for t in tasks):
        return result(
            "blocked", "A task failed or stopped. Inspect its output and explicitly retry it."
        )
    if all(t.status == "verified" for t in tasks):
        return result(
            "awaiting_stage_acceptance", "Review combined stage changes and required human checks."
        )
    blockers = context_blockers(
        design, Path(state.project), task_ids={t.id for t in tasks if t.status == "ready"}
    )
    if blockers:
        return result("blocked", "; ".join(blockers))
    if any(t.status == "awaiting_verification" for t in tasks):
        return result(
            "awaiting_verification", "Accept the completed task's checks before continuing."
        )
    if any(t.status == "ready" for t in tasks):
        return result("ready")
    return result("blocked", "Prerequisite tasks still need accepted verification.")


def run_next(path: Path, stage_id: str, *, retry=False) -> Run:
    state = _current(path)
    schedule = stage_schedule(path, stage_id)
    candidates = [t for t in schedule.tasks if t.status in {"failed", "stopped"}]
    if retry and schedule.status == "blocked" and candidates:
        selected = candidates[0]
    elif not retry and schedule.status == "ready":
        selected = next(t for t in schedule.tasks if t.status == "ready")
    else:
        raise ValueError(schedule.reason or "No task can be dispatched in this stage.")
    task = next(t for t in state.revisions[-1].design.tasks if t.id == selected.id)
    return execution.start_run(state, path, task, task_context(state, task))


def pause(path: Path) -> None:
    state = _current(path)
    active = [
        r
        for r in latest_runs(path, len(state.revisions)).values()
        if r.status in {"starting", "running"}
    ]
    saved = load_coordination(state, path)
    saved.paused = True
    saved.save(checkpoint_path(state, path))
    for run in active:
        execution.stop_run(run)


def resume(path: Path) -> None:
    state = _current(path)
    if execution.is_busy(state.project):
        raise ValueError("Wait until the active run has stopped before resuming.")
    saved = load_coordination(state, path)
    saved.paused = False
    saved.save(checkpoint_path(state, path))


def accept_stage(path: Path, stage_id: str, evidence: str) -> None:
    if not evidence.strip():
        raise ValueError("Record review and manual-test evidence before accepting a stage.")
    state = _current(path)
    schedule = stage_schedule(path, stage_id)
    if schedule.status != "awaiting_stage_acceptance":
        raise ValueError("Every stage task needs accepted verification before stage acceptance.")
    if state.phase != "approved":
        raise ValueError("Approve the current technical design first.")
    review = current_review(state, stage_id)
    if not review or not review.approved:
        raise ValueError("Approve this stage's plan first.")
    check_stage_prerequisites(state, path, stage_id)
    saved = load_coordination(state, path)
    saved.accepted_stages[stage_id] = StageAcceptance(
        run_ids={t.id: t.run_id for t in schedule.tasks}, evidence=evidence.strip()
    )
    saved.save(checkpoint_path(state, path))
