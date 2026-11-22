import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from codex_orchestrator import session_manager as sessions
from codex_orchestrator.codex import CodexError, codex_response, exec_command
from codex_orchestrator.models import PlanContent
from codex_orchestrator.planner import template_plan


def complete(turn, identity=None):
    identity = identity or turn.resume_id or str(uuid4())
    turn.events.observe({"type": "thread.started", "thread_id": identity})
    turn.events.observe(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 20},
        }
    )
    turn.finish("completed")
    return identity


def test_resume_isolated_by_role_model_project_and_checkpoint(tmp_path):
    path = tmp_path / "source.json"
    backend = sessions.begin_turn(path, tmp_path, "worker:backend", "model", "revision")
    original = complete(backend)
    frontend = sessions.begin_turn(path, tmp_path, "worker:frontend", "model", "revision")
    assert frontend.resume_id is None
    assert complete(frontend) != original
    resumed = sessions.begin_turn(path, tmp_path, "worker:backend", "model", "revision")
    assert resumed.resume_id == original
    complete(resumed)
    changed = sessions.begin_turn(path, tmp_path, "worker:backend", "new-model", "revision")
    assert changed.resume_id is None
    complete(changed)
    assert (
        sessions.begin_turn(
            path, tmp_path / "other", "worker:backend", "new-model", "revision"
        ).resume_id
        is None
    )
    assert (
        sessions.begin_turn(
            tmp_path / "other.json", tmp_path, "worker:frontend", "model", "revision"
        ).resume_id
        is None
    )


def test_claimed_failed_rotated_and_forced_fresh_sessions(tmp_path, monkeypatch):
    path = tmp_path / "source.json"
    monkeypatch.setattr(sessions, "MAX_SESSION_TURNS", 2)

    def start():
        return sessions.begin_turn(path, tmp_path, "intake", None, "read-only")

    original = complete(start())
    interrupted = start()
    assert interrupted.resume_id == original
    competing = start()
    assert competing.resume_id is None
    complete(interrupted)  # A stale writer cannot restore a claimed binding.
    new = complete(competing)
    retry = start()
    assert retry.resume_id == new
    retry.finish("failed")
    assert start().resume_id is None
    first = complete(start())
    second = start()
    assert second.resume_id == first
    complete(second)
    assert start().resume_id is None  # Rotate after two turns in this test.
    complete(start())
    monkeypatch.setenv("ORCHESTRATOR_FRESH_SESSIONS", "1")
    assert start().resume_id is None


def test_structured_resumed_call_metrics_and_mismatched_thread(tmp_path, monkeypatch):
    path = tmp_path / "source.json"
    identity = str(uuid4())
    calls = []
    monkeypatch.setattr("codex_orchestrator.codex.find_codex", lambda: "fake")

    def run(command, **kwargs):
        calls.append(command)
        Path(command[command.index("--output-last-message") + 1]).write_text(
            template_plan().model_dump_json()
        )
        events = [
            {"type": "thread.started", "thread_id": identity},
            {"type": "turn.completed", "usage": {"input_tokens": 123, "output_tokens": 12}},
        ]
        return subprocess.CompletedProcess(
            command, 0, stdout="\n".join(json.dumps(event) for event in events), stderr=""
        )

    monkeypatch.setattr("codex_orchestrator.codex.subprocess.run", run)
    for prompt in ("First turn", "Follow-up"):
        with sessions.managed_turn(path, tmp_path, "architect", None, "read-only") as turn:
            assert codex_response(tmp_path, prompt, PlanContent, turn=turn) == template_plan()
    assert "resume" not in calls[0]
    assert "--ephemeral" not in calls[0]
    assert calls[1][-3:] == ["resume", identity, "-"]
    for command in calls:
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[command.index("--ask-for-approval") + 1] == "never"
        assert "--json" in command and "--output-schema" in command
    receipts = sessions.read_turns(path)
    assert len(receipts) == 2
    assert receipts[1].requested_thread_id == identity
    assert receipts[1].usage.input_tokens == 123
    assert receipts[1].usage.cached_input_tokens is None
    assert receipts[1].prompt_bytes == len("Follow-up")
    identity = str(uuid4())
    with pytest.raises(CodexError, match="mismatched"):
        with sessions.managed_turn(path, tmp_path, "architect", None, "read-only") as turn:
            codex_response(tmp_path, "Mismatch", PlanContent, turn=turn)
    assert not sessions.read_registry(path).slots["architect"].binding
    assert sessions.read_turns(path)[-1].status == "failed"


def test_ephemeral_review_never_resumes_and_invalid_resume_fails(tmp_path):
    path = tmp_path / "source.json"
    turn = sessions.begin_turn(path, tmp_path, "reviewer", None, "read-only", persistent=False)
    complete(turn)
    assert not sessions.read_registry(path).slots
    assert sessions.read_turns(path)[0].session_reusable is False
    with pytest.raises(ValueError, match="UUID"):
        exec_command("codex", tmp_path, sandbox="read-only", resume_id="--last")
    assert sessions.TokenUsage.from_event({"input_tokens": True, "output_tokens": -10}) is None


def test_reconnect_warning_does_not_mask_final_completed_turn():
    from codex_orchestrator.session_manager import TurnEvents

    events = TurnEvents()
    events.observe({"type": "error", "message": "Reconnecting... 2/5 (capacity)"})
    events.observe({"type": "turn.completed", "usage": {"input_tokens": 10}})
    assert events.completed and not events.failed
    assert events.reconnect_warning
    events.observe({"type": "turn.failed"})
    assert events.failed
