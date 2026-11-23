"""Explicit Git connection and publication of reviewed stage files."""

import hashlib
import json
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryFile
from urllib.parse import urlsplit

from codex_orchestrator import project_activity
from codex_orchestrator import review_store as reviews
from codex_orchestrator.context_files import is_private_path, sensitive_text
from codex_orchestrator.coordination_store import design_fingerprint, stage_accepted
from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.execution import project_lock
from codex_orchestrator.run_store import latest_runs, run_directory
from codex_orchestrator.session import SessionData
from codex_orchestrator.stage_verification import check_stage_verification
from codex_orchestrator.worker_audit import _file_state, _status
from codex_orchestrator.worker_models import WorkerEvidence, artifact_digest


class GitPreview(SessionData):
    stage_id: str
    paths: list[str]
    head: str | None
    branch: str
    remote: str
    fingerprint: str
    diff: str


class PushPermission(SessionData):
    commit: str
    branch: str
    remote: str


class GitPublication(SessionData):
    design_fingerprint: str
    paths: list[str]
    stage_id: str
    commit: str
    branch: str
    remote: str
    pushed: bool = False
    approval_id: str | None = None
    approval_digest: str | None = None
    reviewed_fingerprint: str | None = None
    evidence_digest: str | None = None
    reviewer_id: str | None = None
    reviewer_digest: str | None = None


