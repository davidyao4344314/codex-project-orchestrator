from codex_orchestrator.design import prepare_design
from codex_orchestrator.design_models import DesignRevision, TechnicalDesign
from codex_orchestrator.job_assessment import assess_job


def test_assessment_summarizes_architect_and_preserves_recommendation_after_override(
    approved_session, technical_payload
):
    source, _ = approved_session
    state, _ = prepare_design(source, None, architect_model=None, agent_model=None)
    design = TechnicalDesign.model_validate(technical_payload)
    state.revisions.append(
        DesignRevision(design=design, agent_models={"developer": None}, elapsed_seconds=0)
    )
    original = assess_job(state)
    assert original.complexity == "routine"
    assert original.task_count == 1 and len(original.roster) == 1
    override = state.revisions[0].model_copy(deep=True)
    override.origin = "assignment"
    override.design.tasks[0].difficulty = "complex"
    state.revisions.append(override)
    assert assess_job(state) == original
    new_design = override.model_copy(deep=True)
    new_design.origin = "architect"
    state.revisions.append(new_design)
    assert assess_job(state).complexity == "complex"
    assert assess_job(state).source_revision == 3
