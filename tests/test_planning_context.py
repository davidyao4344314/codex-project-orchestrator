import json

import pytest

from codex_orchestrator import design, interactive
from codex_orchestrator.codex import CodexError
from codex_orchestrator.planning_context import (
    MAX_MAP_BYTES,
    MAX_MAP_FILES,
    MAX_PROMPT_BYTES,
    planning_prompt,
    repository_map,
)
from codex_orchestrator.scope_outline import is_outline, outline_plan
from codex_orchestrator.session import Brief, ModelPolicy, PlanningRoute, PlanningSession


def test_map_is_bounded_relevant_and_excludes_private_paths(tmp_path):
    for name in (
        "src/git_service.py",
        "README.md",
        ".env",
        "credentials.json",
        "plans/session-aa.json",
        ".git/config",
        "private.key",
        "src/secret_link.py",
    ):
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        if not name.endswith("secret_link.py"):
            file.write_text("NOT INCLUDED IN MAP")
    (tmp_path / "src/secret_link.py").symlink_to(tmp_path / ".env")
    for index in range(100):
        (tmp_path / f"other_{index}.py").touch()
    result = repository_map(tmp_path, "Git service integration")
    assert result["paths"][0] == "src/git_service.py"
    assert "README.md" in result["paths"]
    assert result["partial"]
    assert len(result["paths"]) <= MAX_MAP_FILES
    assert len(json.dumps(result["paths"], ensure_ascii=False).encode()) <= MAX_MAP_BYTES
    assert not any(
        name in result["paths"]
        for name in (
            ".env",
            "credentials.json",
            "plans/session-aa.json",
            "private.key",
            "src/secret_link.py",
            ".git/config",
        )
    )
    assert "NOT INCLUDED IN MAP" not in json.dumps(result)


def test_planning_budget_fails_without_dropping_requirements():
    assert "required" in planning_prompt("Plan", {"goal": "required"})
    with pytest.raises(CodexError, match="prompt budget"):
        planning_prompt("Plan", {"goal": "x" * MAX_PROMPT_BYTES})


def test_outline_uses_no_second_model_and_keeps_scope(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    policy = ModelPolicy(
        routine="routine-model", standard="standard-model", strong_reasoning="strong-model"
    )
    state = interactive.create_session(
        tmp_path, "Git", "Improve Git", path, route=PlanningRoute.OUTLINE, model_policy=policy
    )
    assert state.intake_model == policy.routine
    assert state.planning_model == policy.standard
    state.brief = Brief(
        goal="Improve Git",
        users=[],
        requirements=["Protect main", "Test develop"],
        exclusions=["No deployment"],
        project_context=[],
        constraints=["Keep approvals"],
        assumptions=[],
        success_criteria=["Both checks pass"],
    )
    state.phase = "planning"
    state.save(path)

    def unexpected(*args, **kwargs):
        pytest.fail("Local outline must not make a model call.")

    monkeypatch.setattr(interactive, "codex_response", unexpected)
    interactive.generate_plan(state, path)
    saved = PlanningSession.load(path)
    assert saved.phase == "review" and saved.approved_revision is None
    revision = saved.revisions[-1]
    assert is_outline(revision.plan)
    assert revision.brief == state.brief
    assert revision.plan.tasks[0].requirement_ids == [1, 2]
    assert revision.plan.tasks[0].scope == state.brief.requirements
    assert not saved.response_sources


def test_architect_receives_brief_once_for_exact_outline(
    approved_session, technical_payload, monkeypatch
):
    source, planning = approved_session
    revision = planning.revisions[-1]
    revision.plan = outline_plan(revision.brief)
    planning.save(source)
    state, path = design.prepare_design(source, None, architect_model=None, agent_model=None)
    calls = []

    def respond(project, prompt, response_model, **kwargs):
        calls.append(prompt)
        kwargs["on_response"](None)
        return response_model.model_validate(
            {"question": None, "requires_requirement_change": False, "design": technical_payload}
        )

    monkeypatch.setattr(design, "codex_response", respond)
    design.generate_design(state, path, 30)
    assert state.phase == "review"
    assert len(calls) == 1
    data = json.loads(calls[0].split("\n")[-1])
    assert data["initial_tasks"] == {"local_outline": True}
    assert data["approved_brief"] == revision.brief.model_dump()
    assert "repository_map" in data
