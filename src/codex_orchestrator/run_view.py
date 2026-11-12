"""Run controls and live event display, separate from read-only plan chat."""

import json

import streamlit as st

from codex_orchestrator.context_models import ContextPackage
from codex_orchestrator.context_view import package_details
from codex_orchestrator.execution import (
    open_chat,
    start_run,
    stop_run,
    verify_run,
)
from codex_orchestrator.run_store import Run, latest_runs, read_tail, run_directory, runs
from codex_orchestrator.worker_view import worker_report_view


@st.fragment(run_every="2s")
def run_panel(state, path, task, context, action):
    candidates = [r for r in runs(path) if r.task_id == task.id]
    current = [r for r in candidates if r.revision == len(state.revisions)]
    profile = str(task.workstream) if task.owned_paths else "verification"
    st.caption(f"Planned worker · {profile}")
    if st.button("Start implementation", key=f"start_{path.name}_{task.id}"):
        action("Starting Codex…", lambda: start_run(state, path, task, context))
    st.caption(
        "Implementation workers use workspace-write; verification workers use read-only. "
        "File ownership is checked after execution. No automatic merge or rollback."
    )
    if not candidates:
        st.info("No implementation run for this assignment yet.")
        return
    selected = st.selectbox(
        "Implementation run",
        [r.id for r in reversed(candidates)],
        format_func=lambda i: next(
            f"Revision {r.revision} · {r.status} · {r.id[:8]}" for r in candidates if r.id == i
        ),
        key=f"run_{path.name}_{task.id}",
    )
    folder = run_directory(path) / selected
    run = Run.load(folder / "run.json")
    st.write(
        f"**{run.status.title()}** · "
        f"{'Verification accepted' if run.verified else 'Verification pending'}"
    )
    if run.revision != len(state.revisions):
        st.caption("Historical run: its context and agent belong to an earlier design revision.")
    st.caption(f"Agent · {run.agent_id} | Requested model · {run.model or 'Codex default'}")
    if run.thread_id:
        if st.button("Open chat in Codex", key=f"open_chat_{run.id}"):
            action("Opening Codex chat…", lambda: open_chat(run.thread_id))
    else:
        st.caption("The Codex chat link appears when Codex reports its thread ID.")
    if run.status in {"starting", "running"}:
        if st.button("Stop run", key=f"stop_{run.id}"):
            action("Requesting stop…", lambda: stop_run(run))
    if run.error:
        st.error(run.error)
    with st.expander("Exact context sent to Codex"):
        st.code(run.prompt, language="text")
        st.download_button(
            "Download supplied context", run.prompt, file_name=f"{run.id}-context.txt"
        )
    if (folder / "context.json").exists():
        with st.expander("Context package used for this run"):
            snapshot_key = f"recorded_context_{run.id}"
            if st.button("Load recorded context package", key=f"load_{run.id}"):
                st.session_state[snapshot_key] = ContextPackage.load(
                    folder / "context.json"
                ).model_dump()
            if data := st.session_state.get(snapshot_key):
                package = ContextPackage.model_validate(data)
                if run.context_digest and package.digest() != run.context_digest:
                    st.warning("This context file differs from the run's recorded hash.")
                package_details(package, download_key=run.id)
    worker_report_view(run, folder)
    events = folder / "events.jsonl"
    if events.exists():
        st.markdown("**Live Codex output**")
        for line in read_tail(events):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item", {})
            if not isinstance(item, dict):
                continue
            if item.get("type") == "agent_message" and item.get("text"):
                if run.worker_contract_version and str(item["text"]).lstrip().startswith("{"):
                    st.caption("Structured final message is shown in Worker report.")
                else:
                    with st.chat_message("assistant"):
                        st.write(item["text"])
            elif event.get("type") == "item.completed":
                with st.expander(item.get("type", "Tool output")):
                    st.json(item, expanded=False)
    if run.status == "failed" and (folder / "stderr.log").exists():
        with st.expander("Run diagnostics"):
            st.code("\n".join(read_tail(folder / "stderr.log", max_bytes=8000)))
    if (
        current
        and run.revision == len(state.revisions)
        and run.status == "completed"
        and latest_runs(path, run.revision)[task.id].id == run.id
    ):
        accepted = st.checkbox(
            "I reviewed the output and passed this task's automated and human checks",
            key=f"accept_{run.id}",
        )
        if st.button("Accept verification", disabled=not accepted, key=f"verify_{run.id}"):
            action("Saving verification…", lambda: verify_run(path, run.id))
