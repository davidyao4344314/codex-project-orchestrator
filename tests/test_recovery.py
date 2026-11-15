import subprocess
import sys

import pytest
from test_execution import design as design
from test_git_service import accepted as accepted
from test_git_service import human_commit

from codex_orchestrator import execution, git_service, project_activity, recovery, review_store
from codex_orchestrator.run_store import Run, run_directory
from codex_orchestrator.session import PlanningSession


def unfinished(path, *, worker_pid=2000000000):
    run = Run(
        id="interrupted",
        task_id="implement",
        agent_id="developer",
        revision=1,
        prompt="original",
        status="running",
        worker_pid=worker_pid,
    )
    run.save(run_directory(path) / run.id / "run.json")
    return run


def stale_lease(project, run):
    lease = project_activity.ProjectLease(
        token="old-token",
        owner_pid=2000000000,
        owner_started="old",
        operation="worker",
        run_id=run.id,
    )
    project_activity.lock_path(project).write_text(lease.model_dump_json())
    return lease


def test_resume_requirements_and_design_only_reads_checkpoints(
    approved_session, design, monkeypatch
):
    path, session = approved_session
    _, design_path = design
    monkeypatch.setattr(execution, "start_run", lambda *a: pytest.fail("Started worker"))
    assert recovery.inspect_project(path).view == "Technical design"
    session.phase, session.approved_revision = "intake", None
    session.pending_question = "Who will use this?"
    session.save(path)
    saved = path.read_bytes()
    assert recovery.inspect_project(path, design_path).view == "Requirements"
    assert path.read_bytes() == saved


def test_stale_owner_requires_explicit_recovery_and_keeps_partial_code(design):
    state, path = design
    run = unfinished(path)
    file = path.parent / "counter.py"
    file.write_text("partial work")
    stale_lease(state.project, run)
    before = (run_directory(path) / run.id / "run.json").read_bytes()
    assert recovery.activity(state.project, path).status == "recoverable"
    assert (run_directory(path) / run.id / "run.json").read_bytes() == before
    with pytest.raises(ValueError, match="Confirm"):
        recovery.recover_interrupted(path, run.id)
    recovery.recover_interrupted(path, run.id, confirmed=True)
    saved = Run.load(run_directory(path) / run.id / "run.json")
    assert saved.status == "stopped" and not saved.verified
    assert file.read_text() == "partial work" and not execution.is_busy(state.project)


def test_live_owner_and_legacy_lock_never_reclaimed(design):
    state, path = design
    run = unfinished(path)
    lease = project_activity.create(state.project, "worker", run.id)
    assert recovery.activity(state.project, path).status == "active"
    with pytest.raises(ValueError, match="still alive"):
        recovery.recover_interrupted(path, run.id, confirmed=True)
    project_activity.release(state.project, lease)
    execution.project_lock(state.project).write_text("Legacy lock")
    assert recovery.activity(state.project, path).status == "unknown"
    with pytest.raises(ValueError, match="Legacy"):
        recovery.recover_interrupted(path, run.id, confirmed=True)
    execution.project_lock(state.project).unlink()


def test_real_live_worker_process_blocks_recovery(design):
    state, path = design
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        run = unfinished(path, worker_pid=process.pid)
        stale_lease(state.project, run)
        assert recovery.activity(state.project, path).status == "active"
        with pytest.raises(ValueError, match="still active"):
            recovery.recover_interrupted(path, run.id, confirmed=True)
    finally:
        process.terminate()
        process.wait(timeout=5)
        execution.project_lock(state.project).unlink(missing_ok=True)


def test_missing_lock_can_reconcile_but_unknown_launch_cannot(design):
    state, path = design
    run = unfinished(path, worker_pid=None)
    assert recovery.activity(state.project, path).status == "unknown"
    run.worker_pid = 2000000000
    run.save(run_directory(path) / run.id / "run.json")
    recovery.recover_interrupted(path, run.id, confirmed=True)
    assert not execution.is_busy(state.project)


def test_resume_ai_review_commit_and_push_permission_without_side_effects(accepted, monkeypatch):
    project, path = accepted
    source = path.with_name("source.json")
    assert recovery.inspect_project(source).status == "ai_review_pending"
    reviewed = git_service.preview(path, "core")
    human_commit(path, reviewed, "Reviewed counter")
    before = git_service.publication_path(path).read_bytes()
    monkeypatch.setattr(git_service, "push", lambda *a, **kw: pytest.fail("Pushed"))
    snapshot = recovery.inspect_project(source)
    assert snapshot.status == "push_permission_pending"
    assert snapshot.stage_id == "core" and git_service.publication_path(path).read_bytes() == before
    git_service.git(project, "remote", "set-url", "origin", "https://example.invalid/other.git")
    assert recovery.inspect_project(source).status == "publication_needs_review"


def test_changed_verified_source_is_flagged_for_rerun(accepted):
    project, path = accepted
    (project / "counter.py").write_text("changed after verification")
    snapshot = recovery.inspect_project(path.with_name("source.json"))
    assert snapshot.status == "stale_output" and snapshot.view == "Agent workspace"
    assert snapshot.task_id == "implement"


def test_review_interrupted_record_is_not_assumed_approved(accepted):
    _, path = accepted
    preview = git_service.preview(path, "core")
    review_store.save_review(
        path,
        review_store.ReviewRecord(
            stage_id="core",
            fingerprint=preview.fingerprint,
            source="reviewer",
            result=review_store.ReviewDecision(
                decision="blocked",
                summary="Review interrupted",
                findings=[],
                additional_human_checks=[],
            ),
        ),
    )
    assert recovery.inspect_project(path.with_name("source.json")).status == "ai_review_pending"


def test_lease_release_does_not_delete_a_replacement_lock(tmp_path):
    old = project_activity.create(tmp_path, "worker", "job")
    replacement = old.model_copy(update={"token": "replacement"})
    project_activity.lock_path(tmp_path).write_text(replacement.model_dump_json())
    project_activity.release(tmp_path, old)
    assert project_activity.lock_path(tmp_path).exists()
    project_activity.lock_path(tmp_path).unlink()


def test_recovery_rechecks_lease_before_mutating_run(design, monkeypatch):
    state, path = design
    run = unfinished(path)
    old = stale_lease(state.project, run)
    observed = recovery.activity(state.project, path)
    changed = old.model_copy(update={"token": "new-owner-token"})
    execution.project_lock(state.project).write_text(changed.model_dump_json())
    monkeypatch.setattr(recovery, "activity", lambda *a: observed)
    with pytest.raises(ValueError, match="lease changed"):
        recovery.recover_interrupted(path, run.id, confirmed=True)
    assert Run.load(run_directory(path) / run.id / "run.json").status == "running"
    assert project_activity.load(state.project).token == "new-owner-token"
    execution.project_lock(state.project).unlink()


def test_changed_approved_requirements_direct_back_to_design(accepted):
    _, path = accepted
    source = path.with_name("source.json")
    session = PlanningSession.load(source)
    session.revisions[-1].brief.assumptions.append("Changed approved assumption")
    session.brief = session.revisions[-1].brief.model_copy(deep=True)
    session.save(source)
    snapshot = recovery.inspect_project(source)
    assert snapshot.status == "stale_design" and snapshot.view == "Technical design"
