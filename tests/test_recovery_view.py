import pytest
from test_execution import design as design
from test_git_service import accepted as accepted

from codex_orchestrator import execution, git_service


def test_resume_navigation_is_read_only_across_new_browser_session(accepted, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from codex_orchestrator import web

    _, path = accepted
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(path.parent))
    monkeypatch.setattr(execution, "start_run", lambda *a: pytest.fail("Started worker"))
    monkeypatch.setattr(git_service, "push", lambda *a, **kw: pytest.fail("Pushed"))
    for module in ("review_agent", "dashboard", "design", "interactive"):
        monkeypatch.setattr(
            f"codex_orchestrator.{module}.codex_response",
            lambda *a, **kw: pytest.fail("Inference during resume"),
        )
    for _ in range(2):
        app = AppTest.from_file(web.__file__, default_timeout=10).run()
        next(b for b in app.button if b.label == "Resume project").click().run()
        assert not app.exception
        assert any("Open Git connection" in i.value for i in app.info)
        app.run()
        assert not app.exception
