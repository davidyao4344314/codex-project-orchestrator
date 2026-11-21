import threading

import pytest
from test_context_builder import context_design as context_design
from test_context_builder import file_context, handoff_task, save_handoff
from test_worker_execution import launch

from codex_orchestrator import context_builder, execution
from codex_orchestrator.codex import CodexError
from codex_orchestrator.design_models import ContextReference
from codex_orchestrator.design_validation import validate_design
from codex_orchestrator.run_store import Run, run_directory
from codex_orchestrator.worker_models import WorkerResult


def selected_file(producer, name):
    return ContextReference(
        kind="upstream_file",
        reference=f"{producer.id}:{name}",
        required=True,
        reason="Only the public interface is needed.",
        missing_action="Verify the producer.",
    )


def test_select_only_needed_upstream_files_and_keep_provenance(context_design):
    state, path = context_design
    producer, consumer = handoff_task(state)
    producer.owned_paths.append("other.py")
    (path.parent / "counter.py").write_text("public interface")
    (path.parent / "other.py").write_text("unnecessary upstream implementation")
    run, _ = save_handoff(path, producer)
    consumer.context[0].kind = "task_summary"
    summary_only = context_builder.build_context(state, consumer, path)
    assert not summary_only.blockers
    assert "public interface" not in summary_only.payload()
    consumer.context.append(selected_file(producer, "counter.py"))
    file_context(consumer, "counter.py")  # deduplicate overlapping explicit reference
    validate_design(state.revisions[-1].design, state.source.brief, path.parent)
    selected = context_builder.build_context(state, consumer, path)
    assert not selected.blockers
    files = [r for r in selected.resources if r.reference == "counter.py"]
    assert len(files) == 1 and files[0].source_run_id == run.id
    assert files[0].content == "public interface"
    assert "unnecessary upstream implementation" not in selected.payload()


def test_unauthorized_upstream_file_never_enters_package(context_design):
    state, path = context_design
    producer, consumer = handoff_task(state)
    (path.parent / "other.py").write_text("unrelated content")
    save_handoff(path, producer)
    consumer.context[0].kind = "task_summary"
    consumer.context.append(selected_file(producer, "other.py"))
    with pytest.raises(CodexError, match="not owned"):
        validate_design(state.revisions[-1].design, state.source.brief, path.parent)
    package = context_builder.build_context(state, consumer, path)
    assert package.blockers and "unrelated content" not in package.payload()


def test_generated_agent_history_is_not_an_ordinary_project_input(context_design):
    state, path = context_design
    file = run_directory(path) / "other" / "events.jsonl"
    file.parent.mkdir(parents=True)
    file.write_text("other agent transcript")
    task = state.revisions[-1].design.tasks[0]
    file_context(task, str(file.relative_to(path.parent)))
    package = context_builder.build_context(state, task)
    assert package.blockers and "other agent transcript" not in package.payload()


def test_structured_handoff_is_bound_to_verified_report(
    approved_session, technical_payload, monkeypatch
):
    state, path, run, folder, _ = launch(
        approved_session, technical_payload, monkeypatch, "general"
    )
    execution.verify_run(path, run.id)
    producer, consumer = handoff_task(state)
    consumer.context[0].kind = "task_summary"
    saved = Run.load(folder / "run.json")
    saved.handoff_summary = "Untrusted altered cached summary"
    saved.save(folder / "run.json")
    package = context_builder.build_context(state, consumer, path)
    assert not package.blockers
    assert "Worker finished assigned scope" in package.payload()
    assert "Untrusted altered cached summary" not in package.payload()
    report = WorkerResult.load(folder / "worker_result.json")
    report.handoff = "Tampered report"
    report.save(folder / "worker_result.json")
    assert context_builder.build_context(state, consumer, path).blockers


@pytest.mark.parametrize("change", ["file", "prompt", "context"])
def test_changed_context_is_blocked_before_codex_starts(
    approved_session, technical_payload, monkeypatch, change
):
    root = approved_session[0].parent
    input_file = root / "contract.py"
    input_file.write_text("original contract")
    technical_payload["tasks"][0]["context"].append(
        ContextReference(
            kind="project_file",
            reference="contract.py",
            required=True,
            reason="Required contract",
            missing_action="Restore the contract.",
        ).model_dump()
    )
    original = threading.Thread.start

    def start(thread):
        if thread._target == execution._worker:
            folder = thread._args[2]
            if change == "file":
                input_file.write_text("changed contract")
            elif change == "prompt":
                run = Run.load(folder / "run.json")
                run.prompt += " changed"
                run.save(folder / "run.json")
            else:
                package = context_builder.ContextPackage.load(folder / "context.json")
                package.metadata["project_goal"] = "Changed plan"
                package.save(folder / "context.json")
        return original(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    state, _, run, _, commands = launch(approved_session, technical_payload, monkeypatch, "general")
    assert run.status == "failed" and not commands
    assert "changed" in run.error.lower()
    assert not execution.is_busy(state.project)
