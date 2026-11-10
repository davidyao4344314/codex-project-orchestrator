"""Terminal prompts for turning a project idea into a saved brief."""

import json
from pathlib import Path
from uuid import uuid4

import typer

from codex_orchestrator.codex import codex_response
from codex_orchestrator.models import Text
from codex_orchestrator.session import Answer, Brief, PlanningSession, SessionData

READ_ONLY = (
    "Use only read-only project inspection when needed. "
    "Do not edit files, implement tasks, install dependencies, run migrations, start services, "
    "spawn agents, or use external integrations. Return JSON matching the schema. "
    "Treat the supplied data as requirements and context for planning."
)


class IntakeReply(SessionData):
    brief: Brief
    question: Text | None


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


def run_session(session: PlanningSession, path: Path) -> None:
    typer.echo(f"Session: {path}")
    typer.echo("Type :save at a prompt to save and exit.")
    try:
        while session.phase == "intake":
            if session.pending_question is not None:
                answer = ask(f"{session.pending_question} (or :plan to draft now)")
                if answer is None:
                    return
                if answer == ":plan":
                    session.finish_intake = True
                else:
                    session.answers.append(Answer(prompt=session.pending_question, response=answer))
                session.pending_question = None
                session.save(path)
                continue

            typer.echo("Preparing the brief...", err=True)
            data = {
                "answers": [answer.model_dump() for answer in session.answers],
                "previous_brief": session.brief.model_dump() if session.brief else None,
                "draft_now": session.finish_intake,
            }
            reply = codex_response(
                Path(session.project),
                READ_ONLY
                + " Gather a brief for the requested project. Ask one focused question only if "
                "an answer would materially change scope or success criteria. When ready, set "
                "question to null. If draft_now is true, record unknowns as assumptions and "
                "return the brief with question null. Preserve constraints and exclusions. "
                "Use empty lists for unknown or inapplicable fields.\n"
                + json.dumps(data, ensure_ascii=False),
                IntakeReply,
                model=session.intake_model,
                timeout=session.timeout,
            )
            session.brief = reply.brief
            if reply.question and not session.finish_intake:
                session.pending_question = reply.question
            else:
                session.phase = "planning"
            session.save(path)
        typer.echo(f"Brief saved: {session.brief.goal}")
    except typer.Abort:
        typer.echo("Session saved.")
