import pytest
from test_git_service import accepted as accepted
from test_stage_branch_services import project as project

from codex_orchestrator import git_service as git
from codex_orchestrator import stage_branches as branches


def test_dashboard_branch_setup_is_explicit(project, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from codex_orchestrator import web

    root, path = project
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(path.parent))
    monkeypatch.setattr(
        branches, "refresh_base", lambda *a: pytest.fail("Unexpected network action")
    )
    app = AppTest.from_file(web.__file__, default_timeout=10).run()
    next(b for b in app.button if b.label == "Open dashboard").click().run()
    app.run()
    assert not app.exception
    assert git.git(root, "symbolic-ref", "--short", "HEAD") == "main"
    next(b for b in app.button if b.label == "Create or switch to stage branch").click().run()
    assert not app.exception
    assert "stage-1-core" in git.git(root, "symbolic-ref", "--short", "HEAD"), [
        e.value for e in app.error
    ] + [w.value for w in app.warning]
