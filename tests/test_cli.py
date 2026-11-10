from pathlib import Path

import pytest
from typer.testing import CliRunner

from codex_orchestrator.cli import app
from codex_orchestrator.models import ProjectPlan

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "frontend" in result.output


def test_plan_writes_valid_json_and_leaves_project_alone(tmp_path: Path) -> None:
    project = tmp_path / "app"
    project.mkdir()
    existing = project / "existing.txt"
    existing.write_text("unchanged", encoding="utf-8")
    output = tmp_path / "plans" / "demo.json"
    result = runner.invoke(app, ["plan", str(project), "Build a booking app", "-o", str(output)])
    assert result.exit_code == 0, result.output
    plan = ProjectPlan.model_validate_json(output.read_text(encoding="utf-8"))
    assert plan.project_name == "app"
    assert plan.brief == "Build a booking app"
    assert plan.planning_mode == "template"
    assert "generic" in result.output
    assert list(project.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8") == "unchanged"


def test_refuses_overwrite_unless_forced(tmp_path: Path) -> None:
    output = tmp_path / "plan.json"
    output.write_text("keep this", encoding="utf-8")
    arguments = ["plan", str(tmp_path), "Build an app", "-o", str(output)]
    assert runner.invoke(app, arguments).exit_code != 0
    assert output.read_text(encoding="utf-8") == "keep this"
    assert runner.invoke(app, [*arguments, "--force"]).exit_code == 0
    ProjectPlan.model_validate_json(output.read_text(encoding="utf-8"))


def test_brief_file_and_name(tmp_path: Path) -> None:
    brief = tmp_path / "brief.txt"
    brief.write_text("Build a café app\nInclude bookings.\n", encoding="utf-8")
    output = tmp_path / "plan.json"
    result = runner.invoke(
        app,
        ["plan", str(tmp_path), "--brief-file", str(brief), "--name", "Café", "-o", str(output)],
    )
    assert result.exit_code == 0, result.output
    plan = ProjectPlan.model_validate_json(output.read_text(encoding="utf-8"))
    assert plan.project_name == "Café"
    assert plan.brief == "Build a café app\nInclude bookings."


@pytest.mark.parametrize("brief", [None, "", "   "])
def test_reject_missing_or_blank_brief(tmp_path: Path, brief: str | None) -> None:
    output = tmp_path / "plan.json"
    args = ["plan", str(tmp_path)]
    if brief is not None:
        args.append(brief)
    result = runner.invoke(app, [*args, "-o", str(output)])
    assert result.exit_code == 2
    assert not output.exists()


def test_reject_two_briefs(tmp_path: Path) -> None:
    brief = tmp_path / "brief.txt"
    brief.write_text("A brief", encoding="utf-8")
    result = runner.invoke(
        app, ["plan", str(tmp_path), "Another brief", "--brief-file", str(brief)]
    )
    assert result.exit_code == 2


def test_reject_missing_project(tmp_path: Path) -> None:
    result = runner.invoke(app, ["plan", str(tmp_path / "missing"), "Build an app"])
    assert result.exit_code == 2
