"""Inspect and connect a project's GitHub origin through standard Git and gh."""

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codex_orchestrator import git_service


@dataclass(frozen=True)
class Connection:
    repository: bool
    branch: str | None = None
    origin_present: bool = False
    github_repository: str | None = None
    gh_installed: bool = False
    authenticated: bool = False
    repository_access: bool = False


def github_repository(url: str) -> str:
    url = git_service.validate_remote(url)
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:)"
        r"([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?",
        url,
    )
    if not match or match[2] in {".", ".."}:
        raise ValueError("Enter a GitHub HTTPS or SSH repository URL without credentials.")
    return f"{match[1]}/{match[2]}"


def gh_ok(executable: str, *args: str) -> bool:
    try:
        return (
            subprocess.run(
                [executable, *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def inspect(project: Path) -> Connection:
    if not project.is_dir():
        raise ValueError("Choose an existing project folder.")
    repo = git_service.repository(project)
    if not repo:
        return Connection(repository=False)
    branch = git_service.git(project, "symbolic-ref", "--short", "HEAD", optional=True)
    fetch = git_service.git(project, "remote", "get-url", "origin", optional=True)
    origin = git_service.git(
        project, "remote", "get-url", "--push", "--all", "origin", optional=True
    )
    name = None
    if fetch and origin:
        try:
            if github_repository(fetch) == github_repository(origin):
                name = github_repository(origin)
        except ValueError:
            pass
    executable = shutil.which("gh")
    authenticated = bool(
        executable
        and name
        and gh_ok(
            executable, "auth", "status", "--hostname", "github.com", "--active", "--no-refresh"
        )
    )
    access = bool(
        authenticated
        and name
        and gh_ok(executable, "repo", "view", f"github.com/{name}", "--json", "nameWithOwner")
    )
    return Connection(
        repo, branch or None, bool(fetch or origin), name, bool(executable), authenticated, access
    )


def connect(project: Path, url: str) -> None:
    github_repository(url)
    if not git_service.repository(project):
        git_service.initialize(project)
    git_service.connect(project, url)


def login() -> bool:
    executable = shutil.which("gh")
    if not executable:
        return False
    # The user completes GitHub's own interactive authorization; no credentials are captured.
    return (
        subprocess.run(
            [executable, "auth", "login", "--hostname", "github.com", "--web"],
            check=False,
        ).returncode
        == 0
    )
