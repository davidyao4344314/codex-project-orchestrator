"""Read-only resume guidance and explicit reconciliation of interrupted workers."""

from pathlib import Path
from typing import Literal

from pydantic import Field

from codex_orchestrator import (
    execution,
    git_service,
    project_activity,
    review_store,
    stage_branches,
)
from codex_orchestrator.codex import CodexError
from codex_orchestrator.coordination_store import load_coordination
from codex_orchestrator.coordinator import stage_schedule
from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.recovery_brief import safe_folder
from codex_orchestrator.run_store import Run, latest_runs, runs
from codex_orchestrator.session import PlanningSession, SessionData
from codex_orchestrator.worker_runtime import check_worker_verification


class ResumeSnapshot(SessionData):
    view: Literal["Requirements", "Technical design", "Dashboard", "Agent workspace"] = "Dashboard"
    status: str
    next_action: str
    last_completed: str | None = None
    stage_id: str | None = None
    task_id: str | None = None
    branch: str | None = None
    head: str | None = None
    warnings: list[str] = Field(default_factory=list)


class InterruptedWork(SessionData):
    run_id: str | None = None
    lease_token: str | None = None
    status: Literal["idle", "active", "recoverable", "unknown"]
    reason: str


def activity(project: Path, path: Path) -> InterruptedWork:
    unfinished = [r for r in runs(path) if r.status in {"starting", "running"}]
    lease = project_activity.load(project)
    locked = execution.project_lock(project).exists()
    if lease and project_activity.owner_alive(lease):
        return InterruptedWork(
            status="active",
            run_id=lease.run_id,
            reason="The owning application process is still alive. Inspect or stop its work first.",
        )
    if locked and lease is None:
        return InterruptedWork(
            status="unknown",
            reason="Legacy or unreadable lock. Inspect processes and the lock in your terminal.",
        )
    for run in unfinished:
        if (
            run.id in execution._active
            or project_activity.alive(run.worker_pid)
            or project_activity.group_alive(run.worker_pid)
        ):
            return InterruptedWork(
                status="active",
                run_id=run.id,
                reason="A worker or its group is still active. Do not start another writer.",
            )
    if lease and (lease.operation != "worker" or lease.run_id not in {r.id for r in unfinished}):
        return InterruptedWork(
            status="unknown",
            reason="Interrupted Git/review: inspect processes before manual lock recovery.",
        )
    if unfinished:
        if len(unfinished) != 1 or not unfinished[0].worker_pid or unfinished[0].worker_pid <= 0:
            return InterruptedWork(
                status="unknown",
                reason="Worker identity is incomplete. Inspect Codex processes manually.",
            )
        return InterruptedWork(
            status="recoverable",
            lease_token=lease.token if lease else None,
            run_id=unfinished[0].id,
            reason="No owner or worker was found. Inspect edits, then recover explicitly.",
        )
    if locked:
        return InterruptedWork(
            status="unknown",
            reason="Unmatched project lock. Inspect it manually.",
        )
    return InterruptedWork(status="idle", reason="No unfinished worker is recorded.")


def recover_interrupted(path: Path, run_id: str, *, confirmed=False) -> None:
    if not confirmed:
        raise ValueError(
            "Confirm you inspected partial edits and stopped external writers before recovery."
        )
    state = DesignSession.load(path)
    project = Path(state.project).resolve()
    with execution._guard:
        observed = activity(project, path)
        if observed.status != "recoverable" or observed.run_id != run_id:
            raise ValueError(observed.reason)
        lease = project_activity.load(project)
        if (lease.token if lease else None) != observed.lease_token:
            raise ValueError("The project lease changed. Check saved progress again.")
        if lease and (lease.run_id != run_id or project_activity.owner_alive(lease)):
            raise ValueError("The project lock belongs to a different run.")
        # If the old lock is gone, exclusively acquire the same lock before reconciliation.
        acquired = None
        if lease is None:
            acquired = project_activity.create(project, "recovery", run_id)
        try:
            folder = safe_folder(path, run_id)
            run = Run.load(folder / "run.json")
            if (
                run.status not in {"starting", "running"}
                or project_activity.alive(run.worker_pid)
                or project_activity.group_alive(run.worker_pid)
            ):
                raise ValueError("The run or worker changed. Check saved progress again.")
            run.status, run.verified = "stopped", False
            run.error = "Recovered worker. Partial files preserved; retry and rerun all checks."
            run.save(folder / "run.json")
        finally:
            if acquired:
                project_activity.release(project, acquired)
        if lease:
            project_activity.release(project, lease)


