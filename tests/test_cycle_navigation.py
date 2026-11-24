from pathlib import Path

import pytest
from test_git_service import accepted as accepted
from test_git_service import human_commit
from test_github_pr import connected as connected
from test_stage_branch_services import project as project
from test_stage_branches import run_task
from typer.testing import CliRunner

from codex_orchestrator import cycle_cli, cycle_manager, git_service, stage_branches
from codex_orchestrator.cli import app
from codex_orchestrator.cycle_store import find_history, histories
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.session import PlanningSession


@pytest.fixture(autouse=True)
def forbid_live_connection(monkeypatch):
    monkeypatch.setattr(
        "codex_orchestrator.cycle_navigation.show_connection",
        lambda *args: pytest.fail("Unexpected GitHub connection request"),
    )


def test_new_project_menu_saves_and_resumes_without_inference(tmp_path, monkeypatch):
    project = tmp_path / "target"
    project.mkdir()
    directory = tmp_path / "plans"
    calls = []
    monkeypatch.setattr(cycle_cli, "combined_plan", lambda source, **kwargs: calls.append(source))
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["cycle", "start", "--directory", str(directory), "--local"],
        input=f"1\n{project}\nBuild a counter\n:save\n",
    )
    assert result.exit_code == 0, result.output
    assert not calls
    history = histories(directory)[0][1]
    session = directory / history.cycles[-1].session_name
    assert PlanningSession.load(session).phase == "intake"
    result = runner.invoke(
        app, ["cycle", "start", "--directory", str(directory)], input="1\n1\n:save\n"
    )
    assert result.exit_code == 0, result.output
    assert calls == [session]


def test_local_delivery_navigation_merges_only_after_confirmation(project):
    root, path = project
    source = Path(DesignSession.load(path).source_file)
    cycle_manager.register(source)
    git_service.git(root, "remote", "remove", "origin")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["cycle", "start", str(source)],
        input="1\nTested counter\ny\n1\nn\n:save\n",
    )
    assert result.exit_code == 0, result.output
    assert "Review PR or merge the delivered feature" in result.output
    item = stage_branches.current(DesignSession.load(path), path)[1].stages["core"]
    assert not item.merged_commit
    result = runner.invoke(app, ["cycle", "start", str(source)], input="1\ny\n:save\n")
    assert result.exit_code == 0, result.output
    item = stage_branches.current(DesignSession.load(path), path)[1].stages["core"]
    assert item.merged_commit
    assert "Continue feature delivery" in result.output


def test_github_navigation_delivers_and_merges_in_one_session(connected):
    _, _, source, remote = connected
    result = CliRunner().invoke(
        app,
        ["cycle", "start", str(source)],
        input="1\nTested counter\ny\n1\ny\n:save\n",
    )
    assert result.exit_code == 0, result.output
    assert remote.posts == remote.merges == 1
    assert find_history(source)[1].cycles[-1].pull_requests[0].number == 7
    assert "Continue feature delivery" in result.output


def test_adjust_context_from_menu_without_starting_work(project, monkeypatch):
    _, path = project
    source = Path(DesignSession.load(path).source_file)
    cycle_manager.register(source)
    calls = []
    monkeypatch.setattr(cycle_cli, "feature", lambda *args, **kwargs: calls.append(args))
    result = CliRunner().invoke(app, ["cycle", "start", str(source)], input="2\n48\n128\n:save\n")
    assert result.exit_code == 0, result.output
    assert not calls
    assert DesignSession.load(path).context_limits.max_file_bytes == 48 * 1024


def test_finish_and_start_next_cycle_from_menu(project):
    root, path = project
    source = Path(DesignSession.load(path).source_file)
    cycle_manager.register(source)
    git_service.git(root, "remote", "remove", "origin")
    for stage in ("core", "delivery"):
        stage_branches.prepare(path, stage)
        run_task(root, path, stage)
        human_commit(path, git_service.preview(path, stage), "Deliver stage")
        if stage == "core":
            stage_branches.merge_local(path, stage)
    result = CliRunner().invoke(
        app,
        ["cycle", "start", str(source)],
        input="1\ny\n1\nDelivered counter\nWorks\n1\nImprove display\n:save\n",
    )
    assert result.exit_code == 0, result.output
    history = find_history(source)[1]
    assert [cycle.status for cycle in history.cycles] == ["completed", "active"]
    assert history.cycles[-1].goal == "Improve display"
    assert git_service.git(root, "symbolic-ref", "--short", "HEAD") == "main"
