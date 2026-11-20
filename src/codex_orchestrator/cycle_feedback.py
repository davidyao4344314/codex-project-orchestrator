"""Targeted feedback within an approved assignment and explicit goal acceptance."""

from pathlib import Path

from codex_orchestrator import git_service, stage_branches
from codex_orchestrator.context_files import sensitive_text
from codex_orchestrator.coordination_store import design_fingerprint, stage_accepted
from codex_orchestrator.cycle_manager import active_cycle, require_active
from codex_orchestrator.cycle_store import FeedbackRound, find_history
from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.design_validation import ancestors, project_path, task_graph
from codex_orchestrator.run_store import latest_runs, run_directory, runs
from codex_orchestrator.worker_models import WorkerSpec


def current_rounds(state, path):
    found = find_history(Path(state.source_file))
    if not found:
        return []
    name = Path(state.source_file).name
    cycle = next((c for c in found[1].cycles if name in c.session_names()), None)
    if cycle is None:
        return []
    return [
        r
        for r in cycle.feedback_rounds
        if r.revision == len(state.revisions)
        and r.design_digest == design_fingerprint(state)
        and (r.session_name is None or r.session_name == name)
    ]


def task_feedback(state, path, task_id):
    return next(
        (
            r
            for r in reversed(current_rounds(state, path))
            if r.kind == "improve" and r.task_id == task_id
        ),
        None,
    )


def needs_retry(state, path, task_id, run):
    feedback = task_feedback(state, path, task_id)
    return feedback is not None and (run is None or run.feedback_id != feedback.id)


def pending(state, path):
    latest = latest_runs(path, len(state.revisions))
    return [
        t.id
        for t in state.revisions[-1].design.tasks
        if task_feedback(state, path, t.id)
        and (
            needs_retry(state, path, t.id, latest.get(t.id))
            or latest[t.id].status != "completed"
            or not latest[t.id].verified
        )
    ]


def acceptable_note(note):
    if not note.strip() or len(note) > 300 or sensitive_text(note):
        raise ValueError("Enter feedback of 1–300 characters without credentials.")
    return note.strip()


def _state(source):
    path = source.with_name(source.stem + ".design.json")
    state = DesignSession.load(path)
    check_source(state)
    require_active(source)
    if Path(state.source_file).resolve() != source.resolve() or state.phase != "approved":
        raise ValueError("Use the approved design for the current cycle.")
    return state, path


def _unpublished(state, path, stage_id):
    stage_branches.require_branch(state, path, stage_id)
    receipt = git_service.publication_path(path)
    if receipt.exists():
        saved = git_service.GitPublication.load(receipt)
        if saved.stage_id == stage_id and saved.design_fingerprint == design_fingerprint(state):
            raise ValueError(
                "This stage already has a commit receipt. Do not rewrite reviewed work."
            )


def improve(source: Path, task_id: str, note: str):
    note = acceptable_note(note)
    _, history, _ = active_cycle(source)
    with git_service.writer(Path(history.project)):
        file, history, cycle = active_cycle(source)
        state, path = _state(source)
        design = state.revisions[-1].design
        task = next((t for t in design.tasks if t.id == task_id), None)
        if task is None or not task.owned_paths:
            raise ValueError("Choose an existing implementation task with exact owned files.")
        latest = latest_runs(path, len(state.revisions))
        old = latest.get(task_id)
        if not old or old.status not in {"completed", "failed", "blocked", "stopped"}:
            raise ValueError("Test or run this assignment before recording improvement feedback.")
        if any(r.status in {"starting", "running"} for r in runs(path)):
            raise ValueError("Wait for unfinished workers before recording feedback.")
        settings, revision = stage_branches.current(state, path)
        if (
            settings
            and task.stage_id in revision.stages
            and revision.stages[task.stage_id].merged_commit
        ):
            from codex_orchestrator.correction_delivery import create_correction

            return create_correction(source, state, path, task, old, note, file, history, cycle)
        _unpublished(state, path, task.stage_id)
        # Do not invalidate downstream checks implicitly. Existing dependent runs need
        # a wider approved scope, even if the target worker owns different files.
        predecessors = ancestors(task_graph(design))
        root = Path(state.project)
        owned = {project_path(root, name) for name in task.owned_paths}
        for other in design.tasks:
            previous = latest.get(other.id)
            if other.id == task_id or not previous:
                continue
            audit = set(other.owned_paths)
            spec = run_directory(path) / previous.id / "worker_spec.json"
            if spec.exists():
                audit.update(WorkerSpec.load(spec).audit_paths)
            if task_id in predecessors[other.id] or owned & {
                project_path(root, name) for name in audit
            }:
                raise ValueError(
                    f"Feedback affects existing task {other.id}; approve a wider plan first."
                )
        record = FeedbackRound(
            kind="improve",
            revision=len(state.revisions),
            design_digest=design_fingerprint(state),
            stage_id=task.stage_id,
            note=note,
            task_id=task.id,
            agent_id=task.agent_id,
            workstream=str(task.workstream),
            owned_paths=task.owned_paths,
            prior_run_id=old.id,
            session_name=source.name,
        )
        cycle.feedback_enabled = True
        cycle.feedback_rounds.append(record)
        history.save(file)
        return record


