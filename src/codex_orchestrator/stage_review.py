"""Revision-scoped user review of delivery-stage plans."""

from pathlib import Path

from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import DesignSession, StageReview


def review_stage(state: DesignSession, path: Path, stage_id: str, feedback: str, *, approved=False):
    check_source(state)
    if not state.revisions or stage_id not in {s.id for s in state.revisions[-1].design.stages}:
        raise ValueError("Choose a stage in the current design.")
    if not approved and not feedback.strip():
        raise ValueError("Enter feedback first.")
    if not approved:
        state.phase = "review"
        state.approved_revision = None
    state.stage_reviews.append(
        StageReview(
            revision=len(state.revisions),
            stage_id=stage_id,
            feedback=feedback.strip(),
            approved=approved,
        )
    )
    state.save(path)


def current_review(state: DesignSession, stage_id: str):
    return next(
        (
            r
            for r in reversed(state.stage_reviews)
            if r.revision == len(state.revisions) and r.stage_id == stage_id
        ),
        None,
    )
