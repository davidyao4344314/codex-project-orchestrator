"""Validated run snapshots and bounded log reads, independent of UI and processes."""

import time
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field

from codex_orchestrator.session import SessionData
from codex_orchestrator.session_manager import TokenUsage


class Run(SessionData):
    id: str
    created_at: float = Field(default_factory=time.time)
    task_id: str
    agent_id: str
    revision: int = Field(ge=1)
    status: Literal["starting", "running", "stopped", "completed", "failed", "blocked"] = "starting"
    thread_id: str | None = None
    resume_thread_id: str | None = None
    session_turn_id: str | None = None
    token_usage: TokenUsage | None = None
    session_warning: str | None = None
    branch: str | None = None
    worker_pid: int | None = None
    worker_started: str | None = None
    recovery_from: str | None = None
    feedback_id: str | None = None
    model: str | None = None
    prompt: str
    error: str | None = None
    verified: bool = False
    worker_contract_version: Literal[1] | None = None
    worker_profile: str | None = None
    worker_result_digest: str | None = None
    worker_spec_digest: str | None = None
    workspace_before_digest: str | None = None
    workspace_after_digest: str | None = None
    context_digest: str | None = None
    handoff_summary: str | None = None
    handoff_truncated: bool = False


def run_directory(path):
    return path.parent / (path.stem + ".runs")


@lru_cache(maxsize=128)
def _read_run(path: Path, modified_ns: int, size: int) -> Run:
    return Run.load(path)


def runs(path: Path) -> list[Run]:
    snapshots = []
    for file in run_directory(path).glob("*/run.json"):
        stat = file.stat()
        # Return independent snapshots so callers cannot mutate cached state.
        snapshots.append(_read_run(file, stat.st_mtime_ns, stat.st_size).model_copy(deep=True))
    return sorted(snapshots, key=lambda run: run.created_at)


def latest_runs(path: Path, revision: int) -> dict[str, Run]:
    return {run.task_id: run for run in runs(path) if run.revision == revision}


def read_tail(path: Path, *, max_bytes: int = 256 * 1024, lines: int = 60) -> list[str]:
    """Bound log previews in both disk IO and memory, including incomplete writes."""
    if max_bytes < 1 or lines < 1:
        raise ValueError("Tail limits must be positive.")
    with path.open("rb") as stream:
        stream.seek(0, 2)
        start = max(0, stream.tell() - max_bytes)
        stream.seek(start)
        data = stream.read(max_bytes)
    if start:
        _, _, data = data.partition(b"\n")
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]
