"""Read-only architect requests bound to an approved Stage 1 revision."""

import json
from pathlib import Path
from time import monotonic

import typer

from codex_orchestrator.codex import CodexError, codex_response
from codex_orchestrator.design_models import (
    DesignReply,
    DesignRevision,
    DesignSession,
    source_digest,
)
from codex_orchestrator.interactive import READ_ONLY, ask, model_label
from codex_orchestrator.session import Answer, PlanningSession
from codex_orchestrator.setup import ensure_codex

ARCHITECT_INSTRUCTIONS = (
    "Act as the architect for Stage 2. Inspect the existing repository with read-only operations. "
    "Use the approved brief as the scope. Explain existing code to reuse and new work needed. "
    "Return either a focused clarification question or a complete technical design. "
    "Set requires_requirement_change true only when answering the question would require "
    "changing approved requirements or exclusions. Never silently expand the scope. "
    "Scale the design to the request. A terminal program can use function interfaces "
    "and have no database. "
    "Define components, data flow, relevant interfaces, storage, and justified technology choices. "
    "Divide complex work into independently verifiable delivery stages. Each stage must include "
    "agent checks with expected outcomes, acceptance criteria, any required user tests, review and "
    "failure owners, and evidence needed before merging. All checks are proposed, not completed. "
    "The merge gate requires checks on the combined stage changes and the tested commit, "
    "including regressions, review, and required user acceptance. Dependent stages wait for it. "
    "Use logical agents only where justified. Give each agent a responsibility, exclusions, "
    "required skills, model capability, and an allocation reason. Skill locations "
    "must identify actual SKILL.md files you inspected, or null if availability is unknown. "
    "Assign every task to an agent and stage. Number approved requirements R1, R2, etc. "
    "Use integer requirement_ids to link tasks to the approved brief and cover every requirement. "
    "Include component ownership and exact repository-relative owned file paths "
    "(no globs or directories), "
    "scope exclusions, context inputs, deliverables, acceptance checks, handoff recipients and "
    "payloads, and escalation conditions. Use empty owned_paths for read-only review tasks. "
    "Reference context by kind: requirement uses R1-style IDs, component and interface use IDs, "
    "project_file uses a repository-relative file, task_output uses the producer task ID. "
    "Explain why each context item is needed and what to do if it is missing. "
    "A required upstream output needs a dependency on its producer. Existing files needed for "
    "implementation belong in context, while future files belong in upstream task outputs. "
    "Keep stage and task dependencies acyclic. Order tasks that write the same file. "
    "Handoff recipients and gate owners are agent IDs or user; failure owners must be agents. "
    "Designate owners for shared interfaces. Explain actual opportunities for parallelism. "
    "You are proposing a workflow for user review. Return only JSON matching the schema."
)


def approved_source(path: Path) -> PlanningSession:
    source = PlanningSession.load(path)
    if source.phase != "approved" or source.approved_revision != len(source.revisions):
        raise CodexError(
            f"Stage 2 needs an approved Stage 1 revision. Run 'orchestrator resume {path}'."
        )
    if not Path(source.project).is_dir():
        raise CodexError(f"The source project's directory is unavailable: {source.project}")
    return source


def check_source(state: DesignSession) -> PlanningSession:
    source = approved_source(Path(state.source_file))
    if (
        source.approved_revision != state.source_revision
        or source_digest(source.revisions[-1]) != state.source_digest
        or str(Path(source.project).resolve()) != state.project
    ):
        raise CodexError(
            "The approved Stage 1 source has changed. Start a new design with --output "
            "pointing to a new file so the previous design remains available."
        )
    return source


def prepare_design(
    source_file: Path,
    output: Path | None,
    *,
    architect_model: str | None,
    agent_model: str | None,
) -> tuple[DesignSession, Path]:
    source_file = source_file.resolve()
    source = approved_source(source_file)
    output = (output or source_file.with_name(f"{source_file.stem}.design.json")).resolve()
    if output == source_file:
        raise CodexError("The design output must have a different path from the Stage 1 session.")
    if output.exists():
        state = DesignSession.load(output)
        if Path(state.source_file).resolve() != source_file:
            raise CodexError("This design belongs to a different Stage 1 session.")
        check_source(state)
    else:
        state = DesignSession(
            source_file=str(source_file),
            project=str(Path(source.project).resolve()),
            source_revision=source.approved_revision,
            source=source.revisions[-1],
            source_digest=source_digest(source.revisions[-1]),
            architect_model=source.planning_model,
        )
        state.save(output, new=True)
    if architect_model is not None:
        state.architect_model = normalize_model(architect_model)
    if agent_model is not None:
        state.default_agent_model = normalize_model(agent_model)
    state.save(output)
    return state, output


def normalize_model(value: str) -> str | None:
    value = value.strip()
    if not value:
        raise typer.BadParameter("Model ID must not be empty.")
    return None if value.casefold() == "default" else value


def generate_design(state: DesignSession, output: Path, timeout: int) -> None:
    check_source(state)
    data = {
        "project_name": approved_source(Path(state.source_file)).project_name,
        "approved_brief": state.source.brief.model_dump(),
        "initial_tasks": state.source.plan.model_dump(),
        "clarifications": [answer.model_dump() for answer in state.answers],
        "previous_design": (state.revisions[-1].design.model_dump() if state.revisions else None),
    }
    reported = []
    started = monotonic()
    typer.echo("Architect is inspecting the project and designing the workflow...", err=True)
    reply = codex_response(
        Path(state.project),
        READ_ONLY + ARCHITECT_INSTRUCTIONS + "\n" + json.dumps(data, ensure_ascii=False),
        DesignReply,
        model=state.architect_model,
        timeout=timeout,
        on_response=reported.append,
    )
    elapsed = monotonic() - started
    check_source(state)
    actual_model = reported[-1] if reported else None
    if reply.design is None:
        state.pending_question = reply.question
        state.pending_question_model = model_label(state.architect_model, actual_model)
        state.requires_requirement_change = reply.requires_requirement_change
    else:
        previous_models = state.revisions[-1].agent_models if state.revisions else {}
        allocations = {
            agent.id: previous_models.get(agent.id, state.default_agent_model)
            for agent in reply.design.agents
        }
        state.revisions.append(
            DesignRevision(
                design=reply.design,
                requested_model=state.architect_model,
                reported_model=actual_model,
                agent_models=allocations,
                elapsed_seconds=elapsed,
            )
        )
        state.phase = "review"
        state.requires_requirement_change = False
    state.save(output)


def run_design(state: DesignSession, output: Path, timeout: int) -> None:
    typer.echo(f"Technical-design session: {output}")
    typer.echo("Type :save at a question to save and exit.")
    try:
        if state.phase == "draft":
            ensure_codex()
        while state.phase == "draft":
            check_source(state)
            if state.pending_question:
                typer.echo(f"Question model: {state.pending_question_model or 'unrecorded'}")
                if state.requires_requirement_change:
                    typer.echo(
                        "This decision may change the approved scope. Keep your answer within "
                        "the brief, or use :save and revise Stage 1 before continuing."
                    )
                answer = ask(state.pending_question)
                if answer is None:
                    return
                state.answers.append(Answer(prompt=state.pending_question, response=answer))
                state.pending_question = None
                state.pending_question_model = None
                state.save(output)
            else:
                generate_design(state, output, timeout)
        typer.echo(f"Saved technical-design revision {len(state.revisions)} for review.")
    except typer.Abort:
        typer.echo("Technical-design session saved.")
