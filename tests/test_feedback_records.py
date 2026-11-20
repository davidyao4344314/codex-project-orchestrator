from codex_orchestrator.cycle_store import Cycle, FeedbackRound


def test_feedback_rounds_preserve_timestamp_and_boundary():
    cycle = Cycle(number=1, session_name="one.json", goal="Notes")
    assert not cycle.feedback_rounds
    cycle.feedback_rounds.append(
        FeedbackRound(
            kind="improve",
            revision=1,
            design_digest="design",
            stage_id="ui",
            note="Improve button",
            task_id="layout",
            agent_id="frontend",
            workstream="frontend",
            owned_paths=["app.py"],
            prior_run_id="old",
        )
    )
    restored = Cycle.model_validate_json(cycle.model_dump_json())
    assert restored.feedback_rounds == cycle.feedback_rounds
    assert restored.feedback_rounds[0].recorded_at.utcoffset().total_seconds() == 0
