"""Technical workflow rendering and stage review controls."""

from graphlib import TopologicalSorter
from pathlib import Path

import streamlit as st

from codex_orchestrator.design import generate_design, revise_design
from codex_orchestrator.design_validation import skill_status, task_graph
from codex_orchestrator.interactive import model_label
from codex_orchestrator.stage_review import current_review, review_stage
from codex_orchestrator.web_widgets import bullet_list


def workflow_view(state, path, action):
    revision = state.revisions[-1]
    design = revision.design
    project = Path(state.project)
    st.write(design.overview)
    st.caption(
        f"Design revision {len(state.revisions)} · {revision.origin.replace('_', ' ')} · "
        f"{model_label(revision.requested_model, revision.reported_model)}"
    )
    with st.expander("Technical structure", expanded=True):
        st.dataframe(
            [
                {
                    "Component": item.name,
                    "Responsibility": item.responsibility,
                    "Paths": ", ".join(item.paths),
                }
                for item in design.components
            ],
            hide_index=True,
            use_container_width=True,
        )
        st.markdown("**Data flow**")
        bullet_list(design.data_flow)
        for interface in design.interfaces:
            st.markdown(f"**{interface.id}**")
            st.write(
                f"{interface.source_component} → {interface.target_component or 'user / external'}"
            )
            st.write("Inputs", ", ".join(interface.inputs))
            st.write("Outputs", ", ".join(interface.outputs))
        if design.storage:
            st.markdown("**Storage**")
            bullet_list(design.storage)
        if design.exclusions:
            st.markdown("**Scope boundaries**")
            bullet_list(design.exclusions)
        if design.assumptions:
            st.markdown("**Assumptions**")
            bullet_list(design.assumptions)
    st.subheader("Agent assignments")
    st.dataframe(
        [
            {
                "Agent": agent.role,
                "Selected model": revision.agent_models[agent.id] or "Codex default",
                "Capability": agent.model_capability,
                "Responsibility": agent.responsibility,
            }
            for agent in design.agents
        ],
        hide_index=True,
        use_container_width=True,
    )
    for agent in design.agents:
        with st.expander(f"{agent.role} · skills and boundaries"):
            st.write(agent.model_reason)
            bullet_list(agent.exclusions)
            for skill in agent.skills:
                st.write(f"{skill.name} — {skill_status(skill.location, project)}")
    st.subheader("Delivery stages")
    st.write(design.parallel_strategy)
    stages = {stage.id: stage for stage in design.stages}
    by_task = {task.id: task for task in design.tasks}
    ordered_tasks = [
        by_task[task_id] for task_id in TopologicalSorter(task_graph(design)).static_order()
    ]
    for number, stage_id in enumerate(
        TopologicalSorter(
            {stage.id: set(stage.depends_on) for stage in design.stages}
        ).static_order(),
        start=1,
    ):
        stage = stages[stage_id]
        with st.expander(f"{number}. {stage.title}", expanded=True):
            st.write(stage.goal)
            review = current_review(state, stage.id)
            if review:
                st.success(
                    "Stage plan approved" if review.approved else "Feedback saved · review needed"
                )
                if review.feedback:
                    st.write(review.feedback)
            st.caption(
                "Approval covers this stage's plan for this revision. "
                "Implementation checks remain pending."
            )
            with st.form(f"stage_review_{path.name}_{len(state.revisions)}_{stage.id}"):
                feedback = st.text_area(
                    "Stage feedback", key=f"feedback_{path.name}_{stage.id}_{len(state.revisions)}"
                )
                save = st.form_submit_button("Save stage feedback")
                approve = st.form_submit_button("Approve stage plan")
                revise = st.form_submit_button("Revise stage with AI")
                if save or approve:
                    action(
                        "Saving stage review…",
                        lambda stage=stage, feedback=feedback, approve=approve: review_stage(
                            state, path, stage.id, feedback, approved=approve
                        ),
                    )
                if revise:

                    def revise_stage(stage=stage, feedback=feedback):
                        review_stage(state, path, stage.id, feedback)
                        revise_design(
                            state,
                            path,
                            f"Feedback for stage {stage.id} ({stage.title}): {feedback}. "
                            "Focus on this stage; preserve other stages unless "
                            "dependencies require changes. "
                            "Keep within the approved requirements.",
                        )
                        generate_design(state, path, 300)

                    action("Revising stage…", revise_stage)

            if stage.depends_on:
                st.caption("Starts after verified merge of " + ", ".join(stage.depends_on))
            for task in ordered_tasks:
                if task.stage_id != stage.id:
                    continue
                st.markdown(f"**{task.title}**")
                st.caption(f"Owner · {task.agent_id}  |  Difficulty · {task.difficulty}")
                st.write(task.description)
                st.write("Owned files", ", ".join(task.owned_paths) or "Read-only review")
                bullet_list(task.exclusions)
                with st.expander(f"Context and handoff · {task.id}"):
                    for context in task.context:
                        st.write(
                            f"{'Required' if context.required else 'Optional'} · "
                            f"{context.kind} · {context.reference}"
                        )
                        st.caption(f"{context.reason} If missing: {context.missing_action}")
                    st.write("Scope")
                    bullet_list(task.scope)
                    st.write("Dependencies", ", ".join(task.depends_on) or "Stage prerequisites")
                    st.write("Requirements", ", ".join(f"R{n}" for n in task.requirement_ids))
                    st.write("Deliverables")
                    bullet_list(task.deliverables)
                    st.write("Acceptance criteria")
                    bullet_list(task.acceptance_criteria)
                    st.write("Planned automated checks")
                    bullet_list(task.automated_checks)
                    st.write("Planned human checks")
                    bullet_list(task.human_checks)
                    st.write(f"Handoff to {', '.join(task.handoff_to)}")
                    st.write(task.handoff_summary)
                    st.write("Escalation")
                    bullet_list(task.escalation)
            st.markdown("**Verification gate · planned**")
            for check in stage.gate.agent_checks:
                st.write(f"{check.agent_id} · {check.check}")
                st.caption("Expected result · " + check.expected_result)
            st.markdown("**Your checks before merge**")
            bullet_list(stage.gate.user_checks)
            st.markdown("**Stage acceptance criteria**")
            bullet_list(stage.gate.acceptance_criteria)
            st.markdown("**Evidence required**")
            bullet_list(stage.gate.required_evidence)
            st.caption(
                f"Review owner · {stage.gate.review_owner}  |  "
                f"Failure owner · {stage.gate.failure_owner}"
            )
