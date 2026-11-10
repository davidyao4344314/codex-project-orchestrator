import pytest
from pydantic import ValidationError

from codex_orchestrator.models import PlanContent, ProjectPlan, Workstream
from codex_orchestrator.planner import template_plan


def test_template_has_all_workstreams_and_valid_dependencies() -> None:
    content = template_plan()
    assert {task.workstream for task in content.tasks} == set(Workstream)
    assert PlanContent.model_validate_json(content.model_dump_json()) == content


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("duplicate_id", "unique"),
        ("unknown_dependency", "unknown dependencies"),
        ("self_dependency", "itself"),
        ("duplicate_dependency", "duplicate dependencies"),
        ("cycle", "cycle"),
        ("empty_acceptance", "at least 1 item"),
        ("blank_title", "at least 1 character"),
        ("extra_field", "Extra inputs"),
    ],
)
def test_reject_invalid_tasks(change: str, message: str) -> None:
    data = template_plan().model_dump(mode="json")
    first, second = data["tasks"][:2]
    if change == "duplicate_id":
        second["id"] = first["id"]
    elif change == "unknown_dependency":
        first["depends_on"] = ["missing"]
    elif change == "self_dependency":
        first["depends_on"] = [first["id"]]
    elif change == "duplicate_dependency":
        second["depends_on"] = [first["id"], first["id"]]
    elif change == "cycle":
        first["depends_on"] = [second["id"]]
    elif change == "empty_acceptance":
        first["acceptance_criteria"] = []
    elif change == "blank_title":
        first["title"] = "  "
    else:
        first["execute_shell"] = "do something"
    with pytest.raises(ValidationError, match=message):
        PlanContent.model_validate(data)


def test_plan_preserves_metadata() -> None:
    plan = ProjectPlan(
        project_name="Example",
        brief="Build a planner",
        planning_mode="template",
        **template_plan().model_dump(),
    )
    assert ProjectPlan.model_validate_json(plan.model_dump_json()) == plan
    assert plan.schema_version == 1
