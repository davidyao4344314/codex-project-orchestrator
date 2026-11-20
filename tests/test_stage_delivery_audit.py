"""Regression tests for mandatory branches and composed worker verification."""

import pytest
from test_coordinator import coordinated as coordinated
from test_cycle_feedback import wait_for
from test_worker_execution import launch

from codex_orchestrator import coordinator, cycle_manager, execution, recovery
from codex_orchestrator import git_service as git
from codex_orchestrator import stage_branches as branches


def test_managed_cycle_requires_branch_setup(coordinated, monkeypatch):
    state, path = coordinated
    source = path.with_name("source.json")
    root = path.parent
    cycle_manager.register(source, imported=False)
    monkeypatch.setattr(execution, "find_codex", lambda: pytest.fail("Unexpected Codex call"))
    assert coordinator.stage_schedule(path, "core").status == "blocked"
    assert recovery.inspect_project(source).status == "branch_setup_needed"
    with pytest.raises(ValueError, match="Enable stage branches"):
        coordinator.run_next(path, "core")
    with pytest.raises(ValueError, match="Enable stage branches"):
        execution.start_run(state, path, state.revisions[-1].design.tasks[0])
    git.git(root, "init", "-b", "main")
    git.git(root, "config", "user.name", "Test")
    git.git(root, "config", "user.email", "test@example.invalid")
    (root / ".gitignore").write_text("source*\n")
    git.git(root, "add", ".gitignore")
    git.git(root, "commit", "-m", "Initial project")
    branches.enable(path)
    assert coordinator.stage_schedule(path, "core").status == "blocked"
    branches.prepare(path, "core")
    assert coordinator.stage_schedule(path, "core").status == "ready"


@pytest.mark.parametrize("intervening_edit", [False, True])
def test_ordered_shared_file_has_a_verified_snapshot_chain(
    approved_session, technical_payload, monkeypatch, intervening_edit
):
    state, path, first, _, _ = launch(approved_session, technical_payload, monkeypatch, "general")
    root = path.parent
    design = state.revisions[-1].design
    second = design.tasks[0].model_copy(deep=True)
    second.id, second.title, second.depends_on = "refine", "Refine counter", ["implement"]
    design.tasks.append(second)
    state.save(path)
    execution.verify_run(path, first.id)
    if intervening_edit:
        (root / "counter.py").write_text("print(99)\n")
    script = root / "fake_worker.py"
    script.write_text(script.read_text().replace("print(5)", "print(6)"))
    run = wait_for(root, path, coordinator.run_next(path, "core"))
    execution.verify_run(path, run.id)
    if intervening_edit:
        with pytest.raises(ValueError, match="changed between tasks"):
            coordinator.accept_stage(path, "core", "Checked final result")
        return
    coordinator.accept_stage(path, "core", "Checked final result")
    git.connect(root, "https://example.invalid/counter.git")
    assert "print(6)" in git.preview(path, "core").diff
    assert recovery.inspect_project(path.with_name("source.json")).status != "stale_output"
    (root / "counter.py").write_text("print(7)\n")
    with pytest.raises(ValueError, match="changed after execution"):
        git.preview(path, "core")
