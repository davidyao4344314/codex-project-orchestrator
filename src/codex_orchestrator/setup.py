"""Terminal setup using Codex's own installer and browser sign-in."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from codex_orchestrator.codex import CodexError, find_codex

INSTALL_GUIDE = "https://learn.chatgpt.com/docs/codex/cli"


def ensure_codex(*, interactive: bool = True) -> str:
    """Find Codex, offer installation, and verify the existing login."""
    executable = find_codex()
    if executable is None:
        if not interactive:
            raise CodexError("Run 'orchestrator setup' in a terminal to install and sign in.")
        executable = install_codex()
    typer.echo(f"Using Codex at {executable}", err=True)
    try:
        status = subprocess.run(
            [executable, "login", "status"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if status.returncode == 0:
            return executable
        if not interactive:
            raise CodexError("Codex needs sign-in. Run 'orchestrator setup' in a terminal.")
        typer.echo("Codex needs sign-in. Continue in your browser with your ChatGPT account.")
        if not typer.confirm("Open Codex sign-in now?", default=True):
            raise CodexError("Sign-in postponed. Your saved planning session can be resumed.")
        login = subprocess.run([executable, "login"], timeout=300, check=False)
        if login.returncode != 0:
            raise CodexError("Codex sign-in did not finish. Run 'orchestrator setup' to retry.")
        status = subprocess.run(
            [executable, "login", "status"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if status.returncode != 0:
            raise CodexError("Codex still needs sign-in. Run 'orchestrator setup' to retry.")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexError(
            "Could not complete Codex setup. Run 'orchestrator setup' to retry."
        ) from exc
    return executable


def install_codex() -> str:
    typer.echo(f"Codex CLI could not be found. Installation guide: {INSTALL_GUIDE}")
    npm = shutil.which("npm")
    try:
        if npm and typer.confirm("Install Codex CLI with npm now?", default=False):
            result = subprocess.run(
                [npm, "install", "--global", "@openai/codex"], timeout=300, check=False
            )
            if result.returncode != 0:
                raise CodexError(
                    f"Installation failed. Follow {INSTALL_GUIDE}, then run 'orchestrator setup'."
                )
            executable = find_codex()
            if executable:
                return executable
            # npm's configured install directory can be outside this terminal's PATH.
            prefix = subprocess.run(
                [npm, "prefix", "--global"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if prefix.returncode == 0 and prefix.stdout.strip():
                root = Path(prefix.stdout.strip())
                candidate = root / ("codex.cmd" if sys.platform == "win32" else "bin/codex")
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    os.environ["CODEX_EXECUTABLE"] = str(candidate.resolve())
                    return str(candidate.resolve())
        typer.echo("After installing, enter the full path to the Codex executable.")
        answer = typer.prompt("Codex executable path (Enter to exit)", default="").strip()
        if answer:
            candidate = Path(answer).expanduser()
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                raise CodexError(
                    "That path is not an executable file. Run 'orchestrator setup' to retry."
                )
            os.environ["CODEX_EXECUTABLE"] = str(candidate.resolve())
            return str(candidate.resolve())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexError("Installation did not finish. Run 'orchestrator setup' to retry.") from exc
    raise CodexError("Setup postponed. Install Codex, then run 'orchestrator setup'.")
