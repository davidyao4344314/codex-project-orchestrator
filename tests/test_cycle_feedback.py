import json
import time

import pytest
from test_git_service import accepted as accepted
from test_stage_branch_services import project as project
from test_stage_branches import publish, run_task
from test_worker_execution import launch

from codex_orchestrator import coordinator, cycle_feedback, cycle_manager, execution, review_agent
from codex_orchestrator import git_service as git
from codex_orchestrator import stage_branches as branches
from codex_orchestrator.coordination_store import stage_accepted
from codex_orchestrator.cycle_store import find_history
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.models import Workstream
from codex_orchestrator.run_store import Run, latest_runs, run_directory
from codex_orchestrator.worker_models import WorkerSpec


def wait_for(root, path, run):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        saved = Run.load(run_directory(path) / run.id / "run.json")
        if saved.status not in {"running", "starting"} and not execution.is_busy(root):
            assert saved.status == "completed", saved.error
            return saved
        time.sleep(0.02)
    pytest.fail("Fake worker did not finish")


def test_frontend_feedback_only_redeploys_selected_worker(
    approved_session, technical_payload, monkeypatch
):
    state, path, frontend, _, commands = launch(
        approved_session, technical_payload, monkeypatch, "frontend"
    )
    source = path.with_name("source.json")
    root = path.parent
    design = state.revisions[-1].design
    backend = design.tasks[0].model_copy(deep=True)
    specialist = design.agents[0].model_copy(
        update={"id": "backend-dev", "role": "Backend developer"}
    )
    design.agents.append(specialist)
    state.revisions[-1].agent_models["backend-dev"] = "backend-model"
    backend.agent_id = specialist.id
    backend.id, backend.workstream, backend.owned_paths = (
        "backend",
        Workstream.BACKEND,
        ["service.py"],
    )
    design.tasks.append(backend)
    state.save(path)
    execution.verify_run(path, frontend.id)
    backend_run = wait_for(root, path, execution.start_run(state, path, backend))
    execution.verify_run(path, backend_run.id)
    coordinator.accept_stage(path, "core", "Both verified")
    cycle_manager.register(source)
    record = cycle_feedback.improve(source, frontend.task_id, "Improve display spacing")
    assert record.workstream == "frontend" and record.owned_paths == ["counter.py"]
    assert not stage_accepted(state, path, "core")
    assert coordinator.stage_schedule(path, "core").status == "blocked"
    with pytest.raises(ValueError, match="targeted correction"):
        execution.verify_run(path, frontend.id)
    with pytest.raises(ValueError, match="selected feedback task"):
        execution.start_run(state, path, backend)
    script = root / "fake_worker.py"
    script.write_text(script.read_text().replace("print(5)", "print(6)"))
    retry = wait_for(root, path, coordinator.run_next(path, "core", retry=True))
    assert retry.task_id == frontend.task_id and retry.feedback_id == record.id
    payload = json.loads(retry.prompt.split("\n", 1)[1])
    assert payload["assignment_context"]["metadata"]["user_feedback"]["note"] == record.note
    spec = WorkerSpec.load(run_directory(path) / retry.id / "worker_spec.json")
    assert spec.owned_paths == ["counter.py"] and any(c.id == "FEEDBACK" for c in spec.checks)
    assert latest_runs(path, 1)["backend"].id == backend_run.id
    assert len(commands) == 3
    assert (root / "service.py").read_text() == "print(5)\n"
    with pytest.raises(ValueError, match="selected feedback task"):
        execution.start_run(state, path, backend)
    execution.verify_run(path, retry.id)
    coordinator.accept_stage(path, "core", "Correction and regression checks passed")
    assert stage_accepted(state, path, "core")
    assert find_history(source)[1].cycles[0].feedback_rounds[0].id == record.id


def test_goal_achieved_feedback_goes_in_reviewed_final_commit(project, monkeypatch):
    root, path = project
    source = path.with_name("source.json")
    cycle_manager.register(source)
    history_file, history = find_history(source)
    history.cycles[0].feedback_enabled = True
    history.save(history_file)
    branches.prepare(path, "core")
    run_task(root, path, "core")
    receipt = publish(root, path, "core", monkeypatch)
    git.git(root, "switch", "main")
    git.git(root, "merge", "--ff-only", receipt.branch)
    branches.record_merge(path, "core")
    branches.prepare(path, "delivery")
    run_task(root, path, "delivery")
    original = git.preview(path, "delivery")
    review = review_agent.run_review(path, original)
    with pytest.raises(ValueError, match="goal-achieved"):
        git.commit(path, original, "Not yet accepted", approval_id=review.id)
    feedback = cycle_feedback.accept_goal(source, "delivery", "All required behavior works.")
    revised = git.preview(path, "delivery")
    assert revised.fingerprint != original.fingerprint
    final = publish(root, path, "delivery", monkeypatch)
    assert final.commit != receipt.commit
    message = git.git(root, "show", "-s", "--format=%B", final.commit)
    assert "Cycle 001: Goal achieved" in message
    assert feedback.note in message and feedback.id in message
    git.git(root, "switch", "main")
    git.git(root, "merge", "--ff-only", final.branch)
    branches.record_merge(path, "delivery")
    completed = cycle_manager.complete(source, "Delivered", feedback.note)
    assert completed.feedback == feedback.note and final.commit in completed.commits


def test_feedback_blocks_if_dependent_task_already_ran(accepted):
    _, path = accepted
    source = path.with_name("source.json")
    state = DesignSession.load(path)
    design = state.revisions[-1].design
    task = design.tasks[0]
    dependent = task.model_copy(deep=True)
    dependent.id, dependent.depends_on, dependent.owned_paths = "dependent", [task.id], []
    design.tasks.append(dependent)
    state.save(path)
    old = latest_runs(path, 1)[task.id]
    copy = old.model_copy(update={"id": "dependent-run", "task_id": "dependent"})
    copy.save(run_directory(path) / copy.id / "run.json")
    cycle_manager.register(source)
    with pytest.raises(ValueError, match="wider plan"):
        cycle_feedback.improve(source, task.id, "Change output contract")
