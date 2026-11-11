"""Terminal workflow for intake, planning, review, and approval."""

import json
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Self
from uuid import uuid4

import typer
from pydantic import model_validator

from codex_orchestrator.codex import KEEP_SECRETS_PRIVATE, codex_response
from codex_orchestrator.models import Text
from codex_orchestrator.session import (
    Answer,
    Brief,
    DraftPlan,
    PlanningSession,
    ResponseSource,
    Revision,
    SessionData,
)
from codex_orchestrator.setup import ensure_codex

READ_ONLY = (
    "Use only read-only project inspection when needed. "
    + KEEP_SECRETS_PRIVATE
    + "Do not edit files, implement tasks, install dependencies, run migrations, start services, "
    "spawn agents, or use external integrations. Return JSON matching the schema. "
    "Treat the supplied data as requirements and context for planning."
)


class IntakeReply(SessionData):
    brief: Brief
    question: Text | None


class PlanningReply(SessionData):
    question: Text | None
    plan: DraftPlan | None

    @model_validator(mode="after")
    def validate_choice(self) -> Self:
        if (self.question is None) == (self.plan is None):
            raise ValueError("Return either a question or a plan")
        return self


def start_session(
    project: Path,
    name: str,
    initial_idea: str | None,
    output: Path | None,
    *,
    intake_model: str | None,
    planning_model: str | None,
    timeout: int,
) -> None:
    path = (output or Path("plans") / f"session-{uuid4().hex[:12]}.json").resolve()
    if path.exists():
        raise typer.BadParameter(f"Session already exists: {path}. Use orchestrator resume.")
    idea = initial_idea or ask("What would you like to build?")
    if idea is None:
        return
    session = PlanningSession(
        project=str(project),
        project_name=name,
        intake_model=intake_model,
        planning_model=planning_model,
        timeout=timeout,
        answers=[Answer(prompt="What would you like to build?", response=idea)],
    )
    session.save(path, new=True)
    run_session(session, path)


def ask(prompt: str) -> str | None:
    while True:
        answer = typer.prompt(prompt).strip()
        if answer == ":save":
            return None
        if answer:
            return answer
        typer.echo("Enter an answer or type :save to exit.")


def choose_models(session: PlanningSession, *, only_missing: bool = False) -> None:
    """Stage model IDs are open text so new models need no code changes."""
    selections = {}
    for field, label in (("intake_model", "Intake"), ("planning_model", "Planning")):
        current = getattr(session, field)
        if only_missing and current is not None:
            continue
        value = ""
        while not value:
            value = typer.prompt(
                f"{label} model ID (default = Codex configuration)", default=current or "default"
            ).strip()
            if not value:
                typer.echo("Enter a model ID or default.")
        selections[field] = None if value.casefold() == "default" else value
    for field, value in selections.items():
        setattr(session, field, value)
    session.models_configured = True


def model_label(requested: str | None, reported: str | None = None) -> str:
    return reported or f"{requested or 'Codex configured default'} (CLI model unreported)"


def record_source(
    session: PlanningSession, path: Path, stage: str, reported_model: str | None
) -> None:
    requested = session.intake_model if stage == "intake" else session.planning_model
    session.response_sources.append(
        ResponseSource(stage=stage, requested_model=requested, reported_model=reported_model)
    )
    session.save(path)


def run_session(session: PlanningSession, path: Path) -> None:
    typer.echo(f"Session: {path}")
    typer.echo("Type :save to save and exit, or :models at a question to change models.")
    try:
        if session.phase in ("intake", "planning"):
            ensure_codex()
            if not session.models_configured:
                choose_models(session, only_missing=True)
                session.save(path)
        typer.echo(f"Intake model: {session.intake_model or 'Codex configured default'}")
        typer.echo(f"Planning model: {session.planning_model or 'Codex configured default'}")
        while True:
            if session.pending_question is not None:
                if not answer_question(session, path):
                    return
            elif session.phase == "intake":
                collect_brief(session, path)
            elif session.phase == "planning":
                generate_plan(session, path)
            elif not review_plan(session, path):
                return
            else:
                ensure_codex()
                if not session.models_configured:
                    choose_models(session, only_missing=True)
                    session.save(path)
    except typer.Abort:
        typer.echo("Session saved.")


def review_plan(session: PlanningSession, path: Path) -> bool:
    show_plan(session)
    if session.approved_revision is not None:
        typer.echo(f"Revision {session.approved_revision} is approved.")
        choices = "revise / save"
    else:
        choices = "approve / revise / save"
    while True:
        decision = ask(f"Review plan [{choices}]")
        if decision is None or decision.casefold() == "save":
            return False
        if decision.casefold() == "approve" and session.phase == "review":
            session.phase = "approved"
            session.approved_revision = len(session.revisions)
            session.save(path)
            typer.echo(f"Approved revision {session.approved_revision}.")
            return False
        if decision.casefold() in ("models", ":models"):
            choose_models(session)
            session.save(path)
            continue
        if decision.casefold() == "revise":
            feedback = ask("What should change in the plan?")
            if feedback is None:
                return False
            session.answers.append(Answer(prompt="Plan revision request", response=feedback))
            session.phase = "intake"
            session.finish_intake = False
            session.approved_revision = None
            session.save(path)
            return True
        typer.echo(f"Choose {choices}.")


