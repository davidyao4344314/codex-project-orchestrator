import copy

import pytest

from codex_orchestrator import coordinator, execution
from codex_orchestrator.coordination_store import checkpoint_path, load_coordination
from codex_orchestrator.design import prepare_design
from codex_orchestrator.design_models import DesignRevision, TechnicalDesign
from codex_orchestrator.run_store import Run, run_directory
from codex_orchestrator.stage_review import review_stage


@pytest.fixture
def coordinated(approved_session, technical_payload):
    source, _ = approved_session
    payload = copy.deepcopy(technical_payload)
    second = copy.deepcopy(payload["tasks"][0])
    second.update(id="verify", title="Verify counter", depends_on=["implement"], owned_paths=[])
    payload["tasks"].append(second)
    state, path = prepare_design(source, None, architect_model=None, agent_model=None)
    state.revisions.append(
        DesignRevision(
            design=TechnicalDesign.model_validate(payload),
            agent_models={"developer": None},
            elapsed_seconds=0,
        )
    )
    state.phase, state.approved_revision = "approved", 1
    state.save(path)
    review_stage(state, path, "core", "", approved=True)
    return state, path


def record(path, task_id, status="completed", verified=False, suffix="1"):
    run = Run(
        id=task_id + suffix,
        task_id=task_id,
        agent_id="developer",
        revision=1,
        status=status,
        verified=verified,
        prompt="saved context",
    )
    folder = run_directory(path) / run.id
    folder.mkdir(parents=True, exist_ok=True)
    run.save(folder / "run.json")
    return run


def test_scheduling_requires_verification_before_dependent_dispatch(coordinated, monkeypatch):
    state, path = coordinated
    calls = []

    def start(saved, file, task, context):
        calls.append((task.id, context))
        return record(file, task.id, status="running")

    monkeypatch.setattr(execution, "start_run", start)
    schedule = coordinator.stage_schedule(path, "core")
    assert [t.status for t in schedule.tasks] == ["ready", "queued"]
    assert calls == [] and not checkpoint_path(state, path).exists()
    coordinator.run_next(path, "core")
    assert calls[0][0] == "implement"
    assert calls[0][1]["assignment"]["id"] == "implement"
    assert coordinator.stage_schedule(path, "core").status == "running"
    record(path, "implement")
    assert coordinator.stage_schedule(path, "core").status == "awaiting_verification"
    with pytest.raises(ValueError, match="checks"):
        coordinator.run_next(path, "core")
    record(path, "implement", verified=True)
    assert coordinator.stage_schedule(path, "core").status == "ready"
    coordinator.run_next(path, "core")
    assert calls[-1][0] == "verify"


def test_failure_requires_explicit_retry_and_pause_survives_reload(coordinated, monkeypatch):
    state, path = coordinated
    record(path, "implement", status="failed")
    assert coordinator.stage_schedule(path, "core").status == "blocked"
    calls = []
    monkeypatch.setattr(execution, "start_run", lambda *args: calls.append(args) or "started")
    with pytest.raises(ValueError, match="failed"):
        coordinator.run_next(path, "core")
    assert not calls
    assert coordinator.run_next(path, "core", retry=True) == "started"
    coordinator.pause(path)
    assert load_coordination(state, path).paused
    assert coordinator.stage_schedule(path, "core").status == "paused"
    with pytest.raises(ValueError):
        coordinator.run_next(path, "core", retry=True)
    coordinator.resume(path)
    assert not load_coordination(state, path).paused
    assert len(calls) == 1  # Resume alone never starts a task.


def test_stage_gate_and_retry_invalidate_prior_acceptance(coordinated):
    state, path = coordinated
    for task in state.revisions[-1].design.tasks:
        record(path, task.id, verified=True)
    assert coordinator.stage_schedule(path, "core").status == "awaiting_stage_acceptance"
    with pytest.raises(ValueError, match="evidence"):
        coordinator.accept_stage(path, "core", "")
    coordinator.accept_stage(path, "core", "Reviewed combined changes; terminal checks passed")
    assert coordinator.stage_schedule(path, "core").status == "accepted"
    record(path, "implement", status="failed", suffix="2")
    assert coordinator.stage_schedule(path, "core").status == "blocked"


