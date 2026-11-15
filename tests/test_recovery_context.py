import pytest
from test_execution import design as design
from test_git_service import accepted as accepted
from test_recovery import unfinished

from codex_orchestrator import execution, project_activity
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.run_store import run_directory


def test_retry_context_preserves_history_and_uses_current_files(accepted, monkeypatch):
    import json
    import threading

    project, path = accepted
    run = next(iter(execution.latest_runs(path, 1).values()))
    run.status, run.verified, run.error = "stopped", False, "Interrupted before final report"
    run.save(run_directory(path) / run.id / "run.json")
    history = (run_directory(path) / run.id / "run.json").read_bytes()
    (project / "counter.py").write_text("print('partial edited after interruption')")
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    state = DesignSession.load(path)
    new = execution.start_run(state, path, state.revisions[-1].design.tasks[0])
    try:
        payload = json.loads(new.prompt.split("\n", 1)[1])
        brief = payload["recovery_brief"]
        assert new.recovery_from == run.id == brief["previous_run_id"]
        assert brief["must_rerun_all_required_checks"]
        assert brief["commands_observed"] and brief["current_owned_inputs"]
        assert "partial edited after interruption" in json.dumps(payload["assignment_context"])
        assert (run_directory(path) / run.id / "run.json").read_bytes() == history
        assert len(json.dumps(brief).encode()) <= 16 * 1024
    finally:
        execution._active.pop(new.id, None)
        execution._workers.pop(new.id, None)
        lease = project_activity.load(project)
        project_activity.release(project, lease)


def test_retry_brief_excludes_other_revisions_and_secrets(accepted):
    from codex_orchestrator.context_builder import build_context
    from codex_orchestrator.recovery_brief import build_recovery_brief

    _, path = accepted
    state = DesignSession.load(path)
    task = state.revisions[-1].design.tasks[0]
    run = next(iter(execution.latest_runs(path, 1).values()))
    run.error = "ghp_" + "a" * 30
    run.save(run_directory(path) / run.id / "run.json")
    package = build_context(state, task, path)
    brief = build_recovery_brief(state, task, path, package)
    assert "ghp_" not in str(brief)
    state.revisions.append(state.revisions[-1].model_copy(deep=True))
    assert build_recovery_brief(state, task, path, package) is None


def test_unfinished_run_without_lock_blocks_dispatch(accepted, monkeypatch):
    _, path = accepted
    unfinished(path)
    state = DesignSession.load(path)
    monkeypatch.setattr(execution, "find_codex", lambda: pytest.fail("Started another worker"))
    with pytest.raises(ValueError, match="unfinished run"):
        execution.start_run(state, path, state.revisions[-1].design.tasks[0])
