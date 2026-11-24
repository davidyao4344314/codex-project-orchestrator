"""Validate the final stage tree through ordered, individually audited worker attempts."""

from pathlib import Path

from codex_orchestrator.design_validation import ancestors, stage_check_description, task_graph
from codex_orchestrator.run_store import latest_runs, run_directory
from codex_orchestrator.worker_audit import WorkspaceSnapshot, snapshot_workspace
from codex_orchestrator.worker_models import artifact_digest
from codex_orchestrator.worker_runtime import file_signature, verified_artifacts


class StageVerificationError(ValueError):
    def __init__(self, task_id, message):
        super().__init__(message)
        self.task_id = task_id


def check_stage_verification(state, path, stage_id, *, partial=False):
    design = state.revisions[-1].design
    tasks = {task.id: task for task in design.tasks if task.stage_id == stage_id}
    latest = latest_runs(path, len(state.revisions))
    if partial:
        from codex_orchestrator.stage_branches import valid_runs

        latest = valid_runs(state, path, latest)
    predecessors = ancestors(task_graph(design))
    attempts = []
    for task_id in tasks:
        run = latest.get(task_id)
        if partial and (not run or run.status != "completed"):
            continue
        if not run or run.status != "completed" or (not partial and not run.verified):
            raise ValueError(f"Task {task_id} needs accepted verification.")
        if run.agent_branch and not run.agent_commit:
            raise ValueError(f"Task {task_id} has no integrated agent commit.")
        if run.worker_contract_version != 1:
            if partial:
                continue
            raise ValueError(
                "Rerun legacy tasks with structured worker evidence before publishing."
            )
        attempts.append(run)
    if not attempts:
        return

    # The durable after snapshot of each attempt must match the next observer's
    # before snapshot. Only an approved dependent writer may supersede it.
    expected = {}
    observed_gate_checks = set()
    for run in sorted(attempts, key=lambda item: item.created_at):
        folder = run_directory(path) / run.id
        spec, after = verified_artifacts(run, folder)
        from codex_orchestrator.git_service import review_evidence

        evidence = review_evidence(folder)
        observed_gate_checks.update(
            check.description for check in spec.checks if check.id in evidence.check_matches
        )
        before = None
        if run.workspace_before_digest:
            before = WorkspaceSnapshot.load(folder / "workspace_before.json")
            if artifact_digest(before) != run.workspace_before_digest:
                raise ValueError("Worker before snapshot changed; rerun the task.")
        for name in spec.audit_paths:
            end = file_signature(after.files.get(name))
            previous = expected.get(name)
            if previous:
                prior, observer = previous
                if before and file_signature(before.files.get(name)) != prior:
                    raise StageVerificationError(
                        run.task_id, f"Worker input/output changed between tasks: {name}."
                    )
                if end != prior and (
                    before is None
                    or name not in spec.owned_paths
                    or observer not in predecessors[run.task_id]
                ):
                    raise StageVerificationError(
                        run.task_id, f"Unverified or unordered change between tasks: {name}."
                    )
            expected[name] = (end, run.task_id)

    final = snapshot_workspace(Path(state.project), expected, run_directory(path))
    if not partial:
        stage = next(s for s in design.stages if s.id == stage_id)
        for index, check in enumerate(stage.gate.agent_checks, 1):
            description = stage_check_description(stage, index, check)
            if any(description in task.automated_checks for task in tasks.values()):
                if description not in observed_gate_checks:
                    raise ValueError(f"Missing passing evidence for {description}")
    for name, (signature, task_id) in expected.items():
        if file_signature(final.files.get(name)) != signature:
            raise StageVerificationError(
                task_id, f"Worker input/output changed after execution: {name}. Rerun the task."
            )
