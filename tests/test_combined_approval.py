import pytest
from typer.testing import CliRunner

from codex_orchestrator.cli import app
from codex_orchestrator.codex import CodexError
from codex_orchestrator.design import check_source, prepare_design
from codex_orchestrator.design_models import DesignReply, DesignSession
from codex_orchestrator.interactive import IntakeReply
from codex_orchestrator.scope_outline import outline_plan
from codex_orchestrator.session import PlanningSession


def test_combined_plan_draft_requires_one_final_approval(
    approved_session, technical_payload, monkeypatch
):
    source_path, source = approved_session
    source.revisions[-1].plan = outline_plan(source.revisions[-1].brief)
    source.phase, source.approved_revision = "review", None
    source.save(source_path)
    monkeypatch.setattr("codex_orchestrator.design.ensure_codex", lambda: "fake")

    calls = []

    def respond(*args, **kwargs):
        calls.append(args)
        kwargs["on_response"](None)
        return DesignReply(
            question=None, requires_requirement_change=False, design=technical_payload
        )

    monkeypatch.setattr("codex_orchestrator.design.codex_response", respond)
    runner = CliRunner()
    result = runner.invoke(
        app, ["cycle", "plan", str(source_path)], input="default\ndefault\nsave\n"
    )
    assert result.exit_code == 0, result.output
    path = source_path.with_suffix(".design.json")
    draft = DesignSession.load(path)
    assert draft.phase == "review"
    assert PlanningSession.load(source_path).phase == "review"
    with pytest.raises(CodexError, match="approved Stage 1"):
        check_source(draft)
    result = runner.invoke(app, ["cycle", "plan", str(source_path)], input="approve\n")
    assert result.exit_code == 0, result.output
    assert "Review requirements and technical design" in result.output
    assert len(calls) == 1
    assert PlanningSession.load(source_path).phase == "approved"
    assert DesignSession.load(path).phase == "approved"
    check_source(DesignSession.load(path))


def test_combined_plan_rejects_non_outline_unapproved_source(approved_session):
    source_path, source = approved_session
    source.phase, source.approved_revision = "review", None
    source.save(source_path)
    with pytest.raises(CodexError, match="local scope outline"):
        prepare_design(
            source_path, None, architect_model=None, agent_model=None, combined_approval=True
        )


def test_combined_command_from_intake_uses_two_model_turns(
    approved_session, technical_payload, monkeypatch
):
    path, source = approved_session
    brief = source.brief
    source.phase, source.approved_revision = "intake", None
    source.revisions = []
    source.models_configured = True
    source.save(path)
    calls = []

    def intake(*args, **kwargs):
        calls.append("intake")
        kwargs["on_response"](None)
        return IntakeReply(brief=brief, question=None, recommended_route="standard")

    def architect(*args, **kwargs):
        calls.append("architect")
        kwargs["on_response"](None)
        return DesignReply(
            question=None, requires_requirement_change=False, design=technical_payload
        )

    monkeypatch.setattr("codex_orchestrator.interactive.ensure_codex", lambda: "fake")
    monkeypatch.setattr("codex_orchestrator.design.ensure_codex", lambda: "fake")
    monkeypatch.setattr("codex_orchestrator.interactive.codex_response", intake)
    monkeypatch.setattr("codex_orchestrator.design.codex_response", architect)
    result = CliRunner().invoke(
        app, ["cycle", "plan", str(path)], input="default\ndefault\napprove\n"
    )
    assert result.exit_code == 0, result.output
    assert calls == ["intake", "architect"]
    assert "Review plan [" not in result.output
    assert PlanningSession.load(path).phase == "approved"
    assert DesignSession.load(path.with_suffix(".design.json")).phase == "approved"
