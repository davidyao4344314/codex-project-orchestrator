from test_git_service import accepted as accepted
from test_stage_branch_services import project as project
from typer.testing import CliRunner

from codex_orchestrator import git_service, review_agent, review_store, stage_branches
from codex_orchestrator.cli import app
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.run_store import latest_runs


def test_feature_delivers_local_commit_with_one_publication_confirmation(project):
    root, path = project
    git_service.git(root, "remote", "remove", "origin")
    source = DesignSession.load(path).source_file
    runner = CliRunner()
    result = runner.invoke(
        app, ["cycle", "feature", source, "--stage", "core"], input="Tested feature\ny\n"
    )
    assert result.exit_code == 0, result.output
    assert "automated checks verified" in result.output
    assert "Approve feature and commit locally?" in result.output
    run = latest_runs(path, 1)["implement"]
    assert run.verified and run.verification_source == "automated"
    receipt = git_service.GitPublication.load(git_service.publication_path(path))
    assert not receipt.remote and not receipt.pushed
    assert receipt.commit == git_service.git(root, "rev-parse", "HEAD")
    result = runner.invoke(app, ["cycle", "feature", source, "--stage", "core"])
    assert result.exit_code == 0, result.output
    assert git_service.git(root, "rev-parse", "HEAD") == receipt.commit
    git_service.git(root, "switch", "main")
    git_service.git(root, "merge", "--ff-only", receipt.branch)
    stage_branches.record_merge(path, "core")


def test_declining_feature_publication_leaves_code_uncommitted(project):
    root, path = project
    git_service.git(root, "remote", "remove", "origin")
    source = DesignSession.load(path).source_file
    result = CliRunner().invoke(
        app, ["cycle", "feature", source, "--stage", "core"], input="Tested feature\nn\n"
    )
    assert result.exit_code == 0, result.output
    assert not git_service.publication_path(path).exists()
    assert (root / "counter.py").exists()


def test_unchanged_rejected_review_is_reused(project, monkeypatch):
    root, path = project
    git_service.git(root, "remote", "remove", "origin")
    source = DesignSession.load(path).source_file
    calls = []

    def reject(*args, **kwargs):
        calls.append(1)
        return review_store.ReviewDecision(
            decision="changes_requested",
            summary="Needs correction",
            findings=[],
            additional_human_checks=[],
        )

    monkeypatch.setattr(review_agent, "codex_response", reject)
    runner = CliRunner()
    command = ["cycle", "feature", source, "--stage", "core"]
    result = runner.invoke(app, command, input="Tested feature\n")
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    assert "Needs correction" in result.output
    assert len(calls) == 1
    result = runner.invoke(app, [*command, "--retry-review"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 2
