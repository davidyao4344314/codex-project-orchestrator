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
    github_pr,
    review_agent,
    review_store,
    stage_branches,
)
from codex_orchestrator.coordination_store import design_fingerprint
from codex_orchestrator.cycle_store import find_history
from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.design_validation import stage_check_description
from codex_orchestrator.github_pr_cli import show_description
from codex_orchestrator.pr_description import description
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
            if run.agent_branch:
                typer.echo(f"  Agent branch: {run.agent_branch}")
                typer.echo(f"  Agent workspace: {run.agent_worktree}")
            try:
                while execution.is_busy(project):
                    time.sleep(0.5)
            except KeyboardInterrupt:
                execution.stop_run(run)
                raise
            finished = Run.load(run_directory(path) / run.id / "run.json")
            typer.echo(f"{finished.task_id}: {finished.status}")
            if finished.agent_commit:
                typer.echo("  Audited agent changes integrated into the stage branch.")
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
        run = Run.load(folder / "run.json")
        if run.agent_branch:
            typer.echo(f"  Agent branch: {run.agent_branch}")
            typer.echo(f"  Integrated draft: {run.agent_commit or 'not integrated'}")
        report_path = folder / "worker_result.json"
        human_checks = list(task.human_checks)
        if report_path.exists():
            report = WorkerResult.load(report_path)
            for check in report.checks:
                typer.echo(f"  Automated check {check.check_id}: {check.outcome} — {check.detail}")
            human_checks.extend(report.human_checks)
        for check in dict.fromkeys(human_checks):
            typer.echo(f"  User check: {check}")
    for index, check in enumerate(stage.gate.agent_checks, 1):
        typer.echo(f"  Combined check: {check.check} (expected: {check.expected_result})")
        description = stage_check_description(stage, index, check)
        if not any(
            description in task.automated_checks
            for task in state.revisions[-1].design.tasks
            if task.stage_id == stage.id
        ):
            typer.echo(
                "    Legacy plan: no separate evidence mapping; confirm this check manually."
            )
    for check in stage.gate.user_checks:
        typer.echo(f"  User test: {check}")


def deliver_feature(
    source: Path, stage_id: str, base: str | None, *, retry=False, retry_review=False
) -> None:
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
    if retry_review and coordinator.stage_schedule(path, stage_id).status != "accepted":
        raise ValueError("Complete feature testing before retrying independent review.")
    saved_branches = stage_branches.load(path)
    if saved_branches is None or stage_branches.revision_key(state) not in saved_branches.revisions:
        stage_branches.enable(
            path,
            base
            or (saved_branches.base if saved_branches else stage_branches.default_base(project)),
        )
    settings, revision = stage_branches.current(state, path)
    if base and base != settings.base:
        raise ValueError(f"This cycle uses integration base {settings.base}.")
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
                details = None
                if github_pr.repository_for(receipt.remote) and receipt.commit != item.start_commit:
                    details = description(state, path, stage_id, receipt.paths)
                    show_description(details)
                question = (
                    "Approve push and GitHub pull request for this commit?"
                    if details
                    else "Approve push of this existing commit?"
                )
                if typer.confirm(question):
                    pushed = push_receipt(path, receipt)
                    if details:
                        publish_pr(path, source, pushed, details)
            else:
                typer.echo(f"Feature committed: {receipt.commit}. Merge and record the stage.")
                if receipt.pushed and github_pr.repository_for(receipt.remote):
                    pr_guidance(path, source, state, receipt)
            return
    stage_branches.prepare(path, stage_id)
    github_pr.preflight_branch(state, path, stage_id, git_service.git(project, "rev-parse", "HEAD"))
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
    github_pr.preflight_branch(state, path, stage_id, preview.head)
    review = review_store.latest_reviewer(path, stage_id)
    if retry_review or review is None or review.fingerprint != preview.fingerprint:
        review = review_agent.run_review(path, preview)
    elif review.input_id == review.id:
        typer.echo("Review was interrupted. Resume with --retry-review to request another review.")
        return
    typer.echo(review.result.model_dump_json(indent=2))
    if review.result.decision != "approved":
        typer.echo(
            "Review needs attention. Record scoped feedback or use --retry-review "
            "to retry only the review."
        )
        return
    typer.echo(f"Branch: {preview.branch}\nDestination: {preview.remote or 'local Git repository'}")
    typer.echo("Files: " + ", ".join(preview.paths))
    details = None
    if github_pr.repository_for(preview.remote):
        details = description(state, path, stage_id, preview.paths)
        show_description(details)
    question = (
        "Approve feature, commit, push and create GitHub pull request?"
        if details
        else "Approve feature, commit and push?"
        if preview.remote
        else "Approve feature and commit locally?"
    )
    if not typer.confirm(question):
        return
    approval = git_service.approve_user(path, preview)
    receipt = git_service.commit(path, preview, f"Deliver {stage.title}", approval_id=approval.id)
    typer.echo(f"Committed {receipt.commit}")
    if receipt.remote:
        pushed = push_receipt(path, receipt)
        if details:
            publish_pr(path, source, pushed, details)
            return
    typer.echo(f"Merge the reviewed stage into {settings.base} and record its merge.")


def push_receipt(path, receipt):
    permission = git_service.PushPermission(
        commit=receipt.commit, branch=receipt.branch, remote=receipt.remote
    )
    pushed = git_service.push(path, permission=permission)
    typer.echo(f"Pushed {receipt.commit} to {receipt.branch}")
    return pushed


def pr_guidance(path, source, state, receipt):
    _, revision = stage_branches.current(state, path)
    if receipt.commit == revision.stages[receipt.stage_id].start_commit:
        typer.echo(
            f"Next: orchestrator cycle deliver '{source}' record-merge --stage {receipt.stage_id}"
        )
        return
    record = github_pr.saved_pr(path, state, receipt.stage_id)
    if record:
        typer.echo(f"PR #{record.number}: {record.url}")
        action = "merge" if record.merge_commit else "status"
    else:
        action = "create"
    typer.echo(f"Next: orchestrator cycle pr '{source}' {action} --stage {receipt.stage_id}")


def publish_pr(path, source, receipt, details):
    state = DesignSession.load(path)
    _, revision = stage_branches.current(state, path)
    if receipt.commit == revision.stages[receipt.stage_id].start_commit:
        typer.echo("No new stage commit for a PR. Record the existing base merge.")
        pr_guidance(path, source, state, receipt)
        return
    try:
        record = github_pr.create(path, receipt.stage_id, approved_description=details)
    except (OSError, ValueError):
        typer.echo("The approved push was saved. Resume PR creation without rerunning workers.")
        pr_guidance(path, source, state, receipt)
        raise
    typer.echo(f"PR #{record.number}: {record.url}")
    typer.echo(f"Next: orchestrator cycle pr '{source}' status --stage {receipt.stage_id}")