def test_pausing_requests_stop_for_active_run(coordinated, monkeypatch):
    state, path = coordinated
    active = record(path, "implement", status="running")
    calls = []
    monkeypatch.setattr(execution, "stop_run", lambda run: calls.append(run.id))
    coordinator.pause(path)
    assert calls == [active.id]
    assert load_coordination(state, path).paused


def test_upstream_stage_needs_acceptance_even_for_individual_runner(coordinated):
    state, path = coordinated
    design = state.revisions[-1].design
    stage = design.stages[0].model_copy(deep=True)
    stage.id, stage.depends_on = "delivery", ["core"]
    design.stages.append(stage)
    task = design.tasks[-1].model_copy(deep=True)
    task.id, task.stage_id, task.depends_on = "deliver", "delivery", ["verify"]
    design.tasks.append(task)
    state.save(path)
    review_stage(state, path, "delivery", "", approved=True)
    for item in design.tasks[:2]:
        record(path, item.id, verified=True)
    assert coordinator.stage_schedule(path, "delivery").status == "blocked"
    with pytest.raises(ValueError, match="prerequisite stage"):
        execution.start_run(state, path, task, {})
    coordinator.accept_stage(path, "core", "Checked the combined stage")
    assert coordinator.stage_schedule(path, "delivery").status == "ready"


def test_dashboard_polling_is_read_only_and_dispatch_is_explicit(coordinated, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from codex_orchestrator import web

    state, path = coordinated
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(path.parent))
    calls = []

    def start(saved, file, task, context):
        calls.append(task.id)
        return record(file, task.id, status="running")

    monkeypatch.setattr(execution, "start_run", start)
    app = AppTest.from_file(web.__file__).run()
    next(b for b in app.button if b.label == "Open dashboard").click().run()
    assert not app.exception
    assert any(h.value == "Execution coordinator" for h in app.subheader)
    app.run()
    assert not app.exception and calls == []
    next(b for b in app.button if b.label == "Run next ready task").click().run()
    assert not app.exception and calls == ["implement"]
    next(b for b in app.button if b.label == "Inspect task and verification").click().run()
    assert not app.exception
    assert next(r for r in app.radio if r.label == "Stage").value == "Agent workspace"


def test_new_revision_does_not_reuse_coordinator_acceptance_or_pause(coordinated):
    state, path = coordinated
    for task in state.revisions[-1].design.tasks:
        record(path, task.id, verified=True)
    coordinator.accept_stage(path, "core", "Reviewed combined changes")
    coordinator.pause(path)
    old_checkpoint = checkpoint_path(state, path)
    state.revisions.append(state.revisions[-1].model_copy(deep=True))
    state.phase, state.approved_revision = "review", None
    state.save(path)
    current = load_coordination(state, path)
    assert current.accepted_stages == {} and not current.paused
    assert checkpoint_path(state, path) != old_checkpoint
    assert old_checkpoint.exists()
    assert coordinator.stage_schedule(path, "core").status == "blocked"


def test_dashboard_records_stage_evidence_without_starting_work(coordinated, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from codex_orchestrator import web

    state, path = coordinated
    for task in state.revisions[-1].design.tasks:
        record(path, task.id, verified=True)
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(path.parent))
    monkeypatch.setattr(execution, "start_run", lambda *a: pytest.fail("Unexpected dispatch"))
    app = AppTest.from_file(web.__file__).run()
    next(b for b in app.button if b.label == "Open dashboard").click().run()
    assert not app.exception
    next(t for t in app.text_area if t.label == "Review and manual-test evidence").set_value(
        "Reviewed combined changes. Focused and manual checks passed."
    )
    next(c for c in app.checkbox if c.label.startswith("I reviewed the combined stage")).check()
    next(b for b in app.button if b.label == "Accept stage verification").click().run()
    assert not app.exception
    assert coordinator.stage_schedule(path, "core").status == "accepted"
    assert (
        "Focused and manual checks passed"
        in load_coordination(state, path).accepted_stages["core"].evidence
    )
