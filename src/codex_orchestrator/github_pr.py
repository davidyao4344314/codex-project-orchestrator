"""Revision-bound GitHub PR management for approved and pushed stage commits."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from codex_orchestrator import git_service as git
from codex_orchestrator import github_api, review_store, stage_branches
from codex_orchestrator.coordination_store import design_fingerprint
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.github_connection import github_repository
from codex_orchestrator.pr_description import description
from codex_orchestrator.session import SessionData
from codex_orchestrator.worker_models import artifact_digest


class PullRequest(SessionData):
    stage_id: str
    design_fingerprint: str
    repository: str
    number: int = Field(gt=0)
    base: str
    base_commit: str
    branch: str
    commit: str
    merge_commit: str | None = None
    merge_approved_at: datetime | None = None

    @property
    def url(self):
        return f"https://github.com/{self.repository}/pull/{self.number}"


class PullRequestLog(SessionData):
    records: list[PullRequest] = Field(default_factory=list)


class RemotePR(SessionData):
    number: int
    url: str
    state: Literal["OPEN", "CLOSED", "MERGED"]
    baseRefName: str
    baseRefOid: str
    headRefName: str
    headRefOid: str
    isCrossRepository: bool
    isDraft: bool
    mergeable: str
    mergeStateStatus: str
    reviewDecision: str | None
    statusCheckRollup: list[dict] | None
    autoMergeRequest: dict | None
    mergeCommit: dict | None


class MergePermission(SessionData):
    repository: str
    number: int
    base: str
    base_commit: str
    commit: str


@dataclass
class Delivery:
    state: DesignSession
    project: Path
    stage: stage_branches.StageBranch
    base: str
    receipt: git.GitPublication
    repository: str


def log_path(path):
    return path.with_suffix(".pull-requests.json")


def load_log(path):
    file = log_path(path)
    return PullRequestLog.load(file) if file.exists() else PullRequestLog()


def saved_pr(path, state, stage_id):
    return next(
        (
            r
            for r in load_log(path).records
            if r.design_fingerprint == design_fingerprint(state) and r.stage_id == stage_id
        ),
        None,
    )


def _save(path, record):
    log = load_log(path)
    log.records = [
        r
        for r in log.records
        if (r.design_fingerprint, r.stage_id) != (record.design_fingerprint, record.stage_id)
    ] + [record]
    log.save(log_path(path))


def repository_for(remote):
    try:
        return github_repository(remote)
    except ValueError:
        return None


def validate_origin(project, remote):
    repo = repository_for(remote)
    fetch = git.git(project, "remote", "get-url", "origin", optional=True)
    push = git.git(project, "remote", "get-url", "--push", "--all", "origin", optional=True)
    if not repo or repository_for(fetch) != repo or push != remote:
        raise ValueError("Fetch and reviewed push origin must identify the same GitHub repository.")
    return repo


def preflight_branch(state, path, stage_id, head):
    project = Path(state.project)
    remote = git.git(project, "remote", "get-url", "--push", "--all", "origin", optional=True)
    if remote:
        git.validate_remote(remote)
    if repository_for(remote):
        validate_origin(project, remote)
        _, revision = stage_branches.current(state, path)
        if head != revision.stages[stage_id].start_commit:
            raise ValueError(
                "The stage contains additional commits; review the full branch before publishing."
            )


def _delivery(path, stage_id):
    state, project = stage_branches.state_for(path)
    settings, revision = stage_branches.current(state, path)
    if not settings or stage_id not in revision.stages:
        raise ValueError("Prepare and publish this stage before managing its pull request.")
    item = revision.stages[stage_id]
    receipt_file = git.publication_path(path)
    if not receipt_file.exists():
        raise ValueError("Commit and push the reviewed stage before creating a pull request.")
    receipt = git.GitPublication.load(receipt_file)
    if (
        receipt.stage_id != stage_id
        or receipt.design_fingerprint != design_fingerprint(state)
        or receipt.branch != item.branch
        or not receipt.pushed
    ):
        raise ValueError("The current stage needs its exact reviewed and approved push receipt.")
    repo = validate_origin(project, receipt.remote)
    approval = review_store.require_approval(
        path, receipt.approval_id, receipt.reviewed_fingerprint or ""
    )
    if approval.source != "user" or artifact_digest(approval) != receipt.approval_digest:
        raise ValueError("A recorded user approval is required for this GitHub delivery.")
    return Delivery(state, project, item, settings.base, receipt, repo)


def _validate_open_delivery(path, delivery):
    git.validate_publication(path, delivery.receipt)
    if not validate_stage_commit(delivery.project, delivery.receipt, delivery.stage.start_commit):
        raise ValueError("This stage has no commits to publish as a pull request.")


def validate_stage_commit(project, receipt, base):
    """Reject extra commits or files before pushing a managed GitHub stage."""
    if receipt.commit == base:
        if receipt.paths:
            raise ValueError("The stage commit does not contain its reviewed paths.")
        return False
    parents = git.git(project, "rev-list", "--parents", "-n", "1", receipt.commit).split()
    if parents != [receipt.commit, base]:
        raise ValueError("The stage contains additional commits outside the reviewed publication.")
    changed = set(
        git.git(project, "diff", "--name-only", "--no-renames", "-z", base, receipt.commit).split(
            "\0"
        )
    ) - {""}
    if changed != set(receipt.paths):
        raise ValueError("The published stage differs from the reviewed file set.")
    return True


def view(record):
    result = github_api.request(
        "pr",
        "view",
        str(record.number),
        "--repo",
        f"github.com/{record.repository}",
        "--json",
        ",".join(RemotePR.model_fields),
    )
    remote = RemotePR.model_validate(result)
    if (
        remote.number != record.number
        or remote.url.lower() != record.url.lower()
        or remote.isCrossRepository
        or remote.baseRefName != record.base
        or remote.headRefName != record.branch
        or remote.headRefOid != record.commit
    ):
        raise ValueError(
            "The GitHub pull request no longer matches the approved branch and commit."
        )
    return remote


def readiness(record, remote):
    if remote.state == "MERGED":
        return []
    reasons = []
    if record.merge_commit:
        reasons.append("A merge receipt is saved; wait for GitHub to confirm the merged state.")
    if remote.state != "OPEN" or remote.isDraft:
        reasons.append("Pull request must be open and ready for review.")
    if remote.baseRefOid != record.base_commit:
        reasons.append(
            "Integration base changed since the stage started; re-review before merging."
        )
    if remote.mergeable != "MERGEABLE" or remote.mergeStateStatus != "CLEAN":
        reasons.append(f"GitHub merge state: {remote.mergeable} / {remote.mergeStateStatus}.")
    if remote.reviewDecision not in {None, "", "APPROVED"}:
        reasons.append(f"GitHub review: {remote.reviewDecision}.")
    if remote.autoMergeRequest is not None:
        reasons.append("An automatic merge is already configured on GitHub.")
    if remote.statusCheckRollup is None:
        reasons.append("GitHub check results are unavailable.")
    for check in remote.statusCheckRollup or []:
        if check.get("__typename") == "CheckRun":
            passed = check.get("status") == "COMPLETED" and check.get("conclusion") in {
                "SUCCESS",
                "NEUTRAL",
                "SKIPPED",
            }
        else:
            passed = check.get("state") == "SUCCESS"
        if not passed:
            name = check.get("name") or check.get("context") or "unknown"
            reasons.append(f"GitHub check pending or unsuccessful: {name}")
    return reasons


def _matches_delivery(record, delivery):
    return (
        record.repository == delivery.repository
        and record.commit == delivery.receipt.commit
        and record.branch == delivery.receipt.branch
        and record.base == delivery.base
        and record.base_commit == delivery.stage.start_commit
    )


def preview_description(path, stage_id):
    delivery = _delivery(path, stage_id)
    _validate_open_delivery(path, delivery)
    return description(delivery.state, path, stage_id, delivery.receipt.paths)


def create(path, stage_id, *, approved_description=None):
    delivery = _delivery(path, stage_id)
    with git.writer(delivery.project):
        delivery = _delivery(path, stage_id)
        existing = saved_pr(path, delivery.state, stage_id)
        if existing:
            if not _matches_delivery(existing, delivery):
                raise ValueError("Saved PR differs from the currently approved delivery.")
            view(existing)
            return existing
        _validate_open_delivery(path, delivery)
        receipt = delivery.receipt
        actual = git.git(
            delivery.project,
            "ls-remote",
            "--heads",
            "--",
            receipt.remote,
            f"refs/heads/{receipt.branch}",
        )
        if actual != f"{receipt.commit}\trefs/heads/{receipt.branch}":
            raise ValueError("Remote branch does not contain the exact approved commit.")
        candidates = github_api.request(
            "pr",
            "list",
            "--repo",
            f"github.com/{delivery.repository}",
            "--head",
            receipt.branch,
            "--base",
            delivery.base,
            "--state",
            "all",
            "--limit",
            "100",
            "--json",
            "number",
        )
        if not isinstance(candidates, list) or len(candidates) > 1:
            raise ValueError("Multiple or unknown PR matches; inspect GitHub before continuing.")
        if candidates:
            response = candidates[0]
        else:
            title, body = description(delivery.state, path, stage_id, receipt.paths)
            if approved_description != (title, body):
                raise ValueError(
                    "Review and approve the current PR description before creating it."
                )
            response = github_api.request(
                "api",
                "--hostname",
                "github.com",
                "--method",
                "POST",
                f"repos/{delivery.repository}/pulls",
                "--input",
                "-",
                body={
                    "title": title,
                    "body": body,
                    "head": receipt.branch,
                    "base": delivery.base,
                    "maintainer_can_modify": False,
                },
            )
        number = response.get("number") if isinstance(response, dict) else None
        if type(number) is not int or number <= 0:
            raise ValueError("GitHub did not return a valid pull request number.")
        record = PullRequest(
            stage_id=stage_id,
            design_fingerprint=receipt.design_fingerprint,
            repository=delivery.repository,
            number=number,
            base=delivery.base,
            base_commit=delivery.stage.start_commit,
            branch=receipt.branch,
            commit=receipt.commit,
        )
        view(record)
        _save(path, record)
        return record


def status(path, stage_id):
    state = DesignSession.load(path)
    record = saved_pr(path, state, stage_id)
    if record is None:
        raise ValueError("No recorded pull request. Run the PR create command first.")
    return record, view(record)


def permission_for(record):
    return MergePermission(
        repository=record.repository,
        number=record.number,
        base=record.base,
        base_commit=record.base_commit,
        commit=record.commit,
    )


def _merge_sha(remote):
    sha = (remote.mergeCommit or {}).get("oid")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("GitHub has not reported a valid merge commit yet.")
    return sha


def merge(path, stage_id, permission=None):
    delivery = _delivery(path, stage_id)
    with git.writer(delivery.project):
        delivery = _delivery(path, stage_id)
        record, remote = status(path, stage_id)
        if not _matches_delivery(record, delivery):
            raise ValueError("Saved PR differs from the currently approved delivery.")
        if remote.state != "MERGED":
            _validate_open_delivery(path, delivery)
            stage_branches.clean(delivery.project)
            if reasons := readiness(record, remote):
                raise ValueError("Cannot merge. " + " ".join(reasons))
            if permission != permission_for(record):
                raise ValueError("Explicit user approval of this PR and commit is required.")
            record.merge_approved_at = datetime.now(UTC)
            _save(path, record)
            result = github_api.request(
                "api",
                "--hostname",
                "github.com",
                "--method",
                "PUT",
                f"repos/{record.repository}/pulls/{record.number}/merge",
                "--input",
                "-",
                body={"sha": record.commit, "merge_method": "merge"},
            )
            sha = (
                result.get("sha")
                if isinstance(result, dict) and result.get("merged") is True
                else None
            )
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ValueError("GitHub did not confirm a merge. Check PR status before retrying.")
            record.merge_commit = sha
            _save(path, record)
            remote = view(record)
            if remote.state != "MERGED" or _merge_sha(remote) != sha:
                raise ValueError(
                    "GitHub merge confirmation is pending. Check PR status and resume."
                )
        else:
            sha = _merge_sha(remote)
            if record.merge_commit and record.merge_commit != sha:
                raise ValueError("GitHub merge commit differs from the saved receipt.")
            record.merge_commit = sha
            _save(path, record)
    _synchronize(path, delivery, record)
    return record


def _synchronize(path, delivery, record):
    """Fetch the approved merge, prove ancestry, and record normal stage acceptance."""
    if not delivery.stage.merged_commit or not stage_branches.ancestor(
        delivery.project, record.merge_commit, f"refs/heads/{record.base}"
    ):
        stage_branches.refresh_base(path)
    parents = git.git(
        delivery.project, "rev-list", "--parents", "-n", "1", record.merge_commit
    ).split()
    if parents != [record.merge_commit, record.base_commit, record.commit] and not (
        record.merge_commit == record.commit and parents == [record.commit, record.base_commit]
    ):
        raise ValueError("GitHub merge ancestry differs from the reviewed stage and base.")
    if not stage_branches.ancestor(
        delivery.project, record.merge_commit, f"refs/heads/{record.base}"
    ):
        raise ValueError("The confirmed GitHub merge is not on the local integration base yet.")
    if not delivery.stage.merged_commit:
        stage_branches.record_merge(path, record.stage_id)
    _record_history(path, delivery, record)


def _record_history(path, delivery, record):
    from codex_orchestrator.cycle_store import PullRequestDelivery, find_history

    source = Path(delivery.state.source_file)
    with git.writer(delivery.project):
        found = find_history(source)
        if not found:
            return
        file, history = found
        cycle = history.cycles[-1]
        if cycle.session_name != source.name or cycle.status != "active":
            raise ValueError("This delivery cycle is no longer active.")
        entry = PullRequestDelivery(
            session_name=source.name,
            stage_id=record.stage_id,
            repository=record.repository,
            number=record.number,
            base=record.base,
            commit=record.commit,
            merge_commit=record.merge_commit,
        )
        existing = next(
            (
                item
                for item in cycle.pull_requests
                if (item.session_name, item.stage_id) == (entry.session_name, entry.stage_id)
            ),
            None,
        )
        if existing and existing != entry:
            raise ValueError("Cycle history contains a different PR for this stage.")
        if existing is None:
            cycle.pull_requests.append(entry)
            history.save(file)
