import json

import pytest
from test_git_service import accepted as accepted
from test_persistent_workers import wait

from codex_orchestrator import coordinator, execution, review_agent
from codex_orchestrator import git_service as git
from codex_orchestrator.coordination_store import checkpoint_path, load_coordination
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.design_validation import assign_stage_checks
from codex_orchestrator.run_store import run_directory
from codex_orchestrator.stage_verification import check_stage_verification


def test_review_payload_retains_untracked_source_and_shared_contracts(accepted):
    _, path = accepted
    data = json.loads(review_agent.review_payload(path, git.preview(path, "core")))
    assert data["human_test_observation"] == "Reviewed and tested"
    context = data["assignments"][0]["context"]
    assert context["requirements"] == ["R1"]
    assert data["requirements"]["requirements"] == ["Count recursively from 0 to 5"]
    assert data["shared_context"]["agents"][context["agent"]]["role"]
    assert data["files"]["counter.py"]["text"] == "print(5)\n"
    assert "remote" not in data
    assert data["assignments"][0]["commands"][0]["exit_code"] == 0


def test_changed_manual_evidence_changes_review_identity(accepted):
    _, path = accepted
    before = git.preview(path, "core")
    state = DesignSession.load(path)
    coordination = load_coordination(state, path)
    coordination.accepted_stages["core"].evidence = "Tested again"
    coordination.save(checkpoint_path(state, path))
    after = git.preview(path, "core")
    assert before.fingerprint != after.fingerprint


def test_combined_check_requires_matching_worker_evidence(accepted):
    _, path = accepted
    state = DesignSession.load(path)
    assign_stage_checks(state.revisions[-1].design)
    with pytest.raises(ValueError, match="Missing passing evidence"):
        check_stage_verification(state, path, "core")


def test_review_uses_exact_tracked_diff_hash_and_bounded_command_evidence(accepted):
    root, path = accepted
    git.git(root, "add", "counter.py")
    git.git(root, "commit", "-m", "Initial tracked counter")
    script = root / "fake_worker.py"
    script.write_text(script.read_text().replace("print(5)", "print(6)"))
    state = DesignSession.load(path)
    run = wait(path, root, execution.start_run(state, path, state.revisions[-1].design.tasks[0]))
    assert run.status == "completed", run.error
    execution.verify_run(path, run.id)
    coordinator.accept_stage(path, "core", "Observed the corrected counter")
    folder = run_directory(path) / run.id
    evidence = git.review_evidence(folder)
    evidence.commands[0].output = "x" * 2500
    evidence.save(folder / "worker_evidence.json")
    data = json.loads(review_agent.review_payload(path, git.preview(path, "core")))
    assert "-print(5)" in data["diff"] and "+print(6)" in data["diff"]
    file = data["files"]["counter.py"]
    assert file["sha256"] and file["content_mode"] == "read_current" and "text" not in file
    observed = data["assignments"][0]["commands"][0]
    assert observed["command"] == evidence.commands[0].command and observed["exit_code"] == 0
    assert observed["output_truncated"] and len(observed["output"].encode()) == 1024
