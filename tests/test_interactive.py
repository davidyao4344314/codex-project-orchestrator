import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codex_orchestrator.cli import app
from codex_orchestrator.interactive import choose_models as real_choose_models
from codex_orchestrator.session import PlanningSession

runner = CliRunner()


def intake(requirement: str, question: str | None = None, route="standard") -> dict:
    return {
        "question": question,
        "recommended_route": route,
        "brief": {
            "goal": "Improve the activity editor",
            "users": ["Members"],
            "requirements": [requirement],
            "exclusions": ["Notifications"],
            "project_context": [],
            "constraints": [],
            "assumptions": [],
            "success_criteria": [requirement],
        },
    }


def draft(requirement: str, requirement_id: int = 1) -> dict:
    return {
        "question": None,
        "plan": {
            "assumptions": [],
            "agent_strategy": "single",
            "agent_rationale": "One focused change.",
            "tasks": [
                {
                    "id": "editor",
                    "title": requirement,
                    "workstream": "frontend",
                    "description": "Update the existing editor.",
                    "depends_on": [],
                    "acceptance_criteria": [requirement],
                    "requirement_ids": [requirement_id],
                    "scope": ["Activity editor"],
                    "difficulty": "routine",
                    "automated_checks": ["Run editor tests"],
                    "human_checks": ["Try saving an activity"],
                },
            ],
        },
    }


@pytest.fixture
def codex(monkeypatch: pytest.MonkeyPatch):
    replies = []
    calls = []
    monkeypatch.setattr("codex_orchestrator.interactive.ensure_codex", lambda: "/tools/codex")
    monkeypatch.setattr("codex_orchestrator.codex.shutil.which", lambda _: "/tools/codex")

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess:
        calls.append((command, options["input"]))
        reply = replies.pop(0)
        if reply is None:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Unavailable")
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps(reply), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("codex_orchestrator.codex.subprocess.run", run)
    return replies, calls


def test_intake_planner_questions_revision_and_approval(tmp_path: Path, codex) -> None:
    replies, calls = codex
    original = "Members can edit activity names"
    revised = "Members can edit activity notes"
    replies.extend(
        [
            intake(original, "Who can edit?"),
            intake(original),
            {"question": "Should blank names be accepted?", "plan": None},
            intake(original),
            draft(original),
        ]
    )
    path = tmp_path / "session.json"
    result = runner.invoke(
        app,
        [
            "plan",
            str(tmp_path),
            "--interactive",
            "--output",
            str(path),
            "--intake-model",
            "intake-model",
            "--model",
            "planner-model",
        ],
        input="Add activity editing\nMembers\nNo, require a name\napprove\n",
    )
    assert result.exit_code == 0, result.output
    assert "Approved revision 1" in result.output
    assert "Automated check: Run editor tests" in result.output
    assert [command[command.index("--model") + 1] for command, _ in calls] == [
        "intake-model",
        "intake-model",
        "planner-model",
        "intake-model",
        "planner-model",
    ]
    assert "No, require a name" in calls[3][1]
    saved = PlanningSession.load(path)
    assert saved.approved_revision == 1
    assert saved.revisions[0].brief.requirements == [original]

    replies.extend([intake(revised), draft(revised)])
    result = runner.invoke(app, ["resume", str(path)], input="revise\nNotes only, please\nsave\n")
    assert result.exit_code == 0, result.output
    saved = PlanningSession.load(path)
    assert saved.phase == "review"
    assert saved.approved_revision is None
    assert [rev.brief.requirements for rev in saved.revisions] == [[original], [revised]]
    assert "Notes only, please" in calls[5][1]

    assert runner.invoke(app, ["resume", str(path)], input="approve\n").exit_code == 0
    result = runner.invoke(app, ["resume", str(path)], input="save\n")
    assert result.exit_code == 0, result.output
    assert "Revision 2 is approved" in result.output
    assert len(calls) == 7


def test_resume_after_question_and_provider_failure(tmp_path: Path, codex) -> None:
    replies, calls = codex
    requirement = "Members can edit notes"
    replies.extend([intake(requirement, "Who edits notes?"), None])
    path = tmp_path / "session.json"
    result = runner.invoke(
        app,
        ["plan", str(tmp_path), "Edit notes", "--interactive", "-o", str(path)],
        input=":save\n",
    )
    assert result.exit_code == 0, result.output
    assert PlanningSession.load(path).pending_question == "Who edits notes?"

    result = runner.invoke(app, ["resume", str(path)], input="Members\n")
    assert result.exit_code == 1
    assert "Unavailable" in result.output
    assert PlanningSession.load(path).answers[-1].response == "Members"

    replies.extend([intake(requirement), draft(requirement, requirement_id=99)])
    result = runner.invoke(app, ["resume", str(path)])
    assert result.exit_code == 1
    assert "unknown requirements" in result.output
    saved = PlanningSession.load(path)
    assert saved.phase == "planning"
    assert not saved.revisions

    replies.append(draft(requirement))
    result = runner.invoke(app, ["resume", str(path)], input="approve\n")
    assert result.exit_code == 0, result.output
    assert PlanningSession.load(path).approved_revision == 1
    assert "Members" in calls[2][1]


@pytest.fixture(autouse=True)
def default_model_choices(monkeypatch: pytest.MonkeyPatch):
    def choose(session, **kwargs):
        session.models_configured = True

    monkeypatch.setattr("codex_orchestrator.interactive.choose_models", choose)


def test_choose_models_change_during_intake_and_preserve_revision_source(
    tmp_path: Path, codex, monkeypatch: pytest.MonkeyPatch
):
    # Exercise the real selector, unlike the older workflow tests above.
    monkeypatch.setattr("codex_orchestrator.interactive.choose_models", real_choose_models)
    replies, calls = codex
    replies.extend(
        [
            intake("Edit notes", "Who can edit?"),
            intake("Edit notes"),
            draft("Edit notes"),
        ]
    )
    path = tmp_path / "session.json"
    result = runner.invoke(
        app,
        ["plan", str(tmp_path), "--interactive", "-o", str(path)],
        input=(
            "Edit notes\nfirst-intake\nfirst-planner\n:models\n"
            "second-intake\nsecond-planner\nMembers\napprove\n"
        ),
    )
    assert result.exit_code == 0, result.output
    assert "Question model: first-intake" in result.output
    assert "Plan model: second-planner" in result.output
    session = PlanningSession.load(path)
    assert session.models_configured
    assert session.intake_model == "second-intake"
    assert session.planning_model == "second-planner"
    assert [source.requested_model for source in session.response_sources] == [
        "first-intake",
        "second-intake",
        "second-planner",
    ]
    assert session.revisions[0].requested_model == "second-planner"

    result = runner.invoke(
        app,
        ["resume", str(path), "--intake-model", "next-intake", "--planning-model", "next-planner"],
        input="save\n",
    )
    assert result.exit_code == 0, result.output
    assert "Plan model: second-planner" in result.output
    assert PlanningSession.load(path).planning_model == "next-planner"
    assert len(calls) == 3
