import stat
import sys

from typer.testing import CliRunner

from codex_orchestrator.cli import app
from codex_orchestrator.terminal_recording import record_terminal


def test_recording_captures_live_output_and_hides_recognizable_credentials(tmp_path, capsys):
    with record_terminal(tmp_path) as path:
        print("Feature completed")
        print("Check pending", file=sys.stderr)
        print("ghp_" + "a" * 30)
        sys.stdout.write("ghp_")
        sys.stdout.write("b" * 30)
        print()
        sys.stdout.write("Next step? ")
        assert "Next step?" in path.read_text()
        assert "Feature completed" in path.read_text()
    data = path.read_text()
    assert "Feature completed" in data and "Check pending" in data
    assert "Recognizable credential output omitted" in data
    assert "ghp_" not in data
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "Feature completed" in capsys.readouterr().out


def test_start_recording_keeps_menu_interactive(tmp_path):
    result = CliRunner().invoke(
        app, ["cycle", "start", "--directory", str(tmp_path), "--record"], input=":save\n"
    )
    assert result.exit_code == 0, result.output
    logs = list((tmp_path / "terminal-logs").glob("*.log"))
    assert len(logs) == 1
    assert "Choose project" in logs[0].read_text()


def test_start_records_failure_and_remains_opt_in(tmp_path, monkeypatch):
    from codex_orchestrator import cycle_navigation

    def fail(*args, **kwargs):
        raise ValueError("Example workflow error")

    monkeypatch.setattr(cycle_navigation, "run", fail)
    runner = CliRunner()
    command = ["cycle", "start", "--directory", str(tmp_path)]
    assert runner.invoke(app, command).exit_code == 1
    assert not (tmp_path / "terminal-logs").exists()
    result = runner.invoke(app, [*command, "--record"])
    assert result.exit_code == 1
    log = next((tmp_path / "terminal-logs").glob("*.log"))
    assert "Example workflow error" in log.read_text()
