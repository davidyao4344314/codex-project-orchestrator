import json

import pytest

pytest.importorskip("streamlit")

from codex_orchestrator.dashboard import (
    AgentChats,
    AgentReply,
    ask_agent,
    source_files,
    task_context,
)
from codex_orchestrator.design import prepare_design
from codex_orchestrator.design_models import DesignRevision, TechnicalDesign


def test_chat_scopes_context_and_preserves_revision_history(
    approved_session, technical_payload, monkeypatch
):
    source, _ = approved_session
    state, path = prepare_design(source, None, architect_model=None, agent_model=None)
    revision = DesignRevision(
        design=TechnicalDesign.model_validate(technical_payload),
        agent_models={"developer": "chosen-model"},
        elapsed_seconds=0,
    )
    state.revisions.append(revision)
    state.phase = "review"
    state.save(path)
    task = revision.design.tasks[0]
    calls = []

    def respond(project, prompt, response_model, **kwargs):
        calls.append(json.loads(prompt.split("\n", 1)[1]))
        assert kwargs["model"] == "chosen-model"
        kwargs["on_response"]("actual-model")
        return AgentReply(answer="Proposed code: `print(1)`")

    monkeypatch.setattr("codex_orchestrator.dashboard.codex_response", respond)
    ask_agent(state, path, task, "What will you build?")
    assert calls[0]["context"]["metadata"]["assignment"] == task_context(state, task)["assignment"]
    assert calls[0]["context"]["purpose"] == "discussion"
    assert calls[0]["history"] == []
    saved = AgentChats.load(path.with_suffix(".chat.json"))
    assert saved.messages[-1].model == "actual-model"
    ask_agent(state, path, task, "Explain more")
    assert len(calls[1]["history"]) == 2
    state.revisions.append(revision.model_copy(deep=True))
    ask_agent(state, path, task, "New revision")
    assert calls[2]["history"] == []


def test_source_preview_stays_in_owned_project_paths(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("print(1)")
    (project / ".env").write_text("SECRET=private")
    (project / "auth.json").write_text("{}")
    outside = tmp_path / "outside.py"
    outside.write_text("private")
    (project / "escape.py").symlink_to(outside)
    assert list(source_files(project, ["*", "../outside.py", str(outside)])) == ["app.py"]


def test_discussion_history_has_a_byte_budget_and_reports_omissions(
    approved_session, technical_payload, monkeypatch
):
    from codex_orchestrator.dashboard import ChatMessage

    source, _ = approved_session
    state, path = prepare_design(source, None, architect_model=None, agent_model=None)
    state.revisions.append(
        DesignRevision(
            design=TechnicalDesign.model_validate(technical_payload),
            agent_models={"developer": None},
            elapsed_seconds=0,
        )
    )
    state.phase = "review"
    state.save(path)
    task = state.revisions[-1].design.tasks[0]
    AgentChats(
        messages=[
            ChatMessage(
                revision=1,
                agent_id=task.agent_id,
                task_id=task.id,
                role="assistant",
                text="long discussion " * 1000,
            )
            for _ in range(16)
        ]
    ).save(path.with_suffix(".chat.json"))
    calls = []

    def respond(project, prompt, response_model, **kwargs):
        calls.append(json.loads(prompt.split("\n", 1)[1]))
        return AgentReply(answer="Brief answer")

    monkeypatch.setattr("codex_orchestrator.dashboard.codex_response", respond)
    ask_agent(state, path, task, "Summarize the plan")
    assert len(json.dumps(calls[0]["history"], ensure_ascii=False).encode()) <= 16 * 1024
    assert calls[0]["older_messages_omitted"] == 16 - len(calls[0]["history"])
