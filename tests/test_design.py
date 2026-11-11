import pytest
from typer.testing import CliRunner

from codex_orchestrator.cli import app
from codex_orchestrator.codex import CodexError
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
    with pytest.raises(CodexError, match="source has changed"):
        check_source(state)
    assert DesignSession.load(output).source.brief.requirements == ["Count recursively from 0 to 5"]
    with pytest.raises(CodexError, match="different path"):
        prepare_design(path, path, architect_model=None, agent_model=None)
    assert PlanningSession.load(path).phase == "approved"


def test_complete_review_allocation_revision_and_approval(
    approved_session, technical_payload, monkeypatch
):
    source_path, _ = approved_session
    monkeypatch.setattr("codex_orchestrator.design.ensure_codex", lambda: "/tools/codex")
    calls = []

    def respond(project, prompt, response_model, **kwargs):
        calls.append(prompt)
        kwargs["on_response"]("architect-actual")
        return DesignReply(
            question=None, requires_requirement_change=False, design=technical_payload
        )

    monkeypatch.setattr("codex_orchestrator.design.codex_response", respond)
    result = runner.invoke(
        app,
        ["design", str(source_path), "--model", "architect-request"],
        input="models\narchitect-request\nworker-model\napprove\n",
    )
    assert result.exit_code == 0, result.output
    for text in (
        "Component structure",
        "Agent allocation",
        "Stage core",
        "Owned files",
        "Context inputs",
        "If missing:",
        "Handoff to user",
        "Escalate when",
        "Verification gate",
        "Required user tests before merge",
        "Merge requires the tested commit",
        "Selected model: worker-model",
        "Approved technical-design revision 2",
    ):
        assert text in result.output
    output = source_path.with_name("source.design.json")
    saved = DesignSession.load(output)
    assert saved.phase == "approved"
    assert saved.revisions[0].agent_models == {"developer": None}
    assert saved.revisions[1].agent_models == {"developer": "worker-model"}
    assert saved.revisions[1].origin == "model_allocation"
    assert saved.revisions[1].reported_model == "architect-actual"
    assert len(calls) == 1
    before = output.read_bytes()
    result = runner.invoke(app, ["design", str(source_path), "--show"])
    assert result.exit_code == 0, result.output
    assert "approved" in result.output
    assert len(calls) == 1
    assert output.read_bytes() == before
    result = runner.invoke(
        app,
        ["design", str(source_path)],
        input="models\narchitect-request\nanother-worker\nsave\n",
    )
    assert result.exit_code == 0, result.output
    changed = DesignSession.load(output)
    assert changed.phase == "review"
    assert changed.approved_revision is None
    assert len(changed.revisions) == 3
    assert changed.revisions[1].agent_models == {"developer": "worker-model"}
    assert changed.revisions[2].agent_models == {"developer": "another-worker"}
    assert len(calls) == 1


def test_approval_blocked_on_missing_required_context(
    approved_session, technical_payload, monkeypatch
):
    source_path, _ = approved_session
    technical_payload["tasks"][0]["context"].append(
        {
            "kind": "project_file",
            "reference": "existing.py",
            "reason": "Reuse existing logic",
            "required": True,
            "missing_action": "Locate the existing code before implementation",
        }
    )
    monkeypatch.setattr("codex_orchestrator.design.ensure_codex", lambda: "/tools/codex")
    monkeypatch.setattr(
        "codex_orchestrator.design.codex_response",
        lambda *args, **kwargs: DesignReply(
            question=None, requires_requirement_change=False, design=technical_payload
        ),
    )
    result = runner.invoke(
        app, ["design", str(source_path), "--model", "architect"], input="approve\nsave\n"
    )
    assert result.exit_code == 0, result.output
    assert "Approval blocked" in result.output
    output = source_path.with_name("source.design.json")
    assert DesignSession.load(output).approved_revision is None
    (source_path.parent / "existing.py").touch()
    result = runner.invoke(app, ["design", str(source_path)], input="approve\n")
    assert result.exit_code == 0, result.output
    assert DesignSession.load(output).approved_revision == 1


