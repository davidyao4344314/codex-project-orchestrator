"""Reconcile structured worker reports with observed commands and file changes."""

import shlex
from pathlib import Path

from codex_orchestrator.worker_models import CommandEvidence, WorkerResult

MAX_RESULT_BYTES = 256 * 1024


def command_key(command):
    try:
        parts = shlex.split(command)
        for _ in range(3):
            if (
                len(parts) >= 3
                and Path(parts[0]).name in {"sh", "bash", "zsh"}
                and parts[1] in {"-c", "-lc"}
            ):
                parts = shlex.split(parts[2])
            else:
                break
        return tuple(parts)
    except ValueError:
        return (command.strip(),)


def observe_command(event):
    item = event.get("item")
    if (
        event.get("type") == "item.completed"
        and isinstance(item, dict)
        and item.get("type") == "command_execution"
        and isinstance(item.get("command"), str)
    ):
        from codex_orchestrator.context_files import sensitive_text

        output = str(item.get("aggregated_output", ""))
        if sensitive_text(output):
            output = "Recognizable credential content excluded from the evidence preview."
        return CommandEvidence(
            event_id=item.get("id"),
            command=item["command"],
            exit_code=item.get("exit_code"),
            output=output[-8000:],
        )
    return None


def load_result(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_RESULT_BYTES + 1)
    if len(raw) > MAX_RESULT_BYTES:
        raise ValueError("Worker result exceeds the 256 KiB result limit.")
    return WorkerResult.model_validate_json(raw)


def reconcile_result(spec, report, evidence):
    if (report.task_id, report.agent_id, report.profile, report.context_digest) != (
        spec.task_id,
        spec.agent_id,
        spec.profile,
        spec.context_digest,
    ):
        raise ValueError(
            "Worker report does not match its task, agent, profile, or context snapshot."
        )
    required_ids = {check.id for check in spec.checks}
    actual_ids = [check.check_id for check in report.checks]
    if set(actual_ids) != required_ids or len(actual_ids) != len(required_ids):
        raise ValueError("Worker must report every planned automated check exactly once.")
    reported_paths = [str(Path(c.path)) for c in report.changes]
    observed_paths = {c.path for c in evidence.changes}
    if len(set(reported_paths)) != len(reported_paths) or set(reported_paths) != observed_paths:
        evidence.violations.append("Reported changes do not match observed changed files.")
    for path in reported_paths:
        if path not in spec.owned_paths:
            evidence.violations.append(f"Reported change is outside assigned ownership: {path}")
    for check in report.checks:
        if check.outcome == "not_run":
            continue
        matches = [
            c for c in evidence.commands if command_key(c.command) == command_key(check.command)
        ]
        match = matches[-1] if matches else None
        expected_pass = check.outcome == "passed"
        if match is None or match.exit_code is None or (match.exit_code == 0) != expected_pass:
            evidence.violations.append(
                f"No matching command/exit-code evidence for check {check.check_id}."
            )
        else:
            evidence.check_matches[check.check_id] = match.event_id or match.command
    # The user must see all mandatory manual checks, even if omitted by the model.
    report.human_checks = list(dict.fromkeys(spec.human_checks + report.human_checks))
    if report.outcome == "failed":
        return "failed"
    if report.outcome == "blocked" or evidence.violations:
        return "blocked"
    return "completed"


def handoff_text(report):
    return (
        report.summary
        + "\n\n"
        + report.handoff
        + "\n\nChecks:\n"
        + "\n".join(f"{c.check_id}: {c.outcome} — {c.detail}" for c in report.checks)
        + "\n\nUser checks:\n"
        + "\n".join(report.human_checks)
    )
