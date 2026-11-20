"""Project history layered over independent planning-session checkpoints."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from codex_orchestrator.session import SessionData


def now() -> datetime:
    return datetime.now(UTC)


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

    @model_validator(mode="after")
    def valid_cycle(self) -> Self:
        if Path(self.session_name).name != self.session_name or not self.session_name.endswith(
            ".json"
        ):
            raise ValueError("Cycle session must be a local JSON filename.")
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
        if len({c.session_name for c in self.cycles}) != len(self.cycles):
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
            if any(c.session_name == source.name for c in history.cycles)
        ),
        None,
    )
