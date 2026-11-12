import hashlib

import pytest

from codex_orchestrator.context_builder import build_context
from codex_orchestrator.context_models import ContextLimits
from codex_orchestrator.design import prepare_design
from codex_orchestrator.design_models import (
    ContextReference,
    DesignRevision,
    Skill,
    TechnicalDesign,
)


@pytest.fixture
def context_design(approved_session, technical_payload):
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
    return state, path


def file_context(task, name, required=True):
    task.context.append(
        ContextReference(
            kind="project_file",
            reference=name,
            required=required,
            reason="Needed for implementation",
            missing_action="Supply the file or revise the task",
        )
    )


def test_context_contains_only_selected_files_and_deduplicates_reads(context_design, monkeypatch):
    state, path = context_design
    project = path.parent
    (project / "counter.py").write_text("print('counter')\n")
    (project / "unrelated.py").write_text("private sibling context")
    task = state.revisions[-1].design.tasks[0]
    file_context(task, "counter.py")
    from codex_orchestrator import context_builder

    original = context_builder.read_file
    reads = []

    def read(*args):
        reads.append(args[1].reference)
        return original(*args)

    monkeypatch.setattr(context_builder, "read_file", read)
    package = build_context(state, task)
    assert reads == ["counter.py"]
    resource = next(r for r in package.resources if r.reference == "counter.py")
    assert resource.sha256 == hashlib.sha256(b"print('counter')\n").hexdigest()
    assert resource.aliases == ["owned_file: counter.py"]
    assert "private sibling context" not in package.payload()
    assert not package.blockers
    assert package.size_bytes() <= package.limits.max_package_bytes


@pytest.mark.parametrize("name", [".env", "auth.json", "secrets.toml", "private.key"])
def test_sensitive_file_paths_are_not_included(context_design, name):
    state, path = context_design
    (path.parent / name).write_text("TOP_SECRET_VALUE")
    task = state.revisions[-1].design.tasks[0]
    file_context(task, name)
    package = build_context(state, task)
    assert package.blockers
    assert "TOP_SECRET_VALUE" not in package.payload()
    with pytest.raises(ValueError, match="incomplete"):
        package.require_ready()


def test_missing_truncated_binary_and_outside_files_are_explicit(context_design, tmp_path):
    state, path = context_design
    task = state.revisions[-1].design.tasks[0]
    (path.parent / "large.py").write_text("x" * 1000)
    (path.parent / "binary.dat").write_bytes(b"\x00\x01")
    outside = path.parent.parent / (path.parent.name + "-outside.txt")
    outside.write_text("outside-private")
    (path.parent / "escape.txt").symlink_to(outside)
    for name in ["missing.txt", "large.py", "binary.dat", "escape.txt"]:
        file_context(task, name)
    package = build_context(state, task, limits=ContextLimits(max_file_bytes=128))
    statuses = {r.reference: r.status for r in package.resources}
    assert statuses["missing.txt"] == "missing"
    assert statuses["large.py"] == "truncated"
    assert statuses["binary.dat"] == statuses["escape.txt"] == "excluded"
    assert "outside-private" not in package.payload()
    assert len(package.blockers) == 4


def test_skill_resolution_and_optional_missing_file(context_design):
    state, path = context_design
    skill = path.parent / "skills" / "python" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Use focused Python tests.")
    state.revisions[-1].design.agents[0].skills = [
        Skill(name="Python", location="skills/python/SKILL.md")
    ]
    task = state.revisions[-1].design.tasks[0]
    file_context(task, "optional.txt", required=False)
    package = build_context(state, task)
    assert not package.blockers
    assert any(
        r.kind == "skill" and r.content == "Use focused Python tests." for r in package.resources
    )
    assert any("optional.txt" in warning for warning in package.warnings)


def test_budget_never_silently_drops_required_input(context_design):
    state, path = context_design
    (path.parent / "needed.py").write_text("x" * 7000)
    task = state.revisions[-1].design.tasks[0]
    file_context(task, "needed.py")
    package = build_context(state, task, limits=ContextLimits(max_package_bytes=5000))
    assert package.blockers
    assert "x" * 7000 not in package.payload()


