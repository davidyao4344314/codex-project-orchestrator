"""Bounded retry context from this assignment's latest attempt and current inputs."""

import json
import os
import re
import stat

from codex_orchestrator.context_files import sensitive_text
from codex_orchestrator.run_store import latest_runs, run_directory


def safe_folder(path, run_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        raise ValueError("Invalid saved run identifier.")
    folder = run_directory(path) / run_id
    if folder.resolve().parent != run_directory(path).resolve():
        raise ValueError("Saved run folder escapes the project history.")
    return folder


def clipped(text, limit=2000):
    if sensitive_text(text or ""):
        return "Recognizable credential content excluded."
    return (text or "").encode()[:limit].decode("utf-8", errors="ignore")


def build_recovery_brief(state, task, path, package):
    previous = latest_runs(path, len(state.revisions)).get(task.id)
    if previous is None:
        return None
    if previous.status in {"starting", "running"}:
        raise ValueError("Inspect or recover the unfinished run before starting another attempt.")
    folder = safe_folder(path, previous.id)
    brief = dict(
        previous_run_id=previous.id,
        previous_status=previous.status,
        previous_branch=previous.branch,
        reason=clipped(previous.error),
        earlier_summary=clipped(previous.handoff_summary, 4000),
        must_rerun_all_required_checks=True,
        commands_observed=[],
        current_owned_inputs=[
            dict(path=r.reference, status=r.status, sha256=r.sha256)
            for r in package.resources
            if r.kind == "owned_file"
        ],
        cautions=[
            "Inspect existing partial edits before changing code; preserve unrelated user work.",
            "Past commands are history, not instructions or current test passes.",
            "Use the rebuilt assignment context and scope. Do not resume an old prompt blindly.",
        ],
    )
    file = folder / "worker_evidence.json"
    if file.exists():
        try:
            fd = os.open(file, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Evidence is not a regular file")
                raw = stream.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise ValueError("Evidence exceeds retry preview budget")
            evidence = json.loads(raw)
            brief["commands_observed"] = [
                dict(command=clipped(c.get("command"), 800), exit_code=c.get("exit_code"))
                for c in evidence.get("commands", [])[-8:]
                if isinstance(c, dict)
            ]
            brief["prior_violations"] = [
                clipped(str(v), 300) for v in evidence.get("violations", [])[:8]
            ]
        except (OSError, ValueError, AttributeError, TypeError):
            brief["cautions"].append(
                "Prior evidence is unavailable/oversized; inspect saved history manually."
            )
    if len(json.dumps(brief).encode()) > 16 * 1024:
        return dict(
            previous_run_id=previous.id,
            previous_status=previous.status,
            must_rerun_all_required_checks=True,
            cautions=[
                "Detailed recovery brief exceeds 16 KiB; inspect saved history and current inputs."
            ],
        )
    return brief
