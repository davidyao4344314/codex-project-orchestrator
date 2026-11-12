"""Validated session data and atomic checkpoints for interactive planning."""

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from codex_orchestrator.models import PlanContent, Task, Text, Workstream


class SessionData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate_json(path.read_bytes())

    def require_current(self, path: Path) -> None:
        """Reject stale callers before they overwrite another saved update.

        This is an optimistic check, not a multi-process transaction lock.
        """
        if type(self).load(path).model_dump() != self.model_dump():
            raise ValueError("The saved session changed. Refresh before making this update.")

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


class ScopedTask(Task):
    workstream: Workstream | Literal["general"]
    requirement_ids: Annotated[list[int], Field(min_length=1)]
    scope: Annotated[list[Text], Field(min_length=1)]
    difficulty: Literal["routine", "moderate", "complex"]
    automated_checks: list[Text]
    human_checks: list[Text]


class DraftPlan(PlanContent):
    tasks: Annotated[list[ScopedTask], Field(min_length=1)]
    agent_strategy: Literal["single", "multiple"]
    agent_rationale: Text


class ResponseSource(SessionData):
    stage: Literal["intake", "planning"]
    requested_model: Text | None
    reported_model: Text | None


class Revision(SessionData):
    requested_model: Text | None = None
    reported_model: Text | None = None
    brief: Brief
    plan: DraftPlan

    @model_validator(mode="after")
    def validate_requirement_coverage(self) -> Self:
        expected = set(range(1, len(self.brief.requirements) + 1))
        assigned = {number for task in self.plan.tasks for number in task.requirement_ids}
        unknown = assigned - expected
        missing = expected - assigned
        if unknown:
            raise ValueError(f"Tasks reference unknown requirements: {sorted(unknown)}")
        if missing:
            raise ValueError(f"Plan is missing tasks for requirements: {sorted(missing)}")
        return self


class PlanningSession(SessionData):
    schema_version: Literal[1] = 1
    project: Text
    project_name: Text
    intake_model: Text | None = None
    planning_model: Text | None = None
    models_configured: bool = False
    response_sources: list[ResponseSource] = Field(default_factory=list)
    pending_question_model: Text | None = None
    timeout: int = Field(default=300, ge=1)
    phase: Literal["intake", "planning", "review", "approved"] = "intake"
    answers: list[Answer] = Field(default_factory=list)
    brief: Brief | None = None
    pending_question: Text | None = None
    finish_intake: bool = False
    revisions: list[Revision] = Field(default_factory=list)

    approved_revision: int | None = None

    @model_validator(mode="after")
    def validate_phase(self) -> Self:
        if self.phase != "intake" and self.brief is None:
            raise ValueError("The session is missing its brief")
        if self.phase in ("review", "approved"):
            if not self.revisions or self.brief != self.revisions[-1].brief:
                raise ValueError("The session is missing its current plan revision")
            if self.pending_question is not None:
                raise ValueError("A plan in review cannot have a pending question")
        expected_approval = len(self.revisions) if self.phase == "approved" else None
        if self.approved_revision != expected_approval:
            raise ValueError("Approval must refer to the current approved revision")
        return self
