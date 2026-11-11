"""Run controls and live event display, separate from read-only plan chat."""

import json

import streamlit as st

from codex_orchestrator.execution import (
    Run,
    open_chat,
    run_directory,
    runs,
    start_run,
    stop_run,
    verify_run,
)


@st.fragment(run_every="2s")
def run_panel(state, path, task, context, action):
    candidates = [r for r in runs(path) if r.task_id == task.id]
    current = [r for r in candidates if r.revision == len(state.revisions)]
    if st.button("Start implementation", key=f"start_{path.name}_{task.id}"):
        action("Starting Codex…", lambda: start_run(state, path, task, context))
    st.caption(
        "Runs edit the local project using Codex's workspace-write sandbox. "
        "Owned paths are instructions; the sandbox covers the project. No automatic merge."
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
    events = folder / "events.jsonl"
    if events.exists():
        st.markdown("**Live Codex output**")
        for line in events.read_text(errors="replace").splitlines()[-60:]:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                with st.chat_message("assistant"):
                    st.write(item["text"])
            elif event.get("type") == "item.completed":
                with st.expander(item.get("type", "Tool output")):
                    st.json(item, expanded=False)
    if run.status == "failed" and (folder / "stderr.log").exists():
        with st.expander("Run diagnostics"):
            st.code((folder / "stderr.log").read_text(errors="replace")[-8000:])
    if current and run.revision == len(state.revisions) and run.status == "completed":
        accepted = st.checkbox(
            "I reviewed the output and passed this task's automated and human checks",
            key=f"accept_{run.id}",
        )
        if st.button("Accept verification", disabled=not accepted, key=f"verify_{run.id}"):
            action("Saving verification…", lambda: verify_run(path, run.id))
