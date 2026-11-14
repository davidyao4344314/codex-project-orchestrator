import pytest
from test_git_service import accepted as accepted

from codex_orchestrator import git_service as git
from codex_orchestrator import stage_branches as branches
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.stage_review import review_stage


@pytest.fixture
def project(accepted):
    root, path = accepted
    state = DesignSession.load(path)
    design = state.revisions[-1].design
    stage = design.stages[0].model_copy(deep=True)
    stage.id, stage.depends_on = "delivery", ["core"]
    stage.title = "Delivery verification"
    design.stages.append(stage)
    task = design.tasks[0].model_copy(deep=True)
    task.id, task.stage_id, task.depends_on, task.owned_paths = (
        "check",
        "delivery",
        ["implement"],
        [],
    )
    design.tasks.append(task)
    state.save(path)
    review_stage(state, path, "delivery", "Ready", approved=True)
    (root / ".gitignore").write_text("source*\nfake_worker.py\n")
    git.git(root, "branch", "-M", "main")
    git.git(root, "add", ".gitignore", "counter.py")
    git.git(root, "commit", "-m", "Initial project")
    git.git(root, "rm", "counter.py")
    git.git(root, "commit", "-m", "Prepare implementation fixture")
    branches.enable(path)
    return root, path


def test_dirty_files_and_active_operation_prevent_branch_switch(project):
    root, path = project
    (root / "personal.txt").write_text("Keep this")
    with pytest.raises(ValueError, match="preserve all current changes"):
        branches.prepare(path, "core")
    assert git.git(root, "symbolic-ref", "--short", "HEAD") == "main"
    with git.writer(root):
        with pytest.raises(ValueError, match="active project"):
            branches.prepare(path, "core")


def test_new_revision_needs_explicit_setup_and_preserves_history(project):
    root, path = project
    branches.prepare(path, "core")
    state = DesignSession.load(path)
    state.revisions.append(state.revisions[-1].model_copy(deep=True))
    state.approved_revision = 2
    state.save(path)
    with pytest.raises(ValueError, match="new design revision"):
        branches.require_branch(state, path, "core")
    git.git(root, "switch", "main")
    branches.enable(path)
    assert len(branches.load(path).revisions) == 2
    branches.prepare(path, "core")
    assert "-r2-" in git.git(root, "symbolic-ref", "--short", "HEAD")


def test_refresh_base_fast_forwards_but_never_overwrites_divergence(project, monkeypatch):
    root, path = project
    remote = root.parent / (root.name + "-remote.git")
    upstream = root.parent / (root.name + "-upstream")
    # All transport is to temporary local repositories; no real hosting/account traffic.
    git.git(root, "init", "--bare", str(remote))
    git.git(root, "push", str(remote), "main:refs/heads/main")
    git.git(root, "clone", "-b", "main", str(remote), str(upstream))
    git.git(upstream, "config", "user.name", "Test")
    git.git(upstream, "config", "user.email", "test@example.invalid")
    (upstream / "README.md").write_text("Reviewed upstream integration")
    git.git(upstream, "add", "README.md")
    git.git(upstream, "commit", "-m", "Upstream change")
    git.git(upstream, "push", "origin", "main")
    git.git(root, "remote", "set-url", "origin", str(remote))
    monkeypatch.setattr(git, "validate_remote", lambda url: url)
    branches.refresh_base(path)
    assert git.git(root, "rev-parse", "main") == git.git(upstream, "rev-parse", "main")
    git.git(root, "commit", "--allow-empty", "-m", "Local-only change")
    local_head = git.git(root, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="diverged"):
        branches.refresh_base(path)
    assert git.git(root, "rev-parse", "HEAD") == local_head
