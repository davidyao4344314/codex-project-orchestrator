"""Terminal interface for cycle history and explicit delivery actions."""

import json
import time
from enum import StrEnum
from pathlib import Path

import typer

from codex_orchestrator import (
    coordinator,
    cycle_feedback,
    cycle_manager,
    execution,
    git_service,
    recovery,
    review_agent,
    review_store,
    stage_branches,
    usage_report,
)
from codex_orchestrator.codex import CodexError
from codex_orchestrator.cycle_store import find_history
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.run_store import Run, latest_runs, run_directory
from codex_orchestrator.session import PlanningRoute, PlanningSession
from codex_orchestrator.stage_review import review_stage

app = typer.Typer(no_args_is_help=True)


def fail(exc):
    typer.echo(str(exc), err=True)
    raise typer.Exit(1) from exc


@app.command("plan")
def combined_plan(source: Path, timeout: int = typer.Option(300, min=1)):
    """Gather requirements and review the scope and technical design together."""
    from codex_orchestrator.design import prepare_design, run_design
    from codex_orchestrator.interactive import run_session

    try:
        source = source.resolve()
        cycle_manager.require_active(source)
        session = PlanningSession.load(source)
        if session.phase in {"intake", "planning"}:
            session.planning_route = PlanningRoute.OUTLINE
            session.save(source)
            run_session(session, source, draft_only=True)
        if session.phase not in {"review", "approved"}:
            return
        state, path = prepare_design(
            source, None, architect_model=None, agent_model=None, combined_approval=True
        )
        if state.phase != "approved":
            run_design(state, path, timeout)
        if state.phase == "approved":
            stage = next(s for s in state.revisions[-1].design.stages if not s.depends_on)
            typer.echo(f"Next: orchestrator cycle feature '{source}' --stage {stage.id}")
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command()
def feature(
    source: Path,
    stage: str = typer.Option(..., help="Approved delivery stage ID"),
    base: str | None = typer.Option(None, help="Integration base (defaults to checked-out branch)"),
    retry: bool = typer.Option(False, help="Explicitly retry one blocked task"),
    retry_review: bool = typer.Option(False, help="Explicitly retry independent review"),
):
    """Deliver an approved feature with one user testing and publication checkpoint."""
    from codex_orchestrator.feature_delivery import deliver_feature

    try:
        deliver_feature(source.resolve(), stage, base, retry=retry, retry_review=retry_review)
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command("new")
def new(
    project: Path,
    goal: str | None = None,
    directory: Path = Path("plans"),
    route: PlanningRoute = PlanningRoute.AUTO,
    local: bool = typer.Option(False, "--local", help="Skip GitHub connection setup"),
):
    """Create a project history and its first cycle without calling a model."""
    try:
        project = project.resolve()
        if not local:
            from codex_orchestrator.github_cli import show_connection

            show_connection(project)
        goal = goal if goal is not None else typer.prompt("What would you like to build?")
        path = cycle_manager.create_project(project, goal, directory.resolve(), route=route)
        typer.echo(f"Cycle 001 session: {path}")
        typer.echo(f"Continue with: orchestrator cycle plan '{path}'")
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command("github")
def github(project: Path, status_only: bool = typer.Option(False, "--status-only")):
    """Check GitHub connection or connect an existing remote before planning."""
    from codex_orchestrator.github_cli import show_connection

    try:
        show_connection(project.resolve(), offer=not status_only)
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
            snapshot = recovery.inspect_project(current)
            typer.echo(snapshot.model_dump_json(indent=2))
            design_path = current.with_suffix(".design.json")
            if design_path.exists():
                state = DesignSession.load(design_path)
                if state.phase == "approved" and snapshot.status not in {
                    "stale_design",
                    "active",
                    "recoverable",
                    "unknown",
                    "paused",
                    "stale_output",
                    "retry_pending",
                    "merge_pending",
                    "publication_needs_review",
                }:
                    settings, revision = stage_branches.current(state, design_path)
                    merged = (
                        {key for key, item in revision.stages.items() if item.merged_commit}
                        if revision
                        else set()
                    )
                    ready = next(
                        (
                            s
                            for s in state.revisions[-1].design.stages
                            if s.id not in merged and set(s.depends_on) <= merged
                        ),
                        None,
                    )
                    if ready:
                        base = (
                            settings.base
                            if settings
                            else stage_branches.default_base(Path(state.project))
                        )
                        typer.echo(
                            f"Continue feature: orchestrator cycle feature '{current}' "
                            f"--stage {ready.id} --base {base}"
                        )
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command()
def usage(
    source: Path, details: bool = typer.Option(False, help="Include individual turn records.")
):
    """Inspect local token and timing metrics without calling Codex."""
    try:
        typer.echo(json.dumps(usage_report.usage_report(source, details=details), indent=2))
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command()
def sessions(source: Path):
    """Inspect saved requirement, architect and worker session IDs."""
    try:
        typer.echo(json.dumps(usage_report.session_report(source), indent=2))
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command()
def models(
    source: Path,
    show: bool = False,
    routine: str | None = None,
    standard: str | None = None,
    strong: str | None = None,
):
    """Discover model IDs and approve a difficulty-based model palette for this cycle."""
    from codex_orchestrator.model_catalog import discover_models, selected_policy

    try:
        source = source.resolve()
        state = PlanningSession.load(source)
        if show:
            if any(value is not None for value in (routine, standard, strong)):
                raise ValueError("Omit model overrides when using --show.")
            if state.model_policy is None:
                typer.echo("No automatic model palette configured.")
            else:
                typer.echo(state.model_policy.model_dump_json(indent=2))
            return
        cycle_manager.require_active(source)
        if execution.is_busy(state.project):
            raise ValueError("Wait for the active project operation before changing models.")
        offers = discover_models(Path(state.project))
        typer.echo("Selectable models reported by Codex:")
        for offer in offers:
            typer.echo(f"  {offer.id} — {offer.label}")
        policy = selected_policy(offers, routine=routine, standard=standard, strong=strong)
        typer.echo("Proposed model palette:")
        typer.echo(policy.model_dump_json(indent=2))
        typer.echo("Null uses your Codex default; account access may change.")
        if not typer.confirm(
            "Use this palette for future planning and automatic agent allocation?"
        ):
            return
        with git_service.writer(Path(state.project)):
            cycle_manager.require_active(source)
            state.require_current(source)
            state.model_policy = policy
            state.intake_model = policy.routine
            state.planning_model = policy.standard
            state.models_configured = True
            state.save(source)
        typer.echo(
            "Saved. Future designs use this palette; existing approved models are unchanged."
        )
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command()
def context(
    source: Path,
    task: str = "",
    full: bool = False,
    budget_kib: int | None = None,
    file_kib: int | None = None,
    handoff_kib: int | None = None,
    max_resources: int | None = None,
):
    """Inspect a task's context or explicitly adjust local context budgets."""
    from codex_orchestrator.context_builder import build_context, update_context_limits
    from codex_orchestrator.context_models import ContextLimits
    from codex_orchestrator.design import check_source

    try:
        source = source.resolve()
        cycle_manager.require_active(source)
        path = source.with_suffix(".design.json")
        state = DesignSession.load(path)
        check_source(state)
        if not state.revisions:
            raise ValueError("Create a technical design first.")
        changes = {
            key: value * 1024
            for key, value in (
                ("max_package_bytes", budget_kib),
                ("max_file_bytes", file_kib),
                ("max_handoff_bytes", handoff_kib),
            )
            if value is not None
        }
        if max_resources is not None:
            changes["max_resources"] = max_resources
        if changes:
            limits = ContextLimits.model_validate({**state.context_limits.model_dump(), **changes})
            with git_service.writer(Path(state.project)):
                cycle_manager.require_active(source)
                update_context_limits(state, path, limits)
        design = state.revisions[-1].design
        if not task:
            typer.echo(
                json.dumps(
                    {
                        "limits": state.context_limits.model_dump(),
                        "tasks": [
                            {"id": t.id, "title": t.title, "agent": t.agent_id}
                            for t in design.tasks
                        ],
                    },
                    indent=2,
                )
            )
            return
        assignment = next((t for t in design.tasks if t.id == task), None)
        if assignment is None:
            raise ValueError("Unknown task ID. Run cycle context without --task to list tasks.")
        package = build_context(state, assignment, path)
        from codex_orchestrator.worker_profiles import worker_spec
        from codex_orchestrator.worker_runtime import compact_worker_prompt

        wire_preview = None
        wire_blocker = None
        if not package.blockers:
            try:
                prompt, _, _ = compact_worker_prompt(worker_spec(package), package, [])
                wire_preview = len(prompt.encode("utf-8"))
            except ValueError as exc:
                wire_blocker = str(exc)
        if full:
            typer.echo(package.model_dump_json(indent=2))
        else:
            typer.echo(
                json.dumps(
                    {
                        "task_id": task,
                        "agent_id": package.agent_id,
                        "snapshot_bytes": package.size_bytes(),
                        "fresh_worker_prompt_bytes": wire_preview,
                        "worker_prompt_blocker": wire_blocker,
                        "owned_file_count": len(assignment.owned_paths),
                        "limits": package.limits.model_dump(),
                        "blockers": package.blockers,
                        "warnings": package.warnings,
                        "resources": [
                            resource.model_dump(exclude={"content"})
                            for resource in package.resources
                        ],
                        "note": "Fresh preview; branch, audit paths and retry context add bytes.",
                    },
                    indent=2,
                )
            )
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command("finish")
def finish(source: Path, changes: str | None = None, feedback: str | None = None):
    """Record delivered changes and feedback after all stage merges."""
    try:
        changes = changes if changes is not None else typer.prompt("What changed in this cycle?")
        _, _, cycle = cycle_manager.active_cycle(source.resolve())
        if cycle.feedback_enabled:
            accepted = next(
                (r for r in reversed(cycle.feedback_rounds) if r.kind == "goal_achieved"), None
            )
            feedback = accepted.note if accepted else ""
        else:
            feedback = (
                feedback if feedback is not None else typer.prompt("Your feedback on this cycle")
            )
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
        typer.echo(f"Continue with: orchestrator cycle plan '{path}'")
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


