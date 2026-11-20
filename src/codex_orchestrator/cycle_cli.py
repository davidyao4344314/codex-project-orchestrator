"""Terminal interface for cycle history and explicit delivery actions."""

import time
from enum import StrEnum
from pathlib import Path

import typer

from codex_orchestrator import (
    coordinator,
    cycle_manager,
    execution,
    git_service,
    recovery,
    review_agent,
    review_store,
    stage_branches,
)
from codex_orchestrator.codex import CodexError
from codex_orchestrator.cycle_store import find_history
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.run_store import Run, run_directory
from codex_orchestrator.stage_review import review_stage

app = typer.Typer(no_args_is_help=True)


def fail(exc):
    typer.echo(str(exc), err=True)
    raise typer.Exit(1) from exc


@app.command("new")
def new(project: Path, goal: str | None = None, directory: Path = Path("plans")):
    """Create a project history and its first cycle without calling a model."""
    try:
        project = project.resolve()
        goal = goal if goal is not None else typer.prompt("What would you like to build?")
        path = cycle_manager.create_project(project, goal, directory.resolve())
        typer.echo(f"Cycle 001 session: {path}")
        typer.echo(f"Continue with: orchestrator resume '{path}'")
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command("register")
def register(source: Path):
    """Adopt an existing planning session as cycle one."""
    try:
        history = cycle_manager.register(source.resolve())
        typer.echo(f"{history.name}: cycle {history.cycles[-1].number:03d} registered.")
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command()
def history(source: Path):
    """Display timestamped goals, outcomes, commits and feedback."""
    try:
        found = find_history(source.resolve())
        if not found:
            raise ValueError("Register this planning session first.")
        typer.echo(found[1].model_dump_json(indent=2))
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command()
def status(source: Path):
    """Inspect the current cycle and recommended next step without launching work."""
    try:
        found = find_history(source.resolve())
        if not found:
            raise ValueError("Register this planning session first.")
        cycle = found[1].cycles[-1]
        typer.echo(f"Cycle {cycle.number:03d} · {cycle.status} · {cycle.goal}")
        current = source.resolve().parent / cycle.session_name
        typer.echo(f"Session: {current}")
        if cycle.status == "completed":
            typer.echo("Ready for the next goal. Run orchestrator cycle next with this session.")
        else:
            typer.echo(recovery.inspect_project(current).model_dump_json(indent=2))
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command("finish")
def finish(source: Path, changes: str | None = None, feedback: str | None = None):
    """Record delivered changes and feedback after all stage merges."""
    try:
        changes = changes if changes is not None else typer.prompt("What changed in this cycle?")
        feedback = feedback if feedback is not None else typer.prompt("Your feedback on this cycle")
        cycle = cycle_manager.complete(source.resolve(), changes, feedback)
        typer.echo(f"Cycle {cycle.number:03d} completed at {cycle.completed_at.isoformat()}.")
        typer.echo("Use cycle next when you have another idea.")
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command("next")
def next_cycle(source: Path, goal: str | None = None):
    """Ask for the next idea and create its independent planning checkpoint."""
    try:
        goal = (
            goal if goal is not None else typer.prompt("What would you like to build or improve?")
        )
        path = cycle_manager.start_next(source.resolve(), goal)
        typer.echo(f"New cycle session: {path}")
        typer.echo(f"Continue with: orchestrator resume '{path}'")
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


class DeliveryAction(StrEnum):
    enable = "enable"
    approve = "approve"
    prepare = "prepare"
    status = "status"
    run = "run"
    retry = "retry"
    verify = "verify"
    accept = "accept"
    pause = "pause"
    resume = "resume"
    review = "review"
    commit = "commit"
    push = "push"
    refresh = "refresh"
    merge = "record-merge"
    recover = "recover"


@app.command()
def deliver(
    source: Path,
    action: DeliveryAction,
    stage: str = "",
    base: str = "main",
    run_id: str = "",
    evidence: str = "",
    message: str = "Implement reviewed stage",
):
    """Operate existing delivery stages without the browser."""
    try:
        source = source.resolve()
        cycle_manager.require_active(source)
        path = source.with_name(source.stem + ".design.json")
        state = DesignSession.load(path)
        project = Path(state.project)
        if action == DeliveryAction.enable:
            stage_branches.enable(path, base)
        elif action == DeliveryAction.approve:
            if typer.confirm("Approve this stage plan?"):
                review_stage(state, path, stage, "", approved=True)
        elif action == DeliveryAction.prepare:
            stage_branches.prepare(path, stage)
        elif action == DeliveryAction.status:
            typer.echo(coordinator.stage_schedule(path, stage).model_dump_json(indent=2))
        elif action in {DeliveryAction.run, DeliveryAction.retry}:
            run = coordinator.run_next(path, stage, retry=action == DeliveryAction.retry)
            folder = run_directory(path) / run.id
            typer.echo(f"Run: {run.id}. Evidence folder: {folder}")
            try:
                while execution.is_busy(project):
                    time.sleep(0.5)
            except KeyboardInterrupt:
                execution.stop_run(run)
                while execution.is_busy(project):
                    time.sleep(0.5)
            typer.echo(Run.load(folder / "run.json").status)
        elif action == DeliveryAction.verify:
            from codex_orchestrator.recovery_brief import safe_folder

            folder = safe_folder(path, run_id)
            typer.echo(f"Inspect evidence at {folder}")
            if typer.confirm("Have you verified the required automated and human checks?"):
                execution.verify_run(path, run_id)
        elif action == DeliveryAction.accept:
            coordinator.accept_stage(path, stage, evidence or typer.prompt("Stage test evidence"))
        elif action == DeliveryAction.pause:
            coordinator.pause(path)
        elif action == DeliveryAction.resume:
            coordinator.resume(path)
        elif action == DeliveryAction.review:
            preview = git_service.preview(path, stage)
            typer.echo(review_agent.run_review(path, preview).model_dump_json(indent=2))
        elif action == DeliveryAction.commit:
            preview = git_service.preview(path, stage)
            reviewer = review_store.require_reviewer(path, stage, preview.fingerprint)
            typer.echo(reviewer.result.model_dump_json(indent=2))
            typer.echo(preview.diff)
            if typer.confirm("Create a local commit for these reviewed changes?"):
                typer.echo(
                    git_service.commit(path, preview, message, approval_id=reviewer.id).commit
                )
        elif action == DeliveryAction.push:
            record = git_service.GitPublication.load(git_service.publication_path(path))
            git_service.validate_publication(path, record)
            typer.echo(f"Commit: {record.commit}\nBranch: {record.branch}\nRemote: {record.remote}")
            if typer.confirm("Give permission to push this commit and its branch history?"):
                permission = git_service.PushPermission(
                    commit=record.commit, branch=record.branch, remote=record.remote
                )
                git_service.push(path, permission=permission)
        elif action == DeliveryAction.refresh:
            stage_branches.refresh_base(path)
        elif action == DeliveryAction.merge:
            stage_branches.record_merge(path, stage)
        elif action == DeliveryAction.recover:
            recovery.recover_interrupted(
                path,
                run_id,
                confirmed=typer.confirm(
                    "Have you inspected partial work and stopped external writers?"
                ),
            )
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)
