"""Dashboard controls for explicit coordinator dispatch and stage acceptance."""

from pathlib import Path

import streamlit as st

from codex_orchestrator import coordinator
from codex_orchestrator.codex import CodexError
from codex_orchestrator.design_models import DesignSession


def coordinator_panel(state, path, source_name, on_open, action):
    st.subheader("Execution coordinator")
    st.caption("Choose a delivery stage. Run one ready assignment, verify it, then continue.")
    stages = {s.id: s for s in state.revisions[-1].design.stages}
    stage = st.selectbox(
        "Delivery stage",
        list(stages),
        format_func=lambda i: stages[i].title,
        key=f"coordinator_stage_{path.name}_{len(state.revisions)}",
    )
    progress(path, stage, source_name, on_open, action)


@st.fragment(run_every="2s")
def progress(path: Path, stage_id: str, source_name, on_open, action):
    try:
        schedule = coordinator.stage_schedule(path, stage_id)
    except (OSError, ValueError, CodexError) as exc:
        st.warning(str(exc))
        return
    st.markdown(f"**Stage status · {schedule.status.replace('_', ' ')}**")
    if schedule.reason:
        st.info(schedule.reason)
    verified = sum(t.status == "verified" for t in schedule.tasks)
    st.progress(
        verified / len(schedule.tasks), text=f"{verified}/{len(schedule.tasks)} tasks verified"
    )
    controls = st.columns(3)
    if controls[0].button("Run next ready task", disabled=schedule.status != "ready"):
        action("Dispatching next assignment…", lambda: coordinator.run_next(path, stage_id))
    if controls[1].button("Pause coordinator", disabled=schedule.status == "paused"):
        action("Pausing and requesting stop…", lambda: coordinator.pause(path))
    if controls[2].button("Resume coordinator", disabled=schedule.status != "paused"):
        action("Resuming coordinator…", lambda: coordinator.resume(path))
    failed = any(t.status in {"failed", "stopped"} for t in schedule.tasks)
    if failed and schedule.status == "blocked":
        if st.button("Retry failed or stopped task"):
            action("Retrying assignment…", lambda: coordinator.run_next(path, stage_id, retry=True))
    st.caption(
        "Refresh only reads progress. Resume does not launch work; use Run next ready task. "
        "Pause requests cancellation; existing file changes remain for the next attempt."
    )
    for task in schedule.tasks:
        with st.container(border=True):
            st.markdown(f"**{task.title}** · {task.status.replace('_', ' ')}")
            st.caption(
                f"Agent · {task.agent_id} | Prerequisites · "
                + (", ".join(task.dependencies) or "None")
            )
            if st.button(
                "Inspect task and verification",
                key=f"inspect_{path.name}_{task.id}",
                on_click=on_open,
                args=(path.name, source_name, task.id),
            ):
                st.rerun()
    if schedule.status == "awaiting_stage_acceptance":
        state = DesignSession.load(path)
        stage = next(s for s in state.revisions[-1].design.stages if s.id == stage_id)
        st.markdown("**Stage verification gate**")
        st.write(
            "Review the combined changes and collect this evidence before accepting the stage."
        )
        for item in stage.gate.required_evidence:
            st.markdown(f"- {item}")
        st.markdown("**Your manual checks**")
        for check in stage.gate.user_checks:
            st.markdown(f"- {check}")
        with st.form(f"accept_stage_{path.name}_{stage_id}_{len(state.revisions)}"):
            evidence = st.text_area("Review and manual-test evidence")
            accepted = st.checkbox(
                "I reviewed the combined stage changes and passed the required checks"
            )
            if st.form_submit_button("Accept stage verification"):

                def accept():
                    if not accepted:
                        raise ValueError("Complete the stage checks before accepting verification.")
                    coordinator.accept_stage(path, stage_id, evidence)

                action("Recording stage acceptance…", accept)
    elif schedule.status == "accepted":
        st.success("Stage verification accepted. You can select the next approved stage.")
    st.caption(
        "Stage acceptance is recorded by you. This coordinator does not merge code "
        "or provide an independent AI review."
    )
