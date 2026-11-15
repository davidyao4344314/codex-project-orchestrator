"""Shared project leases and conservative process observations for recovery."""

import hashlib
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from codex_orchestrator.session import SessionData


class ProjectLease(SessionData):
    token: str
    owner_pid: int
    owner_started: str | None
    operation: str
    run_id: str | None = None


def lock_path(project) -> Path:
    identity = hashlib.sha256(str(Path(project).resolve()).encode()).hexdigest()
    directory = Path(tempfile.gettempdir()) / f"codex-orchestrator-locks-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory / f"{identity}.lock"


def process_started(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            env=dict(os.environ, TZ="UTC", LC_ALL="C"),
            text=True,
            timeout=5,
            check=False,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def owner_alive(lease: ProjectLease) -> bool:
    if not alive(lease.owner_pid):
        return False
    current = process_started(lease.owner_pid)
    # Unavailable birth metadata means uncertainty, not permission to release a lock.
    return not (lease.owner_started and current and lease.owner_started != current)


def group_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def create(project, operation: str, run_id=None) -> ProjectLease:
    lease = ProjectLease(
        token=uuid4().hex,
        owner_pid=os.getpid(),
        owner_started=process_started(os.getpid()),
        operation=operation,
        run_id=run_id,
    )
    fd = os.open(lock_path(project), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(lease.model_dump_json())
    return lease


def load(project) -> ProjectLease | None:
    try:
        fd = os.open(lock_path(project), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                return None
            return ProjectLease.model_validate_json(stream.read(4097))
    except (OSError, ValueError):
        return None  # Legacy/partial locks remain blocked for manual investigation.


def release(project, lease: ProjectLease) -> None:
    current = load(project)
    if current and current.token == lease.token:
        lock_path(project).unlink(missing_ok=True)
