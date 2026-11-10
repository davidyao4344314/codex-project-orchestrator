"""Command-line entry point."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from codex_orchestrator.models import PlanningMode, ProjectPlan
from codex_orchestrator.planner import template_plan

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
    output: Annotated[Path, typer.Option("--output", "-o", help="Destination JSON file.")] = Path(
        "plans/plan.json"
    ),
    force: Annotated[bool, typer.Option(help="Replace an existing output file.")] = False,
) -> None:
    """Save a validated starter plan without implementing tasks."""
    if (brief is None) == (brief_file is None):
        raise typer.BadParameter("Provide either a brief argument or --brief-file, exactly one.")
    try:
        text = brief_file.read_text(encoding="utf-8") if brief_file else brief
        if not text or not text.strip():
            raise typer.BadParameter("The brief must not be empty.")
        if name is not None and not name.strip():
            raise typer.BadParameter("The project name must not be empty.")
        if output.exists() and not force:
            raise typer.BadParameter(f"Output already exists: {output}. Use --force to replace it.")
        content = template_plan()
        result = ProjectPlan(
            project_name=name if name is not None else project.name,
            brief=text,
            planning_mode=PlanningMode.TEMPLATE,
            **content.model_dump(),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w" if force else "x", encoding="utf-8") as destination:
            destination.write(result.model_dump_json(indent=2) + "\n")
    except (OSError, UnicodeError, ValidationError) as exc:
        typer.echo(f"Could not create plan: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Saved {len(result.tasks)} tasks (template) to {output.resolve()}")
    typer.echo("Template tasks are generic; refine them against your brief before implementation.")
