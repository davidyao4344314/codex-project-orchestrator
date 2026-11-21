import json

from test_context_builder import context_design as context_design
from typer.testing import CliRunner

from codex_orchestrator import execution
from codex_orchestrator.cli import app
from codex_orchestrator.design_models import DesignSession


def test_terminal_context_preview_and_budget_changes_do_not_start_models(
    context_design, monkeypatch
):
    state, path = context_design
    source = path.with_name("source.json")
    (path.parent / "counter.py").write_text("private assigned source")
    monkeypatch.setattr(
        execution,
        "start_run",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Unexpected worker")),
    )
    runner = CliRunner()
    args = ["cycle", "context", str(source), "--task", "implement"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["task_id"] == "implement" and report["resources"]
    assert "private assigned source" not in result.output
    result = runner.invoke(app, [*args, "--full"])
    assert result.exit_code == 0 and "private assigned source" in result.output
    result = runner.invoke(
        app, [*args, "--budget-kib", "32", "--file-kib", "8", "--handoff-kib", "4"]
    )
    assert result.exit_code == 0, result.output
    assert DesignSession.load(path).context_limits.max_package_bytes == 32768
    assert state.revisions == DesignSession.load(path).revisions
