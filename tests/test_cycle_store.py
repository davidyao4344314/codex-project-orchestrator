import pytest

from codex_orchestrator.cycle_store import Cycle, ProjectHistory, find_history, history_path


def test_history_round_trip(tmp_path):
    source = tmp_path / "session.json"
    history = ProjectHistory(
        project=str(tmp_path),
        name="Demo",
        cycles=[Cycle(number=1, session_name=source.name, goal="Build notes")],
    )
    history.save(history_path(source))
    assert find_history(source)[1] == history
    assert history.cycles[0].started_at.utcoffset().total_seconds() == 0


def test_history_rejects_multiple_active_cycles(tmp_path):
    with pytest.raises(ValueError, match="newest"):
        ProjectHistory(
            project=str(tmp_path),
            name="Demo",
            cycles=[
                Cycle(number=1, session_name="one.json", goal="One"),
                Cycle(number=2, session_name="two.json", goal="Two"),
            ],
        )