def test_recognizable_credentials_in_source_are_excluded(context_design):
    state, path = context_design
    key = "sk-" + "a" * 30
    (path.parent / "config.py").write_text(f"KEY = '{key}'")
    task = state.revisions[-1].design.tasks[0]
    file_context(task, "config.py")
    package = build_context(state, task)
    assert package.blockers and key not in package.payload()


def handoff_task(state):
    producer = state.revisions[-1].design.tasks[0]
    consumer = producer.model_copy(deep=True)
    consumer.id = "consumer"
    consumer.owned_paths = []
    consumer.depends_on = [producer.id]
    consumer.context = [
        ContextReference(
            kind="task_output",
            reference=producer.id,
            required=True,
            reason="Need implementation and test evidence",
            missing_action="Finish and verify the producer first",
        )
    ]
    state.revisions[-1].design.tasks.append(consumer)
    return producer, consumer


def save_handoff(path, producer, **updates):
    from codex_orchestrator.run_store import Run, run_directory

    data = dict(
        id="upstream",
        task_id=producer.id,
        agent_id=producer.agent_id,
        revision=1,
        status="completed",
        verified=True,
        prompt="Unrelated private conversation",
        handoff_summary="Implemented counter; tests pass",
    )
    data.update(updates)
    run = Run(**data)
    folder = run_directory(path) / run.id
    folder.mkdir(parents=True, exist_ok=True)
    run.save(folder / "run.json")
    return run, folder


def test_verified_declared_handoff_includes_summary_and_source_without_transcript(context_design):
    state, path = context_design
    producer, consumer = handoff_task(state)
    (path.parent / "counter.py").write_text("print('implementation')")
    run, _ = save_handoff(path, producer)
    package = build_context(state, consumer, path)
    assert not package.blockers
    summary = next(r for r in package.resources if r.kind == "task_output")
    assert summary.source_run_id == run.id
    assert summary.content == "Implemented counter; tests pass"
    assert any(
        r.kind == "upstream_file" and r.content == "print('implementation')"
        for r in package.resources
    )
    assert "Unrelated private conversation" not in package.payload()


@pytest.mark.parametrize("updates", [{"verified": False}, {"status": "failed"}, {"revision": 2}])
def test_unverified_failed_or_other_revision_output_is_not_handed_off(context_design, updates):
    state, path = context_design
    producer, consumer = handoff_task(state)
    save_handoff(path, producer, **updates)
    package = build_context(state, consumer, path)
    resource = next(r for r in package.resources if r.kind == "task_output")
    assert resource.status == "pending" and resource.content is None
    assert package.blockers
    assert "Implemented counter; tests pass" not in package.payload()


def test_legacy_run_summary_is_resolved_from_bounded_final_message(context_design):
    import json

    state, path = context_design
    producer, consumer = handoff_task(state)
    (path.parent / "counter.py").write_text("print(1)")
    _, folder = save_handoff(path, producer, handoff_summary=None)
    (folder / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Legacy handoff; focused tests passed"},
            }
        )
        + "\n"
    )
    package = build_context(state, consumer, path)
    assert any(r.content == "Legacy handoff; focused tests passed" for r in package.resources)
    assert not package.blockers


def test_handoff_does_not_fall_back_to_old_success_after_new_failure(context_design):
    state, path = context_design
    producer, consumer = handoff_task(state)
    save_handoff(path, producer, created_at=1)
    save_handoff(path, producer, id="new-failed", created_at=2, status="failed", verified=False)
    package = build_context(state, consumer, path)
    assert package.blockers
    assert "Implemented counter; tests pass" not in package.payload()


