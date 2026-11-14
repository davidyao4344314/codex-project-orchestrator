import time

import pytest
from test_git_service import accepted as accepted
from test_stage_branch_services import project as project

from codex_orchestrator import coordinator, execution, review_agent
from codex_orchestrator import git_service as git
from codex_orchestrator import stage_branches as branches
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.run_store import Run


def run_task(root, path, stage):
    state = DesignSession.load(path)
    task = next(t for t in state.revisions[-1].design.tasks if t.stage_id == stage)
    run = execution.start_run(state, path, task)
    folder = execution.run_directory(path) / run.id
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = Run.load(folder / "run.json")
        if run.status not in {"starting", "running"} and not execution.is_busy(root):
            break
        time.sleep(0.02)
    assert run.status == "completed", run.error
    execution.verify_run(path, run.id)
    coordinator.accept_stage(path, stage, "Manual and automated checks passed")
    return run


def publish(root, path, stage, monkeypatch):
    preview = git.preview(path, stage)
    reviewed = review_agent.run_review(path, preview)
    receipt = git.commit(path, preview, "Implement stage", approval_id=reviewed.id)
    actual = git.git

    def push(project, *args, **kwargs):
        return "" if "push" in args else actual(project, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(git, "git", push)
        git.push(
            path,
            permission=git.PushPermission(
                commit=receipt.commit, branch=receipt.branch, remote=receipt.remote
            ),
        )
    return receipt


def test_execution_and_publication_require_stage_branch(project, monkeypatch):
    root, path = project
    state = DesignSession.load(path)
    assert coordinator.stage_schedule(path, "core").status == "blocked"
    monkeypatch.setattr(execution, "find_codex", lambda: pytest.fail("Called before branch check"))
    with pytest.raises(ValueError, match="stage's branch"):
        execution.start_run(state, path, state.revisions[-1].design.tasks[0])
    branches.prepare(path, "core")
    name = git.git(root, "symbolic-ref", "--short", "HEAD")
    assert "stage-1-core" in name and name.startswith("orchestrator/")
    assert coordinator.stage_schedule(path, "core").status == "ready"
    (root / "counter.py").write_text("partial work")
    branches.prepare(path, "core")
    assert (root / "counter.py").read_text() == "partial work"


def test_stage_merge_gates_next_branch_and_no_change_stage(project, monkeypatch):
    root, path = project
    branches.prepare(path, "core")
    run = run_task(root, path, "core")
    assert run.branch == git.git(root, "symbolic-ref", "--short", "HEAD")
    receipt = publish(root, path, "core", monkeypatch)
    assert coordinator.stage_schedule(path, "delivery").status == "blocked"
    with pytest.raises(ValueError, match="Merge prerequisite"):
        branches.prepare(path, "delivery")
    with pytest.raises(ValueError, match="not merged"):
        branches.record_merge(path, "core")
    git.git(root, "switch", "main")
    git.git(root, "merge", "--ff-only", receipt.branch)
    branches.record_merge(path, "core")
    branches.prepare(path, "delivery")
    item = branches.current(DesignSession.load(path), path)[1].stages["delivery"]
    assert item.start_commit == receipt.commit
    run_task(root, path, "delivery")
    preview = git.preview(path, "delivery")
    assert preview.paths == [] and preview.diff == ""
    unchanged = publish(root, path, "delivery", monkeypatch)
    assert unchanged.commit == receipt.commit  # Verification-only work needs no empty commit.
    branches.record_merge(path, "delivery")
    assert branches.current(DesignSession.load(path), path)[1].stages["delivery"].merged_commit


def test_merge_requires_user_approved_push_not_just_local_ancestry(project):
    root, path = project
    branches.prepare(path, "core")
    run_task(root, path, "core")
    preview = git.preview(path, "core")
    approval = review_agent.run_review(path, preview)
    receipt = git.commit(path, preview, "Local only", approval_id=approval.id)
    git.git(root, "switch", "main")
    git.git(root, "merge", "--ff-only", receipt.branch)
    with pytest.raises(ValueError, match="user-approved push"):
        branches.record_merge(path, "core")


def test_new_evidence_invalidates_recorded_prerequisite_merge(project, monkeypatch):
    from codex_orchestrator.run_store import run_directory

    root, path = project
    branches.prepare(path, "core")
    run = run_task(root, path, "core")
    receipt = publish(root, path, "core", monkeypatch)
    git.git(root, "switch", "main")
    git.git(root, "merge", "--ff-only", receipt.branch)
    branches.record_merge(path, "core")
    folder = run_directory(path) / run.id
    evidence = git.review_evidence(folder)
    evidence.commands[0].output = "Evidence changed after merge"
    evidence.save(folder / "worker_evidence.json")
    with pytest.raises(ValueError, match="Merge prerequisite"):
        branches.prepare(path, "delivery")
