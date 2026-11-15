import json
import sys
import threading

import pytest

from codex_orchestrator import execution
from codex_orchestrator.design import prepare_design
from codex_orchestrator.design_models import DesignRevision, TechnicalDesign
from codex_orchestrator.reassignment import reassign
from codex_orchestrator.stage_review import review_stage


@pytest.fixture
def design(approved_session, technical_payload):
    source, _ = approved_session
    state, path = prepare_design(source, None, architect_model=None, agent_model=None)
    state.revisions.append(
        DesignRevision(
            design=TechnicalDesign.model_validate(technical_payload),
            agent_models={"developer": None},
            elapsed_seconds=0,
        )
    )
    state.phase = "review"
    state.save(path)
    return state, path


def test_reassignment_versions_ownership_and_invalidates_approval(design):
    state, path = design
    before = state.revisions[0].model_dump()
    reassign(state, path, "implement", "developer", new_role="Test specialist", model="selected")
    assert len(state.revisions) == 2
    assert state.revisions[0].model_dump() == before
    revision = state.revisions[-1]
    owner = revision.design.tasks[0].agent_id
    assert owner != "developer"
    assert revision.agent_models[owner] == "selected"
    assert state.phase == "review"
    assert revision.origin == "assignment"
    execution.project_lock(state.project).write_text("active")
    with pytest.raises(ValueError, match="Stop"):
        reassign(state, path, "implement", "developer")


def test_execution_requires_approvals_and_serializes_writers(design, monkeypatch):
    state, path = design
    task = state.revisions[-1].design.tasks[0]
    with pytest.raises(ValueError, match="technical design"):
        execution.start_run(state, path, task, {})
    state.phase, state.approved_revision = "approved", 1
    state.save(path)
    with pytest.raises(ValueError, match="stage"):
        execution.start_run(state, path, task, {})
    review_stage(state, path, "core", "", approved=True)
    monkeypatch.setattr(execution, "find_codex", lambda: "fake-codex")
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    run = execution.start_run(state, path, task, {"assignment": task.model_dump()})
    assert run.status == "starting"
    assert "Implement only" in run.prompt
    from codex_orchestrator.context_models import ContextPackage

    snapshot = ContextPackage.load(execution.run_directory(path) / run.id / "context.json")
    assert snapshot.task_id == task.id
    assert snapshot.digest() == run.context_digest
    assert snapshot.metadata["assignment"] == task.model_dump()
    with pytest.raises(ValueError, match="active"):
        execution.start_run(state, path, task, {})
    execution._active.pop(run.id)
    execution.project_lock(state.project).unlink()


