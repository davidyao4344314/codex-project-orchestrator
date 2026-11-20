import pytest
from test_git_service import accepted as accepted
from test_stage_branch_services import project as project
from test_stage_branches import publish, run_task

from codex_orchestrator import cycle_manager as cycles
from codex_orchestrator import git_service as git
from codex_orchestrator import stage_branches as branches
from codex_orchestrator.cycle_store import find_history
from codex_orchestrator.session import PlanningSession


def test_import_and_incomplete_cycle_cannot_advance(accepted):
    _, design = accepted
    source = design.with_name("source.json")
    before = source.read_bytes()
    history = cycles.register(source)
    assert history.cycles[0].imported
    assert source.read_bytes() == before
    with pytest.raises(ValueError, match="Complete"):
        cycles.start_next(source, "Another feature")
    with pytest.raises(ValueError, match="Enable stage branches"):
        cycles.complete(source, "Delivered", "Works")


def test_complete_and_start_another_cycle(project, monkeypatch):
    root, design = project
    source = design.with_name("source.json")
    cycles.register(source)
    for stage in ("core", "delivery"):
        branches.prepare(design, stage)
        run_task(root, design, stage)
        receipt = publish(root, design, stage, monkeypatch)
        git.git(root, "switch", "main")
        git.git(root, "merge", "--ff-only", receipt.branch)
        branches.record_merge(design, stage)
    completed = cycles.complete(source, "Implemented counter", "Improve display")
    assert completed.completed_at >= completed.started_at and completed.commits
    original = source.read_bytes()
    next_path = cycles.start_next(source, "Improve display")
    history = find_history(next_path)[1]
    assert [c.status for c in history.cycles] == ["completed", "active"]
    assert source.read_bytes() == original
    next_session = PlanningSession.load(next_path)
    assert next_session.phase == "intake"
    assert "Improve display" in next_session.previous_cycle_context
    assert "Implemented counter" in next_session.previous_cycle_context
    assert next_session.answers[0].response == "Improve display"
    with pytest.raises(ValueError, match="historical"):
        cycles.require_active(source)
    with pytest.raises(ValueError):
        cycles.start_next(source, "Duplicate")
