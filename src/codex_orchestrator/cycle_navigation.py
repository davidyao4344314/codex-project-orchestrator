"""Interactive terminal navigation over saved cycle checkpoints."""

from pathlib import Path

import typer

from codex_orchestrator import (
    coordinator,
    cycle_feedback,
    cycle_manager,
    git_service,
    github_pr,
    recovery,
    stage_branches,
)
from codex_orchestrator.codex import CodexError
from codex_orchestrator.cycle_store import find_history, histories
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.github_cli import show_connection
from codex_orchestrator.github_pr_cli import PRAction, operate
from codex_orchestrator.run_store import latest_runs


def choose(label, options, *, default=None):
    """Only a listed action can dispatch work; save is available at every menu."""
    for index, (title, _) in enumerate(options, 1):
        typer.echo(f"  {index}. {title}")
    while True:
        answer = typer.prompt(label, default=default or str(len(options))).strip()
        if answer == ":save":
            return None
        if answer.isdecimal() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][1]
        typer.echo("Choose a listed number or :save.")


def select_project(directory, *, local=False):
    entries = histories(directory)
    options = [
        (
            f"{history.name} — cycle {history.cycles[-1].number:03d} "
            f"({history.cycles[-1].status}) — {history.project}",
            file.parent / history.cycles[-1].session_name,
        )
        for file, history in entries
    ]
    options.extend([("Create a project cycle", "new"), ("Save and exit", None)])
    selected = choose("Choose project", options)
    if selected != "new":
        return selected
    project = Path(typer.prompt("Local project folder")).expanduser().resolve()
    for file, history in entries:
        if Path(history.project).resolve() == project:
            return file.parent / history.cycles[-1].session_name
    if not local:
        show_connection(project)
    goal = typer.prompt("What would you like to build?")
    return cycle_manager.create_project(project, goal, directory)


def merge_stage(source, state, stage):
    path = source.with_suffix(".design.json")
    receipt = git_service.GitPublication.load(git_service.publication_path(path))
    settings, revision = stage_branches.current(state, path)
    item = revision.stages[stage]
    if (
        receipt.remote
        and github_pr.repository_for(receipt.remote)
        and receipt.commit != item.start_commit
    ):
        if github_pr.saved_pr(path, state, stage) is None:
            operate(source, PRAction.create, stage)
        else:
            operate(source, PRAction.merge, stage)
        return
    if receipt.remote:
        if typer.confirm(f"Refresh {settings.base} and record the approved merge?", default=False):
            stage_branches.refresh_base(path)
            stage_branches.record_merge(path, stage)
        return
    if not typer.confirm(f"Merge the approved local commit into {settings.base}?", default=False):
        return
    stage_branches.merge_local(path, stage)


