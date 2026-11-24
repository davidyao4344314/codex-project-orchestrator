import subprocess
import sys
import time

import pytest

from codex_orchestrator import execution
from codex_orchestrator.coordinator import stage_schedule
from codex_orchestrator.design import prepare_design
from codex_orchestrator.design_models import DesignRevision, TechnicalDesign
from codex_orchestrator.models import Workstream
from codex_orchestrator.stage_review import review_stage
from codex_orchestrator.worker_models import WorkerEvidence, WorkerResult, WorkerSpec

FAKE_CODEX = """import json, pathlib, shlex, subprocess, sys, uuid
payload = json.loads(sys.stdin.read().split("\\n", 1)[1])
spec = payload["worker_spec"]
mode = MODE
root = pathlib.Path.cwd()
identity = sys.argv[-2] if sys.argv[-3] == "resume" else str(uuid.uuid4())
if mode == "timeout":
    import time
    print(json.dumps({"type":"thread.started",
        "thread_id":identity}), flush=True)
    time.sleep(60)
changes = []
if not spec["read_only"] and mode != "blocked":
    for name in spec["owned_paths"]:
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("print(5)\\n")
        changes.append({"path": name, "description": "Implemented assigned scope"})
if mode == "outside":
    (root / "unowned.py").write_text("out of scope")
check_code = "assert 2 + 2 == 5" if mode == "test_failed" else "assert 2 + 2 == 4"
command = shlex.join([sys.executable, "-c", check_code])
checks = []
print(json.dumps({"type":"thread.started",
    "thread_id":identity}), flush=True)
for i, required in enumerate(spec["checks"]):
    if mode != "blocked":
        result = subprocess.run([sys.executable, "-c", check_code], capture_output=True)
        if mode != "unsupported":
            print(json.dumps({"type":"item.completed","item":{"id":f"check-{i}",
                "type":"command_execution","command":command,"exit_code":result.returncode,
                "aggregated_output":"focused check finished"}}), flush=True)
    checks.append({"check_id":required["id"],"command":None if mode == "blocked" else command,
        "outcome":"not_run" if mode == "blocked" else
            ("failed" if mode == "test_failed" else "passed"),
        "detail":"runtime unavailable" if mode == "blocked" else "focused assertion passed"})
report = {"task_id":spec["task_id"],"agent_id":spec["agent_id"],"profile":spec["profile"],
    "context_digest":spec["context_digest"],
    "outcome":"blocked" if mode == "blocked" else
        ("failed" if mode == "test_failed" else "completed"),
    "summary":"Worker finished assigned scope","changes":changes,"checks":checks,
    "blockers":["Missing runtime"] if mode == "blocked" else [],"human_checks":[],
    "handoff":"Review changes and manual checks"}
raw = "{bad JSON" if mode == "malformed" else json.dumps(report)
output = pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1])
output.write_text(raw)
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":raw}}), flush=True)
print(json.dumps({"type":"turn.completed","usage":{
    "input_tokens":100,"cached_input_tokens":80,"output_tokens":20}}), flush=True)
"""


def launch(approved_session, technical_payload, monkeypatch, area, mode="completed", policy=None):
    source, _ = approved_session
    if policy:
        from codex_orchestrator.session import PlanningSession

        session = PlanningSession.load(source)
        session.model_policy = policy
        session.save(source)
    state, path = prepare_design(source, None, architect_model=None, agent_model=None)
    design = TechnicalDesign.model_validate(technical_payload)
    task = design.tasks[0]
    if area == "verification":
        task.owned_paths = []
    else:
        task.workstream = "general" if area == "general" else Workstream(area)
    from codex_orchestrator.model_allocation import revision_models

    choices = (
        revision_models(state, design)
        if policy
        else {"agent_models": {"developer": "worker-model"}}
    )
    state.revisions.append(DesignRevision(design=design, elapsed_seconds=0, **choices))
    state.phase, state.approved_revision = "approved", 1
    state.save(path)
    review_stage(state, path, "core", "", approved=True)
    subprocess.run(["git", "init", "-q", str(path.parent)], check=True)
    script = path.parent / "fake_worker.py"
    script.write_text(FAKE_CODEX.replace("mode = MODE", f"mode = {mode!r}"))
    real_popen = execution.subprocess.Popen
    commands = []

    def spawn(command, **kwargs):
        if command[0] == "fake-codex":
            commands.append(command)
            return real_popen([sys.executable, str(script), *command[1:]], **kwargs)
        return real_popen(command, **kwargs)

    monkeypatch.setattr(execution, "find_codex", lambda: "fake-codex")
    monkeypatch.setattr(execution.subprocess, "Popen", spawn)
    run = execution.start_run(state, path, task)
    folder = execution.run_directory(path) / run.id
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = execution.Run.load(folder / "run.json")
        if current.status not in {"starting", "running"} and not execution.is_busy(state.project):
            return state, path, current, folder, commands
        time.sleep(0.02)
    execution.stop_run(run)
    pytest.fail("Fake worker did not finish")


