"""Terminal PR creation, inspection and exact-commit merge approval."""

from enum import StrEnum
from pathlib import Path

import typer

from codex_orchestrator import github_pr
from codex_orchestrator.design_models import DesignSession


class PRAction(StrEnum):
    create = "create"
    status = "status"
    merge = "merge"


def show_description(details):
    title, body = details
    typer.echo(f"\nGitHub pull request: {title}\n{body}\n")


def show_status(record, remote):
    typer.echo(f"PR #{record.number}: {record.url}")
    typer.echo(f"Target: {record.base}\nHead: {record.commit}\nState: {remote.state}")
    typer.echo(f"GitHub merge state: {remote.mergeStateStatus}")
    if remote.state == "MERGED":
        typer.echo("GitHub reports the PR merged.")
        return []
    if remote.statusCheckRollup == []:
        typer.echo("No GitHub CI checks reported.")
    else:
        for check in remote.statusCheckRollup or []:
            name = check.get("name") or check.get("context") or "Check"
            outcome = check.get("conclusion") or check.get("state") or check.get("status")
            typer.echo(f"  {name}: {outcome}")
    reasons = github_pr.readiness(record, remote)
    for reason in reasons:
        typer.echo(reason)
    if not reasons:
        typer.echo("Ready for explicit merge approval.")
    return reasons


def operate(source: Path, action: PRAction, stage_id: str):
    path = source.with_suffix(".design.json")
    if action == PRAction.create:
        state = DesignSession.load(path)
        details = None
        if github_pr.saved_pr(path, state, stage_id) is None:
            details = github_pr.preview_description(path, stage_id)
            show_description(details)
            if not typer.confirm(
                "Create or link the pull request for this approved stage?", default=False
            ):
                return
        record = github_pr.create(path, stage_id, approved_description=details)
        typer.echo(f"PR #{record.number}: {record.url}")
        typer.echo(f"Next: orchestrator cycle pr '{source}' status --stage {stage_id}")
        return
    record, remote = github_pr.status(path, stage_id)
    reasons = show_status(record, remote)
    if action == PRAction.status:
        return
    permission = None
    if remote.state != "MERGED":
        if reasons:
            raise ValueError("Resolve the GitHub merge blockers before requesting merge.")
        if not typer.confirm(
            f"Merge PR #{record.number} into {record.base} at the approved head and sync locally?",
            default=False,
        ):
            return
        permission = github_pr.permission_for(record)
    merged = github_pr.merge(path, stage_id, permission)
    typer.echo(f"Merged PR #{merged.number}: {merged.merge_commit}")
    typer.echo(f"Integrated and recorded on {merged.base}.")
