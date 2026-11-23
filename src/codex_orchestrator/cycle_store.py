"""Project history layered over independent planning-session checkpoints."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from codex_orchestrator.session import SessionData


def now() -> datetime:
    return datetime.now(UTC)


class FeedbackRound(SessionData):
    id: str = Field(default_factory=lambda: uuid4().hex)
    recorded_at: datetime = Field(default_factory=now)
    kind: Literal["improve", "goal_achieved"]
    revision: int = Field(ge=1)
    design_digest: str
    stage_id: str
    note: str
    task_id: str | None = None
    agent_id: str | None = None
    workstream: str | None = None
    owned_paths: list[str] = Field(default_factory=list)
    prior_run_id: str | None = None
    evidence_digest: str | None = None
    session_name: str | None = None


class ArchivedDelivery(SessionData):
    session_name: str
    design_digest: str
    archived_at: datetime = Field(default_factory=now)
    merged_stages: dict[str, str]


class PullRequestDelivery(SessionData):
    session_name: str
    stage_id: str
    repository: str
    number: int = Field(gt=0)
    base: str
    commit: str
    merge_commit: str


class Cycle(SessionData):
    number: int = Field(ge=1)
    session_name: str
    goal: str
    started_at: datetime = Field(default_factory=now)
    imported: bool = False
    completed_at: datetime | None = None
    status: Literal["active", "completed"] = "active"
    changes: str = ""
    feedback: str = ""
    commits: list[str] = Field(default_factory=list)
    pull_requests: list[PullRequestDelivery] = Field(default_factory=list)
    feedback_enabled: bool = False
    feedback_rounds: list[FeedbackRound] = Field(default_factory=list)
    archived_deliveries: list[ArchivedDelivery] = Field(default_factory=list)

    def session_names(self) -> set[str]:
        return {self.session_name, *(d.session_name for d in self.archived_deliveries)}

    @model_validator(mode="after")
    def valid_cycle(self) -> Self:
        if any(
            Path(name).name != name or not name.endswith(".json") for name in self.session_names()
        ):
            raise ValueError("Cycle session must be a local JSON filename.")
        if len(self.session_names()) != 1 + len(self.archived_deliveries):
            raise ValueError("Delivery session names must be unique.")
        if not self.goal.strip():
            raise ValueError("Cycle needs a goal.")
        if (self.status == "completed") != (self.completed_at is not None):
            raise ValueError("Completion status must match its timestamp.")
        return self


class ProjectHistory(SessionData):
    project: str
    name: str
    cycles: list[Cycle]

    @model_validator(mode="after")
    def validate_history(self) -> Self:
        if not self.cycles or [c.number for c in self.cycles] != list(
            range(1, len(self.cycles) + 1)
        ):
            raise ValueError("Cycle numbers must be consecutive.")
        if len(set().union(*(c.session_names() for c in self.cycles))) != sum(
            len(c.session_names()) for c in self.cycles
        ):
            raise ValueError("Cycle sessions must be unique.")
        if any(c.status != "completed" for c in self.cycles[:-1]):
            raise ValueError("Only the newest cycle may be active.")
        return self


def history_path(source: Path) -> Path:
    return source.with_suffix(".cycles.json")


def histories(directory: Path) -> list[tuple[Path, ProjectHistory]]:
    return [(file, ProjectHistory.load(file)) for file in sorted(directory.glob("*.cycles.json"))]


def find_history(source: Path) -> tuple[Path, ProjectHistory] | None:
    return next(
        (
            (file, history)
            for file, history in histories(source.parent)
            if any(source.name in c.session_names() for c in history.cycles)
        ),
        None,
    )
