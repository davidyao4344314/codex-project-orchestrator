"""Reconcile a false-negative command match locally, without replaying commands."""

import json
import time
from pathlib import Path

from codex_orchestrator import cycle_feedback, git_service, stage_branches, worker_runtime
from codex_orchestrator.context_files import sensitive_text
from codex_orchestrator.recovery_brief import safe_folder
from codex_orchestrator.run_store import Run, latest_runs
from codex_orchestrator.session import SessionData
from codex_orchestrator.session_manager import TurnEvents
from codex_orchestrator.worker_audit import WorkspaceSnapshot, workspace_changes
from codex_orchestrator.worker_models import WorkerEvidence, artifact_digest
from codex_orchestrator.worker_results import (
    handoff_text,
    load_result,
    observe_command,
    reconcile_result,
)


class Reconciliation(SessionData):
    timestamp: float
    original_run: Run
    original_evidence: WorkerEvidence
    reconciled_evidence_digest: str


def recorded_commands(folder, run):
    turn = TurnEvents()
    commands = []
    with (folder / "events.jsonl").open("rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("Saved events exceed the reconciliation limit.")
    for line in data.splitlines():
        event = json.loads(line)
        if not isinstance(event, dict):
            continue
        turn.observe(event)
        if observed := observe_command(event):
            commands.append(observed)
            commands = commands[-200:]
    if (
        not run.thread_id
        or turn.thread != run.thread_id
        or not turn.matches(run.resume_thread_id)
        or not turn.completed
        or turn.failed
        or turn.usage != run.token_usage
    ):
        raise ValueError("Saved events do not establish a completed matching Codex turn.")
    return commands


def reconcile_blocked_run(path: Path, run_id: str) -> Run:
    state, project = stage_branches.state_for(path)
    with git_service.writer(project):
        state.require_current(path)
        folder = safe_folder(path, run_id)
        run = Run.load(folder / "run.json")
        latest = stage_branches.valid_runs(state, path, latest_runs(path, len(state.revisions)))
        task = next((t for t in state.revisions[-1].design.tasks if t.id == run.task_id), None)
        if (
            run.id != run_id
            or run.status != "blocked"
            or run.verified
            or task is None
            or latest.get(run.task_id) != run
            or cycle_feedback.needs_retry(state, path, run.task_id, run)
            or run.worker_contract_version != 1
            or not all(
                (
                    run.worker_spec_digest,
                    run.worker_result_digest,
                    run.workspace_before_digest,
                    run.workspace_after_digest,
                    run.context_digest,
                    run.prompt_digest,
                )
            )
        ):
            raise ValueError("Only the latest intact blocked worker run can be reconciled.")
        stage_branches.require_branch(state, path, task.stage_id)
        spec, after = worker_runtime.verified_artifacts(run, folder)
        before = WorkspaceSnapshot.load(folder / "workspace_before.json")
        if artifact_digest(before) != run.workspace_before_digest:
            raise ValueError("Original workspace snapshot changed.")
        report = load_result(folder / "worker_result.json")
        original = WorkerEvidence.load(folder / "worker_evidence.json")
        allowed = {
            f"No matching command/exit-code evidence for check {check.check_id}."
            for check in report.checks
        }
        if (
            report.outcome != "completed"
            or not original.violations
            or not set(original.violations) <= allowed
            or run.error != "Worker blocked: " + "; ".join(original.violations)
            or sensitive_text(handoff_text(report))
        ):
            raise ValueError("The worker was blocked for reasons other than command matching.")
        evidence = workspace_changes(before, after, spec.owned_paths)
        evidence.commands = recorded_commands(folder, run)
        if evidence.commands != original.commands or evidence.changes != original.changes:
            raise ValueError("Saved evidence does not match the original observations.")
        if any(
            snapshot.files[name].marker
            for snapshot in (before, after)
            for name in spec.audit_paths
            if name in snapshot.files
        ):
            raise ValueError("Workspace contains unauditable observations.")
        raw = load_result(folder / "result.raw.json")
        if reconcile_result(spec, raw, evidence) != "completed" or evidence.violations:
            raise ValueError("Saved observations still do not establish successful checks.")
        if artifact_digest(raw) != run.worker_result_digest:
            raise ValueError("Original report changed.")
        worker_runtime.check_worker_verification(run, project, folder)
        Reconciliation(
            timestamp=time.time(),
            original_run=run,
            original_evidence=original,
            reconciled_evidence_digest=artifact_digest(evidence),
        ).save(folder / "evidence_reconciliation.json", new=True)
        evidence.save(folder / "worker_evidence.json")
        run.status = "completed"
        run.error = None
        run.save(folder / "run.json")
        return run