@pytest.mark.parametrize("success", [True, False])
def test_worker_persists_thread_events_and_releases_lock(tmp_path, monkeypatch, success):
    # Exercise the real subprocess IO and checkpoint path with a local fake Codex program.
    script = tmp_path / "fake.py"
    events = [
        {"type": "thread.started", "thread_id": "12345678-1234-1234-1234-123456789abc"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Done"}},
    ]
    if success:
        events.append({"type": "turn.completed", "usage": {}})
    script.write_text(
        "import sys,json\nsys.stdin.read()\n"
        + "\n".join(f"print({json.dumps(json.dumps(e))}, flush=True)" for e in events)
    )
    original = execution.subprocess.Popen
    commands = []

    def fake(command, **kwargs):
        if command[0] == "ps":
            return original(command, **kwargs)
        commands.append(command)
        return original([sys.executable, str(script)], **kwargs)

    monkeypatch.setattr(execution.subprocess, "Popen", fake)
    folder = tmp_path / "runs" / "job"
    folder.mkdir(parents=True)
    run = execution.Run(id="job", task_id="t", agent_id="a", revision=1, prompt="context")
    run.save(folder / "run.json")
    lock = tmp_path / "lock"
    lock.touch()
    execution._worker("codex", str(tmp_path), folder, lock, threading.Event())
    result = execution.Run.load(folder / "run.json")
    assert result.thread_id == events[0]["thread_id"]
    assert result.handoff_summary == "Done"
    assert result.status == ("completed" if success else "failed")
    assert not lock.exists()
    assert "workspace-write" in commands[0]
    assert "--ephemeral" not in commands[0]
    assert "--dangerously-bypass-approvals-and-sandbox" not in commands[0]
    assert (folder / "events.jsonl").read_text()


def test_native_chat_launcher_uses_recorded_thread_id(monkeypatch):
    calls = []
    monkeypatch.setattr(execution.sys, "platform", "darwin")
    monkeypatch.setattr(execution.subprocess, "run", lambda args, **kw: calls.append(args))
    execution.open_chat("12345678-1234-1234-1234-123456789abc")
    assert calls == [["open", "codex://threads/12345678-1234-1234-1234-123456789abc"]]
    with pytest.raises(ValueError):
        execution.open_chat("https://example.com")


def test_stop_terminates_run_before_unlocking_project(design, monkeypatch, tmp_path):
    import time

    state, path = design
    state.phase, state.approved_revision = "approved", 1
    state.save(path)
    review_stage(state, path, "core", "", approved=True)
    script = tmp_path / "slow.py"
    script.write_text(
        "import sys,time\nsys.stdin.read()\n"
        'print(\'{"type":"thread.started", '
        '"thread_id":"12345678-1234-1234-1234-123456789abc"}\', flush=True)\n'
        "time.sleep(60)\n"
    )
    original = execution.subprocess.Popen
    monkeypatch.setattr(execution, "find_codex", lambda: "fake-codex")
    monkeypatch.setattr(
        execution.subprocess,
        "Popen",
        lambda cmd, **kw: original(
            [sys.executable, str(script)] if cmd[0] == "fake-codex" else cmd, **kw
        ),
    )
    run = execution.start_run(state, path, state.revisions[-1].design.tasks[0], {})
    output = execution.run_directory(path) / run.id / "run.json"
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline and not execution.Run.load(output).thread_id:
            time.sleep(0.02)
        assert execution.Run.load(output).thread_id
        assert execution.is_busy(state.project)
        execution.stop_run(run)
        while time.monotonic() < deadline and execution.is_busy(state.project):
            time.sleep(0.02)
        assert not execution.is_busy(state.project)
        assert execution.Run.load(output).status == "stopped"
    finally:
        if run.id in execution._active:
            execution.stop_run(run)


def test_prerequisite_verification_blocks_dependent_task(design):
    state, path = design
    first = state.revisions[-1].design.tasks[0]
    second = first.model_copy(deep=True)
    second.id = "verify"
    second.owned_paths = []
    second.depends_on = [first.id]
    state.revisions[-1].design.tasks.append(second)
    state.phase, state.approved_revision = "approved", 1
    state.save(path)
    review_stage(state, path, "core", "", approved=True)
    with pytest.raises(ValueError, match="prerequisite"):
        execution.start_run(state, path, second, {})
    folder = execution.run_directory(path) / "failed-job"
    folder.mkdir(parents=True)
    execution.Run(
        id="failed-job",
        task_id=first.id,
        agent_id=first.agent_id,
        revision=1,
        status="failed",
        prompt="context",
    ).save(folder / "run.json")
    with pytest.raises(ValueError, match="completed"):
        execution.verify_run(path, "failed-job")


def test_new_failed_attempt_invalidates_old_prerequisite_verification(design):
    state, path = design
    first = state.revisions[-1].design.tasks[0]
    dependent = first.model_copy(deep=True)
    dependent.id, dependent.owned_paths, dependent.depends_on = "dependent", [], [first.id]
    state.revisions[-1].design.tasks.append(dependent)
    state.phase, state.approved_revision = "approved", 1
    state.save(path)
    review_stage(state, path, "core", "", approved=True)
    for run_id, timestamp, status, verified in [
        ("old", 1, "completed", True),
        ("new", 2, "failed", False),
    ]:
        folder = execution.run_directory(path) / run_id
        folder.mkdir(parents=True)
        execution.Run(
            id=run_id,
            created_at=timestamp,
            task_id=first.id,
            agent_id=first.agent_id,
            revision=1,
            prompt="context",
            status=status,
            verified=verified,
        ).save(folder / "run.json")
    with pytest.raises(ValueError, match="prerequisite"):
        execution.start_run(state, path, dependent, {})
    with pytest.raises(ValueError, match="latest run"):
        execution.verify_run(path, "old")
