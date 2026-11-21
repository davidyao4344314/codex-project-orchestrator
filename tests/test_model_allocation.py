import copy
import hashlib

import pytest
from test_persistent_workers import wait
from test_worker_execution import launch
from typer.testing import CliRunner

from codex_orchestrator import execution
from codex_orchestrator.cli import app
from codex_orchestrator.coordination_store import design_fingerprint
from codex_orchestrator.design import prepare_design, update_design_models
from codex_orchestrator.design_models import DesignRevision, DesignSession, TechnicalDesign
from codex_orchestrator.model_allocation import apply_automatic_models, automatic_models
from codex_orchestrator.session import ModelPolicy, PlanningSession

PALETTE = ModelPolicy(
    routine="small-model", standard="standard-model", strong_reasoning="strong-model"
)


def mixed_design(technical_payload):
    payload = copy.deepcopy(technical_payload)
    payload["agents"][0]["model_capability"] = "strong_reasoning"
    for task_id, difficulty, file in (
        ("refine", "moderate", "refine.py"),
        ("complex", "complex", "complex.py"),
    ):
        task = copy.deepcopy(payload["tasks"][0])
        task.update(
            id=task_id,
            title=task_id,
            difficulty=difficulty,
            workstream="general",
            owned_paths=[file],
            depends_on=["implement"],
        )
        payload["tasks"].append(task)
    return payload


def test_different_task_difficulties_select_models_even_for_same_agent(technical_payload):
    design = TechnicalDesign.model_validate(mixed_design(technical_payload))
    agents, tasks, rationale = automatic_models(design, PALETTE)
    assert agents == {"developer": "strong-model"}
    assert tasks == {
        "implement": "small-model",
        "refine": "standard-model",
        "complex": "strong-model",
    }
    assert "complex: strong_reasoning" in rationale
    # A stronger architect recommendation that isn't accounted for in task ratings
    # must not quietly be downgraded.
    design.tasks = design.tasks[:1]
    assert automatic_models(design, PALETTE)[1] == {"implement": "strong-model"}


def test_worker_cli_receives_approved_task_specific_models(
    approved_session, technical_payload, monkeypatch
):
    state, path, first, _, calls = launch(
        approved_session, mixed_design(technical_payload), monkeypatch, "general", policy=PALETTE
    )
    assert first.status == "completed" and first.model == "small-model"
    execution.verify_run(path, first.id)
    threads = [first.thread_id]
    for task in state.revisions[-1].design.tasks[1:]:
        run = wait(path, state.project, execution.start_run(state, path, task))
        assert run.status == "completed", run.error
        assert run.model == state.revisions[-1].model_for_task(task)
        assert run.resume_thread_id is None  # Changing model has a fresh session boundary.
        threads.append(run.thread_id)
        execution.verify_run(path, run.id)
    assert [cmd[cmd.index("--model") + 1] for cmd in calls] == [
        "small-model",
        "standard-model",
        "strong-model",
    ]
    assert len(set(threads)) == 3
    with pytest.raises(ValueError, match="Delivery has started"):
        apply_automatic_models(state, path)


def test_auto_model_revision_preserves_old_fingerprints_and_requires_new_approval(
    approved_session, technical_payload, monkeypatch
):
    source, _ = approved_session
    state, path = prepare_design(source, None, architect_model=None, agent_model=None)
    revision = DesignRevision(
        design=TechnicalDesign.model_validate(mixed_design(technical_payload)),
        agent_models={"developer": "old-model"},
        elapsed_seconds=0,
    )
    state.revisions = [revision]
    state.phase, state.approved_revision = "approved", 1
    state.save(path)
    # Pre-feature revisions omitted task_models entirely; preserve their original identity.
    original_digest = hashlib.sha256(
        revision.model_dump_json(exclude={"task_models"}).encode()
    ).hexdigest()
    assert design_fingerprint(DesignSession.load(path)) == original_digest
    session = PlanningSession.load(source)
    session.model_policy = PALETTE
    session.save(source)
    monkeypatch.setattr(
        "codex_orchestrator.design.codex_response",
        lambda *a, **kw: pytest.fail("Automatic allocation should not use a model call"),
    )
    result = CliRunner().invoke(app, ["design", str(source), "--auto-models"], input="save\n")
    assert result.exit_code == 0, result.output
    updated = DesignSession.load(path)
    assert updated.phase == "review" and updated.approved_revision is None
    assert updated.revisions[0] == revision and len(updated.revisions) == 2
    assert updated.revisions[-1].task_models["implement"] == "small-model"
    assert design_fingerprint(updated) != original_digest
    update_design_models(updated, path, None, {"developer": "manual-model"})
    assert updated.revisions[-1].task_models is None
    assert updated.auto_model_allocation is False
    assert (
        updated.revisions[-1].model_for_task(updated.revisions[-1].design.tasks[0])
        == "manual-model"
    )
