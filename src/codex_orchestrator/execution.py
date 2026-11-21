"""Bounded persistent Codex execution, with one writer per project."""

import atexit
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

from codex_orchestrator import project_activity
from codex_orchestrator.codex import CodexError, exec_command, find_codex
from codex_orchestrator.context_builder import build_context
from codex_orchestrator.context_models import ContextLimits
from codex_orchestrator.coordination_store import check_stage_prerequisites, load_coordination
from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.design_validation import task_graph, validate_design
from codex_orchestrator.recovery_brief import build_recovery_brief
from codex_orchestrator.run_store import Run, latest_runs, run_directory, runs
from codex_orchestrator.session_manager import TurnEvents, begin_turn
from codex_orchestrator.stage_review import current_review
from codex_orchestrator.worker_audit import snapshot_workspace
from codex_orchestrator.worker_models import WorkerSpec, artifact_digest
from codex_orchestrator.worker_results import observe_command
from codex_orchestrator.worker_runtime import (
    check_worker_verification,
    finalize_worker,
    prepare_worker,
    worker_prompt,
)

RUN_TIMEOUT_SECONDS = 30 * 60
TERMINATE_GRACE_SECONDS = 5

_guard = threading.Lock()
_active = {}
_processes = {}
_workers = {}


def project_lock(project):
    return project_activity.lock_path(project)


def is_busy(project):
    return project_lock(project).exists()


def start_run(state, path, task, context=None):
    from codex_orchestrator.cycle_manager import require_active

    require_active(Path(state.source_file))
    check_source(state)
    state.require_current(path)
    validate_design(state.revisions[-1].design, state.source.brief, Path(state.project))
    if state.phase != "approved":
        raise ValueError("Approve the current technical design before starting implementation.")
    if load_coordination(state, path).paused:
        raise ValueError("Resume the paused execution coordinator before starting a task.")
    check_stage_prerequisites(state, path, task.stage_id)
    review = current_review(state, task.stage_id)
    if not review or not review.approved:
        raise ValueError("Approve this stage's plan before starting implementation.")
    from codex_orchestrator.cycle_feedback import needs_retry
    from codex_orchestrator.stage_branches import require_branch, valid_runs

    previous = {
        task_id
        for task_id, run in valid_runs(state, path, latest_runs(path, len(state.revisions))).items()
        if run.verified and run.status == "completed" and not needs_retry(state, path, task_id, run)
    }
    dependencies = task_graph(state.revisions[-1].design)[task.id]
    if not set(dependencies) <= previous:
        raise ValueError("Verify prerequisite tasks and stages before starting this task.")
    require_branch(state, path, task.stage_id)
    if any(r.status in {"starting", "running"} for r in runs(path)):
        raise ValueError("An active or unfinished run exists. Inspect or recover it first.")
    executable = find_codex()
    if not executable:
        raise CodexError("Run orchestrator setup to install and sign in to Codex.")
    run = Run(
        id=uuid4().hex,
        task_id=task.id,
        agent_id=task.agent_id,
        revision=len(state.revisions),
        prompt="",
        model=state.revisions[-1].agent_models[task.agent_id],
    )
    folder = run_directory(path) / run.id
    lock = project_lock(state.project)
    turn = None
    with _guard:
        try:
            lease = project_activity.create(state.project, "worker", run.id)
        except FileExistsError as exc:
            raise ValueError(
                "A project run is active. Stop it before starting or reassigning work."
            ) from exc
        try:
            # Caller manifests are previews; dispatch rebuilds under the writer lock.
            check_source(state)
            state.require_current(path)
            require_active(Path(state.source_file))
            if any(r.status in {"starting", "running"} for r in runs(path)):
                raise ValueError("An active or unfinished run exists. Inspect or recover it first.")
            require_branch(state, path, task.stage_id)
            from codex_orchestrator.cycle_feedback import pending, task_feedback

            outstanding = pending(state, path)
            if outstanding and task.id not in outstanding:
                raise ValueError("Run the selected feedback task before deploying other agents.")
            feedback = task_feedback(state, path, task.id)
            if feedback and (
                task.agent_id != feedback.agent_id
                or str(task.workstream) != feedback.workstream
                or task.owned_paths != feedback.owned_paths
            ):
                raise ValueError("The feedback assignment changed; approve a new scope first.")
            from codex_orchestrator.stage_branches import load as load_branches

            if load_branches(path) is not None:
                from codex_orchestrator.git_service import git

                run.branch = git(Path(state.project), "symbolic-ref", "--short", "HEAD")
            package = build_context(state, task, path)
            if feedback:
                run.feedback_id = feedback.id
                package.metadata["user_feedback"] = feedback.model_dump(mode="json")
                if package.size_bytes() > package.limits.max_package_bytes:
                    raise ValueError("Feedback context exceeds the task context budget.")
            package.require_ready()
            run.context_digest = package.digest()
            folder.mkdir(parents=True)
            spec = prepare_worker(package, Path(state.project), folder)
            run.worker_contract_version = 1
            run.worker_profile = spec.profile
            run.worker_spec_digest = artifact_digest(spec)
            mode = "verify" if spec.read_only else "write"
            role = f"worker:{task.agent_id}:{task.workstream}:{mode}"
            turn = begin_turn(
                path,
                Path(state.project),
                role,
                run.model,
                {
                    "design_revision": package.design_revision,
                    "design_fingerprint": package.design_fingerprint,
                    "profile": spec.profile,
                    "sandbox": "read-only" if spec.read_only else "workspace-write",
                },
                task_id=task.id,
                stage_id=task.stage_id,
                run_id=run.id,
            )
            run.session_turn_id, run.resume_thread_id = turn.record.id, turn.resume_id
            previous_runs = [
                r.model_dump(include={"id", "status", "model", "revision", "thread_id"})
                for r in runs(path)
                if r.task_id == task.id
            ][-3:]
            recovery = build_recovery_brief(state, task, path, package)
            if recovery:
                run.recovery_from = recovery["previous_run_id"]
            # Do not repeat old command output/summary when the same successful attempt is
            # already in the resumed chat. Current input hashes and test requirements stay.
            if (
                turn.resume_id
                and recovery
                and previous_runs
                and (
                    previous_runs[-1]["thread_id"] == turn.resume_id
                    and previous_runs[-1]["status"] == "completed"
                )
            ):
                recovery.pop("earlier_summary", None)
                recovery.pop("commands_observed", None)
            run.prompt = worker_prompt(spec, package, previous_runs, recovery, run.branch)
            turn.prepare(run.prompt)
            package.save(folder / "context.json")
            run.save(folder / "run.json")
            stop = threading.Event()
            _active[run.id] = stop
            worker = threading.Thread(
                target=_worker,
                args=(executable, state.project, folder, lock, stop, turn),
                daemon=True,
            )
            _workers[run.id] = worker
            worker.start()
        except BaseException:
            try:
                if turn:
                    turn.finish("failed")
            finally:
                project_activity.release(state.project, lease)
                _active.pop(run.id, None)
            raise
    return run


