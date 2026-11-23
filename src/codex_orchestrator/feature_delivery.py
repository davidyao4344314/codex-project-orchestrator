"""Explicit terminal feature delivery using existing execution and publication gates."""

import time
from pathlib import Path

import typer

from codex_orchestrator import (
    coordinator,
    cycle_feedback,
    cycle_manager,
    execution,
    git_service,
    review_agent,
    review_store,
    stage_branches,
)
from codex_orchestrator.coordination_store import design_fingerprint
from codex_orchestrator.cycle_store import find_history
from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.run_store import Run, run_directory
from codex_orchestrator.stage_review import current_review, review_stage


def advance_tasks(path, stage_id, project, *, retry=False):
    while True:
        schedule = coordinator.stage_schedule(path, stage_id)
        if schedule.status in {"accepted", "awaiting_stage_acceptance"}:
            return schedule
        if schedule.status == "awaiting_verification":
            task = next(t for t in schedule.tasks if t.status == "awaiting_verification")
            execution.verify_run(path, task.run_id, automated=True)
            typer.echo(f"{task.id}: automated checks verified")
        elif schedule.status == "ready" or (schedule.status == "blocked" and retry):
            retrying = schedule.status == "blocked"
            run = coordinator.run_next(path, stage_id, retry=retrying)
            retry = False
            typer.echo(f"Running {run.task_id}; evidence: {run_directory(path) / run.id}")
            try:
                while execution.is_busy(project):
                    time.sleep(0.5)
            except KeyboardInterrupt:
                execution.stop_run(run)
                raise
            finished = Run.load(run_directory(path) / run.id / "run.json")
            typer.echo(f"{finished.task_id}: {finished.status}")
            if finished.status != "completed":
                raise ValueError(
                    f"Task {finished.task_id} stopped: {finished.error or finished.status}"
                )
        else:
            raise ValueError(schedule.reason or f"Stage is {schedule.status}.")


def feature_tests(state, path, stage, schedule):
    from codex_orchestrator.worker_models import WorkerResult

    typer.echo(f"\nFeature: {stage.title}\nProject: {state.project}")
    for item in schedule.tasks:
        task = next(t for t in state.revisions[-1].design.tasks if t.id == item.id)
        typer.echo(f"{task.id}: {item.status} — {task.title}")
        typer.echo("Files: " + ", ".join(task.owned_paths))
        folder = run_directory(path) / item.run_id
        report_path = folder / "worker_result.json"
        human_checks = list(task.human_checks)
        if report_path.exists():
            report = WorkerResult.load(report_path)
            for check in report.checks:
                typer.echo(f"  Automated check {check.check_id}: {check.outcome} — {check.detail}")
            human_checks.extend(report.human_checks)
        for check in dict.fromkeys(human_checks):
            typer.echo(f"  User check: {check}")
    for check in stage.gate.agent_checks:
        typer.echo(f"  Combined check: {check.check} (expected: {check.expected_result})")
    for check in stage.gate.user_checks:
        typer.echo(f"  User test: {check}")


def deliver_feature(source: Path, stage_id: str, base: str, *, retry=False) -> None:
    cycle_manager.require_active(source)
    path = source.with_suffix(".design.json")
    state = DesignSession.load(path)
    check_source(state)
    if state.phase != "approved":
        raise ValueError("Approve the technical design before delivering this feature.")
    stage = next((s for s in state.revisions[-1].design.stages if s.id == stage_id), None)
    if stage is None:
        raise ValueError("Unknown delivery stage.")
    project = Path(state.project)
    if execution.is_busy(project):
        raise ValueError("Wait for the active project operation to finish.")
    if stage_branches.load(path) is None:
        stage_branches.enable(path, base)
    settings, revision = stage_branches.current(state, path)
    item = revision.stages.get(stage_id)
    if item and item.merged_commit:
        typer.echo("Feature already merged.")
        return
    receipt_path = git_service.publication_path(path)
    if receipt_path.exists():
        receipt = git_service.GitPublication.load(receipt_path)
        if receipt.stage_id == stage_id and receipt.design_fingerprint == design_fingerprint(state):
            if receipt.remote and not receipt.pushed:
                git_service.validate_publication(path, receipt)
                typer.echo(f"Commit {receipt.commit} → {receipt.remote} ({receipt.branch})")
                if typer.confirm("Approve push of this existing commit?"):
                    push_receipt(path, receipt)
            else:
                typer.echo(f"Feature committed: {receipt.commit}. Merge and record the stage.")
            return
    stage_branches.prepare(path, stage_id)
    if not (review := current_review(state, stage_id)) or not review.approved:
        review_stage(state, path, stage_id, "", approved=True)
    schedule = advance_tasks(path, stage_id, project, retry=retry)
    state = DesignSession.load(path)
    feature_tests(state, path, stage, schedule)
    if schedule.status != "accepted":
        note = typer.prompt("Enter combined test evidence, or :improve / :save")
        if note == ":save":
            return
        if note == ":improve":
            task_id = typer.prompt("Affected task ID")
            cycle_feedback.improve(source, task_id, typer.prompt("Describe the improvement"))
            typer.echo("Feedback saved. Run cycle feature again with --retry to continue.")
            return
        coordinator.accept_stage(path, stage_id, note)
    found = find_history(source)
    if (
        found
        and found[1].cycles[-1].feedback_enabled
        and cycle_feedback.final_stage(state, path, stage_id)
        and cycle_feedback.decision(state, path, stage_id) is None
    ):
        cycle_feedback.accept_goal(source, stage_id, "Feature checks accepted by operator.")
    preview = git_service.preview(path, stage_id)
    try:
        review = review_store.require_reviewer(path, stage_id, preview.fingerprint)
    except ValueError:
        review = review_agent.run_review(path, preview)
    typer.echo(review.result.model_dump_json(indent=2))
    if review.result.decision != "approved":
        typer.echo("Review needs attention. Record scoped feedback before retrying.")
        return
    typer.echo(f"Branch: {preview.branch}\nDestination: {preview.remote or 'local Git repository'}")
    typer.echo("Files: " + ", ".join(preview.paths))
    question = (
        "Approve feature, commit and push?"
        if preview.remote
        else "Approve feature and commit locally?"
    )
    if not typer.confirm(question):
        return
    approval = git_service.approve_user(path, preview)
    receipt = git_service.commit(path, preview, f"Deliver {stage.title}", approval_id=approval.id)
    typer.echo(f"Committed {receipt.commit}")
    if receipt.remote:
        push_receipt(path, receipt)
    typer.echo(f"Merge the reviewed stage into {settings.base} and record its merge.")


def push_receipt(path, receipt):
    permission = git_service.PushPermission(
        commit=receipt.commit, branch=receipt.branch, remote=receipt.remote
    )
    git_service.push(path, permission=permission)
    typer.echo(f"Pushed {receipt.commit} to {receipt.branch}")
