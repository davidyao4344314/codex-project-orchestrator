import json

import pytest

from codex_orchestrator import allocation
from codex_orchestrator.codex import CodexError
from codex_orchestrator.design import prepare_design
from codex_orchestrator.design_models import (
    AgentPreference,
    DesignRevision,
    DesignSession,
    TechnicalDesign,
)
from codex_orchestrator.execution import project_lock


@pytest.fixture
def allocation_design(approved_session, technical_payload):
    source, _ = approved_session
    state, path = prepare_design(source, None, architect_model=None, agent_model=None)
    state.revisions.append(
        DesignRevision(
            design=TechnicalDesign.model_validate(technical_payload),
            agent_models={"developer": None},
            elapsed_seconds=0,
        )
    )
    state.phase, state.approved_revision = "approved", 1
    state.save(path)
    return state, path


def reply_for(design, roster):
    agents = [design.agents[0].model_copy(update={"id": a.id, "role": a.role}) for a in roster]
    return allocation.AllocationReply(
        agents=agents,
        tasks=[
            allocation.TaskAllocation(
                task_id=t.id,
                agent_id=roster[i % len(roster)].id,
                reason="Matches the requested specialty",
                handoff_summary="Provide code and test evidence to review",
            )
            for i, t in enumerate(design.tasks)
        ],
        gates=[
            allocation.GateAllocation(
                stage_id=s.id,
                check_agents=[roster[-1].id for _ in s.gate.agent_checks],
                review_owner="user",
                failure_owner=roster[0].id,
            )
            for s in design.stages
        ],
        overview="Scoped work shared across the requested team",
        parallel_strategy="Follow existing dependencies; one task executes at a time",
        rationale="Use a developer and a verifier for focused ownership",
    )


def test_propose_then_apply_preserves_scope_and_history(allocation_design, monkeypatch):
    state, path = allocation_design
    original = state.revisions[-1].model_copy(deep=True)
    roster = [
        AgentPreference(id="coder", role="Python developer", model="chosen-model"),
        AgentPreference(id="tester", role="Test specialist"),
    ]
    calls = []

    def respond(project, prompt, response_model, **kwargs):
        calls.append(json.loads(prompt.split("\n", 1)[1]))
        kwargs["on_response"]("planner-model")
        return reply_for(original.design, roster)

    monkeypatch.setattr(allocation, "codex_response", respond)
    proposal = allocation.propose_allocation(state, path, roster)
    assert len(calls) == 1
    assert len(state.revisions) == 1
    assert state.phase == "approved"
    allocation.apply_allocation(state, path, proposal)
    saved = DesignSession.load(path)
    assert saved.revisions[0] == original
    assert saved.phase == "review" and saved.approved_revision is None
    revision = saved.revisions[-1]
    assert revision.agent_models == {"coder": "chosen-model", "tester": None}
    assert revision.reported_model == "planner-model"
    assert revision.allocation_rationale == proposal.reply.rationale
    allowed = {"agent_id", "handoff_to", "handoff_summary"}
    for before, after in zip(original.design.tasks, revision.design.tasks, strict=True):
        assert before.model_dump(exclude=allowed) == after.model_dump(exclude=allowed)
    assert saved.agent_roster == roster
    with pytest.raises(ValueError, match="design changed"):
        allocation.apply_allocation(saved, path, proposal)


@pytest.mark.parametrize("invalid", ["missing_task", "unknown_owner", "human_gate", "wrong_count"])
def test_invalid_proposals_leave_current_design_unchanged(allocation_design, invalid):
    state, _ = allocation_design
    roster = [AgentPreference(id="developer", role="Python developer")]
    reply = reply_for(state.revisions[-1].design, roster)
    if invalid == "missing_task":
        reply.tasks = []
    elif invalid == "unknown_owner":
        reply.tasks[0].agent_id = "unknown"
    elif invalid == "human_gate":
        reply.gates[0].review_owner = "developer"
    else:
        reply.agents = []
    proposal = allocation.AllocationProposal(
        source_fingerprint=allocation.fingerprint(state),
        roster=roster,
        reply=reply,
        elapsed_seconds=0,
    )
    with pytest.raises((ValueError, CodexError)):
        allocation.allocated_revision(state, proposal)
    assert len(state.revisions) == 1 and state.phase == "approved"


def test_busy_project_blocks_planner_request(allocation_design, monkeypatch):
    state, path = allocation_design
    project_lock(state.project).write_text("active")
    monkeypatch.setattr(
        allocation, "codex_response", lambda *a, **kw: pytest.fail("Unexpected inference")
    )
    with pytest.raises(ValueError, match="active"):
        allocation.propose_allocation(state, path, [AgentPreference(id="worker", role="Developer")])
    project_lock(state.project).unlink()


def test_sidebar_team_change_only_calls_ai_on_submit(allocation_design, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from codex_orchestrator import web

    state, path = allocation_design
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(path.parent))
    calls = []

    def respond(project, prompt, response_model, **kwargs):
        payload = json.loads(prompt.split("\n", 1)[1])
        calls.append(payload)
        roster = [AgentPreference.model_validate(a) for a in payload["roster"]]
        return reply_for(state.revisions[-1].design, roster)

    monkeypatch.setattr(allocation, "codex_response", respond)
    app = AppTest.from_file(web.__file__).run()
    next(b for b in app.button if b.label == "Open dashboard").click().run()
    assert not app.exception
    next(n for n in app.number_input if n.label == "Number of AI agents").set_value(2).run()
    assert not app.exception and calls == []
    next(b for b in app.button if b.label == "Suggest task assignments").click().run()
    assert not app.exception and len(calls) == 1
    assert len(DesignSession.load(path).revisions) == 1
    next(b for b in app.button if b.label == "Apply suggested team").click().run()
    assert not app.exception
    saved = DesignSession.load(path)
    assert len(saved.revisions[-1].design.agents) == 2
    assert saved.phase == "review"
    assert len(calls) == 1
