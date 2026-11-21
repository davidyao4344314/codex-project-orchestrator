import copy

import pytest
from test_interactive import draft
from test_persistent_planning import fake_cli
from typer.testing import CliRunner

from codex_orchestrator import compact_planning, design, interactive
from codex_orchestrator.cli import app
from codex_orchestrator.codex import CodexError
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.session import ModelPolicy, PlanningRoute, PlanningSession


def start(approved_session, technical_payload, monkeypatch, *, change=None):
    _, existing = approved_session
    root = existing.project
    path = approved_session[0].with_name("compact.json")
    state = interactive.create_session(
        approved_session[0].parent, "Counter", "Create a recursion counter", path
    )
    payload = copy.deepcopy(technical_payload)
    if change:
        change(payload)
    replies = [
        {
            "question": None,
            "brief": existing.brief.model_dump(),
            "recommended_route": "compact",
        },
        {"question": None, "standard_reason": None, "design": payload},
    ]
    calls = fake_cli(monkeypatch, replies)
    interactive.collect_brief(state, path)
    assert state.project == root
    return state, path, calls, replies


def test_simple_route_uses_one_combined_call_and_manual_approvals(
    approved_session, technical_payload, monkeypatch
):
    state, path, calls, _ = start(approved_session, technical_payload, monkeypatch)
    state.model_policy = ModelPolicy(
        routine="small-model", standard="standard-model", strong_reasoning="strong-model"
    )
    state.planning_model = state.model_policy.standard
    state.save(path)
    interactive.generate_plan(state, path)
    assert len(calls) == 2  # intake plus combined planning; no extra architecture inference
    assert state.phase == "review" and state.approved_revision is None
    with pytest.raises(CodexError, match="approved Stage 1"):
        design.prepare_design(path, None, architect_model=None, agent_model=None)
    interactive.approve_plan(state, path)
    saved = PlanningSession.load(path)
    assert saved.revisions[0].plan.tasks[0].id == "implement"
    result = CliRunner().invoke(app, ["design", str(path)], input="approve\n")
    assert result.exit_code == 0, result.output
    assert "no additional architect call" in result.output
    technical = DesignSession.load(path.with_suffix(".design.json"))
    assert technical.phase == "approved" and technical.revisions[0].origin == "compact"
    assert technical.revisions[0].task_models == {"implement": "small-model"}
    assert calls[1][0][calls[1][0].index("--model") + 1] == "standard-model"
    assert technical.stage_reviews == []  # each stage still needs explicit approval
    assert len(calls) == 2


def test_complex_compact_result_requires_explicit_standard_retry(
    approved_session, technical_payload, monkeypatch
):
    state, path, calls, replies = start(
        approved_session,
        technical_payload,
        monkeypatch,
        change=lambda payload: payload["storage"].append("SQLite"),
    )
    with pytest.raises(CodexError, match="standard workflow"):
        interactive.generate_plan(state, path)
    assert len(calls) == 2
    saved = PlanningSession.load(path)
    assert saved.planning_route == PlanningRoute.AUTO and saved.compact_fallback
    assert compact_planning.selected_route(saved) == "standard"
    assert saved.phase == "planning" and saved.revisions == []
    replies.append(draft(saved.brief.requirements[0]))
    interactive.generate_plan(saved, path)
    assert len(calls) == 3 and saved.phase == "review"


def test_compact_proposal_must_match_the_approved_plan(
    approved_session, technical_payload, monkeypatch
):
    state, path, _, _ = start(approved_session, technical_payload, monkeypatch)
    interactive.generate_plan(state, path)
    interactive.approve_plan(state, path)
    proposal_path = compact_planning.proposal_path(path, state.revisions[-1], 1)
    proposal = compact_planning.CompactProposal.load(proposal_path)
    proposal.design.tasks[0].title = "Different task"
    proposal.save(proposal_path)
    with pytest.raises(CodexError, match="does not match"):
        design.prepare_design(path, None, architect_model=None, agent_model=None)
    assert not path.with_suffix(".design.json").exists()


def test_auto_route_keeps_standard_planner_for_large_briefs(
    approved_session, technical_payload, monkeypatch
):
    state, path, calls, replies = start(approved_session, technical_payload, monkeypatch)
    # Even an optimistic intake recommendation cannot bypass the local size guard.
    state.brief.requirements = [f"Requirement {n}" for n in range(5)]
    state.save(path)
    assert compact_planning.selected_route(state) == "standard"
    replies[:] = [{"question": "Which requirements are essential?", "plan": None}]
    interactive.generate_plan(state, path)
    assert len(calls) == 2 and state.pending_question
    assert "CompactReply" not in calls[1][1]
