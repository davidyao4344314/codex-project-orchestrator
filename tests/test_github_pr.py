from pathlib import Path

import pytest
from test_git_service import accepted as accepted
from test_stage_branch_services import project as project
from typer.testing import CliRunner

from codex_orchestrator import cycle_manager, git_service, github_api, github_pr, stage_branches
from codex_orchestrator.cli import app
from codex_orchestrator.cycle_store import find_history
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.run_store import latest_runs

REMOTE = "https://github.com/owner/repo.git"


class FakeGitHub:
    def __init__(self, root, path, git):
        self.root, self.path, self.git = root, path, git
        self.created = False
        self.posts = self.merges = 0
        self.pushes = 0
        self.overrides = {}
        self.merge_commit = None
        self.fail_create = False
        self.last_body = None

    def receipt(self):
        return git_service.GitPublication.load(git_service.publication_path(self.path))

    def stage(self):
        state = DesignSession.load(self.path)
        return stage_branches.current(state, self.path)[1].stages["core"]

    def request(self, *args, body=None):
        receipt = self.receipt()
        if args[:2] == ("pr", "list"):
            return [{"number": 7}] if self.created else []
        if args[:2] == ("pr", "view"):
            assert self.created
            return {
                "number": 7,
                "url": "https://github.com/owner/repo/pull/7",
                "state": "MERGED" if self.merge_commit else "OPEN",
                "baseRefName": "main",
                "baseRefOid": self.stage().start_commit,
                "headRefName": receipt.branch,
                "headRefOid": receipt.commit,
                "isCrossRepository": False,
                "isDraft": False,
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "reviewDecision": "APPROVED",
                "statusCheckRollup": [
                    {
                        "__typename": "CheckRun",
                        "name": "tests",
                        "status": "COMPLETED",
                        "conclusion": "SUCCESS",
                    }
                ],
                "autoMergeRequest": None,
                "mergeCommit": {"oid": self.merge_commit} if self.merge_commit else None,
                **self.overrides,
            }
        if "POST" in args:
            self.posts += 1
            self.created = True
            self.last_body = body
            assert body["head"] == receipt.branch and body["base"] == "main"
            if self.fail_create:
                raise ValueError("Connection interrupted after creating PR")
            return {"number": 7}
        if "PUT" in args:
            self.merges += 1
            assert body == {"sha": receipt.commit, "merge_method": "merge"}
            tree = self.git(self.root, "rev-parse", f"{receipt.commit}^{{tree}}")
            self.merge_commit = self.git(
                self.root,
                "commit-tree",
                tree,
                "-p",
                self.stage().start_commit,
                "-p",
                receipt.commit,
                "-m",
                "Merge approved PR",
            )
            return {"merged": True, "sha": self.merge_commit}
        pytest.fail(f"Unexpected GitHub request: {args}")

    def refresh(self, path):
        assert path == self.path and self.merge_commit
        self.git(self.root, "switch", "main")
        self.git(self.root, "merge", "--ff-only", self.merge_commit)


@pytest.fixture
def connected(project, monkeypatch):
    root, path = project
    git_service.git(root, "remote", "set-url", "origin", REMOTE)
    source = Path(DesignSession.load(path).source_file)
    cycle_manager.register(source)
    actual_git = git_service.git
    remote = FakeGitHub(root, path, actual_git)

    def git(root, *args, **kwargs):
        if "push" in args and args[0] == "-c":
            remote.pushes += 1
            return ""
        if args[0] == "ls-remote":
            receipt = remote.receipt()
            return f"{receipt.commit}\trefs/heads/{receipt.branch}"
        return actual_git(root, *args, **kwargs)

    monkeypatch.setattr(git_service, "git", git)
    monkeypatch.setattr(github_api, "request", remote.request)
    monkeypatch.setattr(stage_branches, "refresh_base", remote.refresh)
    return root, path, source, remote


def feature(source):
    return CliRunner().invoke(
        app, ["cycle", "feature", str(source), "--stage", "core"], input="Tested counter\ny\n"
    )


def pr(source, action, answer=""):
    return CliRunner().invoke(
        app, ["cycle", "pr", str(source), action, "--stage", "core"], input=answer
    )


