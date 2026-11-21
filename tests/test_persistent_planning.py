import json
import subprocess
from pathlib import Path
from uuid import uuid4

from test_interactive import draft, intake

from codex_orchestrator import design, interactive, session_manager


def fake_cli(monkeypatch, replies):
    calls = []
    monkeypatch.setattr("codex_orchestrator.codex.find_codex", lambda: "fake-codex")

    def run(command, **kwargs):
        calls.append((command, kwargs["input"]))
        identity = command[-2] if command[-3] == "resume" else str(uuid4())
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps(replies.pop(0))
        )
        events = [
            {"type": "thread.started", "thread_id": identity},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
        ]
        return subprocess.CompletedProcess(
            command, 0, stdout="\n".join(json.dumps(e) for e in events), stderr=""
        )

    monkeypatch.setattr("codex_orchestrator.codex.subprocess.run", run)
    return calls


def test_requirements_resume_sends_only_new_answers_and_planner_is_separate(tmp_path, monkeypatch):
    requirement = "Members can edit notes"
    calls = fake_cli(
        monkeypatch,
        [
            intake(requirement, "Who can edit?"),
            intake(requirement),
            draft(requirement),
        ],
    )
    path = tmp_path / "session.json"
    state = interactive.create_session(tmp_path, "Notes", "Unique initial project idea", path)
    interactive.collect_brief(state, path)
    first = session_manager.read_turns(path)[0]
    assert first.session_reusable
    state = interactive.PlanningSession.load(path)
    interactive.submit_answer(state, path, "Members only")
    interactive.collect_brief(state, path)
    assert calls[1][0][-3:] == ["resume", first.thread_id, "-"]
    assert "Members only" in calls[1][1] and "Unique initial project idea" not in calls[1][1]
    assert "previous_brief" not in calls[1][1]
    assert len(calls[1][1]) < len(calls[0][1])
    interactive.generate_plan(state, path)
    assert "resume" not in calls[2][0]
    records = session_manager.read_turns(path)
    assert records[-1].role == "planner" and records[-1].thread_id != first.thread_id
    assert state.phase == "review"


def test_architect_resumes_clarification_but_reloads_materially_changed_design(
    approved_session, technical_payload, monkeypatch
):
    source, _ = approved_session
    state, path = design.prepare_design(source, None, architect_model=None, agent_model=None)
    answer = {
        "question": None,
        "requires_requirement_change": False,
        "design": technical_payload,
    }
    calls = fake_cli(
        monkeypatch,
        [
            {
                "question": "Which output format?",
                "requires_requirement_change": False,
                "design": None,
            },
            answer,
            answer,
        ],
    )
    design.generate_design(state, path, 30)
    original = session_manager.read_turns(path)[0].thread_id
    design.submit_design_answer(state, path, "Print numbers")
    design.generate_design(state, path, 30)
    assert calls[1][0][-3:] == ["resume", original, "-"]
    assert "Print numbers" in calls[1][1] and "approved_brief" not in calls[1][1]
    assert state.phase == "review"
    design.revise_design(state, path, "Use a different implementation boundary")
    design.generate_design(state, path, 30)
    assert "resume" not in calls[2][0]
    assert "approved_brief" in calls[2][1]
