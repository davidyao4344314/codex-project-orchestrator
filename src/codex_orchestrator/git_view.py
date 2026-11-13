"""Git controls; repository writes happen only on explicit button actions."""

from pathlib import Path

import streamlit as st

from codex_orchestrator import git_service as service
from codex_orchestrator import review_store
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.review_view import review_controls, review_settings
from codex_orchestrator.worker_models import artifact_digest


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
                try:
                    review_store.require_reviewer(design_path, stage, reviewed.fingerprint)
                    ai_approved = True
                except ValueError:
                    ai_approved = False
                    st.info("Run independent AI review and resolve its findings before committing.")
                if st.button(
                    "Commit reviewed AI changes",
                    disabled=not confirmed or not same_selection or not ai_approved,
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
                    try:
                        reviewer = review_store.require_reviewer(
                            design_path, record.stage_id, record.reviewed_fingerprint or ""
                        )
                    except ValueError as exc:
                        st.warning(str(exc))
                        return
                    with st.expander("AI review for this commit"):
                        st.write(reviewer.result.summary)
                        for finding in reviewer.result.findings:
                            st.write(f"{finding.location}: {finding.detail}")
                    st.info(
                        "The AI review approved these changes. Do you give permission to push "
                        "the commit shown above? This also publishes its branch history."
                    )
                    permission = service.PushPermission(
                        commit=record.commit, branch=record.branch, remote=record.remote
                    )
                    # Scope the checkbox to the displayed destination, not a global preference.
                    allowed = st.checkbox(
                        "I give permission to push this commit to the destination shown above",
                        key="push_permission_" + artifact_digest(permission),
                    )
                    if st.button("Approve and push this commit", disabled=not allowed):
                        action(
                            "Pushing your approved commit…",
                            lambda: service.push(design_path, permission=permission),
                        )
        except (OSError, ValueError) as exc:
            st.warning(str(exc))
