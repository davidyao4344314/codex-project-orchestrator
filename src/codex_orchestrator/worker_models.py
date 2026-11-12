"""Worker contracts: model-reported outcomes remain separate from observed evidence."""

import hashlib
from typing import Literal, Self

from pydantic import Field, model_validator

from codex_orchestrator.models import Text
from codex_orchestrator.session import SessionData

WorkerKind = Literal["frontend", "backend", "database", "integration", "general", "verification"]


def artifact_digest(artifact: SessionData) -> str:
    """Hash validated content independently of checkpoint whitespace."""
    return hashlib.sha256(artifact.model_dump_json().encode("utf-8")).hexdigest()


class RequiredCheck(SessionData):
    id: Text
    description: Text


class WorkerSpec(SessionData):
    schema_version: Literal[1] = 1
    task_id: Text
    agent_id: Text
    role: Text
    profile: WorkerKind
    model: Text | None
    context_digest: Text
    owned_paths: list[Text]
    audit_paths: list[Text] = Field(default_factory=list)
    read_only: bool
    instructions: Text
    checks: list[RequiredCheck]
    acceptance_criteria: list[Text]
    human_checks: list[Text]


class ReportedChange(SessionData):
    path: Text
    description: Text


class ReportedCheck(SessionData):
    check_id: Text
    command: Text | None
    outcome: Literal["passed", "failed", "not_run"]
    detail: Text

    @model_validator(mode="after")
    def command_required(self) -> Self:
        if self.outcome != "not_run" and self.command is None:
            raise ValueError("Executed checks need their exact command.")
        return self


class WorkerResult(SessionData):
    task_id: Text
    agent_id: Text
    profile: WorkerKind
    context_digest: Text
    outcome: Literal["completed", "blocked", "failed"]
    summary: Text
    changes: list[ReportedChange]
    checks: list[ReportedCheck]
    blockers: list[Text]
    human_checks: list[Text]
    handoff: Text

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        if self.outcome == "completed" and (
            self.blockers or any(check.outcome != "passed" for check in self.checks)
        ):
            raise ValueError("Completed work cannot contain blockers or unpassed checks.")
        if self.outcome == "blocked" and not self.blockers:
            raise ValueError("Blocked work must identify what is missing.")
        return self


class CommandEvidence(SessionData):
    event_id: str | None
    command: Text
    exit_code: int | None
    output: str = ""


class ObservedChange(SessionData):
    path: Text
    before_sha256: str | None
    after_sha256: str | None
    diff: str | None = None


class WorkerEvidence(SessionData):
    commands: list[CommandEvidence] = Field(default_factory=list)
    changes: list[ObservedChange] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    check_matches: dict[str, str] = Field(default_factory=dict)
