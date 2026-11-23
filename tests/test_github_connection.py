import pytest
from typer.testing import CliRunner

from codex_orchestrator import git_service, github_connection
from codex_orchestrator.cli import app


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo.git",
        "git@github.com:owner/repo.git",
    ],
)
def test_supported_github_origins(url):
    assert github_connection.github_repository(url) == "owner/repo"


@pytest.mark.parametrize(
    "url",
    [
        "https://token@github.com/owner/repo",
        "https://example.com/owner/repo",
        "https://github.com/a/b/c",
    ],
)
def test_invalid_origins_rejected(url):
    with pytest.raises(ValueError):
        github_connection.github_repository(url)


def test_connect_existing_repository_and_validate_access(tmp_path, monkeypatch):
    monkeypatch.setattr(github_connection.shutil, "which", lambda _: "/fake/gh")
    calls = []

    def ok(executable, *args):
        calls.append(args)
        return True

    monkeypatch.setattr(github_connection, "gh_ok", ok)
    result = CliRunner().invoke(
        app,
        ["cycle", "github", str(tmp_path)],
        input="y\nhttps://github.com/owner/repo.git\n",
    )
    assert result.exit_code == 0, result.output
    assert "authenticated and repository accessible" in result.output
    assert git_service.repository(tmp_path)
    assert (
        git_service.git(tmp_path, "remote", "get-url", "origin")
        == "https://github.com/owner/repo.git"
    )
    assert calls == [
        ("auth", "status", "--hostname", "github.com", "--active", "--no-refresh"),
        ("repo", "view", "github.com/owner/repo", "--json", "nameWithOwner"),
    ]


def test_status_does_not_initialize_or_sign_in(tmp_path, monkeypatch):
    monkeypatch.setattr(github_connection, "login", lambda: pytest.fail("unexpected login"))
    result = CliRunner().invoke(app, ["cycle", "github", str(tmp_path), "--status-only"])
    assert result.exit_code == 0, result.output
    assert "GitHub is not connected" in result.output
    assert not git_service.repository(tmp_path)


def test_local_cycle_skips_github_check(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        github_connection, "inspect", lambda _: pytest.fail("unexpected connection check")
    )
    result = CliRunner().invoke(
        app,
        ["cycle", "new", str(project), "--local", "--directory", str(tmp_path / "plans")],
        input="Build a counter\n",
    )
    assert result.exit_code == 0, result.output
    assert "Cycle 001 session:" in result.output


def test_existing_non_github_remote_preserved(tmp_path):
    git_service.initialize(tmp_path)
    git_service.connect(tmp_path, "https://example.invalid/owner/repo.git")
    result = CliRunner().invoke(app, ["cycle", "github", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "leaving it unchanged" in result.output
    assert (
        git_service.git(tmp_path, "remote", "get-url", "origin")
        == "https://example.invalid/owner/repo.git"
    )


def test_optional_sign_in(tmp_path, monkeypatch):
    github_connection.connect(tmp_path, "https://github.com/owner/repo.git")
    monkeypatch.setattr(github_connection.shutil, "which", lambda _: "/fake/gh")
    signed_in = []
    monkeypatch.setattr(github_connection, "gh_ok", lambda *args: bool(signed_in))
    monkeypatch.setattr(github_connection, "login", lambda: signed_in.append(True))
    result = CliRunner().invoke(app, ["cycle", "github", str(tmp_path)], input="y\n")
    assert result.exit_code == 0, result.output
    assert len(signed_in) == 1
    assert "authenticated and repository accessible" in result.output


def test_connected_project_starts_cycle_without_connection_prompts(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    github_connection.connect(project, "https://github.com/owner/repo.git")
    monkeypatch.setattr(github_connection.shutil, "which", lambda _: "/fake/gh")
    monkeypatch.setattr(github_connection, "gh_ok", lambda *args: True)
    result = CliRunner().invoke(
        app,
        [
            "cycle",
            "new",
            str(project),
            "--goal",
            "Build a counter",
            "--directory",
            str(tmp_path / "plans"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "GitHub connected" in result.output
    assert "Cycle 001 session:" in result.output
    assert "Connect an existing GitHub repository now?" not in result.output
