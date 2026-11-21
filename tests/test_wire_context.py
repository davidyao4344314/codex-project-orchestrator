import copy
import json

import pytest
from test_context_builder import context_design as context_design
from test_context_builder import file_context
from test_persistent_workers import wait
from test_worker_execution import launch

from codex_orchestrator import execution, session_manager
from codex_orchestrator.context_builder import build_context
from codex_orchestrator.context_models import ContextPackage
from codex_orchestrator.run_store import run_directory
from codex_orchestrator.worker_profiles import worker_spec
from codex_orchestrator.worker_runtime import compact_worker_prompt


def payload(prompt):
    return json.loads(prompt.split("\n", 1)[1])


def resource_in(prompt, filename):
    return next(
        r for r in payload(prompt)["assignment_context"]["resources"] if r["reference"] == filename
    )


def test_compact_payload_preserves_required_contract_and_keeps_local_snapshot(context_design):
    state, path = context_design
    task = state.revisions[-1].design.tasks[0]
    (path.parent / "counter.py").write_text("important code\n" * 200)
    file_context(task, "counter.py")
    package = build_context(state, task, path)
    spec = worker_spec(package)
    original = package.payload()
    prompt, baseline, reused = compact_worker_prompt(spec, package, [])
    data = payload(prompt)
    assert package.payload() == original
    assert data["worker_spec"] == spec.model_dump()
    assert (
        data["assignment_context"]["metadata"]["requirements"] == package.metadata["requirements"]
    )
    assert data["assignment_context"]["metadata"]["assignment"]["owned_paths"] == task.owned_paths
    assert "automated_checks" not in data["assignment_context"]["metadata"]["assignment"]
    assert resource_in(prompt, "counter.py")["content"] == "important code\n" * 200
    assert len(prompt.encode()) < baseline and reused == 0


@pytest.mark.parametrize("change", [False, True])
def test_real_worker_resumes_only_unchanged_selected_file(
    approved_session, technical_payload, monkeypatch, change
):
    root = approved_session[0].parent
    text = "shared contract line\n" * 150
    (root / "contract.py").write_text(text)
    payload_data = copy.deepcopy(technical_payload)
    first = payload_data["tasks"][0]
    first["context"].append(
        {
            "kind": "project_file",
            "reference": "contract.py",
            "required": True,
            "reason": "Required service contract",
            "missing_action": "Restore the contract",
        }
    )
    second = copy.deepcopy(first)
    second.update(id="refine", depends_on=["implement"], owned_paths=["refined.py"])
    payload_data["tasks"].append(second)
    state, path, first_run, _, commands = launch(
        approved_session, payload_data, monkeypatch, "general"
    )
    assert first_run.status == "completed"
    execution.verify_run(path, first_run.id)
    assert resource_in(first_run.prompt, "contract.py")["content"] == text
    if change:
        (root / "contract.py").write_text("new contract\n" * 150)
    result = wait(
        path,
        state.project,
        execution.start_run(state, path, state.revisions[-1].design.tasks[1]),
    )
    assert result.status == "completed", result.error
    assert commands[1][-3:] == ["resume", first_run.thread_id, "-"]
    wire = resource_in(result.prompt, "contract.py")
    full = ContextPackage.load(run_directory(path) / result.id / "context.json")
    original = next(r for r in full.resources if r.reference == "contract.py")
    if change:
        assert wire["content"] == "new contract\n" * 150
        assert result.reused_context_files == 0
    else:
        assert wire["content_mode"] == "unchanged_reference" and "content" not in wire
        assert wire["sha256"] == original.sha256 and original.content == text
        assert result.reused_context_files == 1
    assert full.digest() == result.context_digest
    receipts = session_manager.read_turns(path)
    assert receipts[-1].full_prompt_bytes > receipts[-1].prompt_bytes
    assert receipts[-1].reused_context_files == result.reused_context_files
    execution.verify_run(path, result.id)


def test_final_prompt_budget_removes_optional_previews_before_required_inputs(context_design):
    state, path = context_design
    task = state.revisions[-1].design.tasks[0]
    (path.parent / "counter.py").write_text("optional source preview\n" * 150)
    (path.parent / "contract.py").write_text("required input\n" * 30)
    file_context(task, "contract.py")
    package = build_context(state, task, path)
    spec = worker_spec(package)
    larger, _, _ = compact_worker_prompt(spec, package, [])
    package.limits.max_package_bytes = len(larger.encode()) - 1000
    spec = worker_spec(package)
    prompt, _, _ = compact_worker_prompt(spec, package, [])
    assert len(prompt.encode()) <= package.limits.max_package_bytes
    assert resource_in(prompt, "counter.py")["content_mode"] == "optional_reference"
    assert resource_in(prompt, "contract.py")["content"] == "required input\n" * 30
    package.limits.max_package_bytes = 1024
    with pytest.raises(ValueError, match="Actual worker prompt exceeds"):
        compact_worker_prompt(worker_spec(package), package, [])
