from pathlib import Path

import pytest
from test_worker_execution import launch

from codex_orchestrator import coordinator, execution
from codex_orchestrator import git_service as service


@pytest.fixture
def accepted(approved_session, technical_payload, monkeypatch):
    state, path, run, folder, _ = launch(
        approved_session, technical_payload, monkeypatch, "general"
    )
    project = Path(state.project)
    service.git(project, "config", "user.email", "test@example.invalid")
    service.git(project, "config", "user.name", "Test")
    service.connect(project, "https://example.invalid/user/test.git")
    execution.verify_run(path, run.id)
    coordinator.accept_stage(path, "core", "Reviewed and tested")
    return project, path


@pytest.mark.parametrize(
    "url",
    [
        "https://token@example.com/repo.git",
        "https://example.com/a?token=secret",
        "file:///tmp/repo",
        "ext::command",
        "-bad",
        "ssh://user:secret@example.com/repo",
    ],
)
def test_remote_rejects_credentials_and_other_transports(url):
    with pytest.raises(ValueError):
        service.validate_remote(url)


def test_initialize_connect_and_writer_lock(tmp_path):
    service.initialize(tmp_path)
    assert service.repository(tmp_path)
    service.connect(tmp_path, "git@example.invalid:user/project.git")
    assert "git@example.invalid" in service.git(tmp_path, "remote", "get-url", "origin")
    with pytest.raises(ValueError, match="already connected"):
        service.connect(tmp_path, "https://example.invalid/other.git")
    with service.writer(tmp_path):
        with pytest.raises(ValueError, match="active"):
            service.connect(tmp_path, "https://example.invalid/other.git")
    assert not execution.is_busy(tmp_path)


def test_commit_only_reviewed_stage_files_and_push_exact_commit(accepted, monkeypatch):
    project, path = accepted
    reviewed = service.preview(path, "core")
    assert reviewed.paths == ["counter.py"] and "print(5)" in reviewed.diff
    record = service.commit(path, reviewed, "Implement counter")
    assert service.git(project, "show", "--pretty=", "--name-only", "HEAD") == "counter.py"
    assert not service.GitPublication.load(service.publication_path(path)).pushed
    actual_git = service.git
    pushes = []

    def fake_push(project, *args, **kwargs):
        if "push" in args:
            pushes.append(args)
            return ""
        return actual_git(project, *args, **kwargs)

    monkeypatch.setattr(service, "git", fake_push)
    assert service.push(path).pushed
    assert pushes == [
        (
            "-c",
            "push.followTags=false",
            "push",
            "--",
            record.remote,
            f"{record.commit}:refs/heads/{record.branch}",
        )
    ]


def test_staged_unrelated_files_block_commit(accepted):
    project, path = accepted
    reviewed = service.preview(path, "core")
    (project / "unrelated.txt").write_text("User edits")
    service.git(project, "add", "unrelated.txt")
    with pytest.raises(ValueError, match="staged changes"):
        service.commit(path, reviewed, "Implement counter")


def test_modified_source_blocks_commit_and_releases_lock(accepted):
    project, path = accepted
    reviewed = service.preview(path, "core")
    (project / "counter.py").write_text("changed after review")
    with pytest.raises(ValueError, match="changed after execution"):
        service.commit(path, reviewed, "Implement counter")
    assert not execution.is_busy(project)


def test_changed_remote_rejects_stale_preview(accepted):
    project, path = accepted
    reviewed = service.preview(path, "core")
    service.git(project, "remote", "set-url", "origin", "https://example.invalid/other.git")
    with pytest.raises(ValueError, match="new preview"):
        service.commit(path, reviewed, "Implement counter")


def test_push_failure_keeps_commit_for_retry(accepted, monkeypatch):
    _, path = accepted
    record = service.commit(path, service.preview(path, "core"), "Implement counter")
    actual_git = service.git

    def fail_push(project, *args, **kwargs):
        if "push" in args:
            raise ValueError("Authentication unavailable")
        return actual_git(project, *args, **kwargs)

    monkeypatch.setattr(service, "git", fail_push)
    with pytest.raises(ValueError, match="Authentication"):
        service.push(path)
    saved = service.GitPublication.load(service.publication_path(path))
    assert not saved.pushed and saved.commit == record.commit


def test_stage_without_acceptance_cannot_publish(accepted):
    _, path = accepted
    from codex_orchestrator.coordination_store import checkpoint_path
    from codex_orchestrator.design_models import DesignSession

    checkpoint_path(DesignSession.load(path), path).unlink()
    with pytest.raises(ValueError, match="Accept this delivery stage"):
        service.preview(path, "core")


def test_dashboard_git_panel_has_no_write_on_refresh(accepted, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from codex_orchestrator import web

    project, path = accepted
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(path.parent))
    monkeypatch.setattr(service, "commit", lambda *a: pytest.fail("Unexpected commit"))
    monkeypatch.setattr(service, "push", lambda *a: pytest.fail("Unexpected push"))
    app = AppTest.from_file(web.__file__).run()
    next(b for b in app.button if b.label == "Open dashboard").click().run()
    assert not app.exception
    assert any(e.label == "Git connection" for e in app.expander)
    next(b for b in app.button if b.label == "Preview accepted stage changes").click().run()
    assert not app.exception
    assert any("print(5)" in c.value for c in app.code)
    app.run()
    assert not app.exception
    assert not service.git(project, "rev-parse", "--verify", "HEAD", optional=True)


def test_new_commit_after_receipt_cannot_be_pushed_as_reviewed(accepted):
    project, path = accepted
    service.commit(path, service.preview(path, "core"), "Implement counter")
    service.git(project, "commit", "--allow-empty", "-m", "Later commit")
    with pytest.raises(ValueError, match="HEAD, branch or origin changed"):
        service.push(path)
