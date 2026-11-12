"""Inspect a context preview without triggering inference or implementation."""

import streamlit as st

from codex_orchestrator.context_builder import build_context, update_context_limits
from codex_orchestrator.context_models import ContextLimits, ContextPackage
from codex_orchestrator.coordination_store import design_fingerprint


def context_preview(state, path, task, action):
    st.write("Build a preview of the resources this assignment will receive.")
    st.caption(
        "Execution rebuilds current inputs under the project lock. A preview is not a run "
        "or a filesystem permission boundary; Codex also loads its own configured instructions."
    )
    with st.expander("Context budgets"):
        st.caption(
            "Limits apply to future builds. Saved run snapshots stay unchanged. "
            "A byte budget is not a token or billing measurement."
        )
        with st.form(f"context_limits_{path.name}_{task.id}"):
            total = st.number_input(
                "Package budget (KiB)",
                min_value=1,
                max_value=2048,
                value=state.context_limits.max_package_bytes // 1024,
            )
            file_limit = st.number_input(
                "Per-file limit (KiB)",
                min_value=1,
                max_value=1024,
                value=max(1, state.context_limits.max_file_bytes // 1024),
            )
            handoff = st.number_input(
                "Handoff limit (KiB)",
                min_value=1,
                max_value=256,
                value=max(1, state.context_limits.max_handoff_bytes // 1024),
            )
            if st.form_submit_button("Save context budgets"):
                action(
                    "Saving context limits…",
                    lambda: update_context_limits(
                        state,
                        path,
                        ContextLimits(
                            max_package_bytes=total * 1024,
                            max_file_bytes=file_limit * 1024,
                            max_handoff_bytes=handoff * 1024,
                            max_resources=state.context_limits.max_resources,
                        ),
                    ),
                )
    key = (
        f"context_preview_{path}_{design_fingerprint(state)}_{task.id}_"
        + state.context_limits.model_dump_json()
    )
    if st.button("Build context preview", key=f"build_{key}"):
        st.session_state[key] = build_context(state, task, path).model_dump()
    if saved := st.session_state.get(key):
        package_details(ContextPackage.model_validate(saved), download_key=key)


def package_details(package, *, download_key):
    st.caption(
        f"Supplied package · {package.size_bytes():,} bytes / "
        f"{package.limits.max_package_bytes:,} byte budget · revision {package.design_revision}"
    )
    st.caption("SHA-256 · " + package.digest())
    if package.blockers:
        for blocker in package.blockers:
            st.error(blocker)
    else:
        st.success("Required context is available in this snapshot.")
    if package.warnings:
        with st.expander("Optional context warnings"):
            for warning in package.warnings:
                st.warning(warning)
    with st.expander("Approved assignment and contracts"):
        st.json(package.metadata, expanded=False)
    for item in package.resources:
        with st.expander(f"{item.kind} · {item.reference} · {item.status}"):
            st.caption("Required" if item.required else "Optional")
            st.write(item.reason)
            if item.status != "included":
                st.write("Next action · " + item.missing_action)
            if item.aliases:
                st.caption("Also used as · " + ", ".join(item.aliases))
            if item.source_run_id:
                st.caption("Source run · " + item.source_run_id)
            if item.sha256:
                st.caption("Included-content SHA-256 · " + item.sha256)
            if item.content is not None:
                st.code(item.content, language=None)
    st.download_button(
        "Download context package",
        package.payload(),
        file_name=f"{package.task_id}-context.json",
        mime="application/json",
        key=f"download_{download_key}",
    )