def context_menu(source, path):
    from codex_orchestrator.cycle_cli import context

    state = DesignSession.load(path)
    limits = state.context_limits
    file_kib = typer.prompt(
        "Maximum context file size (KiB)", type=int, default=limits.max_file_bytes // 1024
    )
    budget_kib = typer.prompt(
        "Total context budget (KiB)",
        type=int,
        default=max(limits.max_package_bytes // 1024, file_kib),
    )
    context(
        source,
        task="",
        full=False,
        budget_kib=budget_kib,
        file_kib=file_kib,
        handoff_kib=None,
        max_resources=None,
    )


def run(source=None, directory=Path("plans"), *, local=False, timeout=300):
    from codex_orchestrator import cycle_cli as cli

    directory = directory.expanduser().resolve()
    source = source.expanduser().resolve() if source else select_project(directory, local=local)
    if source is None:
        return
    if not find_history(source):
        if not typer.confirm("Register this saved planning session?", default=False):
            return
        cycle_manager.register(source)
    while True:
        _, history = find_history(source)
        cycle = history.cycles[-1]
        source = source.parent / cycle.session_name
        path = source.with_suffix(".design.json")
        typer.echo(f"\n{history.name} — Cycle {cycle.number:03d} ({cycle.status})")
        typer.echo(f"Goal: {cycle.goal}\nSession: {source}")
        options = []
        state = None
        stage = base = None
        if cycle.status == "completed":
            options.append(("Start the next development cycle", "next"))
        else:
            snapshot = recovery.inspect_project(source)
            typer.echo(f"Progress: {snapshot.status}\n{snapshot.next_action}")
            if snapshot.branch:
                typer.echo(f"Current Git branch: {snapshot.branch}")
            for warning in snapshot.warnings:
                typer.echo(f"  {warning}")
            if (
                snapshot.view in {"Requirements", "Technical design"}
                and snapshot.status != "stale_design"
            ):
                options.append(("Continue planning and review", "plan"))
            elif snapshot.status == "complete":
                state = DesignSession.load(path)
                options.append(("Finish this cycle and record changes", "finish"))
            elif snapshot.status == "paused":
                options.append(("Resume paused coordinator", "resume"))
            elif snapshot.status == "recoverable":
                options.append(("Recover interrupted worker", "recover"))
            elif snapshot.status == "stale_output":
                state = DesignSession.load(path)
            elif snapshot.status not in {
                "active",
                "unknown",
                "stale_design",
                "stale_output",
                "publication_needs_review",
            }:
                state = DesignSession.load(path)
                stage, base = stage_branches.next_stage(state, path)
                if stage:
                    base = base or stage_branches.default_base(Path(state.project))
                    typer.echo(f"Integration base: {base}")
                    if snapshot.status == "merge_pending":
                        options.append(("Review PR or merge the delivered feature", "merge"))
                    elif snapshot.status == "retry_pending" or (
                        snapshot.status == "blocked" and cycle_feedback.pending(state, path)
                    ):
                        from codex_orchestrator.evidence_recovery import can_reconcile

                        task_run = latest_runs(path, len(state.revisions)).get(snapshot.task_id)
                        if task_run and can_reconcile(path, task_run):
                            options.append(
                                ("Reconcile saved worker evidence (no model call)", "reconcile")
                            )
                        options.append(("Retry the failed or interrupted assignment", "retry"))
                    else:
                        options.append((f"Continue feature {stage}", "feature"))
                    if snapshot.status == "verification_pending" and snapshot.task_id:
                        options.append(("Review and accept task verification manually", "verify"))
                    if snapshot.status in {"ai_review_pending", "commit_pending"}:
                        options.append(
                            ("Explicitly retry the independent AI review", "retry-review")
                        )
            if state and snapshot.status not in {"active", "unknown"}:
                options.append(("Adjust context budget", "context"))
                if snapshot.status != "merge_pending":
                    options.append(("Request a scoped improvement", "improve"))
            if snapshot.status not in {"active", "unknown", "recoverable"}:
                options.append(("Check or connect GitHub", "github"))
        options.extend(
            [
                ("View token usage and performance", "usage"),
                ("View cycle history", "history"),
                ("Save and exit", "exit"),
            ]
        )
        action = choose("Next step", options)
        if action in {None, "exit"}:
            typer.echo(f"Progress saved. Resume with orchestrator cycle start '{source}'")
            return
        try:
            if action == "plan":
                cli.combined_plan(source, timeout=timeout)
            elif action in {"feature", "retry", "retry-review"}:
                cli.feature(
                    source,
                    stage=stage,
                    base=base,
                    retry=action == "retry",
                    retry_review=action == "retry-review",
                )
            elif action == "merge":
                merge_stage(source, state, stage)
            elif action == "next":
                goal = typer.prompt("What would you like to build or improve?")
                source = cycle_manager.start_next(source, goal)
            elif action == "finish":
                cli.finish(source, changes=None, feedback=None)
            elif action == "resume":
                coordinator.resume(path)
            elif action == "recover":
                interrupted = recovery.activity(Path(history.project), path)
                recovery.recover_interrupted(
                    path,
                    interrupted.run_id,
                    confirmed=typer.confirm(
                        "Recover this stopped worker after inspecting partial work?", default=False
                    ),
                )
            elif action == "verify":
                task_run = latest_runs(path, len(state.revisions))[snapshot.task_id]
                cli.deliver(source, cli.DeliveryAction.verify, stage=stage, run_id=task_run.id)
            elif action == "reconcile":
                task_run = latest_runs(path, len(state.revisions))[snapshot.task_id]
                cli.deliver(source, cli.DeliveryAction.reconcile, stage=stage, run_id=task_run.id)
            elif action == "improve":
                for task in state.revisions[-1].design.tasks:
                    if task.owned_paths:
                        typer.echo(f"  {task.id}: {task.title}")
                cli.feedback(source, kind="improve", task="", stage="", note="")
            elif action == "github":
                show_connection(Path(history.project))
            elif action == "context":
                context_menu(source, path)
            elif action == "usage":
                cli.usage(source, details=False)
            elif action == "history":
                cli.history(source)
        except typer.Exit:
            typer.echo("Action stopped. Saved progress is available in this menu.")
        except (OSError, ValueError, CodexError) as exc:
            typer.echo(str(exc), err=True)
