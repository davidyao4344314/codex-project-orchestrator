"""Validated session data and atomic checkpoints for interactive planning."""

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from codex_orchestrator.models import Text


class SessionData(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Brief(SessionData):
    goal: Text
    users: list[Text]
    requirements: list[Text]
    exclusions: list[Text]
    project_context: list[Text]
    constraints: list[Text]
    assumptions: list[Text]
    success_criteria: list[Text]


class Answer(SessionData):
    prompt: Text
    response: Text


class PlanningSession(SessionData):
    schema_version: Literal[1] = 1
    project: Text
    project_name: Text
    intake_model: Text | None = None
    planning_model: Text | None = None
    timeout: int = Field(default=300, ge=1)
    phase: Literal["intake", "planning"] = "intake"
    answers: list[Answer] = Field(default_factory=list)
    brief: Brief | None = None
    pending_question: Text | None = None
    finish_intake: bool = False

    @classmethod
    def load(cls, path: Path) -> "PlanningSession":
        return cls.model_validate_json(path.read_bytes())

    def save(self, path: Path, *, new: bool = False) -> None:
        """Publish a complete JSON file, with exclusive creation for new sessions."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(self.model_dump_json(indent=2) + "\n")
            if new:
                path.hardlink_to(temporary)
            else:
                temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
