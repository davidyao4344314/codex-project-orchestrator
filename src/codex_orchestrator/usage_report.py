"""Read-only aggregation of observed Codex turns across one cycle's deliveries."""

from pathlib import Path

from codex_orchestrator.cycle_store import find_history
from codex_orchestrator.session import PlanningSession
from codex_orchestrator.session_manager import TokenUsage, read_registry, read_turns


def checkpoints(source: Path):
    source = source.resolve()
    found = find_history(source)
    if found:
        cycle = next(c for c in found[1].cycles if source.name in c.session_names())
        names = [a.session_name for a in cycle.archived_deliveries] + [cycle.session_name]
        number = cycle.number
    else:
        PlanningSession.load(source)
        names, number = [source.name], None
    paths = [
        checkpoint
        for name in names
        for checkpoint in (source.parent / name, (source.parent / name).with_suffix(".design.json"))
    ]
    return number, source.parent / names[-1], paths


def stats(records):
    totals, coverage = {}, {}
    for field in TokenUsage.model_fields:
        values = [
            getattr(r.usage, field)
            for r in records
            if r.usage is not None and getattr(r.usage, field) is not None
        ]
        totals[field] = sum(values) if values else None
        coverage[field] = len(values)
    tools = [r.observed_tool_calls for r in records if r.observed_tool_calls is not None]
    uncached = [
        r.usage.input_tokens - r.usage.cached_input_tokens
        for r in records
        if r.usage is not None
        and r.usage.input_tokens is not None
        and r.usage.cached_input_tokens is not None
    ]
    totals["uncached_input_tokens"] = sum(uncached) if uncached else None
    coverage["uncached_input_tokens"] = len(uncached)
    commands = [r.observed_commands for r in records if r.observed_commands is not None]
    return {
        "attempts": len(records),
        "statuses": {
            status: sum(r.status == status for r in records)
            for status in ("started", "completed", "failed", "blocked", "stopped")
        },
        "resume_attempts": sum(r.requested_thread_id is not None for r in records),
        "confirmed_resumes": sum(
            r.requested_thread_id is not None and r.thread_id == r.requested_thread_id
            for r in records
        ),
        "elapsed_seconds": round(sum(r.elapsed_seconds or 0 for r in records), 2),
        "prompt_bytes": sum(r.prompt_bytes for r in records),
        "full_vs_compact_prompt_bytes_saved": sum(
            r.full_prompt_bytes - r.prompt_bytes for r in records if r.full_prompt_bytes is not None
        ),
        "compacted_prompt_count": sum(r.full_prompt_bytes is not None for r in records),
        "reused_context_files": sum(r.reused_context_files for r in records),
        "token_totals": totals,
        "token_coverage_turns": coverage,
        "observed_tool_calls": sum(tools) if tools else None,
        "observed_commands": sum(commands) if commands else None,
        "tool_event_coverage_turns": len(tools),
    }


def _groups(records, field):
    values = sorted({getattr(r, field) for r in records if getattr(r, field) is not None})
    return {value: stats([r for r in records if getattr(r, field) == value]) for value in values}


def usage_report(source: Path, *, details=False):
    number, current, paths = checkpoints(source)
    records = sorted(
        (record for path in paths for record in read_turns(path)),
        key=lambda record: record.started_at,
    )
    report = {
        "cycle": number,
        "current_session": str(current),
        "note": (
            "Observed CLI events, not account billing. Missing usage is unknown, not zero. "
            "Cached input and reasoning counts are subsets, not additional totals. "
            "Fresh/resumed groups are observational, not a controlled benchmark. "
            "Tool counts cover recognized completed CLI items only. "
            "Only managed planning, worker and reviewer turns are counted; earlier "
            "and ad-hoc calls are not reconstructed."
        ),
        "total": stats(records),
        "by_role": _groups(records, "role"),
        "by_stage": _groups(records, "stage_id"),
        "by_task": _groups(records, "task_id"),
        "by_requested_model": {
            **_groups(records, "requested_model"),
            **(
                {"Codex default": stats([r for r in records if r.requested_model is None])}
                if any(r.requested_model is None for r in records)
                else {}
            ),
        },
        "by_session_mode": {
            "fresh": stats([r for r in records if r.requested_thread_id is None]),
            "resumed": stats([r for r in records if r.requested_thread_id is not None]),
        },
    }
    if details:
        report["turns"] = [record.model_dump(mode="json") for record in records]
    return report


def session_report(source: Path):
    number, current, paths = checkpoints(source)
    bindings = []
    active_paths = {current, current.with_suffix(".design.json")}
    for path in paths:
        for role, slot in sorted(read_registry(path).slots.items()):
            binding = slot.binding
            bindings.append(
                {
                    "checkpoint": str(path),
                    "current_delivery": path in active_paths,
                    "role": role,
                    "thread_id": binding.thread_id if binding else None,
                    "turns": binding.turns if binding else None,
                    "saved_binding": binding is not None,
                }
            )
    return {
        "cycle": number,
        "current_session": str(current),
        "note": ("Saved bindings are rechecked before dispatch; reviewer is always fresh."),
        "sessions": bindings,
    }
