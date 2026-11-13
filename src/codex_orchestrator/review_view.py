"""Explicit review actions and optional reviewer-approved publication controls."""

import streamlit as st

from codex_orchestrator import review_agent, review_store


def review_settings(project, path, action):
    policy = review_store.load_policy(path)
    with st.expander("Review and publishing settings"):
        st.caption(
            "Human approval is the default. Reviewer-approved publishing is restricted to an "
            "orchestrator/ branch and the saved remote. Stage tests still need human acceptance. "
            "Push includes existing branch history. No automatic merge or force push."
        )
        with st.form(f"review_branch_{path}"):
            branch = st.text_input("New development branch", value="orchestrator/stage-work")
            if st.form_submit_button("Create development branch"):
                action(
                    "Creating development branch…",
                    lambda: review_agent.create_branch(project, branch),
                )
        with st.form(f"review_policy_{path}"):
            model = st.text_input("Reviewer model", value=policy.reviewer_model or "default")
            enabled = st.checkbox(
                "Allow reviewer-approved commit and push", value=policy.allow_reviewer_push
            )
            if st.form_submit_button("Save review settings"):
                action(
                    "Saving review policy…",
                    lambda: review_agent.save_policy(
                        path, enabled, None if model.strip() in {"", "default"} else model.strip()
                    ),
                )
        if policy.allow_reviewer_push:
            st.caption(f"Enabled destination · {policy.remote} · {policy.branch}")


def review_controls(path, reviewed, message, action, *, disabled=False):
    st.markdown("**Independent review agent**")
    st.caption(
        "A separate read-only Codex turn inspects selected changes and recorded test evidence. "
        "A new review uses account usage. Refresh never starts a review or push."
    )
    if st.button("Run independent review", disabled=disabled):
        action("Reviewing selected changes…", lambda: review_agent.run_review(path, reviewed))
    policy = review_store.load_policy(path)
    if policy.allow_reviewer_push:
        if st.button("Review and publish if approved", disabled=disabled):
            action(
                "Reviewing and publishing only if approved…",
                lambda: review_agent.run_review(path, reviewed, publish=True, message=message),
            )
    records = [
        r for r in review_store.load_reviews(path).records if r.stage_id == reviewed.stage_id
    ]
    if records:
        record = records[-1]
        st.write(f"Latest review · {record.source} · {record.result.decision}")
        if record.fingerprint != reviewed.fingerprint:
            st.warning("This review belongs to an older preview and cannot approve these changes.")
        st.write(record.result.summary)
        if record.source == "reviewer":
            st.caption(
                f"Reviewer model · {record.reported_model or 'unreported'} "
                f"(requested {record.requested_model or 'Codex default'})"
            )
        for finding in record.result.findings:
            st.write(
                f"{'Blocking' if finding.blocking else 'Note'} · "
                f"{finding.location}: {finding.detail}"
            )
        for check in record.result.additional_human_checks:
            st.write("Required human check · " + check)

        if record.input_id and st.button("Load reviewer input"):
            saved = review_store.ReviewInput.load(review_store.input_path(path, record.input_id))
            st.code(saved.content, language="json")
