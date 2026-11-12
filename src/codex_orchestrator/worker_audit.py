"""Bounded workspace observations; detects net edits without rolling them back."""

import difflib
import hashlib
import os
import stat
import subprocess
from pathlib import Path

from pydantic import Field

from codex_orchestrator.context_files import is_private_path, sensitive_text
from codex_orchestrator.session import SessionData
from codex_orchestrator.worker_models import ObservedChange, WorkerEvidence

MAX_FILES = 2000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TEXT_BYTES = 24 * 1024
MAX_DIFF_BYTES = 96 * 1024


class FileState(SessionData):
    exists: bool
    sha256: str | None = None
    marker: str | None = None
    text: str | None = None
    bytes_hashed: int = 0


class WorkspaceSnapshot(SessionData):
    mode: str
    head: str | None
    files: dict[str, FileState]
    warnings: list[str] = Field(default_factory=list)


def _git(project, *args):
    result = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, timeout=20, check=False
    )
    if len(result.stdout) > 1024 * 1024:
        raise ValueError("Git audit output exceeds its limit; narrow the working tree.")
    return result.stdout if result.returncode == 0 else None


def _status(project):
    data = _git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if data is None:
        raise ValueError("Cannot inspect Git status for worker audit.")
    records = data.split(b"\x00")
    paths, index = set(), 0
    while index < len(records):
        row = records[index]
        index += 1
        if not row:
            continue
        paths.add(os.fsdecode(row[3:]))
        if b"R" in row[:2] or b"C" in row[:2]:
            if index < len(records):
                paths.add(os.fsdecode(records[index]))
                index += 1
    return paths


def _file_state(project, name, include_text, remaining):
    file = project / name
    try:
        info = file.lstat()
        if stat.S_ISLNK(info.st_mode):
            return FileState(exists=True, marker="symlink:" + os.readlink(file))
        if not stat.S_ISREG(info.st_mode) or not file.resolve().is_relative_to(project):
            return FileState(exists=True, marker="special-or-outside")
        if is_private_path(file) or info.st_size > min(MAX_FILE_BYTES, remaining):
            return FileState(exists=True, marker=f"not-read:{info.st_size}:{info.st_mtime_ns}")
        with file.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            return FileState(exists=True, marker="grew-beyond-limit")
        text = None
        if include_text and len(raw) <= MAX_TEXT_BYTES and b"\x00" not in raw:
            try:
                decoded = raw.decode("utf-8")
                if not sensitive_text(decoded):
                    text = decoded
            except UnicodeError:
                pass
        return FileState(
            exists=True, sha256=hashlib.sha256(raw).hexdigest(), text=text, bytes_hashed=len(raw)
        )
    except FileNotFoundError:
        return FileState(exists=False)


def snapshot_workspace(project, owned_paths, artifact_dir, *, previous=None):
    project, artifact_dir = Path(project).resolve(), Path(artifact_dir).resolve()
    root = _git(project, "rev-parse", "--show-toplevel")
    git_rooted = root is not None and Path(os.fsdecode(root).strip()).resolve() == project
    paths = set(owned_paths)
    if previous:
        paths.update(previous.files)
    warnings = []
    if git_rooted:
        paths.update(_status(project))
        head_bytes = _git(project, "rev-parse", "--verify", "HEAD")
        head = head_bytes.decode().strip() if head_bytes else None
    else:
        head = None
        warnings.append("No Git root at project: only declared owned paths can be audited.")
    paths = {p for p in paths if p and not (project / p).is_relative_to(artifact_dir)}
    if len(paths) > MAX_FILES:
        raise ValueError(
            "Too many changed files for worker audit; clean or narrow the project first."
        )
    files = {}
    remaining = MAX_TOTAL_BYTES
    for p in sorted(paths, key=lambda p: (p not in owned_paths, p)):
        files[p] = _file_state(project, p, p in owned_paths, remaining)
        remaining -= files[p].bytes_hashed
    if any(f.marker for p, f in files.items() if p in owned_paths):
        warnings.append("Some owned file contents could not be fully audited.")
    return WorkspaceSnapshot(
        mode="git_status" if git_rooted else "owned_paths_only",
        head=head,
        files=files,
        warnings=warnings,
    )


def workspace_changes(before, after, owned_paths):
    evidence = WorkerEvidence(warnings=list(dict.fromkeys(before.warnings + after.warnings)))
    if before.head != after.head:
        evidence.violations.append(
            "Git HEAD changed during the worker run; commits are outside scope."
        )
    diff_remaining = MAX_DIFF_BYTES
    for name in sorted(set(before.files) | set(after.files)):
        old, new = before.files.get(name), after.files.get(name, FileState(exists=False))
        old_signature = (old.exists, old.sha256, old.marker) if old else None
        if old_signature == (new.exists, new.sha256, new.marker):
            continue
        diff = None
        if name in owned_paths and diff_remaining > 0:
            left = (
                old.text
                if old and old.text is not None
                else ("" if not old or not old.exists else None)
            )
            right = new.text if new.text is not None else ("" if not new.exists else None)
            if left is not None and right is not None:
                raw = "".join(
                    difflib.unified_diff(
                        left.splitlines(keepends=True),
                        right.splitlines(keepends=True),
                        fromfile=f"before/{name}",
                        tofile=f"after/{name}",
                    )
                ).encode("utf-8")
                if len(raw) > diff_remaining:
                    evidence.warnings.append(f"Diff preview truncated: {name}")
                diff = raw[:diff_remaining].decode("utf-8", errors="ignore")
                diff_remaining -= len(diff.encode("utf-8"))
        evidence.changes.append(
            ObservedChange(
                path=name,
                before_sha256=old.sha256 if old else None,
                after_sha256=new.sha256,
                diff=diff,
            )
        )
        if name not in owned_paths:
            evidence.violations.append(f"Worker changed a file outside its assignment: {name}")
    return evidence
