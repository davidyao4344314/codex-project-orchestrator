import hashlib
import os
import threading

import pytest

from codex_orchestrator import execution, worker_audit, worker_runtime
from codex_orchestrator.run_store import Run
from codex_orchestrator.worker_audit import FileState, WorkspaceSnapshot
from codex_orchestrator.worker_models import (
    CommandEvidence,
    ReportedChange,
    ReportedCheck,
    RequiredCheck,
    WorkerEvidence,
    WorkerResult,
    WorkerSpec,
)
from codex_orchestrator.worker_results import reconcile_result


def digest(value):
    return hashlib.sha256(value.model_dump_json().encode()).hexdigest()


def contract():
    return WorkerSpec(
        task_id="task",
        agent_id="agent",
        role="Developer",
        profile="general",
        model=None,
        context_digest="context",
        owned_paths=["app.py"],
        audit_paths=["app.py"],
        read_only=False,
        instructions="Implement approved work",
        checks=[RequiredCheck(id="C1", description="Check result")],
        acceptance_criteria=["Works"],
        human_checks=[],
    )


def result(spec, command="python -c pass"):
    return WorkerResult(
        task_id=spec.task_id,
        agent_id=spec.agent_id,
        profile=spec.profile,
        context_digest=spec.context_digest,
        outcome="completed",
        summary="Done",
        changes=[],
        checks=[
            ReportedCheck(check_id="C1", command=command, outcome="passed", detail="Command passed")
        ],
        blockers=[],
        human_checks=[],
        handoff="Review result",
    )


@pytest.mark.parametrize(
    "reported_command, observed_command",
    [
        ("sh -c 'exit \"$1\"' test 1", "sh -c 'exit \"$1\"' test 0"),
        ('test "$HOME" = /tmp', "test '$HOME' = /tmp"),
        ("python -c pass", "sh -lc 'python -c pass'"),
    ],
)
def test_shell_semantics_are_not_discarded(reported_command, observed_command):
    spec = contract()
    reported = result(spec, reported_command)
    evidence = WorkerEvidence(
        commands=[CommandEvidence(event_id="actual", exit_code=0, command=observed_command)]
    )
    assert reconcile_result(spec, reported, evidence) == "blocked"
    assert evidence.violations


def test_post_run_unreadable_owned_file_cannot_complete(tmp_path, monkeypatch):
    spec = contract()
    before = WorkspaceSnapshot(
        mode="owned_paths_only",
        head=None,
        files={"app.py": FileState(exists=True, sha256="before")},
    )
    after = before.model_copy(deep=True)
    after.files["app.py"] = FileState(exists=True, marker="special-or-outside")
    monkeypatch.setattr(worker_runtime, "snapshot_workspace", lambda *a, **kw: after)
    report = result(spec)
    report.changes = [ReportedChange(path="app.py", description="Updated")]
    report.save(tmp_path / "result.raw.json")
    run = Run(
        id="run", task_id="task", agent_id="agent", revision=1, prompt="prompt", status="completed"
    )
    worker_runtime.finalize_worker(
        run,
        spec,
        before,
        tmp_path,
        tmp_path,
        [CommandEvidence(event_id="check", command="python -c pass", exit_code=0)],
    )
    assert run.status == "blocked"
    assert WorkerEvidence.load(tmp_path / "worker_evidence.json").violations


def test_audit_nonblocking_open_handles_file_replaced_with_fifo(tmp_path, monkeypatch):
    file = tmp_path / "app.py"
    file.write_text("original")
    actual_open = os.open
    flags_used = []

    def race(path, flags, *args, **kwargs):
        if path == file:
            flags_used.append(flags)
            file.unlink()
            os.mkfifo(file)
        return actual_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(worker_audit.os, "open", race)
    observed = worker_audit._file_state(tmp_path, "app.py", True, 1024)
    assert flags_used and flags_used[0] & os.O_NONBLOCK
    assert observed.marker and observed.sha256 is None


