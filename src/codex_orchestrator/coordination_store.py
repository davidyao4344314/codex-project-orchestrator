"""Revision-bound coordination checkpoints and human stage acceptance."""

import hashlib
import time
from pathlib import Path
from typing import Literal

from pydantic import Field

from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.run_store import latest_runs
from codex_orchestrator.session import SessionData


class StageAcceptance(SessionData):
    run_ids: dict[str, str]
    evidence: str
    accepted_at: float = Field(default_factory=time.time)


class CoordinationState(SessionData):
    schema_version: Literal[1] = 1
    design_fingerprint: str
    paused: bool = False
    accepted_stages: dict[str, StageAcceptance] = Field(default_factory=dict)


def design_fingerprint(state: DesignSession) -> str:
    return hashlib.sha256(state.revisions[-1].model_dump_json().encode()).hexdigest()


def checkpoint_path(state: DesignSession, path: Path) -> Path:
    return (
        path.parent
        / (path.stem + ".coordination")
        / (f"revision-{len(state.revisions)}-{design_fingerprint(state)}.json")
    )


def load_coordination(state: DesignSession, path: Path) -> CoordinationState:
    file = checkpoint_path(state, path)
    if file.exists():
        saved = CoordinationState.load(file)
        if saved.design_fingerprint != design_fingerprint(state):
            raise ValueError("Coordinator checkpoint belongs to another design revision.")
        return saved
    return CoordinationState(design_fingerprint=design_fingerprint(state))


def stage_accepted(state: DesignSession, path: Path, stage_id: str) -> bool:
    acceptance = load_coordination(state, path).accepted_stages.get(stage_id)
    if acceptance is None:
        return False
    from codex_orchestrator.cycle_feedback import needs_retry
    from codex_orchestrator.stage_branches import valid_runs

    latest = valid_runs(state, path, latest_runs(path, len(state.revisions)))
    tasks = [t for t in state.revisions[-1].design.tasks if t.stage_id == stage_id]
    if any(needs_retry(state, path, t.id, latest.get(t.id)) for t in tasks):
        return False
    return (
        bool(tasks)
        and acceptance.run_ids
        == {
            t.id: latest[t.id].id
            for t in tasks
            if t.id in latest and latest[t.id].status == "completed" and latest[t.id].verified
        }
        and len(acceptance.run_ids) == len(tasks)
    )


def check_stage_prerequisites(state: DesignSession, path: Path, stage_id: str) -> None:
    stages = {s.id: s for s in state.revisions[-1].design.stages}
    if stage_id not in stages:
        raise ValueError("Unknown delivery stage.")
    pending = list(stages[stage_id].depends_on)
    seen = set()
    while pending:
        parent = pending.pop()
        if parent in seen:
            continue
        seen.add(parent)
        if not stage_accepted(state, path, parent):
            raise ValueError(f"Accept prerequisite stage {parent}'s verification gate first.")
        pending.extend(stages[parent].depends_on)