def inspect_project(source: Path, design_path: Path | None = None) -> ResumeSnapshot:
    session = PlanningSession.load(source)
    if session.phase != "approved":
        return ResumeSnapshot(
            view="Requirements",
            status=session.phase,
            next_action="Answer the saved question or review/continue requirements explicitly.",
        )
    path = design_path or source.with_name(source.stem + ".design.json")
    if not path.exists():
        return ResumeSnapshot(
            view="Technical design",
            status="design_needed",
            next_action="Create the technical design from your approved requirements.",
        )
    state = DesignSession.load(path)
    try:
        check_source(state)
    except (ValueError, CodexError) as exc:
        return ResumeSnapshot(view="Technical design", status="stale_design", next_action=str(exc))
    if not state.revisions or state.phase != "approved":
        return ResumeSnapshot(
            view="Technical design",
            status=state.phase,
            next_action="Continue the saved architect question or review and approve the design.",
        )
    project = Path(state.project).resolve()
    snapshot = ResumeSnapshot(
        status="ready",
        last_completed=f"Technical design revision {len(state.revisions)} approved",
        next_action="Select an approved stage and explicitly dispatch its next task.",
    )
    completed = [
        r for r in latest_runs(path, len(state.revisions)).values() if r.status == "completed"
    ]
    if completed:
        last = max(completed, key=lambda r: r.created_at)
        snapshot.last_completed = f"Task {last.task_id}: completed" + (
            " and verification accepted" if last.verified else "; verification pending"
        )
    observed = activity(project, path)
    if observed.status != "idle":
        snapshot.status, snapshot.next_action = observed.status, observed.reason
        snapshot.warnings.append(
            "Opening or refreshing this project never restarts a worker or releases a lock."
        )
        return snapshot
    if git_service.repository(project):
        snapshot.branch = (
            git_service.git(project, "symbolic-ref", "--short", "HEAD", optional=True)
            or "Detached HEAD"
        )
        snapshot.head = (
            git_service.git(project, "rev-parse", "--verify", "HEAD", optional=True) or None
        )
    receipt_path = git_service.publication_path(path)
    if receipt_path.exists():
        receipt = git_service.GitPublication.load(receipt_path)
        if not receipt.pushed and receipt.design_fingerprint == stage_branches.design_fingerprint(
            state
        ):
            snapshot.stage_id, snapshot.status = receipt.stage_id, "push_permission_pending"
            snapshot.last_completed = f"Local commit {receipt.commit[:12]} prepared"
            snapshot.next_action = (
                "Inspect the existing commit and give fresh push permission in Git connection."
            )
            try:
                git_service.validate_publication(path, receipt)
            except (OSError, ValueError, CodexError) as exc:
                snapshot.status = "publication_needs_review"
                snapshot.warnings.append(str(exc))
                snapshot.next_action = "Resolve stale publication inputs before pushing."
            return snapshot
    if load_coordination(state, path).paused:
        snapshot.status, snapshot.next_action = (
            "paused",
            "Resume the coordinator explicitly; resuming does not dispatch a task.",
        )
        return snapshot
    try:
        settings, branch_revision = stage_branches.current(state, path)
    except (ValueError, CodexError) as exc:
        snapshot.status, snapshot.next_action = "branch_setup_needed", str(exc)
        return snapshot
    latest = stage_branches.valid_runs(state, path, latest_runs(path, len(state.revisions)))
    for stage in state.revisions[-1].design.stages:
        if (
            branch_revision
            and stage.id in branch_revision.stages
            and branch_revision.stages[stage.id].merged_commit
        ):
            continue
        schedule = stage_schedule(path, stage.id)
        snapshot.stage_id = stage.id
        for task in schedule.tasks:
            run = latest.get(task.id)
            if run and run.branch and snapshot.branch and run.branch != snapshot.branch:
                snapshot.warnings.append(
                    f"Task {task.id} ran on {run.branch}; current branch is {snapshot.branch}."
                )
            if run and run.status == "completed" and run.worker_contract_version:
                try:
                    check_worker_verification(run, project, safe_folder(path, run.id))
                except (OSError, ValueError, CodexError) as exc:
                    snapshot.view, snapshot.task_id = "Agent workspace", task.id
                    snapshot.status = "stale_output"
                    snapshot.next_action = "Source changed. Inspect differences and rerun the task."
                    snapshot.warnings.append(str(exc))
                    return snapshot
            if run and (run.status != "completed" or not run.verified):
                snapshot.view, snapshot.task_id = "Agent workspace", task.id
                snapshot.status = (
                    "retry_pending" if run.status != "completed" else "verification_pending"
                )
                snapshot.next_action = (
                    "Inspect partial files and diagnostics, then explicitly start a new attempt."
                    if run.status != "completed"
                    else "Review evidence and perform required tests before accepting verification."
                )
                return snapshot
        if schedule.status == "accepted":
            snapshot.status, snapshot.next_action = (
                "ai_review_pending",
                "Open Git connection and run the independent AI review of a fresh preview.",
            )
            if receipt_path.exists():
                receipt = git_service.GitPublication.load(receipt_path)
                if (
                    receipt.pushed
                    and receipt.stage_id == stage.id
                    and receipt.design_fingerprint == stage_branches.design_fingerprint(state)
                ):
                    snapshot.last_completed = f"Stage commit {receipt.commit[:12]} pushed"
                    snapshot.status, snapshot.next_action = (
                        "merge_pending",
                        "Merge the pushed stage, refresh the base and record its merge.",
                    )
                    return snapshot
            try:
                preview = git_service.preview(path, stage.id)
                review_store.require_reviewer(path, stage.id, preview.fingerprint)
                snapshot.status, snapshot.next_action = (
                    "commit_pending",
                    "AI review approved. Prepare a local commit; push needs your permission.",
                )
            except (OSError, ValueError, CodexError) as exc:
                snapshot.warnings.append(str(exc))
            return snapshot
        snapshot.status, snapshot.next_action = (
            schedule.status,
            schedule.reason or snapshot.next_action,
        )
        review = next(
            (
                r
                for r in reversed(review_store.load_reviews(path).records)
                if r.stage_id == stage.id and r.source == "reviewer"
            ),
            None,
        )
        if review:
            snapshot.warnings.append(
                f"Last AI review: {review.result.decision}. Recheck against a current preview."
            )
        return snapshot
    snapshot.status, snapshot.next_action = (
        "complete",
        "All managed stages are merged. Perform final acceptance.",
    )
    return snapshot
