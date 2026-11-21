from test_git_service import accepted as accepted
from test_stage_branch_services import project as project
from test_stage_branches import publish, run_task

from codex_orchestrator import cycle_feedback, cycle_manager
from codex_orchestrator import git_service as git
from codex_orchestrator import stage_branches as branches
from codex_orchestrator.context_builder import build_context
from codex_orchestrator.design_models import ContextReference, DesignSession
from codex_orchestrator.run_store import run_directory
from codex_orchestrator.worker_models import WorkerResult


def test_merged_correction_keeps_declared_summary_without_reloading_upstream_source(
    project, monkeypatch
):
    root, path = project
    source = path.with_name("source.json")
    state = DesignSession.load(path)
    frontend = state.revisions[-1].design.tasks[-1]
    frontend.owned_paths = ["frontend.py"]
    frontend.context.append(
        ContextReference(
            kind="task_summary",
            reference="implement",
            required=True,
            reason="Use the verified upstream contract",
            missing_action="Verify the backend.",
        )
    )
    state.save(path)
    branches.enable(path)
    cycle_manager.register(source)
    core_run = None
    for stage in ("core", "delivery"):
        branches.prepare(path, stage)
        run = run_task(root, path, stage)
        if stage == "core":
            core_run = run
        receipt = publish(root, path, stage, monkeypatch)
        git.git(root, "switch", "main")
        git.git(root, "merge", "--ff-only", receipt.branch)
        branches.record_merge(path, stage)
    correction = cycle_feedback.improve(source, "check", "Improve frontend wording")
    new_source = source.parent / correction.session_name
    new_path = new_source.with_suffix(".design.json")
    new_state = DesignSession.load(new_path)
    task = next(t for t in new_state.revisions[-1].design.tasks if t.id == "check")
    package = build_context(new_state, task, new_path)
    assert not package.blockers
    archived = next(r for r in package.resources if r.kind == "task_output")
    assert archived.source_run_id == core_run.id and "Worker finished" in archived.content
    assert not any(r.reference == "counter.py" for r in package.resources)
    report_file = run_directory(path) / core_run.id / "worker_result.json"
    report = WorkerResult.load(report_file)
    report.handoff = "Changed old evidence"
    report.save(report_file)
    assert build_context(new_state, task, new_path).blockers
