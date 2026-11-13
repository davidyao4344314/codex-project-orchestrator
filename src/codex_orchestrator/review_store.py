"""Persisted independent decisions and reviewer model preferences."""

import time
from pathlib import Path
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from codex_orchestrator.models import Text
from codex_orchestrator.session import SessionData


class ReviewFinding(SessionData):
    location: Text
    detail: Text
    blocking: bool


class ReviewDecision(SessionData):
    decision: Literal["approved", "changes_requested", "blocked"]
    summary: Text
    findings: list[ReviewFinding]
    additional_human_checks: list[Text]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.decision == "approved" and (
            any(f.blocking for f in self.findings) or self.additional_human_checks
        ):
            raise ValueError(
                "Approval cannot contain blocking findings or outstanding human checks."
            )
        return self


class ReviewInput(SessionData):
    content: str


class ReviewRecord(SessionData):
    created_at: float = Field(default_factory=time.time)
    input_id: str | None = None
    id: str = Field(default_factory=lambda: uuid4().hex)
    stage_id: str
    fingerprint: str
    source: Literal["user", "reviewer"]
    requested_model: str | None = None
    reported_model: str | None = None
    input_digest: str | None = None
    result: ReviewDecision


class ReviewLog(SessionData):
    records: list[ReviewRecord] = Field(default_factory=list)


class PublicationPolicy(SessionData):
    # Legacy fields remain readable; automatic publishing is no longer honored.
    allow_reviewer_push: bool = False
    reviewer_model: str | None = None
    branch: str = ""
    remote: str = ""


def log_path(path: Path) -> Path:
    return path.with_suffix(".reviews.json")


def load_reviews(path: Path) -> ReviewLog:
    file = log_path(path)
    return ReviewLog.load(file) if file.exists() else ReviewLog()


def save_review(path: Path, record: ReviewRecord) -> ReviewRecord:
    log = load_reviews(path)
    log.records.append(record)
    log.save(log_path(path))
    return record


def policy_path(path: Path) -> Path:
    return path.with_suffix(".review-policy.json")


def load_policy(path: Path) -> PublicationPolicy:
    file = policy_path(path)
    return PublicationPolicy.load(file) if file.exists() else PublicationPolicy()


def require_approval(path: Path, approval_id: str | None, fingerprint: str) -> ReviewRecord:
    records = load_reviews(path).records
    record = next((r for r in records if r.id == approval_id), None)
    if record is None or record.fingerprint != fingerprint or record.result.decision != "approved":
        raise ValueError("Current changes need approval from the user or independent reviewer.")
    latest = next(
        r for r in reversed(records) if r.stage_id == record.stage_id and r.source == record.source
    )
    if latest.id != record.id:
        raise ValueError("This approval was superseded. Review the current changes again.")
    return record


def input_path(path: Path, input_id: str) -> Path:
    UUID(input_id)
    return path.parent / (path.stem + ".review-inputs") / (input_id + ".json")


def require_reviewer(path: Path, stage_id: str, fingerprint: str) -> ReviewRecord:
    record = next(
        (
            r
            for r in reversed(load_reviews(path).records)
            if r.stage_id == stage_id and r.source == "reviewer"
        ),
        None,
    )
    if record is None:
        raise ValueError("Run the independent AI review before committing or pushing.")
    if record.fingerprint != fingerprint or record.result.decision != "approved":
        raise ValueError("Current changes need approval from the independent AI reviewer.")
    return require_approval(path, record.id, fingerprint)
