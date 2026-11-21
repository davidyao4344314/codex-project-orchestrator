"""Local conversation bindings and per-turn metrics, never credentials or transcripts."""

import fcntl
import hashlib
import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from codex_orchestrator.session import SessionData

MAX_SESSION_TURNS = 8


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def thread_id(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


class TokenUsage(SessionData):
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_output_tokens: int | None = Field(default=None, ge=0)
    cache_write_input_tokens: int | None = Field(default=None, ge=0)

    @classmethod
    def from_event(cls, raw):
        if not isinstance(raw, dict):
            return None
        values = {
            name: value
            for name in cls.model_fields
            if type(value := raw.get(name)) is int and value >= 0
        }
        if not values:
            return None
        if values.get("cached_input_tokens", 0) > values.get("input_tokens", float("inf")):
            values.pop("cached_input_tokens", None)
        return cls(**values)


@dataclass
class TurnEvents:
    thread: str | None = None
    usage: TokenUsage | None = None
    completed: bool = False
    failed: bool = False
    invalid_thread: bool = False
    tool_items: set[str] = field(default_factory=set)
    command_items: set[str] = field(default_factory=set)

    def observe(self, event):
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "thread.started":
            identity = thread_id(event.get("thread_id"))
            if identity is None or (self.thread and self.thread != identity):
                self.invalid_thread = True
            else:
                self.thread = identity
        elif kind == "turn.completed":
            self.completed = True
            self.usage = TokenUsage.from_event(event.get("usage"))
        elif kind == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                if item.get("type") in {"command_execution", "mcp_tool_call", "web_search"}:
                    self.tool_items.add(item["id"])
                if item.get("type") == "command_execution":
                    self.command_items.add(item["id"])
        elif kind in {"turn.failed", "error"}:
            self.failed = True

    def matches(self, requested):
        return not self.invalid_thread and (requested is None or requested == self.thread)


class Binding(SessionData):
    scope_digest: str
    thread_id: str
    turns: int = Field(ge=1)
    cursor: int = Field(default=0, ge=0)
    cursor_digest: str = ""


class Slot(SessionData):
    ticket: str
    binding: Binding | None = None


class Registry(SessionData):
    slots: dict[str, Slot] = Field(default_factory=dict)


class TurnRecord(SessionData):
    id: str
    role: str
    scope_digest: str
    requested_model: str | None
    reported_model: str | None = None
    requested_thread_id: str | None = None
    thread_id: str | None = None
    task_id: str | None = None
    stage_id: str | None = None
    run_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["started", "completed", "failed", "blocked", "stopped"] = "started"
    elapsed_seconds: float | None = Field(default=None, ge=0)
    prompt_bytes: int = Field(default=0, ge=0)
    full_prompt_bytes: int | None = Field(default=None, ge=0)
    context_bytes: int | None = Field(default=None, ge=0)
    reused_context_files: int = Field(default=0, ge=0)
    usage: TokenUsage | None = None
    observed_tool_calls: int | None = Field(default=None, ge=0)
    observed_commands: int | None = Field(default=None, ge=0)
    session_reusable: bool = False


def registry_path(checkpoint: Path) -> Path:
    return checkpoint.with_suffix(".codex-sessions.json")


def turns_directory(checkpoint: Path) -> Path:
    return checkpoint.with_suffix(".codex-turns")


def read_registry(checkpoint: Path) -> Registry:
    path = registry_path(checkpoint)
    return Registry.load(path) if path.exists() else Registry()


def read_turns(checkpoint: Path) -> list[TurnRecord]:
    return sorted(
        (TurnRecord.load(p) for p in turns_directory(checkpoint).glob("*.json")),
        key=lambda record: record.started_at,
    )


@contextmanager
def _registry_lock(checkpoint):
    # Short, process-scoped lock: no model call occurs while this file is locked.
    path = checkpoint.with_suffix(".codex-sessions.lock")
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@dataclass
class ManagedTurn:
    checkpoint: Path
    record: TurnRecord
    previous: Binding | None
    persistent: bool
    events: TurnEvents = field(default_factory=TurnEvents)
    cursor: int = 0
    cursor_digest: str = ""
    started: float = field(default_factory=monotonic)

    @property
    def resume_id(self):
        return self.record.requested_thread_id

    @property
    def receipt(self):
        return turns_directory(self.checkpoint) / f"{self.record.id}.json"

    def prepare(self, prompt):
        self.record.prompt_bytes = len(prompt.encode("utf-8"))
        self.record.save(self.receipt)

    def finish(self, status):
        self.record.status = status
        self.record.elapsed_seconds = monotonic() - self.started
        self.record.thread_id = self.events.thread
        self.record.usage = self.events.usage
        if self.events.completed:
            self.record.observed_tool_calls = len(self.events.tool_items)
            self.record.observed_commands = len(self.events.command_items)
        if self.persistent:
            with _registry_lock(self.checkpoint):
                registry = read_registry(self.checkpoint)
                slot = registry.slots.get(self.record.role)
                # A consumed binding is restored only for the most recent successful turn.
                if slot and slot.ticket == self.record.id:
                    identity = self.events.thread
                    distinct = not any(
                        role != self.record.role
                        and other.binding
                        and other.binding.thread_id == identity
                        for role, other in registry.slots.items()
                    )
                    if (
                        status == "completed"
                        and self.events.completed
                        and not self.events.failed
                        and identity
                        and self.events.matches(self.resume_id)
                        and distinct
                    ):
                        slot.binding = Binding(
                            scope_digest=self.record.scope_digest,
                            thread_id=identity,
                            turns=(self.previous.turns + 1 if self.previous else 1),
                            cursor=self.cursor,
                            cursor_digest=self.cursor_digest,
                        )
                        self.record.session_reusable = True
                    registry.save(registry_path(self.checkpoint))
        self.record.save(self.receipt)


def begin_turn(
    checkpoint: Path,
    project: Path,
    role: str,
    model: str | None,
    boundary,
    *,
    persistent=True,
    resume_if: Callable[[Binding], bool] | None = None,
    task_id=None,
    stage_id=None,
    run_id=None,
) -> ManagedTurn:
    """Consume the previous binding before dispatch so a crash cannot reuse ambiguous history."""
    checkpoint = checkpoint.resolve()
    scope = digest(
        {
            "version": 1,
            "checkpoint": str(checkpoint),
            "project": str(Path(project).resolve()),
            "role": role,
            "model": model,
            "boundary": boundary,
            "codex_home": os.environ.get("CODEX_HOME", ""),
        }
    )
    record = TurnRecord(
        id=uuid4().hex,
        role=role,
        scope_digest=scope,
        requested_model=model,
        task_id=task_id,
        stage_id=stage_id,
        run_id=run_id,
    )
    previous = None
    if persistent:
        with _registry_lock(checkpoint):
            registry = read_registry(checkpoint)
            slot = registry.slots.get(role)
            binding = slot.binding if slot else None
            if (
                binding
                and binding.scope_digest == scope
                and binding.turns < MAX_SESSION_TURNS
                and thread_id(binding.thread_id) == binding.thread_id
                and os.environ.get("ORCHESTRATOR_FRESH_SESSIONS") != "1"
                and (resume_if is None or resume_if(binding))
            ):
                previous = binding
                record.requested_thread_id = binding.thread_id
            registry.slots[role] = Slot(ticket=record.id)
            registry.save(registry_path(checkpoint))
    turn = ManagedTurn(checkpoint, record, previous, persistent)
    record.save(turn.receipt, new=True)
    return turn


@contextmanager
def managed_turn(*args, **kwargs):
    turn = begin_turn(*args, **kwargs)
    try:
        yield turn
    except BaseException:
        turn.finish("failed")
        raise
    else:
        turn.finish("completed")
