"""Sidebar settings and a reviewable AI team allocation."""

from pathlib import Path
from uuid import uuid4

import streamlit as st

from codex_orchestrator.allocation import (
    AllocationProposal,
    apply_allocation,
    fingerprint,
    proposal_path,
    propose_allocation,
)
from codex_orchestrator.design_models import AgentPreference, DesignSession
from codex_orchestrator.execution import is_busy


def allocation_sidebar(source_path, action):
    default = source_path.with_name(f"{source_path.stem}.design.json")
    path = Path(st.session_state.get(f"design_path_{source_path.name}", str(default)))
    with st.sidebar:
        st.divider()
        st.subheader("AI team")
        if not path.exists():
            st.caption("Create a technical design to choose your team and assign tasks.")
            return
        state = DesignSession.load(path)
        if not state.revisions:
            st.caption("Finish the technical design first.")
            return
        revision = state.revisions[-1]
        roster = state.agent_roster or [
            AgentPreference(id=a.id, role=a.role, model=revision.agent_models[a.id])
            for a in revision.design.agents
        ]
        prefix = f"team_{path.name}_{len(state.revisions)}"
        st.caption(f"Current design · {len(revision.design.agents)} agents")
        st.caption("Settings below configure the next suggestion.")
        count = st.number_input(
            "Number of AI agents",
            min_value=1,
            max_value=8,
            value=min(8, len(roster)),
            step=1,
            key=f"{prefix}_count",
        )
        st.caption("Logical team size. Implementation currently runs one task at a time.")
        with st.form(f"{prefix}_settings"):
            preferences = []
            for index in range(count):
                previous = roster[index] if index < len(roster) else None
                with st.expander(f"Agent {index + 1}", expanded=count <= 3):
                    role = st.text_input(
                        f"Agent {index + 1} role",
                        value=previous.role if previous else "General developer",
                    )
                    model = st.text_input(
                        f"Agent {index + 1} model",
                        value=(previous.model or "default") if previous else "default",
                    )
                    preferences.append(
                        {
                            "id": previous.id if previous else f"team-agent-{uuid4().hex[:8]}",
                            "role": role,
                            "model": model,
                        }
                    )
            disabled = is_busy(state.project) or state.phase not in {"review", "approved"}
            if st.form_submit_button("Suggest task assignments", disabled=disabled):

                def suggest():
                    parsed = []
                    for item in preferences:
                        if not item["model"].strip():
                            raise ValueError("Enter a model ID or default for each agent.")
                        parsed.append(
                            AgentPreference(
                                id=item["id"],
                                role=item["role"],
                                model=None
                                if item["model"].strip().casefold() == "default"
                                else item["model"].strip(),
                            )
                        )
                    propose_allocation(state, path, parsed)

                action("AI is assigning tasks to your team…", suggest)
        st.caption(
            "One planner request proposes assignments. Applying them creates a new revision; "
            "implementation starts only when you choose it."
        )
        saved = proposal_path(path)
        if not saved.exists():
            return
        proposal = AllocationProposal.load(saved)
        if (
            proposal.source_fingerprint != fingerprint(state)
            or proposal.roster != state.agent_roster
            or count != len(proposal.roster)
        ):
            st.caption("The previous suggestion belongs to older settings or a design revision.")
            return
        st.markdown("**Suggested allocation**")
        st.write(proposal.reply.rationale)
        for agent in proposal.roster:
            st.caption(f"{agent.role} · {agent.model or 'Codex default'}")
        st.caption(
            "Planner response · "
            + (proposal.reported_model or proposal.requested_model or "Codex default")
        )
        roles = {a.id: a.role for a in proposal.roster}
        titles = {t.id: t.title for t in revision.design.tasks}
        for assignment in proposal.reply.tasks:
            with st.expander(titles[assignment.task_id]):
                st.write(roles[assignment.agent_id])
                st.write(assignment.reason)
        st.caption("Stage verification ownership is included in this proposal.")
        with st.expander("Full allocation details"):
            st.json(proposal.reply.model_dump(), expanded=False)
        if st.button("Apply suggested team", disabled=disabled):
            action("Saving allocation for review…", lambda: apply_allocation(state, path, proposal))
