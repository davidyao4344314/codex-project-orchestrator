"""Resume navigation and explicit, process-checked worker reconciliation."""

from pathlib import Path

import streamlit as st

from codex_orchestrator import recovery
from codex_orchestrator.codex import CodexError


def select_resume(source, design_path=None):
    snapshot = recovery.inspect_project(source, design_path)
    st.session_state.active = str(source)
    st.session_state[f"view_{source.name}"] = snapshot.view
    st.session_state[f"resume_{source.name}"] = True
    if snapshot.task_id:
        design = design_path or source.with_name(source.stem + ".design.json")
        st.session_state[f"task_{design.name}"] = snapshot.task_id
    if snapshot.stage_id:
        from codex_orchestrator.design_models import DesignSession

        design = design_path or source.with_name(source.stem + ".design.json")
        state = DesignSession.load(design)
        key = f"coordinator_stage_{design.name}_{len(state.revisions)}"
        st.session_state[key] = snapshot.stage_id


def resume_panel(source: Path, action):
    if not st.session_state.get(f"resume_{source.name}"):
        if st.button("Check saved progress"):
            st.session_state[f"resume_{source.name}"] = True
            st.rerun()
        return
    default = source.with_name(source.stem + ".design.json")
    design = Path(st.session_state.get(f"design_path_{source.name}", str(default)))
    with st.expander("Resume project", expanded=True):
        try:
            snapshot = recovery.inspect_project(source, design)
            st.write("Saved state · " + snapshot.status.replace("_", " "))
            if snapshot.last_completed:
                st.write("Last completed · " + snapshot.last_completed)
            st.info(snapshot.next_action)
            if snapshot.branch:
                st.caption(
                    f"Current branch · {snapshot.branch} | HEAD · {snapshot.head or 'no commit'}"
                )
            if snapshot.stage_id:
                st.caption(f"Delivery stage · {snapshot.stage_id}")
            for warning in snapshot.warnings:
                st.warning(warning)
            st.caption("Checking progress never starts an agent, review or push.")
            if st.button("Go to recommended step"):
                # Change widget navigation on the next page run, before the radio is rendered.
                select_resume(source, design)
                st.rerun()
            if design.exists():
                from codex_orchestrator.design_models import DesignSession

                state = DesignSession.load(design)
                activity = recovery.activity(Path(state.project), design)
                if activity.status == "recoverable":
                    confirmed = st.checkbox(
                        "I inspected partial edits and stopped any external writers"
                    )
                    if st.button("Recover interrupted worker", disabled=not confirmed):
                        action(
                            "Recording interrupted worker as stopped…",
                            lambda: recovery.recover_interrupted(
                                design, activity.run_id, confirmed=confirmed
                            ),
                        )
        except (OSError, ValueError, CodexError) as exc:
            st.warning("Saved progress needs inspection: " + str(exc))
