"""Build a bounded PR description from existing approved scope and observed checks."""

from codex_orchestrator.context_files import sensitive_text
from codex_orchestrator.coordination_store import load_coordination
from codex_orchestrator.run_store import latest_runs, run_directory
from codex_orchestrator.worker_models import WorkerResult


def _line(value, limit=300):
    if sensitive_text(str(value)):
        raise ValueError("Remove recognizable credentials from the PR description.")
    return " ".join(str(value).split())[:limit].replace("@", "\\@").replace("#", "\\#")


def description(state, path, stage_id, paths):
    stage = next(s for s in state.revisions[-1].design.stages if s.id == stage_id)
    acceptance = load_coordination(state, path).accepted_stages[stage_id]
    latest = latest_runs(path, len(state.revisions))
    tasks = [t for t in state.revisions[-1].design.tasks if t.stage_id == stage_id]
    requirements = sorted({number for task in tasks for number in task.requirement_ids})
    lines = [
        "## Approved feature",
        _line(stage.goal, 500),
        "",
        "## Requirements",
        *[f"- R{i}: {_line(state.source.brief.requirements[i - 1])}" for i in requirements],
        "",
        "## Changes",
        *[f"- {_line(task.title)}" for task in tasks],
        "",
        "## Reviewed files",
        *[f"- `{_line(name)}`" for name in paths],
        "",
        "## Automated verification",
    ]
    for task in tasks:
        run = latest[task.id]
        report = WorkerResult.load(run_directory(path) / run.id / "worker_result.json")
        outcomes = ", ".join(f"{check.check_id}: {check.outcome}" for check in report.checks)
        lines.append(f"- {_line(task.id)}: {_line(outcomes, 500) or 'No automated checks'}")
    lines.extend(
        [
            "",
            "## User testing",
            _line(acceptance.evidence, 1000),
            "",
            "Independent AI review and user publication approval recorded locally.",
        ]
    )
    title = f"Deliver {_line(stage.title, 150)}"
    body = "\n".join(lines)
    if sensitive_text(title + body) or len(body.encode()) > 12 * 1024:
        raise ValueError("PR description contains recognizable credentials or exceeds its budget.")
    return title, body