def final_stage(state, path, stage_id):
    settings, revision = stage_branches.current(state, path)
    if settings is None:
        return False
    return all(
        s.id == stage_id or (s.id in revision.stages and revision.stages[s.id].merged_commit)
        for s in state.revisions[-1].design.stages
    )


def accept_goal(source: Path, stage_id: str, note: str):
    note = acceptable_note(note)
    _, history, _ = active_cycle(source)
    with git_service.writer(Path(history.project)):
        file, history, cycle = active_cycle(source)
        state, path = _state(source)
        _unpublished(state, path, stage_id)
        if not final_stage(state, path, stage_id) or not stage_accepted(state, path, stage_id):
            raise ValueError(
                "Accept the final stage tests and record all other stage merges first."
            )
        settings, revision = stage_branches.current(state, path)
        project = Path(state.project)
        for other in state.revisions[-1].design.stages:
            if other.id == stage_id:
                continue
            merged = revision.stages[other.id]
            if (
                not stage_accepted(state, path, other.id)
                or merged.evidence_digest != git_service.evidence_digest(state, path, other.id)
                or not stage_branches.ancestor(
                    project, merged.merged_commit, f"refs/heads/{settings.base}"
                )
            ):
                raise ValueError(f"Prerequisite merge evidence is stale for stage {other.id}.")
        if pending(state, path):
            raise ValueError("Run the outstanding feedback corrections first.")
        # Reuse publication validation, including current worker outputs.
        git_service._preview(path, stage_id)
        record = FeedbackRound(
            kind="goal_achieved",
            revision=len(state.revisions),
            design_digest=design_fingerprint(state),
            stage_id=stage_id,
            note=note,
            evidence_digest=git_service.evidence_digest(state, path, stage_id),
            session_name=source.name,
        )
        cycle.feedback_enabled = True
        cycle.feedback_rounds.append(record)
        history.save(file)
        return record


def decision(state, path, stage_id):
    latest = next(
        (r for r in reversed(current_rounds(state, path)) if r.stage_id == stage_id), None
    )
    return latest if latest and latest.kind == "goal_achieved" else None


def commit_message(state, path, stage_id, message):
    found = find_history(Path(state.source_file))
    if not found:
        return message, False
    cycle = found[1].cycles[-1]
    if not cycle.feedback_enabled:
        return message, False
    if stage_branches.load(path) is None:
        raise ValueError("Enable stage branches before publishing feedback-enabled cycles.")
    if pending(state, path):
        raise ValueError("Complete targeted feedback before committing.")
    record = decision(state, path, stage_id)
    if final_stage(state, path, stage_id):
        if not record or record.evidence_digest != git_service.evidence_digest(
            state, path, stage_id
        ):
            raise ValueError("Record fresh goal-achieved feedback before the final stage commit.")
        return (
            f"{message}\n\nCycle {cycle.number:03d}: Goal achieved\n"
            f"Feedback: {record.note}\nAccepted at: {record.recorded_at.isoformat()}\n"
            f"Acceptance: {record.id}",
            True,
        )
    return message, False
