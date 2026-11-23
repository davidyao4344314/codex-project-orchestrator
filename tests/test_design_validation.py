from copy import deepcopy

import pytest

from codex_orchestrator.codex import CodexError
from codex_orchestrator.design_models import TechnicalDesign
from codex_orchestrator.design_validation import (
    assign_stage_checks,
    context_blockers,
    validate_design,
)


def test_combined_checks_are_assigned_once(technical_payload):
    design = TechnicalDesign.model_validate(technical_payload)
    assign_stage_checks(design)
    assign_stage_checks(design)
    checks = design.tasks[0].automated_checks
    assert checks.count("[core:G1] Run termination test (expected: Stops at 5)") == 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("agent", "unknown agent"),
        ("stage", "unknown agent or stage"),
        ("component", "unknown component"),
        ("handoff", "unknown handoff"),
        ("requirement", "coverage"),
        ("context", "unknown requirement"),
        ("gate", "verification owner"),
        ("absolute", "repository-relative"),
        ("escape", "repository-relative"),
        ("glob", "repository-relative"),
        ("duplicate_stage", "Duplicate stage"),
        ("unowned_agent", "no task or verification"),
    ],
)
def test_invalid_design_rejected(approved_session, technical_payload, change, message):
    path, source = approved_session
    task = technical_payload["tasks"][0]
    if change == "agent":
        task["agent_id"] = "missing"
    elif change == "stage":
        task["stage_id"] = "missing"
    elif change == "component":
        task["component_ids"] = ["missing"]
    elif change == "handoff":
        task["handoff_to"] = ["missing"]
    elif change == "requirement":
        task["requirement_ids"] = [2]
    elif change == "context":
        task["context"][0]["reference"] = "R99"
    elif change == "gate":
        technical_payload["stages"][0]["gate"]["failure_owner"] = "missing"
    elif change in ("absolute", "escape", "glob"):
        choices = {
            "absolute": "/tmp/outside.py",
            "escape": "../outside.py",
            "glob": "src/*.py",
        }
        task["owned_paths"] = [choices[change]]
    elif change == "duplicate_stage":
        technical_payload["stages"].append(deepcopy(technical_payload["stages"][0]))
    else:
        extra = deepcopy(technical_payload["agents"][0])
        extra["id"] = "unused"
        technical_payload["agents"].append(extra)
    with pytest.raises(CodexError, match=message):
        validate_design(
            TechnicalDesign.model_validate(technical_payload), source.brief, path.parent
        )


def test_ownership_and_required_upstream_context(approved_session, technical_payload):
    path, source = approved_session
    first = technical_payload["tasks"][0]
    second = deepcopy(first)
    second.update(id="verify", title="Verify counter")
    technical_payload["tasks"].append(second)
    with pytest.raises(CodexError, match="Unordered writes"):
        validate_design(
            TechnicalDesign.model_validate(technical_payload), source.brief, path.parent
        )

    second["owned_paths"] = []
    second["context"] = [
        {
            "kind": "task_output",
            "reference": "implement",
            "required": True,
            "reason": "Review implementation",
            "missing_action": "Wait for developer",
        }
    ]
    with pytest.raises(CodexError, match="context producer"):
        validate_design(
            TechnicalDesign.model_validate(technical_payload), source.brief, path.parent
        )
    second["depends_on"] = ["implement"]
    validate_design(TechnicalDesign.model_validate(technical_payload), source.brief, path.parent)


def test_context_file_blocker_and_symlink_escape(approved_session, technical_payload, tmp_path):
    path, source = approved_session
    task = technical_payload["tasks"][0]
    task["context"].append(
        {
            "kind": "project_file",
            "reference": "missing.py",
            "required": True,
            "reason": "Existing code",
            "missing_action": "Ask where the file lives",
        }
    )
    design = TechnicalDesign.model_validate(technical_payload)
    validate_design(design, source.brief, path.parent)
    assert "missing.py" in context_blockers(design, path.parent)[0]
    (path.parent / "missing.py").touch()
    assert context_blockers(design, path.parent) == []
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (path.parent / "link").symlink_to(outside, target_is_directory=True)
    task["owned_paths"] = ["link/counter.py"]
    with pytest.raises(CodexError, match="leaves the project"):
        validate_design(
            TechnicalDesign.model_validate(technical_payload), source.brief, path.parent
        )


def test_stage_dependencies_include_verification_before_downstream_work(
    approved_session, technical_payload
):
    from codex_orchestrator.design_validation import task_graph

    path, source = approved_session
    second_stage = deepcopy(technical_payload["stages"][0])
    second_stage.update(id="integration", title="Integration", depends_on=["core"])
    technical_payload["stages"].append(second_stage)
    second = deepcopy(technical_payload["tasks"][0])
    second.update(id="integrate", stage_id="integration", owned_paths=["counter.py"])
    second["context"] = [
        {
            "kind": "task_output",
            "reference": "implement",
            "required": True,
            "reason": "Integrate verified work",
            "missing_action": "Wait for core stage verification",
        }
    ]
    technical_payload["tasks"].append(second)
    design = TechnicalDesign.model_validate(technical_payload)
    validate_design(design, source.brief, path.parent)
    assert task_graph(design)["integrate"] == {"implement"}

    technical_payload["stages"][0]["depends_on"] = ["integration"]
    with pytest.raises(CodexError, match="cycle"):
        validate_design(
            TechnicalDesign.model_validate(technical_payload), source.brief, path.parent
        )


def test_stage_gate_cannot_omit_agent_checks(technical_payload):
    from pydantic import ValidationError

    technical_payload["stages"][0]["gate"]["agent_checks"] = []
    with pytest.raises(ValidationError, match="at least 1"):
        TechnicalDesign.model_validate(technical_payload)
