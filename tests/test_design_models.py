import pytest
from pydantic import ValidationError

from codex_orchestrator.design_models import (
    DesignReply,
    DesignSession,
    TechnicalDesign,
    source_digest,
)


def test_design_snapshot_roundtrip(approved_session, technical_payload):
    path, source = approved_session
    design = TechnicalDesign.model_validate(technical_payload)
    assert TechnicalDesign.model_validate_json(design.model_dump_json()) == design
    state = DesignSession(
        source_file=str(path),
        project=source.project,
        source_revision=1,
        source=source.revisions[0],
        source_digest=source_digest(source.revisions[0]),
    )
    output = path.with_name("design.json")
    state.save(output, new=True)
    assert DesignSession.load(output) == state
    with pytest.raises(FileExistsError):
        state.save(output, new=True)


def test_reject_false_approval_and_changed_source(approved_session):
    path, source = approved_session
    data = dict(
        source_file=str(path),
        project=source.project,
        source_revision=1,
        source=source.revisions[0],
        source_digest=source_digest(source.revisions[0]),
    )
    with pytest.raises(ValidationError, match="Approval"):
        DesignSession(**data, approved_revision=1)
    data["source_digest"] = "wrong"
    with pytest.raises(ValidationError, match="snapshot"):
        DesignSession(**data)


def test_reply_requires_question_or_design(technical_payload):
    with pytest.raises(ValidationError, match="either"):
        DesignReply(question=None, requires_requirement_change=False, design=None)
    with pytest.raises(ValidationError, match="requirement change"):
        DesignReply(
            question=None,
            requires_requirement_change=True,
            design=TechnicalDesign.model_validate(technical_payload),
        )
