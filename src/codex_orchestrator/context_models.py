"""Versioned, inspectable context packages with explicit resource status."""

import hashlib
import json
from typing import Any, Literal

from pydantic import Field

from codex_orchestrator.session import SessionData


class ContextLimits(SessionData):
    max_package_bytes: int = Field(default=128 * 1024, ge=1024)
    max_file_bytes: int = Field(default=24 * 1024, ge=128)
    max_handoff_bytes: int = Field(default=16 * 1024, ge=128)
    max_resources: int = Field(default=32, ge=1, le=256)


class ContextResource(SessionData):
    kind: Literal[
        "project_file", "owned_file", "upstream_file", "skill", "instruction", "task_output"
    ]
    reference: str
    required: bool = False
    reason: str
    missing_action: str = "Resolve the reference before implementation."
    status: Literal[
        "included", "missing", "pending", "excluded", "truncated", "omitted", "unconfigured"
    ]
    content: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    source_run_id: str | None = None
    aliases: list[str] = Field(default_factory=list)


class ContextPackage(SessionData):
    schema_version: Literal[1] = 1
    design_revision: int = Field(ge=1)
    design_fingerprint: str
    task_id: str
    agent_id: str
    requested_model: str | None
    purpose: Literal["discussion", "execution"]
    metadata: dict[str, Any]
    limits: ContextLimits
    resources: list[ContextResource] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def payload(self) -> str:
        return json.dumps(self.model_dump(), ensure_ascii=False)

    def size_bytes(self) -> int:
        return len(self.payload().encode("utf-8"))

    def digest(self) -> str:
        return hashlib.sha256(self.payload().encode("utf-8")).hexdigest()

    def require_ready(self) -> None:
        if self.blockers:
            raise ValueError("Context is incomplete: " + " ".join(self.blockers))
