"""Terminal GitHub connection setup, independent of model planning."""

from pathlib import Path

import typer

from codex_orchestrator import github_connection


def show_connection(project: Path, *, offer: bool = True) -> None:
    state = github_connection.inspect(project)
    typer.echo(
        "Local Git repository detected." if state.repository else "No local Git repository found."
    )
    if not state.github_repository:
        if state.origin_present:
            typer.echo(
                "Existing origin is not a supported GitHub repository; leaving it unchanged."
            )
            return
        typer.echo("GitHub is not connected.")
        if not offer or not typer.confirm(
            "Connect an existing GitHub repository now?", default=False
        ):
            typer.echo(
                "Continuing locally. Use 'orchestrator cycle github PROJECT' to connect later."
            )
            return
        url = typer.prompt("GitHub repository URL", hide_input=True)
        github_connection.connect(project, url)
        state = github_connection.inspect(project)
    typer.echo(f"GitHub repository: {state.github_repository}")
    typer.echo(f"Git branch: {state.branch or 'detached or unavailable'}")
    if not state.gh_installed:
        typer.echo(
            "GitHub CLI (gh) is missing. Install it from https://cli.github.com/ "
            "and rerun connection setup."
        )
        return
    if not state.authenticated:
        typer.echo("GitHub CLI sign-in could not be verified.")
        if offer and typer.confirm("Sign in to GitHub now?", default=False):
            github_connection.login()
            state = github_connection.inspect(project)
    if state.authenticated and state.repository_access:
        typer.echo("GitHub connected: authenticated and repository accessible.")
    else:
        typer.echo(
            "GitHub repository access could not be verified. "
            "You can continue locally and reconnect later."
        )
