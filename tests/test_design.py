import pytest
from typer.testing import CliRunner

from codex_orchestrator.cli import app
from codex_orchestrator.design import check_source, prepare_design
from codex_orchestrator.design_models import DesignReply, DesignSession
from codex_orchestrator.session import PlanningSession

runner = CliRunner()


def test_design_command_bound_to_approved_source(approved_session, technical_payload, monkeypatch):
    source_path, source = approved_session
    calls = []
    monkeypatch.setattr("codex_orchestrator.design.ensure_codex", lambda: "/tools/codex")

    def respond(project, prompt, response_model, **kwargs):
        calls.append((project, prompt, kwargs["model"]))
        kwargs["on_response"]("architect-actual")
        return DesignReply(
            question=None, requires_requirement_change=False, design=technical_payload
        )

    monkeypatch.setattr("codex_orchestrator.design.codex_response", respond)
    output = source_path.with_name("technical.json")
    result = runner.invoke(
        app,
        ["design", str(source_path), "-o", str(output), "--model", "architect-model"],
    )
    assert result.exit_code == 0, result.output
    state = DesignSession.load(output)
    assert state.phase == "review"
    assert state.source.brief == source.brief
    assert state.revisions[0].reported_model == "architect-actual"
    assert state.revisions[0].agent_models == {"developer": None}
    assert calls[0][0] == source_path.parent
    assert "Website" in calls[0][1]
    assert calls[0][2] == "architect-model"
    # Reopening a finished draft needs no additional model call.
    assert runner.invoke(app, ["design", str(source_path), "-o", str(output)]).exit_code == 0
    assert len(calls) == 1


def test_reject_unapproved_source_before_codex(approved_session, monkeypatch):
    path, source = approved_session
    source.phase = "review"
    source.approved_revision = None
    source.save(path)
    monkeypatch.setattr(
        "codex_orchestrator.design.ensure_codex", lambda: pytest.fail("Unexpected Codex call")
    )
    result = runner.invoke(app, ["design", str(path)])
    assert result.exit_code == 1
    assert "approved Stage 1" in result.output
    assert not path.with_name("source.design.json").exists()


def test_detect_changed_source_and_protect_source_output(approved_session):
    path, source = approved_session
    state, output = prepare_design(path, None, architect_model=None, agent_model=None)
    source.revisions[0].brief.requirements[0] = "Different behavior"
    source.save(path)
    with pytest.raises(Exception, match="source has changed"):
        check_source(state)
    assert DesignSession.load(output).source.brief.requirements == ["Count recursively from 0 to 5"]
    with pytest.raises(Exception, match="different path"):
        prepare_design(path, path, architect_model=None, agent_model=None)
    assert PlanningSession.load(path).phase == "approved"
