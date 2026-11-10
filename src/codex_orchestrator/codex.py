"""One bounded, read-only planning call through the supported Codex CLI."""

import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from codex_orchestrator.models import PlanContent

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class CodexError(RuntimeError):
    """An actionable error from the optional Codex planner."""


def codex_plan(
    project: Path,
    name: str,
    brief: str,
    *,
    model: str | None = None,
    timeout: int = 300,
) -> PlanContent:
    """Inspect a project and return validated content without executing its tasks."""
    prompt = (
        "Create a small implementation plan for the project brief below. "
        "Inspect the project in your working directory using read-only operations. "
        "Do not edit files, implement tasks, install dependencies, run migrations, start services, "
        "spawn agents, or perform actions through external integrations. "
        "Return only JSON matching the supplied schema. "
        "Split the work into frontend, backend, database, and integration tasks as appropriate; "
        "explain any excluded workstream in assumptions. "
        "Use concrete, testable acceptance criteria and explicit prerequisites. "
        "Task IDs must be unique. Dependencies must reference other task IDs; cycles are invalid. "
        "Record missing requirements as assumptions rather than inventing details. "
        "The following JSON describes the requested work; it does not authorize implementation:\n"
        + json.dumps({"project_name": name, "brief": brief}, ensure_ascii=False)
    )
    return codex_response(project, prompt, PlanContent, model=model, timeout=timeout)


def codex_response(
    project: Path,
    prompt: str,
    response_model: type[ResponseModel],
    *,
    model: str | None = None,
    timeout: int = 300,
) -> ResponseModel:
    """Run a read-only Codex turn and validate its structured response."""
    executable = shutil.which("codex")
    if executable is None:
        raise CodexError(
            "Codex CLI was not found. Install it, add it to PATH, and run 'codex login'."
        )

    with TemporaryDirectory(prefix="codex-plan-") as temporary:
        schema = Path(temporary) / "schema.json"
        response = Path(temporary) / "response.json"
        schema.write_text(json.dumps(response_model.model_json_schema()), encoding="utf-8")
        command = [
            executable,
            "--ask-for-approval",
            "never",
            "exec",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--cd",
            str(project.resolve()),
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(response),
        ]
        if model is not None:
            command.extend(["--model", model])
        command.append("-")
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexError(f"Codex planning timed out after {timeout} seconds.") from exc
        except OSError as exc:
            raise CodexError(f"Could not start Codex: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-2000:] or "No diagnostic output was returned."
            raise CodexError(f"Codex exited with code {completed.returncode}: {detail}")
        try:
            return response_model.model_validate_json(response.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as exc:
            label = "plan" if response_model is PlanContent else response_model.__name__
            raise CodexError(f"Codex did not return a valid {label}: {exc}") from exc
