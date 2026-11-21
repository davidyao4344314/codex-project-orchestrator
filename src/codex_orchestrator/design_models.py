"""Stage 2 contracts for technical designs, agent context, and delivery gates."""

import hashlib
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from codex_orchestrator.context_models import ContextLimits
from codex_orchestrator.models import PlanContent, Text
from codex_orchestrator.session import Answer, ModelPolicy, Revision, ScopedTask, SessionData

Items = Annotated[list[Text], Field(min_length=1)]


class Component(SessionData):
    id: Text
    name: Text
    responsibility: Text
    paths: Items


class Interface(SessionData):
    id: Text
    source_component: Text
    target_component: Text | None
    inputs: Items
    outputs: Items
    errors: list[Text]


class Technology(SessionData):
    name: Text
    reason: Text
    already_used: bool


class Skill(SessionData):
    name: Text
    location: Text | None


class Agent(SessionData):
    id: Text
    role: Text
    responsibility: Text
    exclusions: list[Text]
    skills: list[Skill]
    model_capability: Literal["routine", "standard", "strong_reasoning"]
    model_reason: Text


class ContextReference(SessionData):
    kind: Literal[
        "requirement",
        "component",
        "interface",
        "project_file",
        "task_output",
        "task_summary",
        "upstream_file",
    ]
    reference: Text
    reason: Text
    required: bool
    missing_action: Text


class Assignment(ScopedTask):
    stage_id: Text
    agent_id: Text
    component_ids: Items
    owned_paths: list[Text]
    exclusions: list[Text]
    context: Annotated[list[ContextReference], Field(min_length=1)]
    deliverables: Items
    handoff_to: Items
    handoff_summary: Text
    escalation: Items


class AgentCheck(SessionData):
    agent_id: Text
    check: Text
    expected_result: Text


class VerificationGate(SessionData):
    agent_checks: Annotated[list[AgentCheck], Field(min_length=1)]
    user_checks: list[Text]
    acceptance_criteria: Items
    review_owner: Text
    failure_owner: Text
    required_evidence: Items


class DeliveryStage(SessionData):
    id: Text
    title: Text
    goal: Text
    depends_on: list[Text]
    deliverables: Items
    gate: VerificationGate


class TechnicalDesign(PlanContent):
    overview: Text
    repository_findings: Items
    components: Annotated[list[Component], Field(min_length=1)]
    interfaces: list[Interface]
    data_flow: Items
    storage: list[Text]
    technologies: list[Technology]
    exclusions: list[Text]
    stages: Annotated[list[DeliveryStage], Field(min_length=1)]
    agents: Annotated[list[Agent], Field(min_length=1)]
    tasks: Annotated[list[Assignment], Field(min_length=1)]
    parallel_strategy: Text


class DesignReply(SessionData):
    question: Text | None
    requires_requirement_change: bool
    design: TechnicalDesign | None

    @model_validator(mode="after")
    def validate_choice(self) -> Self:
        if (self.question is None) == (self.design is None):
            raise ValueError("Return either a question or a technical design")
        if self.requires_requirement_change and self.question is None:
            raise ValueError("A requirement change must be raised as a question")
        return self


class DesignRevision(SessionData):
    origin: Literal[
        "architect", "model_allocation", "assignment", "team_allocation", "correction", "compact"
    ] = "architect"
    design: TechnicalDesign
    requested_model: Text | None = None
    reported_model: Text | None = None
    allocation_rationale: str | None = None
    task_allocation_reasons: dict[str, str] = Field(default_factory=dict)
    # Allocation is local user configuration, separate from AI capability recommendations.
    agent_models: dict[str, Text | None]
    task_models: dict[str, Text | None] | None = None
    elapsed_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_allocations(self) -> Self:
        if set(self.agent_models) != {agent.id for agent in self.design.agents}:
            raise ValueError("Model allocations must cover exactly the design's agents")
        if self.task_models is not None and set(self.task_models) != {
            t.id for t in self.design.tasks
        }:
            raise ValueError("Task model allocations must cover exactly the design's tasks")
        return self

    def model_for_task(self, task: Assignment) -> str | None:
        if self.task_models is not None:
            return self.task_models[task.id]
        return self.agent_models[task.agent_id]


def revision_digest(revision: DesignRevision) -> str:
    # Preserve old approvals/branch fingerprints when loading legacy revisions.
    exclude = {"task_models"} if revision.task_models is None else set()
    return hashlib.sha256(revision.model_dump_json(exclude=exclude).encode()).hexdigest()


def source_digest(revision: Revision) -> str:
    return hashlib.sha256(revision.model_dump_json().encode()).hexdigest()


class StageReview(SessionData):
    revision: int = Field(ge=1)
    stage_id: Text
    feedback: str
    approved: bool = False


class AgentPreference(SessionData):
    id: Text
    role: Text
    model: Text | None = None


class DesignSession(SessionData):
    context_limits: ContextLimits = Field(default_factory=ContextLimits)
    agent_roster: list[AgentPreference] = Field(default_factory=list)
    stage_reviews: list[StageReview] = Field(default_factory=list)
    schema_version: Literal[1] = 1
    source_file: Text
    project: Text
    source_revision: int = Field(ge=1)
    source: Revision
    source_digest: Text
    architect_model: Text | None = None
    default_agent_model: Text | None = None
    model_policy: ModelPolicy | None = None
    auto_model_allocation: bool = False
    models_configured: bool = False
    phase: Literal["draft", "review", "approved"] = "draft"
    answers: list[Answer] = Field(default_factory=list)
    pending_question: Text | None = None
    pending_question_model: Text | None = None
    requires_requirement_change: bool = False
    validation_feedback: list[Text] = Field(default_factory=list)
    revisions: list[DesignRevision] = Field(default_factory=list)
    approved_revision: int | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.source_digest != source_digest(self.source):
            raise ValueError("The approved source snapshot has changed")
        if self.phase in ("review", "approved") and (
            not self.revisions or self.pending_question is not None
        ):
            raise ValueError("Review requires a completed design without pending questions")
        expected = len(self.revisions) if self.phase == "approved" else None
        if self.approved_revision != expected:
            raise ValueError("Approval must refer to the current technical-design revision")
        return self
