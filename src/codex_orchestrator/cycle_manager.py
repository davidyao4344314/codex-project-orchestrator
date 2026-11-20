"""Explicit cycle transitions; existing planning and delivery services stay independent."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from codex_orchestrator import git_service, stage_branches
from codex_orchestrator.context_files import sensitive_text
from codex_orchestrator.coordination_store import stage_accepted
from codex_orchestrator.cycle_store import (
    Cycle,
    ProjectHistory,
    find_history,
    histories,
    history_path,
    now,
)
from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.interactive import create_session
from codex_orchestrator.run_store import runs
from codex_orchestrator.session import PlanningSession


def register(source: Path, *, imported: bool = True) -> ProjectHistory:
    session = PlanningSession.load(source)
    with git_service.writer(Path(session.project)):
        existing = find_history(source)
        if existing:
            return existing[1]
        if any(
            Path(h.project).resolve() == Path(session.project).resolve()
            for _, h in histories(source.parent)
        ):
            raise ValueError("This folder already has a cycle history. Continue that project.")
        goal = session.answers[0].response if session.answers else session.project_name
        cycle = Cycle(
            number=1,
            session_name=source.name,
            goal=goal,
            imported=imported,
            started_at=datetime.fromtimestamp(source.stat().st_mtime, UTC) if imported else now(),
        )
        history = ProjectHistory(project=session.project, name=session.project_name, cycles=[cycle])
        history.save(history_path(source), new=True)
        return history


def create_project(project: Path, goal: str, directory: Path) -> Path:
    project = project.resolve()
    if not project.is_dir():
        raise ValueError("Choose an existing project folder.")
    if not goal.strip() or len(goal) > 4000 or sensitive_text(goal):
        raise ValueError("Enter a goal under 4,000 characters without credentials.")
    with git_service.writer(project):
        if any(Path(h.project).resolve() == project for _, h in histories(directory)):
            raise ValueError("This folder already has a cycle history.")
        path = directory / f"session-{uuid4().hex[:12]}.json"
        create_session(project, project.name, goal.strip(), path)
        ProjectHistory(
            project=str(project),
            name=project.name,
            cycles=[Cycle(number=1, session_name=path.name, goal=goal.strip())],
        ).save(history_path(path), new=True)
        return path


def active_cycle(source: Path):
    found = find_history(source)
    if not found or found[1].cycles[-1].session_name != source.name:
        raise ValueError("Open the current registered cycle first.")
    file, history = found
    return file, history, history.cycles[-1]


def require_active(source: Path):
    found = find_history(source)
    if found and (
        found[1].cycles[-1].session_name != source.name or found[1].cycles[-1].status == "completed"
    ):
        raise ValueError("This cycle is historical. Start or resume the current cycle.")


def complete(source: Path, changes: str, feedback: str) -> Cycle:
    if not changes.strip() or not feedback.strip():
        raise ValueError("Record delivered changes and user feedback first.")
    if len(changes) > 4000 or len(feedback) > 4000 or sensitive_text(changes + feedback):
        raise ValueError("Keep each entry under 4,000 characters and exclude credentials.")
    _, history, _ = active_cycle(source)
    project = Path(history.project)
    with git_service.writer(project):
        file, history, cycle = active_cycle(source)
        if cycle.status != "active":
            raise ValueError("Cycle is already completed.")
        design_path = source.with_name(source.stem + ".design.json")
        state = DesignSession.load(design_path)
        check_source(state)
        if state.phase != "approved" or Path(state.project).resolve() != project.resolve():
            raise ValueError("An approved design for this project is required.")
        if any(r.status in {"running", "starting"} for r in runs(design_path)):
            raise ValueError("Recover unfinished workers first.")
        settings, revision = stage_branches.current(state, design_path)
        if settings is None:
            raise ValueError("Enable stage branches and record all delivery stage merges first.")
        stage_branches.clean(project)
        if git_service.git(project, "symbolic-ref", "--short", "HEAD") != settings.base:
            raise ValueError("Switch to the integrated base before finishing the cycle.")
        commits = []
        for stage in state.revisions[-1].design.stages:
            item = revision.stages.get(stage.id)
            if (
                not item
                or not item.merged_commit
                or not stage_accepted(state, design_path, stage.id)
                or item.evidence_digest != git_service.evidence_digest(state, design_path, stage.id)
                or not stage_branches.ancestor(project, item.merged_commit, "HEAD")
            ):
                raise ValueError(f"Finish and record the verified merge for stage {stage.id}.")
            commits.append(item.merged_commit)
        cycle.status = "completed"
        cycle.completed_at = now()
        cycle.changes, cycle.feedback = changes.strip(), feedback.strip()
        cycle.commits = list(dict.fromkeys(commits))
        history.save(file)
        return cycle


def start_next(source: Path, goal: str) -> Path:
    if not goal.strip() or len(goal) > 4000 or sensitive_text(goal):
        raise ValueError("Enter a nonempty goal under 4,000 characters without credentials.")
    _, history, _ = active_cycle(source)
    with git_service.writer(Path(history.project)):
        file, history, previous = active_cycle(source)
        if previous.status != "completed":
            raise ValueError("Complete the current cycle first.")
        project = Path(history.project)
        stage_branches.clean(project)
        design_path = source.with_name(source.stem + ".design.json")
        state = DesignSession.load(design_path)
        settings, _ = stage_branches.current(state, design_path)
        if (
            settings is None
            or git_service.git(project, "symbolic-ref", "--short", "HEAD") != settings.base
        ):
            raise ValueError("Return to the integrated base before starting the next cycle.")
        if any(not stage_branches.ancestor(project, commit, "HEAD") for commit in previous.commits):
            raise ValueError("Previous cycle commits are missing from the base.")
        next_path = source.parent / f"session-{uuid4().hex[:12]}.json"
        session = PlanningSession.load(source)
        create_session(
            Path(history.project),
            history.name,
            goal.strip(),
            next_path,
            intake_model=session.intake_model,
            planning_model=session.planning_model,
        )
        history.cycles.append(
            Cycle(number=previous.number + 1, session_name=next_path.name, goal=goal.strip())
        )
        history.save(file)
        return next_path