@pytest.mark.parametrize("changed", [False, True])
def test_reconcile_legacy_wrapper_block(approved_session, technical_payload, monkeypatch, changed):
    from codex_orchestrator import worker_results
    from codex_orchestrator.evidence_recovery import reconcile_blocked_run

    monkeypatch.setitem(
        globals(),
        "FAKE_CODEX",
        FAKE_CODEX.replace(
            '"command_execution","command":command,',
            '"command_execution","command":"/bin/zsh -lc "+shlex.quote(command),',
        ),
    )
    with monkeypatch.context() as legacy:
        legacy.setattr(
            worker_results, "commands_match", lambda reported, actual: reported == actual
        )
        state, path, run, folder, commands = launch(
            approved_session, technical_payload, legacy, "general"
        )
    assert run.status == "blocked"
    if changed:
        owned = state.revisions[-1].design.tasks[0].owned_paths[0]
        (path.parent / owned).write_text("changed after test\n")
        with pytest.raises(ValueError, match="changed after execution"):
            reconcile_blocked_run(path, run.id)
        assert execution.Run.load(folder / "run.json").status == "blocked"
    else:
        recovered = reconcile_blocked_run(path, run.id)
        assert recovered.status == "completed" and not recovered.verified
        assert recovered.token_usage == run.token_usage
        assert (folder / "evidence_reconciliation.json").exists()
        assert not WorkerEvidence.load(folder / "worker_evidence.json").violations
    assert len(commands) == 1


@pytest.mark.parametrize("legacy_blocked", [False, True])
def test_retry_reuses_existing_audited_files(
    approved_session, technical_payload, monkeypatch, legacy_blocked
):
    from test_persistent_workers import wait

    from codex_orchestrator import worker_runtime
    from codex_orchestrator.evidence_recovery import can_reconcile, reconcile_blocked_run
    from codex_orchestrator.worker_results import reconcile_result

    state, path, first, _, commands = launch(
        approved_session, technical_payload, monkeypatch, "general"
    )
    assert first.status == "completed"
    task = state.revisions[-1].design.tasks[0]
    with monkeypatch.context() as patch:
        if legacy_blocked:
            patch.setattr(
                worker_runtime,
                "reconcile_result",
                lambda spec, report, evidence, **kwargs: reconcile_result(spec, report, evidence),
            )
        retry = wait(path, state.project, execution.start_run(state, path, task))
    assert retry.recovery_from == first.id
    folder = execution.run_directory(path) / retry.id
    if legacy_blocked:
        assert retry.status == "blocked" and can_reconcile(path, retry)
        recovered = reconcile_blocked_run(path, retry.id)
        assert recovered.status == "completed" and not recovered.verified
        assert (folder / "evidence_reconciliation.json").exists()
    else:
        assert retry.status == "completed", retry.error
    assert WorkerResult.load(folder / "worker_result.json").changes == []
    assert WorkerResult.load(folder / "result.raw.json").changes
    assert not WorkerEvidence.load(folder / "worker_evidence.json").violations
    execution.verify_run(path, retry.id, automated=True)
    assert execution.Run.load(folder / "run.json").verified
    assert len(commands) == 2