@app.command()
def benchmark(source: Path, run_id: str):
    """Replay a saved worker payload locally without launching Codex."""
    from codex_orchestrator.recovery_brief import safe_folder
    from codex_orchestrator.worker_models import WorkerSpec
    from codex_orchestrator.worker_runtime import compact_worker_prompt, saved_context

    try:
        folder = safe_folder(source.resolve().with_suffix(".design.json"), run_id)
        run = Run.load(folder / "run.json")
        package = saved_context(run, folder)
        spec = WorkerSpec.load(folder / "worker_spec.json")
        prompt, _, _ = compact_worker_prompt(spec, package, [], branch=run.branch)
        original = len(run.prompt.encode("utf-8"))
        optimized = len(prompt.encode("utf-8"))
        typer.echo(
            json.dumps(
                {
                    "original_bytes": original,
                    "fresh_replay_bytes": optimized,
                    "saved_bytes": original - optimized,
                    "largest_source_files": [
                        {"path": resource.reference, "bytes": resource.size_bytes}
                        for resource in sorted(
                            package.resources, key=lambda item: item.size_bytes or 0, reverse=True
                        )[:10]
                    ],
                    "note": "Fresh replay excludes retry history; bytes are not account usage.",
                },
                indent=2,
            )
        )
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
    reconcile = "reconcile"


