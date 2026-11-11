"""Command-line entry point."""

import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from codex_orchestrator.codex import CodexError, codex_plan
from codex_orchestrator.interactive import run_session, start_session
from codex_orchestrator.models import PlanningMode, ProjectPlan
from codex_orchestrator.planner import template_plan
from codex_orchestrator.session import PlanningSession
from codex_orchestrator.setup import ensure_codex

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Plan frontend, backend, database, and integration work."""


@app.command()
def plan(
    project: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, resolve_path=True, help="Project directory."),
    ],
    brief: Annotated[str | None, typer.Argument(help="Feature or project brief.")] = None,
    brief_file: Annotated[
        Path | None,
        typer.Option("--brief-file", exists=True, dir_okay=False, help="UTF-8 brief file."),
    ] = None,
    name: Annotated[
        str | None, typer.Option(help="Project name; defaults to the directory name.")
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Destination JSON file.")
    ] = None,
    provider: Annotated[
        PlanningMode | None,
        typer.Option(help="Planning provider (default: template, or codex for interactive)."),
    ] = None,
    model: Annotated[
        str | None, typer.Option("--model", "--planning-model", help="Planning model ID.")
    ] = None,
    timeout: Annotated[int, typer.Option(min=1, help="Codex timeout in seconds.")] = 300,
    interactive: Annotated[
        bool, typer.Option(help="Collect requirements in the terminal.")
    ] = False,
    intake_model: Annotated[str | None, typer.Option(help="Model for interactive intake.")] = None,
    force: Annotated[bool, typer.Option(help="Replace an existing output file.")] = False,
) -> None:
    """Save a validated plan without implementing tasks."""
    if brief is not None and brief_file is not None:
        raise typer.BadParameter("Provide either a brief argument or --brief-file, exactly one.")
    if not interactive and brief is None and brief_file is None:
        raise typer.BadParameter("Provide either a brief argument or --brief-file, exactly one.")
    if not interactive and intake_model is not None:
        raise typer.BadParameter("--intake-model requires --interactive.")
    if interactive and (force or provider == PlanningMode.TEMPLATE):
        raise typer.BadParameter("Interactive mode uses Codex and creates a new session file.")
    provider = provider or (PlanningMode.CODEX if interactive else PlanningMode.TEMPLATE)
    if model is not None and provider != PlanningMode.CODEX:
        raise typer.BadParameter("--model requires --provider codex.")
    try:
        text = brief_file.read_text(encoding="utf-8") if brief_file else brief
        if text is not None and not text.strip():
            raise typer.BadParameter("The brief must not be empty.")
        if name is not None and not name.strip():
            raise typer.BadParameter("The project name must not be empty.")
        project_name = name.strip() if name is not None else project.name or "project"
        if interactive:
            start_session(
                project,
                project_name,
                text,
                output,
                intake_model=intake_model,
                planning_model=model,
                timeout=timeout,
            )
            return
        output = output or Path("plans/plan.json")
        if output.exists() and not force:
            raise typer.BadParameter(f"Output already exists: {output}. Use --force to replace it.")
        if provider == PlanningMode.CODEX:
            ensure_codex(interactive=sys.stdin.isatty())
            typer.echo("Asking Codex to inspect the project and plan the work...", err=True)
            content = codex_plan(project, project_name, text, model=model, timeout=timeout)
        else:
            content = template_plan()
        result = ProjectPlan(
            project_name=project_name,
            brief=text,
            planning_mode=provider,
            **content.model_dump(),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w" if force else "x", encoding="utf-8") as destination:
            destination.write(result.model_dump_json(indent=2) + "\n")
    except (OSError, UnicodeError, ValidationError, CodexError) as exc:
        typer.echo(f"Could not create plan: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Saved {len(result.tasks)} tasks ({provider.value}) to {output.resolve()}")
    if provider == PlanningMode.TEMPLATE:
        typer.echo(
            "Template tasks are generic; refine them against your brief before implementation."
        )


@app.command()
def resume(
    session_file: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, resolve_path=True, help="Saved session JSON."),
    ],
    intake_model: Annotated[
        str | None, typer.Option(help="Change intake model; default resets it.")
    ] = None,
    planning_model: Annotated[
        str | None,
        typer.Option(
            "--planning-model", "--model", help="Change planning model; default resets it."
        ),
    ] = None,
) -> None:
    """Continue a saved interactive planning session."""
    try:
        session = PlanningSession.load(session_file)
        for field, value in (("intake_model", intake_model), ("planning_model", planning_model)):
            if value is not None:
                if not value.strip():
                    raise typer.BadParameter("Model ID must not be empty.")
                setattr(
                    session, field, None if value.strip().casefold() == "default" else value.strip()
                )
        if intake_model is not None or planning_model is not None:
            session.save(session_file)
        run_session(session, session_file)
    except (OSError, UnicodeError, ValidationError, CodexError) as exc:
        typer.echo(f"Could not continue session: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def setup() -> None:
    """Find or install Codex CLI and sign in with the browser."""
    try:
        ensure_codex()
    except (CodexError, typer.Abort) as exc:
        typer.echo(f"Setup incomplete: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("Codex is ready. Start a plan or resume your saved session.")