def git(project: Path, *args: str, optional=False) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LITERAL_PATHSPECS="1")
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    with TemporaryFile() as output, TemporaryFile() as errors:
        try:
            result = subprocess.run(
                ["git", "-C", str(project), *args],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=errors,
                env=env,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Git timed out. Check the repository and your connection.") from exc
        if result.returncode:
            if optional:
                return ""
            # Git errors can echo a credential-bearing URL; keep them out of the UI.
            raise ValueError(
                "Git failed. Check repository permissions, Git identity and "
                "authentication in your terminal. No force push was attempted."
            )
        if output.tell() > 1024 * 1024:
            raise ValueError("Git output exceeds 1 MiB; narrow or clean the repository.")
        output.seek(0)
        return output.read().decode("utf-8", errors="replace").rstrip("\n")


def repository(project: Path) -> bool:
    root = git(project, "rev-parse", "--show-toplevel", optional=True)
    return bool(root) and Path(root).resolve() == project.resolve()


@contextmanager
def writer(project: Path):
    try:
        lease = project_activity.create(project, "git/review")
    except FileExistsError as exc:
        raise ValueError("Wait for the active project operation to finish.") from exc
    try:
        yield
    finally:
        project_activity.release(project, lease)


def initialize(project: Path):
    with writer(project):
        if git(project, "rev-parse", "--show-toplevel", optional=True):
            raise ValueError("This folder already belongs to a repository; select its root.")
        git(project, "init", "-b", "main")


def validate_remote(url: str) -> str:
    url = url.strip()
    parsed = urlsplit(url)
    https = (
        parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
    )
    ssh = bool(re.fullmatch(r"git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+", url))
    if not (https or ssh) or parsed.query or parsed.fragment or any(c.isspace() for c in url):
        raise ValueError("Use an HTTPS URL without credentials or git@host:owner/repository.git.")
    return url


def connect(project: Path, url: str):
    url = validate_remote(url)
    with writer(project):
        if not repository(project):
            raise ValueError("Initialize a repository at this project's root first.")
        current = git(project, "remote", "get-url", "origin", optional=True)
        if current and current != url:
            raise ValueError("Origin is already connected. Change it explicitly in your terminal.")
        if not current:
            git(project, "remote", "add", "origin", url)


def _approved(path: Path, stage_id: str):
    state = DesignSession.load(path)
    from codex_orchestrator.cycle_manager import require_active

    require_active(Path(state.source_file))
    check_source(state)
    if state.phase != "approved" or not stage_accepted(state, path, stage_id):
        raise ValueError("Accept this delivery stage's verification before committing its work.")
    project = Path(state.project).resolve()
    if not repository(project):
        raise ValueError("Select a project whose folder is the Git repository root.")
    from codex_orchestrator.stage_branches import require_branch

    require_branch(state, path, stage_id)
    tasks = [t for t in state.revisions[-1].design.tasks if t.stage_id == stage_id]
    check_stage_verification(state, path, stage_id)
    return state, project, {p for t in tasks for p in t.owned_paths}


def review_evidence(folder: Path) -> WorkerEvidence:
    with (folder / "worker_evidence.json").open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Worker evidence exceeds the review budget.")
    result = WorkerEvidence.model_validate_json(raw)
    if result.violations:
        raise ValueError("Resolve worker evidence violations before publication.")
    return result


def evidence_digest(state: DesignSession, path: Path, stage_id: str) -> str:
    latest = latest_runs(path, len(state.revisions))
    attempts = []
    for task in state.revisions[-1].design.tasks:
        if task.stage_id != stage_id:
            continue
        run = latest[task.id]
        attempt = (
            task.id,
            run.id,
            run.worker_result_digest,
            artifact_digest(review_evidence(run_directory(path) / run.id)),
        )
        # Preserve evidence identities for already-reviewed legacy attempts.
        if run.workspace_before_digest:
            attempt += (
                run.worker_spec_digest,
                run.workspace_before_digest,
                run.workspace_after_digest,
            )
        if run.prompt_digest:
            attempt += (run.prompt_digest,)
        attempts.append(attempt)
    return hashlib.sha256(json.dumps(attempts).encode()).hexdigest()


def _preview(path: Path, stage_id: str, selected: list[str] | None = None) -> GitPreview:
    state, project, owned = _approved(path, stage_id)
    if git(project, "diff", "--cached", "--name-only"):
        raise ValueError(
            "The Git index contains staged changes. Resolve them in your terminal first."
        )
    branch = git(project, "symbolic-ref", "--quiet", "--short", "HEAD", optional=True)
    if not branch:
        raise ValueError("Check out a branch before publishing.")
    remote = git(project, "remote", "get-url", "--push", "--all", "origin", optional=True)
    if remote:
        remote = validate_remote(remote)
    candidates = sorted(_status(project) & owned)
    paths = candidates if selected is None else sorted(set(selected))
    if (selected == [] and candidates) or len(paths) > 64 or not set(paths) <= set(candidates):
        raise ValueError("Select changed files owned by this accepted stage.")
    observed = {}
    for name in paths:
        file = project / name
        if Path(name).is_absolute() or ".." in Path(name).parts or is_private_path(file):
            raise ValueError("Private or outside-project paths cannot be published here.")
        item = _file_state(project, name, True, 4 * 1024 * 1024)
        if item.marker or (item.exists and item.text is None):
            raise ValueError(
                f"Cannot review {name}: use small text files without recognizable credentials."
            )
        observed[name] = item.model_dump()
    head = git(project, "rev-parse", "--verify", "HEAD", optional=True) or None
    diff = git(project, "diff", "--no-ext-diff", "--no-textconv", "--", *paths) if paths else ""
    for name, item in observed.items():
        if item["exists"] and not git(project, "ls-files", "--", name):
            diff += f"\nNew file: {name}\n" + item["text"]
    if sensitive_text(diff):
        raise ValueError("Recognizable credentials found in the diff; review in your terminal.")
    attempts = evidence_digest(state, path, stage_id)
    from codex_orchestrator.cycle_feedback import decision

    feedback = decision(state, path, stage_id)
    identity = [design_fingerprint(state), head, branch, remote, observed, diff, attempts]
    if feedback:
        payload = feedback.model_dump(mode="json")
        # Old acceptance receipts predate delivery session identities.
        if payload.get("session_name") is None:
            payload.pop("session_name", None)
        identity.append(payload)
    fingerprint = hashlib.sha256(
        json.dumps(
            identity,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return GitPreview(
        stage_id=stage_id,
        paths=paths,
        head=head,
        branch=branch,
        remote=remote,
        fingerprint=fingerprint,
        diff=diff[:48000],
    )


def preview(path: Path, stage_id: str, selected: list[str] | None = None) -> GitPreview:
    project = Path(DesignSession.load(path).project).resolve()
    if project_lock(project).exists():
        raise ValueError("Wait for the active project operation to finish.")
    return _preview(path, stage_id, selected)


def publication_path(path: Path) -> Path:
    return path.with_suffix(".publication.json")


def approve_user(path: Path, reviewed: GitPreview) -> reviews.ReviewRecord:
    project = Path(DesignSession.load(path).project).resolve()
    with writer(project):
        current = _preview(path, reviewed.stage_id, reviewed.paths)
        if current.fingerprint != reviewed.fingerprint:
            raise ValueError("Files or Git settings changed. Build a new preview.")
        reviews.require_reviewer(path, reviewed.stage_id, reviewed.fingerprint)
        return reviews.save_review(
            path,
            reviews.ReviewRecord(
                stage_id=reviewed.stage_id,
                fingerprint=reviewed.fingerprint,
                source="user",
                result=reviews.ReviewDecision(
                    decision="approved",
                    summary="User reviewed selected changes",
                    findings=[],
                    additional_human_checks=[],
                ),
            ),
        )


def commit(
    path: Path, reviewed: GitPreview, message: str, *, approval_id: str | None = None
) -> GitPublication:
    project = Path(DesignSession.load(path).project).resolve()
    with writer(project):
        return _commit(path, reviewed, message, approval_id)


def _commit(
    path: Path, reviewed: GitPreview, message: str, approval_id: str | None
) -> GitPublication:
    if not message.strip() or len(message) > 500 or sensitive_text(message):
        raise ValueError("Enter a commit message of 1–500 characters without credentials.")
    project = Path(DesignSession.load(path).project).resolve()
    current = _preview(path, reviewed.stage_id, reviewed.paths)
    if current.fingerprint != reviewed.fingerprint:
        raise ValueError("Files or Git settings changed. Build a new preview.")
    approval = reviews.require_approval(path, approval_id, reviewed.fingerprint)
    reviewer = reviews.require_reviewer(path, reviewed.stage_id, reviewed.fingerprint)
    from codex_orchestrator.cycle_feedback import commit_message

    message, final_feedback = commit_message(
        DesignSession.load(path), path, reviewed.stage_id, message.strip()
    )
    expected_tree = git(project, "write-tree")
    if reviewed.paths:
        git(project, "add", "--", *reviewed.paths)
        expected_tree = git(project, "write-tree")
        git(project, "commit", "-m", message.strip(), "--only", "--", *reviewed.paths)
    elif final_feedback:
        git(project, "commit", "--allow-empty", "-m", message)
    if git(project, "rev-parse", "HEAD^{tree}") != expected_tree:
        raise ValueError(
            "Commit hooks changed the approved tree. Inspect the local commit before publishing."
        )
    if final_feedback and message not in git(project, "show", "-s", "--format=%B", "HEAD"):
        raise ValueError("The commit message changed; inspect the local commit before publishing.")
    record = GitPublication(
        evidence_digest=evidence_digest(DesignSession.load(path), path, reviewed.stage_id),
        reviewer_id=reviewer.id,
        reviewer_digest=artifact_digest(reviewer),
        approval_id=approval.id,
        approval_digest=artifact_digest(approval),
        reviewed_fingerprint=reviewed.fingerprint,
        design_fingerprint=design_fingerprint(DesignSession.load(path)),
        paths=reviewed.paths,
        stage_id=reviewed.stage_id,
        commit=git(project, "rev-parse", "HEAD"),
        branch=reviewed.branch,
        remote=reviewed.remote,
    )
    record.save(publication_path(path))
    return record


def push(path: Path, *, permission: PushPermission | None = None) -> GitPublication:
    project = Path(DesignSession.load(path).project).resolve()
    with writer(project):
        return _push(path, permission)


def _push(path: Path, permission: PushPermission | None = None) -> GitPublication:
    project = Path(DesignSession.load(path).project).resolve()
    record = GitPublication.load(publication_path(path))
    if not record.remote:
        raise ValueError(
            "No origin was configured when this commit was reviewed; push unavailable."
        )
    if permission is None or (permission.commit, permission.branch, permission.remote) != (
        record.commit,
        record.branch,
        record.remote,
    ):
        raise ValueError(
            "User permission is required for this exact commit, branch and remote before push."
        )
    validate_publication(path, record)
    git(
        project,
        "-c",
        "push.followTags=false",
        "push",
        "--",
        record.remote,
        f"{record.commit}:refs/heads/{record.branch}",
    )
    record.pushed = True
    record.save(publication_path(path))
    return record


def validate_publication(path: Path, record: GitPublication) -> None:
    """Read-only validation for recovery guidance; never grants push permission."""
    project = Path(DesignSession.load(path).project).resolve()
    reviewer = reviews.require_reviewer(path, record.stage_id, record.reviewed_fingerprint or "")
    if reviewer.id != record.reviewer_id or artifact_digest(reviewer) != record.reviewer_digest:
        raise ValueError("The AI review changed or is missing. Review again before pushing.")
    approval = reviews.require_approval(path, record.approval_id, record.reviewed_fingerprint or "")
    if artifact_digest(approval) != record.approval_digest:
        raise ValueError("The approval record changed. Review again before publishing.")
    state, _, _ = _approved(path, record.stage_id)
    if evidence_digest(state, path, record.stage_id) != record.evidence_digest:
        raise ValueError("Task attempts or test evidence changed. Review again before pushing.")
    if design_fingerprint(state) != record.design_fingerprint:
        raise ValueError("The design changed. Commit the newly accepted work before pushing.")
    if _status(project) & set(record.paths):
        raise ValueError("Committed files have new edits. Review and commit them before pushing.")
    if (
        git(project, "rev-parse", "HEAD") != record.commit
        or git(project, "symbolic-ref", "--short", "HEAD") != record.branch
        or git(project, "remote", "get-url", "--push", "--all", "origin", optional=True)
        != record.remote
    ):
        raise ValueError("HEAD, branch or origin changed. Review the repository in your terminal.")