@app.command()
def feedback(
    source: Path,
    kind: str = typer.Option("", help="improve or goal-achieved"),
    task: str = "",
    stage: str = "",
    note: str = "",
):
    """Record a correction or final acceptance without starting a model."""
    try:
        source = source.resolve()
        kind = kind or typer.prompt("Feedback type (improve / goal-achieved)")
        if kind not in {"improve", "goal-achieved"}:
            raise ValueError("Choose improve or goal-achieved.")
        note = note or typer.prompt("What should change, or what worked?")
        if kind == "improve":
            task = task or typer.prompt("Affected task ID")
            record = cycle_feedback.improve(source, task, note)
            typer.echo(record.model_dump_json(indent=2))
            show_correction(source, record)
        elif kind == "goal-achieved":
            stage = stage or typer.prompt("Final stage ID")
            record = cycle_feedback.accept_goal(source, stage, note)
            typer.echo(record.model_dump_json(indent=2))
            typer.echo("Run independent review, then commit and approve the push.")
    except (OSError, ValueError, CodexError) as exc:
        fail(exc)


def show_correction(source, record):
    current = source.parent / (record.session_name or source.name)
    if current != source:
        typer.echo(f"Scoped correction session (same cycle): {current}")
        typer.echo(f"Review its design: orchestrator design '{current}'")
        typer.echo(f"Enable branches: orchestrator cycle deliver '{current}' enable --base <base>")
        typer.echo(f"Approve and prepare stage: {record.stage_id}")
    typer.echo(
        f"Retry only this task: orchestrator cycle deliver '{current}' retry "
        f"--stage {record.stage_id}"
    )


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
            typer.echo(
                "Codex session: "
                + (f"resuming {run.resume_thread_id}" if run.resume_thread_id else "new")
            )
            if run.full_prompt_bytes is not None:
                supplied = len(run.prompt.encode("utf-8"))
                typer.echo(
                    f"Prompt: {supplied:,} bytes "
                    f"({run.full_prompt_bytes - supplied:,} fewer than full payload; "
                    f"{run.reused_context_files} unchanged file references)"
                )
            try:
                while execution.is_busy(project):
                    time.sleep(0.5)
            except KeyboardInterrupt:
                execution.stop_run(run)
                while execution.is_busy(project):
                    time.sleep(0.5)
            finished = Run.load(folder / "run.json")
            typer.echo(f"Run status: {finished.status}")
            if finished.thread_id:
                typer.echo(f"Session ID: {finished.thread_id}")
            if finished.token_usage:
                usage = finished.token_usage
                typer.echo(
                    f"Turn tokens: input {usage.input_tokens}, cached {usage.cached_input_tokens}, "
                    f"output {usage.output_tokens}"
                )
            if finished.session_warning:
                typer.echo(f"Session metadata: {finished.session_warning}", err=True)
            if finished.status != "completed":
                from codex_orchestrator.recovery_brief import clipped

                if finished.error:
                    typer.echo(f"Error: {clipped(finished.error)}", err=True)
                typer.echo(f"Inspect evidence: {folder}", err=True)
                typer.echo("Resolve the problem and explicitly retry the task.", err=True)
                raise typer.Exit(130 if finished.status == "stopped" else 1)
        elif action == DeliveryAction.reconcile:
            from codex_orchestrator.evidence_recovery import reconcile_blocked_run

            recovered = reconcile_blocked_run(path, run_id)
            typer.echo(
                f"Run {recovered.id}: completed. No Codex call; human verification still required."
            )
        elif action == DeliveryAction.verify:
            from codex_orchestrator.recovery_brief import safe_folder

            folder = safe_folder(path, run_id)
            typer.echo(f"Inspect evidence at {folder}")
            record = Run.load(folder / "run.json")
            current = latest_runs(path, len(state.revisions)).get(record.task_id)
            implementation = any(
                t.id == record.task_id and t.owned_paths for t in state.revisions[-1].design.tasks
            )
            if (
                find_history(source)
                and implementation
                and current
                and current.id == record.id
                and typer.confirm(
                    "Do you want to request an improvement to this task before verification?",
                    default=False,
                )
            ):
                feedback_record = cycle_feedback.improve(
                    source, record.task_id, typer.prompt("Describe the correction")
                )
                typer.echo(feedback_record.model_dump_json(indent=2))
                show_correction(source, feedback_record)
                return
            if typer.confirm("Have you verified the required automated and human checks?"):
                execution.verify_run(path, run_id)
        elif action == DeliveryAction.accept:
            coordinator.accept_stage(path, stage, evidence or typer.prompt("Stage test evidence"))
            found = find_history(source)
            if (
                found
                and found[1].cycles[-1].feedback_enabled
                and cycle_feedback.final_stage(state, path, stage)
            ):
                typer.echo("Final stage checks accepted. Record improve or goal-achieved feedback.")
                typer.echo(f"Next: orchestrator cycle feedback '{source}'")
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
                approval = git_service.approve_user(path, preview)
                typer.echo(
                    git_service.commit(path, preview, message, approval_id=approval.id).commit
                )
        elif action == DeliveryAction.push:
            record = git_service.GitPublication.load(git_service.publication_path(path))
            if not record.remote:
                raise ValueError("This is a local-only commit. No push destination is configured.")
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
