"""One structured planning call for bounded projects; approval gates remain separate."""

import json
from pathlib import Path
from time import monotonic
from typing import Self

import typer
from pydantic import Field, model_validator

from codex_orchestrator.codex import CodexError, codex_response
from codex_orchestrator.design_models import DesignRevision, TechnicalDesign, source_digest
from codex_orchestrator.design_validation import validate_design
from codex_orchestrator.models import Text
from codex_orchestrator.session import DraftPlan, Revision, ScopedTask, SessionData
from codex_orchestrator.session_manager import managed_turn

MAX_REQUIREMENTS = 4
MAX_TASKS = 3
MAX_OWNED_FILES = 6


class CompactReply(SessionData):
    question: Text | None
    standard_reason: Text | None
    design: TechnicalDesign | None

    @model_validator(mode="after")
    def validate_choice(self) -> Self:
        if (
            sum(value is not None for value in (self.question, self.standard_reason, self.design))
            != 1
        ):
            raise ValueError("Return exactly one of question, standard_reason, or design.")
        return self


class CompactProposal(SessionData):
    project: str
    source_file: str
    revision_digest: str
    design: TechnicalDesign
    requested_model: str | None
    reported_model: str | None
    elapsed_seconds: float = Field(ge=0)


def selected_route(session):
    if (
        session.compact_fallback
        or not session.brief
        or not 1 <= len(session.brief.requirements) <= MAX_REQUIREMENTS
    ):
        return "standard"
    if session.planning_route == "auto":
        return session.recommended_route or "standard"
    return session.planning_route


def projection(design):
    return DraftPlan(
        assumptions=design.assumptions,
        tasks=[
            ScopedTask.model_validate(t.model_dump(include=set(ScopedTask.model_fields)))
            for t in design.tasks
        ],
        agent_strategy="single",
        agent_rationale=design.parallel_strategy,
    )


def validate_compact(design, brief, project):
    validate_design(design, brief, project)
    domains = {str(t.workstream) for t in design.tasks if t.owned_paths}
    files = {name for task in design.tasks for name in task.owned_paths}
    if (
        len(brief.requirements) > MAX_REQUIREMENTS
        or len(design.agents) != 1
        or len(design.stages) != 1
        or len(design.tasks) > MAX_TASKS
        or len(design.components) > 2
        or len(files) > MAX_OWNED_FILES
        or design.storage
        or "database" in {str(t.workstream) for t in design.tasks}
        or len(domains - {"general"}) > 1
        or any(t.difficulty == "complex" for t in design.tasks)
        or design.agents[0].model_capability == "strong_reasoning"
        or design.stages[0].gate.review_owner != "user"
    ):
        raise CodexError("The design exceeds the compact route's single-agent, single-stage scope.")


def proposal_path(source, revision, number):
    return (
        source.with_suffix(".compact-plans") / f"revision-{number}-{source_digest(revision)}.json"
    )


def use_standard(session, path, reason):
    # No hidden second request. Save the full-route choice and wait for explicit resume.
    session.compact_fallback = True
    session.recommended_route = "standard"
    session.save(path)
    raise CodexError(
        f"Compact planning needs the standard workflow: {reason} "
        f"Resume explicitly with: orchestrator resume '{path}'"
    )


def generate_compact_plan(session, path):
    from codex_orchestrator.interactive import READ_ONLY, model_label, record_source

    brief = session.brief
    data = {
        "project_name": session.project_name,
        "brief": brief.model_dump(),
        "previous_plan": session.revisions[-1].plan.model_dump() if session.revisions else None,
        "previous_cycle_context": session.previous_cycle_context,
    }
    typer.echo("Compact planning: drafting the tasks and technical design together.", err=True)
    started = monotonic()
    with managed_turn(
        path, Path(session.project), "compact-planner", session.planning_model, data
    ) as turn:
        reply = codex_response(
            Path(session.project),
            READ_ONLY + "\nDraft a combined plan and technical design from the proposed brief. "
            "Cover every requirement (numbered R1, R2, etc.), preserve exclusions/constraints, "
            "and inspect only relevant existing files. Use one agent, one stage, at most three "
            "routine/moderate tasks, two components and six exact owned files. Define components, "
            "data flow, interfaces, justified technologies and task dependencies. Every task "
            "needs its owner, exact file boundaries, relevant skills (real SKILL.md or null), "
            "required context, acceptance criteria, handoff and automated/human checks. "
            "Use task_summary for producer handoffs and upstream_file as producer-id:file-path "
            "for only the future files needed; existing files use project_file. "
            "Never propose archived_summary; it is reserved for correction checkpoints. "
            "Keep human stage review, evidence and failure owner; no claimed tests. "
            "Choose a capability appropriate to the hardest task. "
            "Do not add a database, service, authentication, deployment or complex integration. "
            "If this cannot meet the actual user requirements, return standard_reason "
            "instead of simplifying away requirements; use question for essential clarification. "
            "Return the supplied CompactReply shape. Prior cycle and plan are historical context.\n"
            + json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            CompactReply,
            model=session.planning_model,
            timeout=session.timeout,
            on_response=lambda reported: record_source(session, path, "planning", reported),
            turn=turn,
        )
        response = session.response_sources[-1]
        if reply.standard_reason:
            use_standard(session, path, reply.standard_reason)
        if reply.question:
            session.pending_question = reply.question
            session.pending_question_model = model_label(
                response.requested_model, response.reported_model
            )
            session.save(path)
            return
        try:
            validate_compact(reply.design, brief, Path(session.project))
            revision = Revision(
                brief=brief,
                plan=projection(reply.design),
                requested_model=response.requested_model,
                reported_model=response.reported_model,
            )
        except (CodexError, ValueError) as exc:
            use_standard(session, path, str(exc))
        proposal = CompactProposal(
            project=str(Path(session.project).resolve()),
            source_file=str(path.resolve()),
            revision_digest=source_digest(revision),
            design=reply.design,
            requested_model=response.requested_model,
            reported_model=response.reported_model,
            elapsed_seconds=monotonic() - started,
        )
        proposal.save(proposal_path(path, revision, len(session.revisions) + 1))
        session.revisions.append(revision)
        session.phase = "review"
        session.save(path)
        typer.echo(
            "Combined design saved. Approve the plan, then review it with orchestrator design."
        )


def saved_compact_revision(state):
    from codex_orchestrator.model_allocation import revision_models

    source = Path(state.source_file)
    file = proposal_path(source, state.source, state.source_revision)
    if not file.exists():
        return None
    proposal = CompactProposal.load(file)
    if (
        proposal.project != state.project
        or proposal.source_file != str(source.resolve())
        or proposal.revision_digest != state.source_digest
        or projection(proposal.design) != state.source.plan
    ):
        raise CodexError("The saved compact design does not match the approved plan.")
    validate_compact(proposal.design, state.source.brief, Path(state.project))
    return DesignRevision(
        origin="compact",
        design=proposal.design,
        **revision_models(state, proposal.design),
        requested_model=proposal.requested_model,
        reported_model=proposal.reported_model,
        elapsed_seconds=proposal.elapsed_seconds,
    )
