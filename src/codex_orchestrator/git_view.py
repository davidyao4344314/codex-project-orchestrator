"""Git controls; repository writes happen only on explicit button actions."""

from pathlib import Path

import streamlit as st

from codex_orchestrator import git_service as service
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.review_view import review_controls, review_settings


def git_panel(project: Path, design_path: Path, action):
    with st.expander("Git connection"):
        st.caption("Use your existing Git credentials. Never paste a token into a repository URL.")
        try:
            if not service.repository(project):
                st.info("No Git repository rooted at this project folder.")
                if st.button("Initialize Git repository"):
                    action("Initializing Git…", lambda: service.initialize(project))
                return
            branch = service.git(project, "symbolic-ref", "--short", "HEAD", optional=True)
            st.write("Branch · " + (branch or "Detached HEAD"))
            remote = service.git(project, "remote", "get-url", "origin", optional=True)
            if not remote:
                with st.form(f"git_connect_{design_path}"):
                    url = st.text_input(
                        "Repository URL", placeholder="https://github.com/you/project.git"
                    )
                    if st.form_submit_button("Connect to Git"):
                        action("Connecting repository…", lambda: service.connect(project, url))
                st.caption("Create the remote repository first. Connecting saves origin locally.")
                return
            service.validate_remote(remote)
            st.write("Connected · " + remote)
            st.caption(
                "If authentication fails, sign in using GitHub Desktop or gh auth login in your "
                "terminal and retry. Git credentials stay outside project session files."
            )
            if not design_path.exists():
                return
            state = DesignSession.load(design_path)
            if not state.revisions:
                return
            review_settings(project, design_path, action)
            stages = {s.id: s.title for s in state.revisions[-1].design.stages}
            stage = st.selectbox("Stage to publish", list(stages), format_func=stages.get)
            key = f"git_preview_{design_path}"
            if st.button("Preview accepted stage changes"):
                try:
                    st.session_state[key] = service.preview(design_path, stage)
                except (OSError, ValueError) as exc:
                    st.session_state.pop(key, None)
                    st.warning(str(exc))
            reviewed = st.session_state.get(key)
            if reviewed and reviewed.stage_id == stage:
                st.caption(
                    f"Commit target · {reviewed.branch} · {reviewed.head or 'initial commit'}"
                )
                selected = st.multiselect("Files to commit", reviewed.paths, default=reviewed.paths)
                st.code(reviewed.diff or "No text diff available", language="diff")
                message = st.text_input("Commit message", value=f"Implement stage {stage}")
                confirmed = st.checkbox("I reviewed these files and want to commit them")
                same_selection = set(selected) == set(reviewed.paths)
                if not same_selection and st.button(
                    "Update preview for selected files", disabled=not selected
                ):
                    try:
                        st.session_state[key] = service.preview(design_path, stage, selected)
                        st.rerun()
                    except (OSError, ValueError) as exc:
                        st.warning(str(exc))
                if st.button(
                    "Commit reviewed AI changes", disabled=not confirmed or not same_selection
                ):

                    def save_commit():
                        approval = service.approve_user(design_path, reviewed)
                        service.commit(design_path, reviewed, message, approval_id=approval.id)
                        st.session_state.pop(key, None)

                    action("Committing reviewed changes…", save_commit)
                review_controls(design_path, reviewed, message, action, disabled=not same_selection)
            receipt = service.publication_path(design_path)
            if receipt.exists():
                record = service.GitPublication.load(receipt)
                st.write(f"Stage {record.stage_id} commit · {record.commit}")
                st.caption(f"Push destination · {record.remote} · branch {record.branch}")
                if record.pushed:
                    st.success("Push completed.")
                else:
                    st.info(
                        "Push publishes this commit and its branch history. No force push or merge."
                    )
                    if st.button("Push committed AI changes"):
                        action("Pushing commit…", lambda: service.push(design_path))
        except (OSError, ValueError) as exc:
            st.warning(str(exc))
