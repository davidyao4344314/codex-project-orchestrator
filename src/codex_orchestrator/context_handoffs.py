"""Resolve declared upstream outputs without copying unrelated agent conversations."""

import hashlib
import json
from pathlib import Path

from codex_orchestrator.context_files import sensitive_text
from codex_orchestrator.context_models import ContextLimits, ContextResource
from codex_orchestrator.design_validation import ancestors, task_graph
from codex_orchestrator.run_store import latest_runs, read_tail


def handoff_resource(state, task, reference, path: Path | None, limits: ContextLimits):
    resource = ContextResource(
        kind="task_output",
        reference=reference.reference,
        required=reference.required,
        reason=reference.reason,
        missing_action=reference.missing_action,
        status="pending",
    )
    design = state.revisions[-1].design
    by_id = {t.id: t for t in design.tasks}
    producer = by_id.get(reference.reference)
    if producer is None or reference.reference not in ancestors(task_graph(design))[task.id]:
        resource.status = "excluded"
        resource.reason += " Only declared prerequisite outputs may enter this context."
        return resource
    if producer.agent_id != task.agent_id and task.agent_id not in producer.handoff_to:
        resource.status = "excluded"
        resource.reason += " The producer has not declared this agent as a handoff recipient."
        return resource
    if path is None:
        return resource
    from codex_orchestrator.cycle_feedback import needs_retry
    from codex_orchestrator.stage_branches import valid_runs

    try:
        run = valid_runs(state, path, latest_runs(path, len(state.revisions))).get(producer.id)
    except ValueError:
        return resource
    if (
        run is None
        or run.status != "completed"
        or not run.verified
        or run.agent_id != producer.agent_id
        or needs_retry(state, path, producer.id, run)
    ):
        return resource
    resource.source_run_id = run.id
    if run.worker_contract_version == 1:
        from codex_orchestrator.recovery_brief import safe_folder
        from codex_orchestrator.worker_models import artifact_digest
        from codex_orchestrator.worker_results import load_result

        try:
            report = load_result(safe_folder(path, run.id) / "worker_result.json")
            if (
                artifact_digest(report) != run.worker_result_digest
                or report.task_id != producer.id
                or report.agent_id != producer.agent_id
                or report.context_digest != run.context_digest
                or report.outcome != "completed"
            ):
                raise ValueError("Worker handoff report does not match verification.")
            summary = compact_handoff(report)
            truncated = False
        except (OSError, ValueError):
            resource.status = "excluded"
            resource.reason += " Verified handoff evidence is missing or changed."
            return resource
    else:
        summary = run.handoff_summary
        truncated = run.handoff_truncated
    if run.worker_contract_version is None and (summary is None or truncated):
        from codex_orchestrator.recovery_brief import safe_folder

        try:
            events = safe_folder(path, run.id) / "events.jsonl"
        except ValueError:
            resource.status = "excluded"
            return resource
        if events.exists():
            for line in read_tail(events):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                item = event.get("item") if isinstance(event, dict) else None
                if (
                    isinstance(event, dict)
                    and event.get("type") == "item.completed"
                    and isinstance(item, dict)
                    and item.get("type") == "agent_message"
                    and isinstance(item.get("text"), str)
                ):
                    summary = item["text"]
                    truncated = False
    if not summary or not summary.strip():
        resource.status = "missing"
        resource.reason += " The verified run has no saved handoff summary."
        return resource
    if sensitive_text(summary):
        resource.status = "excluded"
        resource.reason += " Recognizable credential content was excluded."
        return resource
    raw = summary.encode("utf-8")
    resource.size_bytes = len(raw)
    truncated |= len(raw) > limits.max_handoff_bytes
    resource.content = raw[: limits.max_handoff_bytes].decode("utf-8", errors="ignore")
    resource.sha256 = hashlib.sha256(resource.content.encode("utf-8")).hexdigest()
    resource.status = "truncated" if truncated else "included"
    return resource


def compact_handoff(report):
    """Keep the implemented contract, outcomes and caveats; detailed logs remain on disk."""
    sections = list(dict.fromkeys([report.summary, report.handoff]))
    if report.changes:
        sections.append("Changed files: " + ", ".join(c.path for c in report.changes))
    if report.checks:
        sections.append("Checks: " + ", ".join(f"{c.check_id}: {c.outcome}" for c in report.checks))
    if report.human_checks:
        sections.append("User checks: " + "; ".join(report.human_checks))
    return "\n".join(sections)
