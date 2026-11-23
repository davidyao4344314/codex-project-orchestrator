"""Local isolated task worktrees and verified integration into an uncommitted stage."""

import hashlib
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryFile

from codex_orchestrator import git_service as git
from codex_orchestrator.context_files import sensitive_text
from codex_orchestrator.worker_audit import MAX_FILE_BYTES, WorkspaceSnapshot, _file_state, _status
from codex_orchestrator.worker_runtime import file_signature


def compatible_inputs(project, spec, package):
    project = Path(project).resolve()
    if any(
        resource.kind == "skill"
        and Path(resource.reference).is_absolute()
        and Path(resource.reference).resolve().is_relative_to(project)
        for resource in package.resources
    ):
        return False
    if _status(project) & set(spec.audit_paths):
        return False
    return all(
        not (project / name).exists() or git.git(project, "ls-files", "--", name)
        for name in spec.audit_paths
    )


def prepare(project, run):
    project = Path(project).resolve()
    # Keep worktrees outside the target tree, and separate from private run artifacts.
    root = project.parent / ".orchestrator-worktrees"
    root.mkdir(exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Choose a regular worktree directory.")
    workspace = root / run.id
    if workspace.exists():
        raise ValueError("Agent worktree already exists; inspect it before retrying.")
    slug = re.sub(r"[^a-z0-9]+", "-", run.agent_id.lower()).strip("-")[:24] or "agent"
    branch = f"{run.branch}-agent-{slug}-{run.id[:12]}"
    git.git(project, "worktree", "add", "-b", branch, str(workspace), "HEAD")
    run.agent_branch = branch
    run.agent_worktree = str(workspace)
    return workspace


def _check_inputs(project, before, spec):
    for name in spec.audit_paths:
        current = _file_state(project, name, False, MAX_FILE_BYTES)
        if file_signature(current) != file_signature(before.files.get(name)):
            raise ValueError(f"Agent inputs differ from the stage: {name}.")


def _apply_patch(project, workspace, base, commit):
    with TemporaryFile() as patch, TemporaryFile() as errors:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "diff",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--no-textconv",
                base,
                commit,
            ],
            stdout=patch,
            stderr=errors,
            check=False,
            timeout=60,
        )
        if result.returncode or patch.tell() > 8 * 1024 * 1024:
            raise ValueError("Agent integration patch could not be generated within its budget.")
        patch.seek(0)
        for args in (["--check"], []):
            patch.seek(0)
            result = subprocess.run(
                ["git", "-C", str(project), "apply", "--binary", *args, "-"],
                stdin=patch,
                stdout=subprocess.DEVNULL,
                stderr=errors,
                check=False,
                timeout=60,
            )
            if result.returncode:
                raise ValueError("Agent patch could not be applied cleanly; worktree preserved.")


def integrate(project, workspace, run, spec, folder, before):
    """Keep agent commits local; apply only audited changes to the feature worktree."""
    project, workspace = Path(project).resolve(), Path(workspace).resolve()
    if (
        git.git(project, "symbolic-ref", "--short", "HEAD") != run.branch
        or git.git(project, "rev-parse", "HEAD") != before.head
        or git.git(project, "diff", "--cached", "--name-only")
    ):
        raise ValueError("Stage branch or index changed during isolated work.")
    _check_inputs(project, before, spec)
    after = WorkspaceSnapshot.load(folder / "workspace_after.json")
    changed = sorted(_status(workspace))
    if not set(changed) <= set(spec.owned_paths):
        raise ValueError("Isolated worktree has changes outside its approved ownership.")
    changes = git.review_evidence(folder).changes
    if set(changed) != {change.path for change in changes}:
        raise ValueError("Isolated worktree status differs from audited worker changes.")
    for name in changed:
        item = _file_state(workspace, name, True, MAX_FILE_BYTES)
        if file_signature(item) != file_signature(after.files.get(name)) or (
            item.exists and (item.text is None or sensitive_text(item.text))
        ):
            raise ValueError(f"Agent output cannot be safely integrated: {name}.")
    if changed:
        git.git(workspace, "add", "-A", "--", *changed)
        if git.git(workspace, "diff", "--cached", "--name-only"):
            git.git(workspace, "commit", "-m", f"Draft agent work: {run.task_id}")
        commit = git.git(workspace, "rev-parse", "HEAD")
        if (
            git.git(workspace, "symbolic-ref", "--short", "HEAD") != run.agent_branch
            or git.git(workspace, "rev-parse", f"{commit}^") != before.head
        ):
            raise ValueError("Agent draft commit changed the expected branch ancestry.")
        if _status(workspace):
            raise ValueError("Agent draft commit left unreviewed workspace edits.")
        if set(git.git(workspace, "diff", "--name-only", before.head, commit).splitlines()) != set(
            changed
        ):
            raise ValueError("Agent draft commit differs from the audited file set.")
        for name in changed:
            expected = after.files[name]
            if expected.exists:
                size = git.git(workspace, "cat-file", "-s", f"{commit}:{name}")
                if int(size) > MAX_FILE_BYTES:
                    raise ValueError("Agent draft file exceeds the audit limit.")
            content = subprocess.run(
                ["git", "-C", str(workspace), "show", f"{commit}:{name}"],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if expected.exists:
                if (
                    content.returncode
                    or hashlib.sha256(content.stdout).hexdigest() != expected.sha256
                ):
                    raise ValueError("Agent draft commit differs from its audited output.")
            elif not content.returncode:
                raise ValueError("Agent draft did not preserve the audited deletion.")
        _apply_patch(project, workspace, before.head, commit)
    else:
        commit = before.head
    _check_inputs(project, after, spec)
    run.agent_commit = commit