def test_approved_feature_creates_pr_and_merges_only_after_consent(connected):
    root, path, source, remote = connected
    main_before = git_service.git(root, "rev-parse", "main")
    result = feature(source)
    assert result.exit_code == 0, result.output
    assert "PR #7" in result.output and remote.posts == 1
    assert "Tested counter" in remote.last_body["body"]
    assert "C1: passed" in remote.last_body["body"]
    assert git_service.git(root, "rev-parse", "main") == main_before
    cycle_status = CliRunner().invoke(app, ["cycle", "status", str(source)])
    assert cycle_status.exit_code == 0 and "cycle pr" in cycle_status.output
    assert pr(source, "create").exit_code == 0
    assert remote.posts == 1
    result = pr(source, "status")
    assert result.exit_code == 0 and "Ready for explicit merge approval" in result.output
    with pytest.raises(ValueError, match="Explicit user approval"):
        github_pr.merge(path, "core")
    assert pr(source, "merge", "n\n").exit_code == 0 and remote.merges == 0
    result = pr(source, "merge", "y\n")
    assert result.exit_code == 0, result.output
    assert remote.merges == 1
    assert git_service.git(root, "rev-parse", "main") == remote.merge_commit
    stage = stage_branches.current(DesignSession.load(path), path)[1].stages["core"]
    assert stage.merged_commit == remote.receipt().commit
    entry = find_history(source)[1].cycles[-1].pull_requests[0]
    assert entry.number == 7 and entry.merge_commit == remote.merge_commit
    assert pr(source, "merge").exit_code == 0 and remote.merges == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"mergeStateStatus": "BLOCKED"},
        {
            "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "tests", "status": "IN_PROGRESS"}
            ]
        },
        {"headRefOid": "a" * 40},
        {"baseRefOid": "a" * 40},
        {"isCrossRepository": True},
        {"reviewDecision": "CHANGES_REQUESTED"},
    ],
)
def test_pr_merge_blockers_prevent_mutation(connected, overrides):
    _, _, source, remote = connected
    result = feature(source)
    assert result.exit_code == 0, result.output
    remote.overrides.update(overrides)
    result = pr(source, "merge", "y\n")
    assert result.exit_code == 1, result.output
    assert remote.merges == 0


def test_create_recovers_after_interrupted_github_response(connected):
    _, path, source, remote = connected
    remote.fail_create = True
    result = feature(source)
    assert result.exit_code == 1 and "approved push was saved" in result.output
    assert remote.posts == 1 and remote.receipt().pushed
    runs_before = latest_runs(path, 1)
    result = pr(source, "create", "y\n")
    assert result.exit_code == 0, result.output
    assert remote.posts == 1
    assert latest_runs(path, 1) == runs_before


def test_merge_recovers_after_remote_merge_and_local_sync_failure(connected, monkeypatch):
    _, path, source, remote = connected
    result = feature(source)
    assert result.exit_code == 0, result.output

    def fail(_):
        raise ValueError("Fetch unavailable")

    monkeypatch.setattr(stage_branches, "refresh_base", fail)
    result = pr(source, "merge", "y\n")
    assert result.exit_code == 1 and remote.merges == 1
    assert github_pr.saved_pr(path, DesignSession.load(path), "core").merge_commit
    monkeypatch.setattr(stage_branches, "refresh_base", remote.refresh)
    result = pr(source, "merge")
    assert result.exit_code == 0, result.output
    assert remote.merges == 1


def test_unpublished_stage_cannot_create_pr(connected):
    _, _, source, remote = connected
    result = pr(source, "create", "y\n")
    assert result.exit_code == 1 and remote.posts == 0


def test_declining_feature_publication_does_not_push_or_create_pr(connected):
    _, path, source, remote = connected
    result = CliRunner().invoke(
        app, ["cycle", "feature", str(source), "--stage", "core"], input="Tested counter\nn\n"
    )
    assert result.exit_code == 0, result.output
    assert remote.pushes == remote.posts == 0
    assert not git_service.publication_path(path).exists()


def test_extra_stage_commit_is_not_pushed_as_reviewed_work(connected):
    root, path, source, remote = connected
    stage_branches.prepare(path, "core")
    (root / "outside.txt").write_text("Unreviewed change")
    git_service.git(root, "add", "outside.txt")
    git_service.git(root, "commit", "-m", "Unreviewed extra change")
    before = latest_runs(path, 1)
    result = feature(source)
    assert result.exit_code == 1 and "additional commits" in result.output
    assert remote.pushes == remote.posts == 0
    assert latest_runs(path, 1) == before


def test_rechecks_github_state_after_user_confirmation(connected, monkeypatch):
    from codex_orchestrator import github_pr_cli

    _, _, source, remote = connected
    assert feature(source).exit_code == 0

    def confirm(*args, **kwargs):
        remote.overrides["headRefOid"] = "a" * 40
        return True

    monkeypatch.setattr(github_pr_cli.typer, "confirm", confirm)
    result = pr(source, "merge")
    assert result.exit_code == 1 and remote.merges == 0


def test_no_ci_is_reported_to_the_user(connected):
    _, _, source, remote = connected
    assert feature(source).exit_code == 0
    remote.overrides["statusCheckRollup"] = []
    result = pr(source, "status")
    assert result.exit_code == 0
    assert "No GitHub CI checks reported" in result.output
    assert remote.merges == 0


def test_pr_description_rejects_recognizable_credentials():
    from codex_orchestrator.pr_description import _line

    with pytest.raises(ValueError, match="credentials"):
        _line("Manual test used ghp_" + "a" * 30)


def test_github_transport_sends_json_without_interactive_auth(monkeypatch):
    import json
    from types import SimpleNamespace

    monkeypatch.setattr(github_api.shutil, "which", lambda _: "/fake/gh")

    def run(command, **kwargs):
        assert command == ["/fake/gh", "api", "--hostname", "github.com", "--input", "-"]
        assert json.loads(kwargs["input"]) == {"sha": "approved"}
        assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
        kwargs["stdout"].write(b'{"merged": true}')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(github_api.subprocess, "run", run)
    assert github_api.request(
        "api", "--hostname", "github.com", "--input", "-", body={"sha": "approved"}
    ) == {"merged": True}
