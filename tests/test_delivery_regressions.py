"""Regression coverage for the terminal cycle delivery review."""

import pytest
from test_cycle_feedback import wait_for
from test_git_service import accepted as accepted
from test_stage_branch_services import project as project
from test_stage_branches import publish, run_task
from test_worker_execution import launch
from typer.testing import CliRunner

from codex_orchestrator import coordinator, cycle_feedback, cycle_manager, execution
from codex_orchestrator import git_service as git
from codex_orchestrator import stage_branches as branches
from codex_orchestrator.cli import app
from codex_orchestrator.coordination_store import stage_accepted
from codex_orchestrator.cycle_store import find_history
from codex_orchestrator.design import approve_design
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.stage_review import review_stage


def test_cli_failed_retry_exits_nonzero(approved_session, technical_payload, monkeypatch):
    _, path, run, _, _ = launch(
        approved_session, technical_payload, monkeypatch, "backend", mode="test_failed"
    )
    assert run.status == "failed"
    result = CliRunner().invoke(
        app, ["cycle", "deliver", str(path.with_name("source.json")), "retry", "--stage", "core"]
    )
    assert result.exit_code == 1
    assert "Run status: failed" in result.output
    assert "Error:" in result.output and "Inspect evidence:" in result.output
    assert "explicitly retry" in result.output


def test_feedback_on_merged_task_preserves_cycle_and_rechecks_delivery(project, monkeypatch):
    root, path = project
    source = path.with_name("source.json")
    cycle_manager.register(source)
    branches.prepare(path, "core")
    original = run_task(root, path, "core")
    receipt = publish(root, path, "core", monkeypatch)
    git.git(root, "switch", "main")
    git.git(root, "merge", "--ff-only", receipt.branch)
    branches.record_merge(path, "core")
    branches.prepare(path, "delivery")
    run_task(root, path, "delivery")
    record = cycle_feedback.improve(source, "implement", "Improve the display")
    new_source = source.parent / record.session_name
    new_path = new_source.with_suffix(".design.json")
    state = DesignSession.load(new_path)
    assert state.phase == "review"
    assert find_history(source)[1].cycles[0].session_name == new_source.name
    archive = find_history(new_source)[1].cycles[0].archived_deliveries[0]
    assert archive.merged_stages == {"core": receipt.commit}
    assert record.prior_run_id == original.id
    assert stage_accepted(DesignSession.load(path), path, "core")
    assert [t.id for t in state.revisions[-1].design.tasks] == ["implement", "check"]
    assert state.revisions[-1].design.tasks[0].owned_paths == ["counter.py"]
    assert state.revisions[-1].design.tasks[1].owned_paths == []
    assert git.git(root, "symbolic-ref", "--short", "HEAD") == "main"
    approve_design(state, new_path)
    review_stage(state, new_path, record.stage_id, "", approved=True)
    assert coordinator.stage_schedule(new_path, record.stage_id).status == "blocked"
    branches.enable(new_path)
    branches.prepare(new_path, record.stage_id)
    script = root / "fake_worker.py"
    script.write_text(script.read_text().replace("print(5)", "print(6)"))
    corrected = wait_for(
        root, new_path, coordinator.run_next(new_path, record.stage_id, retry=True)
    )
    assert corrected.feedback_id == record.id
    execution.verify_run(new_path, corrected.id)
    coordinator.accept_stage(new_path, record.stage_id, "Correction checks passed")
    fix = publish(root, new_path, record.stage_id, monkeypatch)
    git.git(root, "switch", "main")
    git.git(root, "merge", "--ff-only", fix.branch)
    branches.record_merge(new_path, record.stage_id)
    review_stage(DesignSession.load(new_path), new_path, "delivery", "", approved=True)
    branches.prepare(new_path, "delivery")
    run_task(root, new_path, "delivery")
    cycle_feedback.accept_goal(new_source, "delivery", "The corrected output works")
    final = publish(root, new_path, "delivery", monkeypatch)
    git.git(root, "switch", "main")
    git.git(root, "merge", "--ff-only", final.branch)
    branches.record_merge(new_path, "delivery")
    completed = cycle_manager.complete(new_source, "Display corrected", "Works")
    assert completed.number == 1 and completed.commits == [receipt.commit, fix.commit, final.commit]
    assert (root / "counter.py").read_text() == "print(6)\n"


def test_correction_keeps_merged_dependent_work_read_only(project, monkeypatch):
    root, path = project
    source = path.with_name("source.json")
    cycle_manager.register(source)
    for stage in ("core", "delivery"):
        branches.prepare(path, stage)
        run_task(root, path, stage)
        receipt = publish(root, path, stage, monkeypatch)
        git.git(root, "switch", "main")
        git.git(root, "merge", "--ff-only", receipt.branch)
        branches.record_merge(path, stage)
    # A correction must not discard even unrelated user work.
    (root / "personal.txt").write_text("Preserve this")
    with pytest.raises(ValueError, match="preserve all current changes"):
        cycle_feedback.improve(source, "implement", "Improve the display")
    assert (root / "personal.txt").read_text() == "Preserve this"
    assert find_history(source)[1].cycles[0].session_name == source.name
    git.git(root, "add", "personal.txt")
    git.git(root, "commit", "-m", "Preserve user work")
    record = cycle_feedback.improve(source, "implement", "Improve the display")
    new_source = source.parent / record.session_name
    new_path = new_source.with_suffix(".design.json")
    state = DesignSession.load(new_path)
    tasks = state.revisions[-1].design.tasks
    assert len(tasks) == 2 and len(state.revisions[-1].design.stages) == 1
    assert tasks[0].id == "implement" and tasks[0].owned_paths == ["counter.py"]
    assert tasks[1].owned_paths == [] and tasks[1].depends_on == ["implement"]
    assert tasks[1].automated_checks
    approve_design(state, new_path)
    review_stage(state, new_path, record.stage_id, "", approved=True)
    branches.enable(new_path)
    branches.prepare(new_path, record.stage_id)
    script = root / "fake_worker.py"
    script.write_text(script.read_text().replace("print(5)", "print(6)"))
    first = wait_for(root, new_path, coordinator.run_next(new_path, record.stage_id, retry=True))
    execution.verify_run(new_path, first.id)
    regression = wait_for(root, new_path, coordinator.run_next(new_path, record.stage_id))
    assert regression.worker_profile == "verification"
    execution.verify_run(new_path, regression.id)
    coordinator.accept_stage(new_path, record.stage_id, "Corrected output and regression passed")
    cycle_feedback.accept_goal(new_source, record.stage_id, "The correction works")
    fixed = publish(root, new_path, record.stage_id, monkeypatch)
    git.git(root, "switch", "main")
    git.git(root, "merge", "--ff-only", fixed.branch)
    branches.record_merge(new_path, record.stage_id)
    done = cycle_manager.complete(new_source, "Corrected display", "Works")
    assert done.number == 1 and done.commits[-1] == fixed.commit
    assert (root / "personal.txt").read_text() == "Preserve this"