def test_missing_context_prevents_worker_start_and_releases_project_lock(
    context_design, monkeypatch
):
    from codex_orchestrator import execution
    from codex_orchestrator.stage_review import review_stage

    state, path = context_design
    task = state.revisions[-1].design.tasks[0]
    file_context(task, "missing-input.py")
    state.phase, state.approved_revision = "approved", 1
    state.save(path)
    review_stage(state, path, task.stage_id, "", approved=True)
    monkeypatch.setattr(execution, "find_codex", lambda: "fake-codex")
    monkeypatch.setattr(
        execution.threading.Thread, "start", lambda self: pytest.fail("Worker started")
    )
    with pytest.raises(ValueError, match="Context is incomplete"):
        execution.start_run(state, path, task, {})
    assert not execution.is_busy(state.project)


def test_unrelated_outputs_and_undeclared_recipients_are_excluded(context_design):
    state, path = context_design
    producer, consumer = handoff_task(state)
    save_handoff(path, producer)
    consumer.depends_on = []
    package = build_context(state, consumer, path)
    assert package.blockers
    assert "Implemented counter; tests pass" not in package.payload()
    consumer.depends_on = [producer.id]
    other = state.revisions[-1].design.agents[0].model_copy(update={"id": "reviewer"})
    state.revisions[-1].design.agents.append(other)
    state.revisions[-1].agent_models[other.id] = None
    consumer.agent_id = other.id
    package = build_context(state, consumer, path)
    assert package.blockers
    assert "Implemented counter; tests pass" not in package.payload()


def test_nested_instructions_and_explicit_contract_references_are_scoped(context_design):
    from codex_orchestrator.design_models import Component, Interface

    state, path = context_design
    task = state.revisions[-1].design.tasks[0]
    task.owned_paths = ["src/counter.py"]
    nested = path.parent / "src"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("Use local test conventions")
    state.revisions[-1].design.components.append(
        Component(id="shared", name="Contract", responsibility="Shared API", paths=["contract.py"])
    )
    state.revisions[-1].design.interfaces.append(
        Interface(
            id="shared-api",
            source_component="shared",
            target_component=None,
            inputs=["Input"],
            outputs=["Output"],
            errors=[],
        )
    )
    task.context.append(
        ContextReference(
            kind="interface",
            reference="shared-api",
            required=True,
            reason="Use shared contract",
            missing_action="Define it first",
        )
    )
    package = build_context(state, task)
    assert any(r.content == "Use local test conventions" for r in package.resources)
    assert any(c["id"] == "shared" for c in package.metadata["components"])
    assert any(i["id"] == "shared-api" for i in package.metadata["interfaces"])


def test_preview_builds_on_click_and_can_refresh_without_inference(context_design, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from codex_orchestrator import context_builder, execution, web

    state, path = context_design
    (path.parent / "counter.py").write_text("print('preview')")
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(path.parent))
    original = context_builder.read_file
    reads = []

    def read(*args):
        reads.append(args[1].reference)
        return original(*args)

    monkeypatch.setattr(context_builder, "read_file", read)
    monkeypatch.setattr(execution, "start_run", lambda *a: pytest.fail("Unexpected execution"))
    monkeypatch.setattr(
        "codex_orchestrator.dashboard.codex_response",
        lambda *a, **kw: pytest.fail("Unexpected inference"),
    )
    app = AppTest.from_file(web.__file__).run()
    next(b for b in app.button if b.label == "Open dashboard").click().run()
    next(b for b in app.button if b.label == "Open agent workspace").click().run()
    assert not app.exception and reads == []
    next(b for b in app.button if b.label == "Build context preview").click().run()
    assert not app.exception and reads == ["counter.py"]
    assert any(c.value == "print('preview')" for c in app.code)
    app.run()
    assert not app.exception and len(reads) == 1
    (path.parent / "counter.py").write_text("print('updated')")
    next(b for b in app.button if b.label == "Build context preview").click().run()
    assert not app.exception and len(reads) == 2
    assert any(c.value == "print('updated')" for c in app.code)
    next(n for n in app.number_input if n.label == "Package budget (KiB)").set_value(192)
    next(b for b in app.button if b.label == "Save context budgets").click().run()
    assert not app.exception
    from codex_orchestrator.design_models import DesignSession

    assert DesignSession.load(path).context_limits.max_package_bytes == 192 * 1024
