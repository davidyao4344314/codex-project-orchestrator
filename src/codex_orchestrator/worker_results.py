"""Reconcile structured worker reports with observed commands and file changes."""

import shlex
from pathlib import Path

from codex_orchestrator.worker_models import CommandEvidence, WorkerResult

MAX_RESULT_BYTES = 256 * 1024
CHANGE_MISMATCH = "Reported changes do not match observed changed files."


def unchanged_retry_paths(run, spec, before, after):
    """Only inherited, auditable owned files may be omitted from a retry's net changes."""
    if not run.recovery_from:
        return set()
    unchanged = set()
    for name in spec.owned_paths:
        old, new = before.files.get(name), after.files.get(name)
        if (
            old is not None
            and new is not None
            and old.exists
            and new.exists
            and old.sha256
            and old.sha256 == new.sha256
            and not old.marker
            and not new.marker
        ):
            unchanged.add(name)
    return unchanged


def commands_match(reported: str | None, observed: str) -> bool:
    if reported is None:
        return False
    if observed == reported:
        return True
    # Codex's local exec events render an exact command as a login-shell invocation.
    # Match only the known, literal serialization of ONE complete command argument.
    # Never strip arbitrary wrappers, expand variables, normalize shell tokens,
    # accept extra arguments, or execute either string.
    escapes = str.maketrans({c: "\\" + c for c in '\\"$`'})
    literals = {shlex.quote(reported)}
    if "!" not in reported:
        literals.add('"' + reported.translate(escapes) + '"')
    return observed in {
        f"{shell} -lc {literal}" for shell in ("/bin/zsh", "/bin/bash") for literal in literals
    }


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


def reconcile_result(spec, report, evidence, *, unchanged_paths=()):
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
    if not required_ids <= set(actual_ids) or len(set(actual_ids)) != len(actual_ids):
        raise ValueError("Worker must report every planned automated check exactly once.")
    supplemental = sorted(set(actual_ids) - required_ids)
    if supplemental:
        warning = "Supplemental checks outside the approved test list: " + ", ".join(supplemental)
        if warning not in evidence.warnings:
            evidence.warnings.append(warning)
    reported_paths = [str(Path(c.path)) for c in report.changes]
    observed_paths = {c.path for c in evidence.changes}
    reported = set(reported_paths)
    inherited = reported - observed_paths
    if (
        len(reported) != len(reported_paths)
        or not observed_paths <= reported
        or not inherited <= (set(unchanged_paths) & set(spec.owned_paths))
    ):
        evidence.violations.append(CHANGE_MISMATCH)
    elif inherited:
        evidence.warnings.append(
            "Retry reported unchanged owned files as existing work: " + ", ".join(sorted(inherited))
        )
        report.changes = [
            change for change in report.changes if str(Path(change.path)) in observed_paths
        ]
    for path in reported_paths:
        if path not in spec.owned_paths:
            evidence.violations.append(f"Reported change is outside assigned ownership: {path}")
    for check in report.checks:
        if check.outcome == "not_run":
            continue
        # Require the exact recorded command or its known literal shell wrapper.
        matches = [c for c in evidence.commands if commands_match(check.command, c.command)]
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