def _worker(executable, project, folder, lock, stop, turn=None):
    run = Run.load(folder / "run.json")
    process = None
    timer = None
    spec, before, commands = None, None, []
    observed_events = turn.events if turn else TurnEvents()
    folder = folder.resolve()
    try:
        if run.branch:
            from codex_orchestrator.git_service import git

            if git(Path(project), "symbolic-ref", "--short", "HEAD") != run.branch:
                raise ValueError("Stage branch changed before worker execution.")
        if run.worker_contract_version == 1:
            spec = WorkerSpec.load(folder / "worker_spec.json")
            if spec.context_digest != run.context_digest or (
                run.worker_spec_digest is not None
                and artifact_digest(spec) != run.worker_spec_digest
            ):
                raise ValueError(
                    "Worker spec differs from its recorded contract or context digest."
                )
            before = snapshot_workspace(project, spec.audit_paths, folder)
            before.save(folder / "workspace_before.json")
            run.workspace_before_digest = artifact_digest(before)
        command = exec_command(
            executable,
            Path(project),
            sandbox="read-only" if spec and spec.read_only else "workspace-write",
            json_events=True,
            no_daemon=True,
            model=run.model,
            resume_id=run.resume_thread_id,
            schema=folder / "result_schema.json" if spec else None,
            response=folder / "result.raw.json" if spec else None,
        )
        with (folder / "stderr.log").open("w") as errors:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=project,
                start_new_session=True,
            )

            run.worker_pid = process.pid
            run.worker_started = project_activity.process_started(process.pid)
            run.save(folder / "run.json")
            _processes[run.id] = process

            def terminate():
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass

            def monitor():
                requested = stop.wait(RUN_TIMEOUT_SECONDS)
                if process.poll() is None:
                    if not requested:
                        run.error = "Worker exceeded its execution time limit."
                    stop.set()
                    terminate()
                    try:
                        process.wait(timeout=TERMINATE_GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

            timer = threading.Thread(target=monitor, daemon=True)
            timer.start()
            process.stdin.write(run.prompt)
            process.stdin.close()
            run.status = "running"
            run.save(folder / "run.json")
            with (folder / "events.jsonl").open("w") as events:
                for line in process.stdout:
                    events.write(line)
                    events.flush()
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    observed_events.observe(event)
                    if spec and (observed := observe_command(event)):
                        commands.append(observed)
                        commands[:] = commands[-200:]
                    if event.get("type") == "thread.started":
                        run.thread_id = observed_events.thread
                        run.save(folder / "run.json")
                        if not observed_events.matches(run.resume_thread_id):
                            run.error = "Codex returned a mismatched session. Retry fresh."
                            terminate()
                            break
                    item = event.get("item", {})
                    if (
                        event.get("type") == "item.completed"
                        and isinstance(item, dict)
                        and item.get("type") == "agent_message"
                        and isinstance(item.get("text"), str)
                    ):
                        raw = item["text"].encode("utf-8")
                        limit = ContextLimits().max_handoff_bytes
                        run.handoff_summary = raw[:limit].decode("utf-8", errors="ignore")
                        run.handoff_truncated = len(raw) > limit
                    if event.get("type") in {"turn.failed", "error"}:
                        run.error = str(event.get("error", event.get("message", "Codex failed")))
            code = process.wait()
            if not observed_events.matches(run.resume_thread_id):
                run.error = "Codex did not confirm the exact requested session. Retry fresh."
            run.status = (
                "stopped"
                if stop.is_set()
                else (
                    "completed"
                    if code == 0 and observed_events.completed and not run.error
                    else "failed"
                )
            )
            if run.status == "failed" and not run.error:
                run.error = f"Codex exited with code {code}; see diagnostics."
            if run.branch and git(Path(project), "symbolic-ref", "--short", "HEAD") != run.branch:
                raise ValueError("Worker changed the stage branch; inspect the repository.")
            if spec and before:
                finalize_worker(run, spec, before, project, folder, commands)
    except Exception as exc:
        run.status, run.error = "failed", str(exc)[:4000]
    finally:
        try:
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=TERMINATE_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        finally:
            try:
                run.token_usage = observed_events.usage
                run.save(folder / "run.json")
                if turn:
                    try:
                        turn.finish(run.status)
                    except (OSError, ValueError):
                        run.session_warning = (
                            "Could not save session metadata; inspect local history."
                        )
                        run.save(folder / "run.json")
            finally:
                stop.set()
                # A checkpoint error must not orphan a lock once the child has exited.
                if process is None or process.poll() is not None:
                    lock.unlink(missing_ok=True)
                with _guard:
                    _active.pop(run.id, None)
                    _processes.pop(run.id, None)
                    _workers.pop(run.id, None)


def stop_run(run):
    with _guard:
        stop = _active.get(run.id)
        if stop is None:
            raise ValueError(
                "This server does not own the run. Check its Codex chat before proceeding."
            )
        stop.set()


def verify_run(path, run_id):
    folder = run_directory(path) / run_id
    run = Run.load(folder / "run.json")
    if run.status != "completed":
        raise ValueError("Only completed runs can be accepted after verification.")
    state = DesignSession.load(path)
    from codex_orchestrator.cycle_feedback import needs_retry

    if needs_retry(state, path, run.task_id, run):
        raise ValueError("Run the targeted correction before accepting verification.")
    check_source(state)
    if (
        run.revision != len(state.revisions)
        or latest_runs(path, run.revision).get(run.task_id).id != run.id
    ):
        raise ValueError("Only the latest run of the current design revision can be verified.")
    if is_busy(state.project):
        raise ValueError("Wait for the active project run to stop before accepting verification.")
    if run.worker_contract_version == 1:
        check_worker_verification(run, Path(state.project), folder)
    run.verified = True
    run.save(folder / "run.json")


def open_chat(thread_id):
    # Validate rather than interpolate arbitrary URLs into an OS launcher.
    from uuid import UUID

    UUID(thread_id)
    url = f"codex://threads/{thread_id}"
    if sys.platform == "darwin":
        subprocess.run(["open", url], check=True)
    else:
        import webbrowser

        webbrowser.open(url)


@atexit.register
def _shutdown():
    for stop in list(_active.values()):
        stop.set()
    workers = list(_workers.values())
    for process in list(_processes.values()):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    for worker in workers:
        if worker.ident is not None:
            worker.join(timeout=8)
