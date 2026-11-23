import copy
import sys
from pathlib import Path

import pytest
from test_persistent_workers import wait
from test_worker_execution import FAKE_CODEX

from codex_orchestrator import coordinator, execution, stage_branches
from codex_orchestrator import git_service as git
from codex_orchestrator.branch_allocation import isolated_tasks
from codex_orchestrator.design import prepare_design
from codex_orchestrator.design_models import DesignRevision, TechnicalDesign
from codex_orchestrator.design_validation import assign_stage_checks
from codex_orchestrator.stage_review import review_stage


def parallel_design(payload):
    payload = copy.deepcopy(payload)
    agent = copy.deepcopy(payload["agents"][0])
    agent["id"] = "frontend"
    payload["agents"].append(agent)
    task = copy.deepcopy(payload["tasks"][0])
    task.update(id="format", agent_id="frontend", owned_paths=["format.py"])
    payload["tasks"].append(task)
    check = copy.deepcopy(payload["tasks"][0])
    check.update(id="verify", owned_paths=[], depends_on=["implement", "format"], human_checks=[])
    payload["tasks"].append(check)
    design = TechnicalDesign.model_validate(payload)
    assign_stage_checks(design)
    return design


def test_allocator_uses_isolation_only_for_independent_agents(technical_payload):
    simple = TechnicalDesign.model_validate(technical_payload)
    assert isolated_tasks(simple, "core") == set()
    design = parallel_design(technical_payload)
    assert isolated_tasks(design, "core") == {"implement", "format"}
    design.tasks[1].depends_on = ["implement"]
    assert isolated_tasks(design, "core") == set()


def test_allocator_avoids_shared_file_context(technical_payload):
    design = parallel_design(technical_payload)
    reference = design.tasks[1].context[0].model_copy()
    reference.kind, reference.reference = "project_file", "counter.py"
    design.tasks[1].context.append(reference)
    assert isolated_tasks(design, "core") == set()


@pytest.fixture
def isolated_project(approved_session, technical_payload, monkeypatch):
    source, _ = approved_session
    root = source.parent
    state, path = prepare_design(source, None, architect_model=None, agent_model=None)
    design = parallel_design(technical_payload)
    state.revisions.append(
        DesignRevision(
            design=design, elapsed_seconds=0, agent_models={"developer": None, "frontend": None}
        )
    )
    state.phase, state.approved_revision = "approved", 1
    state.save(path)
    review_stage(state, path, "core", "", approved=True)
    git.initialize(root)
    git.git(root, "config", "user.name", "Test")
    git.git(root, "config", "user.email", "test@example.invalid")
    (root / ".gitignore").write_text("source*\nfake_worker.py\n")
    (root / "counter.py").write_text("print('base')\n")
    git.git(root, "add", ".gitignore", "counter.py")
    git.git(root, "commit", "-m", "Initialize project")
    git.git(root, "switch", "-c", "develop")
    assert stage_branches.default_base(root) == "develop"
    stage_branches.enable(path, "develop")
    stage_branches.prepare(path, "core")
    script = root / "fake_worker.py"
    script.write_text(FAKE_CODEX.replace("mode = MODE", "mode = 'completed'"))
    real_popen = execution.subprocess.Popen

    def spawn(command, **kwargs):
        if command[0] == "fake-codex":
            return real_popen([sys.executable, str(script), *command[1:]], **kwargs)
        return real_popen(command, **kwargs)

    monkeypatch.setattr(execution, "find_codex", lambda: "fake-codex")
    monkeypatch.setattr(execution.subprocess, "Popen", spawn)
    return root, path, state, script


def test_agent_worktrees_integrate_and_preserve_stage_publication_gate(
    isolated_project, monkeypatch
):
    from codex_orchestrator import review_agent, review_store

    root, path, state, _ = isolated_project
    stage_head = git.git(root, "rev-parse", "HEAD")
    runs = []
    for task in state.revisions[-1].design.tasks:
        run = wait(path, root, execution.start_run(state, path, task))
        assert run.status == "completed", run.error
        execution.verify_run(path, run.id, automated=True)
        runs.append(run)
    first, second, final = runs
    assert first.agent_branch and second.agent_branch and first.agent_branch != second.agent_branch
    assert first.agent_commit and second.agent_commit and not final.agent_branch
    assert (Path(first.agent_worktree) / "counter.py").exists()
    assert (Path(second.agent_worktree) / "counter.py").read_text() == "print('base')\n"
    assert (root / "counter.py").exists() and (root / "format.py").exists()
    assert git.git(root, "rev-parse", "HEAD") == stage_head
    assert git.git(root, "rev-parse", "main") == stage_head
    assert not git.git(root, "diff", "--cached", "--name-only")
    coordinator.accept_stage(path, "core", "Tested integrated feature")
    preview = git.preview(path, "core")
    assert set(preview.paths) == {"counter.py", "format.py"}
    assert not git.publication_path(path).exists()
    with pytest.raises(ValueError, match="approval"):
        git.commit(path, preview, "Deliver feature")
    monkeypatch.setattr(
        review_agent,
        "codex_response",
        lambda *args, **kwargs: review_store.ReviewDecision(
            decision="approved",
            summary="Reviewed integrated feature",
            findings=[],
            additional_human_checks=[],
        ),
    )
    review_agent.run_review(path, preview)
    approval = git.approve_user(path, preview)
    receipt = git.commit(path, preview, "Deliver integrated feature", approval_id=approval.id)
    git.git(root, "switch", "develop")
    git.git(root, "merge", "--ff-only", receipt.branch)
    stage_branches.record_merge(path, "core")
    assert git.git(root, "rev-parse", "develop") == receipt.commit
    assert git.git(root, "rev-parse", "main") == stage_head


def test_failed_agent_does_not_integrate_partial_edits(isolated_project):
    root, path, state, script = isolated_project
    script.write_text(FAKE_CODEX.replace("mode = MODE", "mode = 'outside'"))
    task = state.revisions[-1].design.tasks[0]
    run = wait(path, root, execution.start_run(state, path, task))
    assert run.status == "blocked", run.error
    assert run.agent_branch and not run.agent_commit
    assert (Path(run.agent_worktree) / "unowned.py").exists()
    assert (root / "counter.py").read_text() == "print('base')\n"
    assert not (root / "unowned.py").exists()


def test_external_edit_blocks_agent_integration(isolated_project, monkeypatch):
    from codex_orchestrator import agent_worktrees

    root, path, state, _ = isolated_project
    real_integrate = agent_worktrees.integrate

    def concurrent_edit(*args):
        (root / "counter.py").write_text("print('user edit')\n")
        return real_integrate(*args)

    monkeypatch.setattr(agent_worktrees, "integrate", concurrent_edit)
    run = wait(path, root, execution.start_run(state, path, state.revisions[-1].design.tasks[0]))
    assert run.status == "failed" and "Agent inputs differ" in run.error
    assert (root / "counter.py").read_text() == "print('user edit')\n"
    assert (Path(run.agent_worktree) / "counter.py").read_text() == "print(5)\n"
    assert not run.agent_commit
