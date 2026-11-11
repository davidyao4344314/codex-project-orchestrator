import pytest

from codex_orchestrator.design import prepare_design
from codex_orchestrator.design_models import DesignRevision, DesignSession, TechnicalDesign
from codex_orchestrator.stage_review import current_review, review_stage


def test_stage_reviews_persist_and_expire_with_revision(approved_session, technical_payload):
    source, _ = approved_session
    state, path = prepare_design(source, None, architect_model=None, agent_model=None)
    revision = DesignRevision(
        design=TechnicalDesign.model_validate(technical_payload),
        agent_models={"developer": None},
        elapsed_seconds=0,
    )
    state.revisions.append(revision)
    state.phase = "review"
    state.save(path)
    stage = revision.design.stages[0].id
    with pytest.raises(ValueError, match="feedback"):
        review_stage(state, path, stage, "")
    with pytest.raises(ValueError, match="Choose"):
        review_stage(state, path, "missing", "change")
    review_stage(state, path, stage, "Make storage simpler")
    assert not current_review(DesignSession.load(path), stage).approved
    review_stage(state, path, stage, "Looks good", approved=True)
    assert current_review(DesignSession.load(path), stage).approved
    state.revisions.append(revision.model_copy(deep=True))
    assert current_review(state, stage) is None
    assert len(state.stage_reviews) == 2
