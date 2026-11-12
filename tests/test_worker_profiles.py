import pytest

from codex_orchestrator.context_builder import build_context
from codex_orchestrator.design import prepare_design
from codex_orchestrator.design_models import DesignRevision, TechnicalDesign
from codex_orchestrator.models import Workstream
from codex_orchestrator.worker_models import WorkerResult
from codex_orchestrator.worker_profiles import worker_spec


@pytest.mark.parametrize(
    "area", ["frontend", "backend", "database", "integration", "general", "verification"]
)
def test_profile_uses_plan_model_and_scope(approved_session, technical_payload, area):
    source, _ = approved_session
    state, path = prepare_design(source, None, architect_model=None, agent_model=None)
    design = TechnicalDesign.model_validate(technical_payload)
    task = design.tasks[0]
    if area == "verification":
        task.owned_paths = []
    else:
        task.workstream = area if area == "general" else Workstream(area)
    state.revisions.append(
        DesignRevision(design=design, agent_models={"developer": "chosen-model"}, elapsed_seconds=0)
    )
    spec = worker_spec(build_context(state, task))
    assert spec.profile == area and spec.model == "chosen-model"
    assert spec.owned_paths == task.owned_paths
    assert spec.read_only == (area == "verification")
    assert spec.checks[0].description == task.automated_checks[0]
    assert spec.context_digest


def test_completed_report_cannot_claim_unrun_checks_passed():
    with pytest.raises(ValueError, match="unpassed checks"):
        WorkerResult(
            task_id="t",
            agent_id="a",
            profile="backend",
            context_digest="digest",
            outcome="completed",
            summary="Done",
            changes=[],
            checks=[dict(check_id="C1", command=None, outcome="not_run", detail="No runtime")],
            blockers=[],
            human_checks=[],
            handoff="Review",
        )