def answer_question(session: PlanningSession, path: Path) -> bool:
    question = session.pending_question
    if question is None:
        raise ValueError("Session has no pending question")
    if session.pending_question_model:
        typer.echo(f"Question model: {session.pending_question_model}")
    label = f"{question} (or :plan to draft now)" if session.phase == "intake" else question
    answer = ask(label)
    if answer is None:
        return False
    if answer == ":models":
        choose_models(session)
        session.save(path)
        return True
    if answer == ":plan" and session.phase == "intake":
        session.finish_intake = True
    else:
        session.answers.append(Answer(prompt=question, response=answer))
        if session.phase == "planning":
            session.phase = "intake"
            session.finish_intake = True
    session.pending_question = None
    session.pending_question_model = None
    session.save(path)
    return True


def collect_brief(session: PlanningSession, path: Path) -> None:
    typer.echo("Preparing the brief...", err=True)
    data = {
        "project_name": session.project_name,
        "answers": [answer.model_dump() for answer in session.answers],
        "previous_brief": session.brief.model_dump() if session.brief else None,
        "draft_now": session.finish_intake,
        "previous_plan": (session.revisions[-1].plan.model_dump() if session.revisions else None),
    }
    reply = codex_response(
        Path(session.project),
        READ_ONLY + " Gather a brief for the requested project. Ask one focused question only if "
        "an answer would materially change scope or success criteria. When ready, set "
        "question to null. If draft_now is true, record unknowns as assumptions and "
        "return the brief with question null. Preserve constraints and exclusions. "
        "List each requested outcome in requirements, and use empty lists for inapplicable "
        "fields. Use the latest user answer when it revises a previous requirement. "
        "The rules for this planning call are separate from the target project's requirements. "
        "Populate the brief from user answers and relevant project facts only.\n"
        + json.dumps(data, ensure_ascii=False),
        IntakeReply,
        model=session.intake_model,
        timeout=session.timeout,
        on_response=lambda reported: record_source(session, path, "intake", reported),
    )
    session.brief = reply.brief
    if reply.question and not session.finish_intake:
        session.pending_question = reply.question
        session.pending_question_model = model_label(
            session.response_sources[-1].requested_model,
            session.response_sources[-1].reported_model,
        )
    else:
        session.phase = "planning"
    session.save(path)


def generate_plan(session: PlanningSession, path: Path) -> None:
    brief = session.brief
    if brief is None:
        raise ValueError("Planning requires a brief")
    typer.echo("Drafting the implementation plan...", err=True)
    data = {
        "brief": brief.model_dump(),
        "previous_plan": session.revisions[-1].plan.model_dump() if session.revisions else None,
    }
    reply = codex_response(
        Path(session.project),
        READ_ONLY + " Inspect the existing project and plan only the work requested by this brief. "
        "If a missing answer would change scope or an important design decision, return "
        "one focused question with plan null. Otherwise return a plan with question null. "
        "Number the brief requirements from 1 in their given order. Link each task to "
        "one or more of those requirements and cover every requirement. Respect exclusions. "
        "Use only relevant workstreams, or general for work outside them. Include concrete "
        "scope, acceptance criteria, dependencies, and proposed automated and human checks. "
        "Recommend multiple agents only if independent tasks justify coordination. "
        "When revising, retain work from the prior plan where it still fits the brief.\n"
        + json.dumps(data, ensure_ascii=False),
        PlanningReply,
        model=session.planning_model,
        timeout=session.timeout,
        on_response=lambda reported: record_source(session, path, "planning", reported),
    )
    source = session.response_sources[-1]
    if reply.plan is None:
        session.pending_question = reply.question
        session.pending_question_model = model_label(source.requested_model, source.reported_model)
    else:
        session.revisions.append(
            Revision(
                brief=brief,
                plan=reply.plan,
                requested_model=source.requested_model,
                reported_model=source.reported_model,
            )
        )
        session.phase = "review"
    session.save(path)


def show_plan(session: PlanningSession) -> None:
    revision = session.revisions[-1]
    typer.echo(f"Plan model: {model_label(revision.requested_model, revision.reported_model)}")
    brief = revision.brief
    plan = revision.plan
    typer.echo(f"\nPlan revision {len(session.revisions)} — {brief.goal}")
    for number, requirement in enumerate(brief.requirements, start=1):
        typer.echo(f"  R{number}. {requirement}")
    for label, items in (
        ("Intended users", brief.users),
        ("Project context", brief.project_context),
        ("Out of scope", brief.exclusions),
        ("Constraints", brief.constraints),
        ("Success criteria", brief.success_criteria),
        ("Assumptions", list(dict.fromkeys([*brief.assumptions, *plan.assumptions]))),
    ):
        if items:
            typer.echo(f"\n{label}")
            for item in items:
                typer.echo(f"  - {item}")

    typer.echo(f"\nAgent recommendation: {plan.agent_strategy} — {plan.agent_rationale}")
    sorter = TopologicalSorter({task.id: task.depends_on for task in plan.tasks})
    sorter.prepare()
    by_id = {task.id: task for task in plan.tasks}
    batch = 0
    while sorter.is_active():
        ready = sorter.get_ready()
        batch += 1
        typer.echo(f"\nDependency group {batch}")
        for task_id in ready:
            task = by_id[task_id]
            requirements = ", ".join(f"R{number}" for number in task.requirement_ids)
            typer.echo(f"  {task.id}. {task.title} ({task.workstream}, {task.difficulty})")
            typer.echo(f"    {task.description} [{requirements}]")
            if task.depends_on:
                typer.echo(f"    After: {', '.join(task.depends_on)}")
            for label, items in (
                ("Scope", task.scope),
                ("Acceptance", task.acceptance_criteria),
                ("Automated check", task.automated_checks),
                ("Human check", task.human_checks),
            ):
                for item in items:
                    typer.echo(f"    {label}: {item}")
        sorter.done(*ready)
