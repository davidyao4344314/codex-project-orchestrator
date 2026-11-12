"""Resolve declared upstream outputs without copying unrelated agent conversations."""

import hashlib
import json
from pathlib import Path

from codex_orchestrator.context_files import sensitive_text
from codex_orchestrator.context_models import ContextLimits, ContextResource
from codex_orchestrator.design_validation import ancestors, task_graph
from codex_orchestrator.run_store import latest_runs, read_tail, run_directory


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
    run = latest_runs(path, len(state.revisions)).get(producer.id)
    if run is None or run.status != "completed" or not run.verified:
        return resource
    resource.source_run_id = run.id
    summary = run.handoff_summary
    truncated = run.handoff_truncated
    if summary is None:
        events = run_directory(path) / run.id / "events.jsonl"
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
