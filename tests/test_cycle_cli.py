from test_git_service import accepted as accepted
from test_git_service import human_commit
from typer.testing import CliRunner

from codex_orchestrator import git_service
from codex_orchestrator.cli import app
from codex_orchestrator.cycle_manager import register
from codex_orchestrator.cycle_store import histories
from codex_orchestrator.session import PlanningSession


def test_new_cycle_prompts_and_history_is_read_only(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    directory = tmp_path / "plans"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["cycle", "new", str(project), "--directory", str(directory)],
        input="Create a counter\n",
    )
    assert result.exit_code == 0, result.output
    file, history = histories(directory)[0]
    source = directory / history.cycles[0].session_name
    assert PlanningSession.load(source).phase == "intake"
    before = file.read_bytes()
    result = runner.invoke(app, ["cycle", "status", str(source)])
    assert result.exit_code == 0 and "Requirements" in result.output
    result = runner.invoke(app, ["cycle", "history", str(source)])
    assert result.exit_code == 0 and "Create a counter" in result.output
    assert file.read_bytes() == before
    result = runner.invoke(app, ["cycle", "next", str(source), "--goal", "More"])
    assert result.exit_code == 1 and "Complete the current cycle" in result.output


def test_terminal_push_requires_user_confirmation(accepted, monkeypatch):
    _, path = accepted
    preview = git_service.preview(path, "core")
    human_commit(path, preview, "Reviewed")
    calls = []
    monkeypatch.setattr(git_service, "push", lambda *a, **kw: calls.append(kw["permission"]))
    source = path.with_name("source.json")
    runner = CliRunner()
    result = runner.invoke(app, ["cycle", "deliver", str(source), "push"], input="n\n")
    assert result.exit_code == 0 and not calls
    result = runner.invoke(app, ["cycle", "deliver", str(source), "push"], input="y\n")
    assert result.exit_code == 0 and len(calls) == 1
    assert (
        calls[0].commit
        == git_service.GitPublication.load(git_service.publication_path(path)).commit
    )


def test_feedback_prompt_records_choice_without_deploying(accepted, monkeypatch):
    from codex_orchestrator import execution
    from codex_orchestrator.cycle_store import find_history
    from codex_orchestrator.run_store import runs

    _, path = accepted
    source = path.with_name("source.json")
    register(source)
    earlier = len(runs(path))
    monkeypatch.setattr(
        execution,
        "start_run",
        lambda *a: (_ for _ in ()).throw(AssertionError("Unexpected deployment")),
    )
    result = CliRunner().invoke(
        app, ["cycle", "feedback", str(source)], input="improve\nImprove spacing\nimplement\n"
    )
    assert result.exit_code == 0, result.output
    assert len(runs(path)) == earlier
    assert find_history(source)[1].cycles[0].feedback_rounds[-1].note == "Improve spacing"