def test_clarification_failure_recovery_and_revision(
    approved_session, technical_payload, monkeypatch
):
    source_path, _ = approved_session
    monkeypatch.setattr("codex_orchestrator.design.ensure_codex", lambda: "/tools/codex")
    replies = [
        DesignReply(
            question="Should output be one number per line?",
            requires_requirement_change=False,
            design=None,
        ),
        CodexError("Provider temporarily unavailable"),
        DesignReply(question=None, requires_requirement_change=False, design=technical_payload),
        DesignReply(question=None, requires_requirement_change=False, design=technical_payload),
    ]
    calls = []

    def respond(project, prompt, response_model, **kwargs):
        calls.append(prompt)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr("codex_orchestrator.design.codex_response", respond)
    args = ["design", str(source_path), "--model", "architect"]
    result = runner.invoke(app, args, input=":save\n")
    assert result.exit_code == 0, result.output
    output = source_path.with_name("source.design.json")
    assert DesignSession.load(output).pending_question is not None

    result = runner.invoke(app, args, input="Yes\n")
    assert result.exit_code == 1, result.output
    state = DesignSession.load(output)
    assert state.answers[-1].response == "Yes"
    assert state.phase == "draft"
    result = runner.invoke(app, args, input="approve\n")
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, args, input="revise\nMake error reporting explicit\napprove\n")
    assert result.exit_code == 0, result.output
    state = DesignSession.load(output)
    assert state.approved_revision == 2
    assert len(state.revisions) == 2
    assert "Make error reporting explicit" in calls[-1]
    assert "Yes" in calls[2]


def test_invalid_design_feedback_is_saved_for_retry(
    approved_session, technical_payload, monkeypatch
):
    from copy import deepcopy

    source_path, _ = approved_session
    monkeypatch.setattr("codex_orchestrator.design.ensure_codex", lambda: "/tools/codex")
    bad = deepcopy(technical_payload)
    bad["tasks"][0]["requirement_ids"] = [99]
    replies = [bad, technical_payload]
    calls = []

    def respond(project, prompt, response_model, **kwargs):
        calls.append(prompt)
        return DesignReply(question=None, requires_requirement_change=False, design=replies.pop(0))

    monkeypatch.setattr("codex_orchestrator.design.codex_response", respond)
    args = ["design", str(source_path), "--model", "architect"]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    output = source_path.with_name("source.design.json")
    state = DesignSession.load(output)
    assert state.phase == "draft"
    assert not state.revisions
    assert state.validation_feedback
    result = runner.invoke(app, args, input="approve\n")
    assert result.exit_code == 0, result.output
    assert "Requirement coverage mismatch" in calls[-1]
    assert not DesignSession.load(output).validation_feedback


def test_real_cli_boundary_and_approval(approved_session, technical_payload, monkeypatch):
    import json
    import sys

    path, source = approved_session
    executable = path.parent / "fake-codex"
    payload = json.dumps(
        {
            "question": None,
            "requires_requirement_change": False,
            "design": technical_payload,
        }
    )
    executable.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "if args == ['login', 'status']:\n"
        "    sys.exit(0)\n"
        "assert args[args.index('--sandbox') + 1] == 'read-only'\n"
        "assert 'Count recursively from 0 to 5' in sys.stdin.read()\n"
        "output = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
        f"output.write_text({payload!r})\n"
        "print('model: fake-architect', file=sys.stderr)\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("CODEX_EXECUTABLE", str(executable))
    before = path.read_bytes()
    result = runner.invoke(app, ["design", str(path)], input="default\ndefault\napprove\n")
    assert result.exit_code == 0, result.output
    state = DesignSession.load(path.with_name("source.design.json"))
    assert state.approved_revision == 1
    assert state.revisions[0].reported_model == "fake-architect"
    assert path.read_bytes() == before
    assert source.revisions[0].brief.requirements == ["Count recursively from 0 to 5"]


def test_source_change_during_architect_request_does_not_publish_design(
    approved_session, technical_payload, monkeypatch
):
    source_path, source = approved_session
    monkeypatch.setattr("codex_orchestrator.design.ensure_codex", lambda: "/tools/codex")

    def respond(*args, **kwargs):
        source.phase = "review"
        source.approved_revision = None
        source.save(source_path)
        return DesignReply(
            question=None, requires_requirement_change=False, design=technical_payload
        )

    monkeypatch.setattr("codex_orchestrator.design.codex_response", respond)
    result = runner.invoke(app, ["design", str(source_path), "--model", "architect"])
    assert result.exit_code == 1, result.output
    state = DesignSession.load(source_path.with_name("source.design.json"))
    assert state.phase == "draft"
    assert state.revisions == []
