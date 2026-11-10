"""Command-line entry point."""

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Plan frontend, backend, database, and integration work."""
