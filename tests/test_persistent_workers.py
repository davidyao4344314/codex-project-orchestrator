import copy
import json
import time

import pytest
from test_worker_execution import launch

from codex_orchestrator import execution, session_manager
from codex_orchestrator.run_store import Run, run_directory


def wait(path, project, run):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        saved = Run.load(run_directory(path) / run.id / "run.json")
        if saved.status not in {"running", "starting"} and not execution.is_busy(project):
            return saved
        time.sleep(0.02)
    pytest.fail("Fake worker did not finish")


def test_worker_sessions_reuse_same_role_and_keep_other_roles_and_read_only_separate(
    approved_session, technical_payload, monkeypatch
):
    payload = copy.deepcopy(technical_payload)
    for task_id, workstream, files in (
        ("service", "backend", ["service.py"]),
        ("ui", "frontend", ["ui.py"]),
        ("storage", "database", ["storage.py"]),
        ("check", "backend", []),
    ):
        task = copy.deepcopy(payload["tasks"][0])
        task.update(
            id=task_id,
            title=task_id,
            workstream=workstream,
            owned_paths=files,
            depends_on=["implement"],
        )
        payload["tasks"].append(task)
    state, path, first, _, commands = launch(approved_session, payload, monkeypatch, "backend")
    assert first.status == "completed" and first.token_usage.input_tokens == 100
    execution.verify_run(path, first.id)
    observed = [first]
    for task in state.revisions[-1].design.tasks[1:]:
        run = wait(path, state.project, execution.start_run(state, path, task))
        assert run.status == "completed", run.error
        context = json.loads(run.prompt.split("\n", 1)[1])["assignment_context"]
        assert context["task_id"] == task.id
        assert context["metadata"]["assignment"]["owned_paths"] == task.owned_paths
        execution.verify_run(path, run.id)
        observed.append(run)
    assert observed[1].thread_id == first.thread_id
    assert observed[1].resume_thread_id == first.thread_id
    assert commands[1][-3:] == ["resume", first.thread_id, "-"]
    assert all("resume" not in command for command in commands[2:])
    assert len({r.thread_id for r in observed}) == 4
    assert commands[-1][commands[-1].index("--sandbox") + 1] == "read-only"
    assert len(session_manager.read_turns(path)) == 5
    assert len(session_manager.read_registry(path).slots) == 4


def test_failed_resumed_worker_is_retired_before_explicit_retry(
    approved_session, technical_payload, monkeypatch
):
    state, path, original, _, commands = launch(
        approved_session, technical_payload, monkeypatch, "backend"
    )
    script = path.parent / "fake_worker.py"
    original_script = script.read_text()
    script.write_text(
        original_script.replace("mode = 'completed'", "mode = 'test_failed'").replace(
            "print(5)", "print(6)"
        )
    )
    task = state.revisions[-1].design.tasks[0]
    failed = wait(path, state.project, execution.start_run(state, path, task))
    assert failed.status == "failed" and failed.resume_thread_id == original.thread_id
    assert len(commands) == 2
    script.write_text(original_script.replace("print(5)", "print(7)"))
    retried = wait(path, state.project, execution.start_run(state, path, task))
    assert retried.status == "completed", retried.error
    assert retried.resume_thread_id is None and retried.thread_id != original.thread_id
