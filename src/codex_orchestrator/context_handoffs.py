"""Resolve declared upstream outputs without copying unrelated agent conversations."""

import hashlib
import json
from pathlib import Path

from codex_orchestrator.codex import CodexError
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


def archived_handoff_resource(state, task, reference, path, limits):
    """Read only an originally declared, verified handoff from this cycle's merged archive."""
    resource = ContextResource(
        kind="task_output",
        reference=reference.reference,
        required=reference.required,
        reason=reference.reason,
        missing_action=reference.missing_action,
        status="excluded",
    )
    from codex_orchestrator import stage_branches
    from codex_orchestrator.coordination_store import design_fingerprint
    from codex_orchestrator.correction_delivery import verified_merges
    from codex_orchestrator.cycle_store import find_history
    from codex_orchestrator.design import check_source
    from codex_orchestrator.design_models import DesignSession
    from codex_orchestrator.design_validation import SUMMARY_KINDS

    source_name, separator, producer_id = reference.reference.partition(":")
    try:
        if (
            not separator
            or not producer_id
            or not any(rev.origin == "correction" for rev in state.revisions)
        ):
            raise ValueError("Archived context is reserved for corrections.")
        source = Path(state.source_file)
        found = find_history(source)
        cycle = (
            next((c for c in found[1].cycles if source.name in c.session_names()), None)
            if found
            else None
        )
        archived = (
            next((a for a in cycle.archived_deliveries if a.session_name == source_name), None)
            if cycle
            else None
        )
        if not archived:
            raise ValueError("Archive does not belong to this cycle.")
        archived_source = source.parent / archived.session_name
        archived_path = archived_source.with_suffix(".design.json")
        old_state = DesignSession.load(archived_path)
        check_source(old_state)
        if (
            old_state.project != state.project
            or Path(old_state.source_file).resolve() != archived_source.resolve()
            or design_fingerprint(old_state) != archived.design_digest
        ):
            raise ValueError("The archived design or project changed.")
        old_tasks = {t.id: t for t in old_state.revisions[-1].design.tasks}
        old_task = old_tasks.get(task.id)
        producer = old_tasks.get(producer_id)
        if not old_task or old_task.agent_id != task.agent_id or not producer:
            raise ValueError("This agent did not receive the original handoff.")
        original = next(
            (
                r
                for r in old_task.context
                if r.kind in SUMMARY_KINDS
                and r.reference == producer_id
                and (r.required or not reference.required)
            ),
            None,
        )
        if original is None:
            raise ValueError("The handoff was not in the original approved assignment.")
        settings, _ = stage_branches.current(old_state, archived_path)
        merged = archived.merged_stages.get(producer.stage_id)
        if (
            not settings
            or not merged
            or (
                verified_merges(old_state, archived_path, settings.base).get(producer.stage_id)
                != merged
            )
        ):
            raise ValueError("Archived merge evidence changed.")
        restored = handoff_resource(old_state, old_task, original, archived_path, limits)
        restored.reference = reference.reference
        restored.reason = reference.reason
        restored.required = reference.required
        restored.missing_action = reference.missing_action
        return restored
    except (OSError, ValueError, CodexError, KeyError):
        resource.reason += " The original merged handoff could not be validated."
        return resource
