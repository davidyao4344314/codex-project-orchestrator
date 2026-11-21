import json
from uuid import uuid4

from typer.testing import CliRunner

from codex_orchestrator import cycle_manager, session_manager
from codex_orchestrator.cli import app
from codex_orchestrator.cycle_store import ArchivedDelivery, find_history
from codex_orchestrator.usage_report import usage_report


def record(path, project, role, raw, *, persistent=True, task_id=None, stage_id=None):
    turn = session_manager.begin_turn(
        path,
        project,
        role,
        "requested-model",
        "test-boundary",
        persistent=persistent,
        task_id=task_id,
        stage_id=stage_id,
    )
    turn.prepare("test prompt")
    turn.events.observe(
        {
            "type": "thread.started",
            "thread_id": turn.resume_id or str(uuid4()),
        }
    )
    turn.events.observe({"type": "turn.completed", "usage": raw})
    turn.finish("completed")
    return turn


def test_usage_report_aggregates_current_and_archived_deliveries_without_inference(
    approved_session, monkeypatch
):
    source, state = approved_session
    project = source.parent
    cycle_manager.register(source)
    first = record(
        source,
        project,
        "requirements",
        {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 20},
    )
    second = record(source, project, "requirements", {"input_tokens": 70, "output_tokens": 10})
    assert second.resume_id == first.events.thread
    new = source.with_name("correction.json")
    state.save(new, new=True)
    history_path, history = find_history(source)
    history.cycles[0].archived_deliveries.append(
        ArchivedDelivery(
            session_name=source.name,
            design_digest="test",
            merged_stages={},
        )
    )
    history.cycles[0].session_name = new.name
    history.save(history_path)
    new_design = new.with_suffix(".design.json")
    record(
        new_design,
        project,
        "worker:dev:backend:write",
        {"input_tokens": 200, "cached_input_tokens": 100, "output_tokens": 50},
        task_id="backend",
        stage_id="core",
    )
    record(new_design, project, "reviewer", {}, persistent=False, stage_id="core")
    report = usage_report(source, details=True)
    assert report["cycle"] == 1 and report["current_session"] == str(new)
    assert report["total"]["attempts"] == 4
    assert report["total"]["resume_attempts"] == 1
    assert report["total"]["token_totals"]["input_tokens"] == 370
    assert report["total"]["token_totals"]["cached_input_tokens"] == 160
    assert report["total"]["token_totals"]["output_tokens"] == 80
    assert report["total"]["token_coverage_turns"]["cached_input_tokens"] == 2
    assert report["by_role"]["reviewer"]["token_totals"]["input_tokens"] is None
    assert report["by_task"]["backend"]["token_totals"]["input_tokens"] == 200
    assert len(report["turns"]) == 4
    monkeypatch.setattr(
        "codex_orchestrator.codex.find_codex",
        lambda: (_ for _ in ()).throw(AssertionError("Unexpected Codex call")),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["cycle", "usage", str(source), "--details"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["total"]["attempts"] == 4
    result = runner.invoke(app, ["cycle", "sessions", str(source)])
    assert result.exit_code == 0, result.output
    bindings = json.loads(result.output)["sessions"]
    assert {b["role"] for b in bindings} == {"requirements", "worker:dev:backend:write"}
    assert next(b for b in bindings if b["role"] == "requirements")["current_delivery"] is False