@pytest.mark.parametrize(
    "area", ["frontend", "backend", "database", "integration", "general", "verification"]
)
def test_all_workers_run_with_selected_model_context_and_evidence(
    approved_session, technical_payload, monkeypatch, area
):
    state, path, run, folder, commands = launch(
        approved_session, technical_payload, monkeypatch, area
    )
    assert run.status == "completed", run.error
    assert run.worker_profile == area and not run.verified
    assert run.worker_spec_digest and run.workspace_after_digest
    spec = WorkerSpec.load(folder / "worker_spec.json")
    result = WorkerResult.load(folder / "worker_result.json")
    evidence = WorkerEvidence.load(folder / "worker_evidence.json")
    assert result.profile == area and spec.context_digest == run.context_digest
    assert result.human_checks == state.revisions[-1].design.tasks[0].human_checks
    assert evidence.check_matches and not evidence.violations
    assert commands[0][commands[0].index("--model") + 1] == "worker-model"
    assert commands[0][commands[0].index("--sandbox") + 1] == (
        "read-only" if area == "verification" else "workspace-write"
    )
    assert len(evidence.changes) == (0 if area == "verification" else 1)
    if area != "verification":
        assert "print(5)" in evidence.changes[0].diff
    execution.verify_run(path, run.id)
    assert execution.Run.load(folder / "run.json").verified


@pytest.mark.parametrize(
    "mode, status",
    [
        ("blocked", "blocked"),
        ("unsupported", "blocked"),
        ("outside", "blocked"),
        ("malformed", "failed"),
        ("test_failed", "failed"),
    ],
)
def test_invalid_or_blocked_work_cannot_advance_stage(
    approved_session, technical_payload, monkeypatch, mode, status
):
    state, path, run, folder, _ = launch(
        approved_session, technical_payload, monkeypatch, "backend", mode
    )
    assert run.status == status, run.error
    assert not run.verified and run.error
    assert stage_schedule(path, "core").status == "blocked"
    with pytest.raises(ValueError, match="completed"):
        execution.verify_run(path, run.id)
    assert not execution.is_busy(state.project)
    if mode == "outside":
        evidence = WorkerEvidence.load(folder / "worker_evidence.json")
        assert any("unowned.py" in error for error in evidence.violations)


def test_verification_rejects_code_modified_after_worker(
    approved_session, technical_payload, monkeypatch
):
    state, path, run, folder, _ = launch(
        approved_session, technical_payload, monkeypatch, "general"
    )
    assert run.status == "completed", run.error
    (path.parent / "counter.py").write_text("print('modified after worker')")
    with pytest.raises(ValueError, match="changed after execution"):
        execution.verify_run(path, run.id)


def test_readonly_worker_verification_binds_declared_input_files(
    approved_session, technical_payload, monkeypatch
):
    source, _ = approved_session
    (source.parent / "upstream.py").write_text("value = 5")
    technical_payload["tasks"][0]["context"].append(
        dict(
            kind="project_file",
            reference="upstream.py",
            required=True,
            reason="Verify input",
            missing_action="Restore implementation",
        )
    )
    state, path, run, folder, _ = launch(
        approved_session, technical_payload, monkeypatch, "verification"
    )
    assert run.status == "completed", run.error
    (path.parent / "upstream.py").write_text("value = 6")
    with pytest.raises(ValueError, match="changed after execution"):
        execution.verify_run(path, run.id)


def test_worker_timeout_stops_and_releases_project(
    approved_session, technical_payload, monkeypatch
):
    monkeypatch.setattr(execution, "RUN_TIMEOUT_SECONDS", 0.1)
    state, path, run, folder, _ = launch(
        approved_session, technical_payload, monkeypatch, "backend", "timeout"
    )
    assert run.status == "stopped" and "time limit" in run.error
    assert not execution.is_busy(state.project)


def test_worker_report_ui_exposes_evidence_and_manual_acceptance(
    approved_session, technical_payload, monkeypatch
):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from codex_orchestrator import web

    state, path, run, folder, _ = launch(
        approved_session, technical_payload, monkeypatch, "backend"
    )
    assert run.status == "completed", run.error
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(path.parent))
    app = AppTest.from_file(web.__file__, default_timeout=10).run()
    next(b for b in app.button if b.label == "Open dashboard").click().run()
    next(b for b in app.button if b.label == "Open agent workspace").click().run()
    assert not app.exception
    assert any("Worker report" in m.value for m in app.markdown)
    next(b for b in app.button if b.label == "Load worker evidence").click().run()
    assert not app.exception
    assert any("print(5)" in c.value for c in app.code)
    next(c for c in app.checkbox if c.label.startswith("I reviewed the output")).check().run()
    next(b for b in app.button if b.label == "Accept verification").click().run()
    assert not app.exception
    assert execution.Run.load(folder / "run.json").verified
