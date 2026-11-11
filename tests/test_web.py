import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from codex_orchestrator import web
from codex_orchestrator.design_models import DesignRevision, DesignSession, TechnicalDesign
from codex_orchestrator.session import PlanningSession


def button(app, label):
    return next(item for item in app.button if item.label == label)


def text_input(app, label):
    return next(item for item in app.text_input if item.label == label)


def test_sample_project_prefills_a_separate_starter(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(tmp_path / "plans"))
    app = AppTest.from_file(web.__file__).run()
    assert not app.exception
    button(app, "Try sample project").click().run()
    assert not app.exception
    assert text_input(app, "Project name").value == "Reading list"
    project = text_input(app, "Local project folder").value
    from pathlib import Path

    assert Path(project).is_relative_to(tmp_path)
    assert (Path(project) / ".git").is_dir()
    assert "SQLite" in app.text_area[0].value
    button(app, "Create planning session").click().run()
    assert not app.exception
    paths = list((tmp_path / "plans").glob("session-*.json"))
    assert len(paths) == 1
    assert PlanningSession.load(paths[0]).project == project


def test_browser_requirements_to_approved_design(
    tmp_path, monkeypatch, approved_session, technical_payload
):
    source_path, source = approved_session
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(tmp_path / "plans"))

    def intake(session, path):
        session.brief = source.brief
        session.phase = "planning"
        session.save(path)

    def plan(session, path):
        session.revisions = [source.revisions[0]]
        session.phase = "review"
        session.save(path)

    def design(state, path, timeout):
        state.revisions = [
            DesignRevision(
                design=TechnicalDesign.model_validate(technical_payload),
                agent_models={"developer": None},
                elapsed_seconds=1,
                reported_model="test-architect",
            )
        ]
        state.phase = "review"
        state.save(path)

    monkeypatch.setattr("codex_orchestrator.interactive.collect_brief", intake)
    monkeypatch.setattr("codex_orchestrator.interactive.generate_plan", plan)
    monkeypatch.setattr("codex_orchestrator.design.generate_design", design)
    app = AppTest.from_file(web.__file__).run()
    text_input(app, "Project name").set_value("Counter")
    text_input(app, "Local project folder").set_value(str(source_path.parent))
    app.text_area[0].set_value("Build a recursion counter")
    button(app, "Create planning session").click().run()
    assert not app.exception
    app.radio[0].set_value("Technical design").run()
    assert "Approve your Stage 1" in app.info[0].value
    app.radio[0].set_value("Requirements").run()
    button(app, "Continue with Codex").click().run()
    assert not app.exception
    button(app, "Approve requirements and plan").click().run()
    assert not app.exception
    app.radio[0].set_value("Technical design").run()
    button(app, "Create technical design").click().run()
    assert not app.exception
    assert any("Verification gate" in item.value for item in app.markdown)
    button(app, "Approve technical design").click().run()
    assert not app.exception
    output = next((tmp_path / "plans").glob("*.design.json"))
    assert DesignSession.load(output).phase == "approved"
    app.radio[0].set_value("Dashboard").run()
    assert not app.exception
    assert {"Frontend", "Backend", "Database"} <= {h.value for h in app.subheader}
    assert any("Start an approved assignment" in info.value for info in app.info)
    assert any(s.label == "Assignment" for s in app.selectbox)
    app.radio[0].set_value("Technical design").run()
    assert not app.exception
    text_input(app, "Python developer model").set_value("another-model")
    button(app, "Save model allocations").click().run()
    assert not app.exception
    saved = DesignSession.load(output)
    assert saved.phase == "review"
    assert saved.approved_revision is None
    assert saved.revisions[-1].agent_models == {"developer": "another-model"}