def test_growing_file_does_not_exceed_remaining_hash_budget(tmp_path, monkeypatch):
    file = tmp_path / "app.py"
    file.write_text("ok")
    real_lstat = type(file).lstat
    grew = False

    def old_stat(path, *args, **kwargs):
        nonlocal grew
        info = real_lstat(path, *args, **kwargs)
        if path == file and not grew:
            grew = True
            file.write_text("x" * 100)
        return info

    monkeypatch.setattr(type(file), "lstat", old_stat)
    observed = worker_audit._file_state(tmp_path, "app.py", True, 8)
    assert observed.bytes_hashed <= 8
    assert observed.marker is not None


def test_growth_during_read_is_bounded_and_not_hashed(tmp_path, monkeypatch):
    file = tmp_path / "app.py"
    file.write_text("ok")
    real_fdopen = os.fdopen
    read_limits = []

    class GrowingReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, limit):
            read_limits.append(limit)
            file.write_text("x" * 100)
            return self.stream.read(limit)

    monkeypatch.setattr(
        worker_audit.os, "fdopen", lambda *a, **kw: GrowingReader(real_fdopen(*a, **kw))
    )
    observed = worker_audit._file_state(tmp_path, "app.py", True, 8)
    assert read_limits == [8] and observed.bytes_hashed == 8
    assert observed.marker == "changed-while-reading" and observed.sha256 is None


@pytest.mark.parametrize("tamper", ["spec", "after"])
def test_changed_spec_or_final_snapshot_cannot_bypass_verification(tmp_path, tamper):
    project = tmp_path / "project"
    project.mkdir()
    file = project / "app.py"
    file.write_text("original")
    folder = tmp_path / "run"
    folder.mkdir()
    spec = contract()
    report = result(spec)
    after = worker_audit.snapshot_workspace(project, spec.audit_paths, folder)
    spec.save(folder / "worker_spec.json")
    report.save(folder / "worker_result.json")
    after.save(folder / "workspace_after.json")
    run = Run(
        id="run",
        task_id="task",
        agent_id="agent",
        revision=1,
        prompt="prompt",
        status="completed",
        context_digest=spec.context_digest,
        worker_result_digest=digest(report),
        worker_spec_digest=digest(spec),
        workspace_after_digest=digest(after),
    )
    file.write_text("changed")
    if tamper == "spec":
        spec.audit_paths = []
        spec.save(folder / "worker_spec.json")
    else:
        altered = worker_audit.snapshot_workspace(project, spec.audit_paths, folder)
        altered.save(folder / "workspace_after.json")
    with pytest.raises(ValueError, match="artifacts changed"):
        worker_runtime.check_worker_verification(run, project, folder)


def test_changed_worker_spec_is_rejected_before_process_start(tmp_path, monkeypatch):
    spec = contract()
    run = Run(
        id="run",
        task_id="task",
        agent_id="agent",
        revision=1,
        prompt="prompt",
        worker_contract_version=1,
        context_digest=spec.context_digest,
        worker_spec_digest=digest(spec),
    )
    run.save(tmp_path / "run.json")
    spec.read_only = True
    spec.save(tmp_path / "worker_spec.json")
    lock = tmp_path / "run.lock"
    lock.touch()
    monkeypatch.setattr(execution.subprocess, "Popen", lambda *a, **kw: pytest.fail("Spawned CLI"))
    execution._worker("fake-codex", str(tmp_path), tmp_path, lock, threading.Event())
    saved = Run.load(tmp_path / "run.json")
    assert saved.status == "failed" and "contract" in saved.error
    assert not lock.exists()


def test_older_worker_checkpoints_keep_verification_compatibility(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("original")
    folder = tmp_path / "run"
    spec = contract()
    report = result(spec)
    after = worker_audit.snapshot_workspace(project, spec.audit_paths, folder)
    spec.save(folder / "worker_spec.json")
    report.save(folder / "worker_result.json")
    after.save(folder / "workspace_after.json")
    # The on-disk shape predates the two additive integrity fields.
    run = Run.model_validate(
        dict(
            id="run",
            task_id="task",
            agent_id="agent",
            revision=1,
            prompt="prompt",
            status="completed",
            context_digest=spec.context_digest,
            worker_result_digest=digest(report),
        )
    )
    assert run.worker_spec_digest is None and run.workspace_after_digest is None
    worker_runtime.check_worker_verification(run, project, folder)
