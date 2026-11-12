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


def test_stale_review_cannot_overwrite_another_saved_review(approved_session, technical_payload):
    from codex_orchestrator.design import prepare_design

    source, _ = approved_session
    state, path = prepare_design(source, None, architect_model=None, agent_model=None)
    state.revisions.append(
        DesignRevision(
            design=TechnicalDesign.model_validate(technical_payload),
            agent_models={"developer": None},
            elapsed_seconds=0,
        )
    )
    state.phase = "review"
    state.save(path)
    stale = DesignSession.load(path)
    stage = state.revisions[-1].design.stages[0].id
    review_stage(state, path, stage, "Keep this feedback")
    with pytest.raises(ValueError, match="saved session changed"):
        review_stage(stale, path, stage, "Stale feedback")
    assert DesignSession.load(path).stage_reviews[-1].feedback == "Keep this feedback"
