"""Explicit stage-branch setup, switching and merge checkpoints."""

import streamlit as st

from codex_orchestrator import stage_branches as branches


def stage_branch_panel(state, path, stage_id, action):
    with st.expander("Delivery stage branch", expanded=True):
        settings = branches.load(path)
        if settings is None or branches.revision_key(state) not in settings.revisions:
            st.caption(
                "Enable one branch per delivery stage for this design revision. Existing projects "
                "keep their legacy execution until enabled. Start with a clean, committed base; "
                "ignore generated sessions and logs before setup."
            )
            base = st.text_input(
                "Integration base branch", value=settings.base if settings else "main"
            )
            if st.button("Enable stage branches for this revision"):
                action("Setting up stage branches…", lambda: branches.enable(path, base))
            return
        revision = settings.revisions[branches.revision_key(state)]
        item = revision.stages.get(stage_id)
        st.write("Base · " + settings.base)
        st.write(
            "Stage branch · "
            + (item.branch if item else branches.branch_name(state, path, stage_id))
        )
        if item and item.merged_commit:
            st.success("Merge recorded · " + item.merged_commit)
        else:
            if st.button("Create or switch to stage branch"):
                action("Preparing stage branch…", lambda: branches.prepare(path, stage_id))
            st.caption(
                "Run tasks here, verify and accept the stage, obtain AI review, then give push "
                "permission in Git connection. Merge on your Git host or in your terminal. "
                "Use a normal merge or fast-forward; squash/rebase merges are not recognized."
            )
            if st.button("Refresh base from origin"):
                action(
                    "Fetching and fast-forwarding the base…", lambda: branches.refresh_base(path)
                )
            if st.button("Record stage merge into base"):
                action(
                    "Checking approved commit ancestry…",
                    lambda: branches.record_merge(path, stage_id),
                )
        st.caption(
            "Refresh only reads branch state. The app never automatically merges stage code."
        )
