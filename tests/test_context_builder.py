import hashlib

import pytest

from codex_orchestrator.context_builder import build_context
from codex_orchestrator.context_models import ContextLimits
from codex_orchestrator.design import prepare_design
from codex_orchestrator.design_models import (
    ContextReference,
    DesignRevision,
    Skill,
    TechnicalDesign,
)


@pytest.fixture
def context_design(approved_session, technical_payload):
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
    return state, path


def file_context(task, name, required=True):
    task.context.append(
        ContextReference(
            kind="project_file",
            reference=name,
            required=required,
            reason="Needed for implementation",
            missing_action="Supply the file or revise the task",
        )
    )


def test_context_contains_only_selected_files_and_deduplicates_reads(context_design, monkeypatch):
    state, path = context_design
    project = path.parent
    (project / "counter.py").write_text("print('counter')\n")
    (project / "unrelated.py").write_text("private sibling context")
    task = state.revisions[-1].design.tasks[0]
    file_context(task, "counter.py")
    from codex_orchestrator import context_builder

    original = context_builder.read_file
    reads = []

    def read(*args):
        reads.append(args[1].reference)
        return original(*args)

    monkeypatch.setattr(context_builder, "read_file", read)
    package = build_context(state, task)
    assert reads == ["counter.py"]
    resource = next(r for r in package.resources if r.reference == "counter.py")
    assert resource.sha256 == hashlib.sha256(b"print('counter')\n").hexdigest()
    assert resource.aliases == ["owned_file: counter.py"]
    assert "private sibling context" not in package.payload()
    assert not package.blockers
    assert package.size_bytes() <= package.limits.max_package_bytes


@pytest.mark.parametrize("name", [".env", "auth.json", "secrets.toml", "private.key"])
def test_sensitive_file_paths_are_not_included(context_design, name):
    state, path = context_design
    (path.parent / name).write_text("TOP_SECRET_VALUE")
    task = state.revisions[-1].design.tasks[0]
    file_context(task, name)
    package = build_context(state, task)
    assert package.blockers
    assert "TOP_SECRET_VALUE" not in package.payload()
    with pytest.raises(ValueError, match="incomplete"):
        package.require_ready()


def test_missing_truncated_binary_and_outside_files_are_explicit(context_design, tmp_path):
    state, path = context_design
    task = state.revisions[-1].design.tasks[0]
    (path.parent / "large.py").write_text("x" * 1000)
    (path.parent / "binary.dat").write_bytes(b"\x00\x01")
    outside = path.parent.parent / (path.parent.name + "-outside.txt")
    outside.write_text("outside-private")
    (path.parent / "escape.txt").symlink_to(outside)
    for name in ["missing.txt", "large.py", "binary.dat", "escape.txt"]:
        file_context(task, name)
    package = build_context(state, task, limits=ContextLimits(max_file_bytes=128))
    statuses = {r.reference: r.status for r in package.resources}
    assert statuses["missing.txt"] == "missing"
    assert statuses["large.py"] == "truncated"
    assert statuses["binary.dat"] == statuses["escape.txt"] == "excluded"
    assert "outside-private" not in package.payload()
    assert len(package.blockers) == 4


def test_skill_resolution_and_optional_missing_file(context_design):
    state, path = context_design
    skill = path.parent / "skills" / "python" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Use focused Python tests.")
    state.revisions[-1].design.agents[0].skills = [
        Skill(name="Python", location="skills/python/SKILL.md")
    ]
    task = state.revisions[-1].design.tasks[0]
    file_context(task, "optional.txt", required=False)
    package = build_context(state, task)
    assert not package.blockers
    assert any(
        r.kind == "skill" and r.content == "Use focused Python tests." for r in package.resources
    )
    assert any("optional.txt" in warning for warning in package.warnings)


def test_budget_never_silently_drops_required_input(context_design):
    state, path = context_design
    (path.parent / "needed.py").write_text("x" * 7000)
    task = state.revisions[-1].design.tasks[0]
    file_context(task, "needed.py")
    package = build_context(state, task, limits=ContextLimits(max_package_bytes=5000))
    assert package.blockers
    assert "x" * 7000 not in package.payload()


def test_recognizable_credentials_in_source_are_excluded(context_design):
    state, path = context_design
    key = "sk-" + "a" * 30
    (path.parent / "config.py").write_text(f"KEY = '{key}'")
    task = state.revisions[-1].design.tasks[0]
    file_context(task, "config.py")
    package = build_context(state, task)
    assert package.blockers and key not in package.payload()
