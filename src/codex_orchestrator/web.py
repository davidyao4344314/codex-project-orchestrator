"""Local browser interface for the same saved plans used by the terminal."""

import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import streamlit as st
from pydantic import ValidationError

from codex_orchestrator.codex import CodexError, find_codex
from codex_orchestrator.dashboard import dashboard
from codex_orchestrator.design import (
    approve_design,
    check_source,
    generate_design,
    normalize_model,
    prepare_design,
    revise_design,
    submit_design_answer,
    update_design_models,
)
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.design_validation import (
    context_blockers,
    validate_design,
)
from codex_orchestrator.explain import explain_selection
from codex_orchestrator.interactive import (
    approve_plan,
    collect_brief,
    create_session,
    generate_plan,
    model_label,
    revise_plan,
    submit_answer,
)
from codex_orchestrator.selection import selection_menu
from codex_orchestrator.session import PlanningSession
from codex_orchestrator.web_widgets import bullet_list
from codex_orchestrator.workflow_view import workflow_view

ROOT = Path(__file__).resolve().parents[2]
PLANS = Path(os.environ.get("ORCHESTRATOR_PLANS", ROOT / "plans")).expanduser().resolve()


def action(label, operation):
    """Model calls occur only after an explicit click, never on a page rerun."""
    try:
        with st.spinner(label):
            operation()
    except (CodexError, OSError, ValueError, ValidationError, subprocess.TimeoutExpired) as exc:
        st.session_state.last_error = str(exc)
        st.rerun()
    else:
        st.rerun()


def advance_requirements(session, path):
    if session.phase == "intake" and not session.pending_question:
        collect_brief(session, path)
    if session.phase == "planning" and not session.pending_question:
        generate_plan(session, path)


def sample_project():
    destination = PLANS / "samples" / f"reading-list-{uuid4().hex[:8]}"
    shutil.copytree(ROOT / "examples" / "reading-list", destination)
    # Isolate project discovery from the orchestrator's own parent Git repository.
    result = subprocess.run(
        ["git", "init", "-b", "main", str(destination)], capture_output=True, check=False
    )
    if result.returncode != 0:
        raise ValueError("Could not initialize the sample project.")
    return destination, (destination / "BRIEF.md").read_text()


def connection():
    st.sidebar.divider()
    st.sidebar.caption("CODEX CONNECTION")
    if st.sidebar.button("Check connection", use_container_width=True):
        try:
            executable = find_codex()
            ready = False
            if executable:
                result = subprocess.run(
                    [executable, "login", "status"],
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                ready = result.returncode == 0
            st.session_state.connection = (executable, ready)
        except (CodexError, OSError, subprocess.TimeoutExpired) as exc:
            st.sidebar.error(str(exc))
    status = st.session_state.get("connection")
    if status:
        executable, ready = status
        if ready:
            st.sidebar.success("Signed in · ready")
        elif executable:
            st.sidebar.warning("Sign-in needed")
            if st.sidebar.button("Sign in with Codex", use_container_width=True):
                try:
                    with st.spinner("Complete Codex sign-in in your browser…"):
                        result = subprocess.run(
                            [executable, "login"],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=300,
                            check=False,
                        )
                    if result.returncode == 0:
                        st.session_state.connection = (executable, True)
                        st.rerun()
                    else:
                        st.sidebar.error("Sign-in did not finish. Try orchestrator setup.")
                except (OSError, subprocess.TimeoutExpired) as exc:
                    st.sidebar.error(str(exc))
        else:
            st.sidebar.info("Run orchestrator setup in your project terminal to install Codex.")


def new_project():
    st.title("What are we building?")
    st.write("Turn an idea into requirements, a technical design, and clear agent handoffs.")
    if st.button("Try sample project", icon="📚"):
        try:
            project, brief = sample_project()
            st.session_state.new_project = str(project)
            st.session_state.new_idea = brief
            st.session_state.new_name = "Reading list"
        except (OSError, ValueError) as exc:
            st.error(str(exc))
    with st.form("new_session"):
        name = st.text_input("Project name", key="new_name")
        project = st.text_input(
            "Local project folder", key="new_project", placeholder="/Users/you/projects/my-project"
        )
        idea = st.text_area("What would you like to build?", key="new_idea", height=210)
        with st.expander("Model choices"):
            intake = st.text_input("Intake model", value="default")
            planning = st.text_input("Planning model", value="default")
            st.caption(
                "Use default for your Codex configuration, or enter a model ID you can access."
            )
        submitted = st.form_submit_button("Create planning session", type="primary")
    if submitted:

        def create():
            if not project.strip():
                raise ValueError("Enter a local project folder or choose Try sample project.")
            path = PLANS / f"session-{uuid4().hex[:12]}.json"
            create_session(
                Path(project),
                name or Path(project).name,
                idea,
                path,
                intake_model=normalize_model(intake),
                planning_model=normalize_model(planning),
            )
            st.session_state.active = str(path)
            st.session_state[f"view_{path.name}"] = "Requirements"

        action("Saving your project…", create)


def requirement_view(session, path):
    st.caption(f"Stage 1 · {session.phase} · saved automatically")
    with st.expander("Model choices"):
        with st.form(f"requirements_models_{path.name}"):
            intake = st.text_input("Intake model", value=session.intake_model or "default")
            planning = st.text_input("Planning model", value=session.planning_model or "default")
            if st.form_submit_button("Save model choices"):

                def save_models():
                    session.intake_model = normalize_model(intake)
                    session.planning_model = normalize_model(planning)
                    session.models_configured = True
                    session.save(path)

                action("Saving model choices…", save_models)

    for index, answer in enumerate(session.answers):
        if index:
            with st.chat_message("assistant"):
                st.write(answer.prompt)
        with st.chat_message("user"):
            st.write(answer.response)
    if session.pending_question:
        with st.chat_message("assistant"):
            st.caption(session.pending_question_model or "Model attribution unavailable")
            st.write(session.pending_question)
        if answer := st.chat_input("Answer the question"):

            def respond():
                submit_answer(session, path, answer)
                advance_requirements(session, path)

            action("Preparing the next step…", respond)
        if session.phase == "intake" and st.button("Draft with what we have"):

            def draft():
                submit_answer(session, path, ":plan", draft_now=True)
                advance_requirements(session, path)

            action("Drafting your plan…", draft)
    elif session.phase in ("intake", "planning"):
        st.info("Your request is saved. Continue when you are ready to ask Codex.")
        if st.button("Continue with Codex", type="primary"):
            action(
                "Codex is working on your requirements…",
                lambda: advance_requirements(session, path),
            )

    if session.brief:
        with st.expander("Structured brief", expanded=not bool(session.revisions)):
            st.write(session.brief.goal)
            st.markdown("**Requirements**")
            bullet_list(session.brief.requirements)
            if session.brief.exclusions:
                st.markdown("**Out of scope**")
                bullet_list(session.brief.exclusions)
            if session.brief.assumptions:
                st.markdown("**Assumptions**")
                bullet_list(session.brief.assumptions)
    if not session.revisions or session.phase not in ("review", "approved"):
        return
    revision = session.revisions[-1]
    st.subheader(f"Your plan · revision {len(session.revisions)}")
    st.caption(f"Response model · {model_label(revision.requested_model, revision.reported_model)}")
    for task in revision.plan.tasks:
        with st.expander(f"{task.id} · {task.title}"):
            st.write(task.description)
            st.caption(f"{task.workstream} · {task.difficulty}")
            st.write("Depends on", ", ".join(task.depends_on) or "Ready to start")
            st.markdown("**Acceptance checks**")
            bullet_list(task.acceptance_criteria)
    if session.phase == "approved":
        st.success(f"Revision {session.approved_revision} approved. Technical design is ready.")
    elif st.button("Approve requirements and plan", type="primary"):
        action("Saving approval…", lambda: approve_plan(session, path))
    with st.expander("Request a revision"):
        with st.form(f"revise_requirements_{path.name}"):
            feedback = st.text_area("What should change?")
            if st.form_submit_button("Revise plan") and feedback.strip():

                def revise():
                    revise_plan(session, path, feedback)
                    advance_requirements(session, path)

                action("Revising the requirements…", revise)


def design_view(session, source_path):
    if session.phase != "approved":
        st.info("Approve your Stage 1 requirements and plan to unlock technical design.")
        return
    default_output = source_path.with_name(f"{source_path.stem}.design.json")
    key = f"design_path_{source_path.name}"
    path = Path(st.session_state.get(key, str(default_output)))
    if not path.exists():
        st.subheader("Design the implementation")
        st.write(
            "The architect will inspect your project and propose stages, agents, "
            "and verification gates."
        )
        with st.form(f"start_design_{source_path.name}"):
            architect = st.text_input("Architect model", value=session.planning_model or "default")
            worker = st.text_input("Default agent model", value="default")
            if st.form_submit_button("Create technical design", type="primary"):

                def create():
                    state, output = prepare_design(
                        source_path,
                        path,
                        architect_model=architect,
                        agent_model=worker,
                    )
                    state.models_configured = True
                    state.save(output)
                    generate_design(state, output, 300)

                action("Architect is designing your workflow…", create)
        return
    state = DesignSession.load(path)
    try:
        check_source(state)
        if state.revisions:
            validate_design(state.revisions[-1].design, state.source.brief, Path(state.project))
    except (CodexError, ValueError) as exc:
        st.warning(str(exc))
        if st.button("Start a fresh technical design"):
            st.session_state[key] = str(
                source_path.with_name(f"{source_path.stem}.design-{uuid4().hex[:6]}.json")
            )
            st.rerun()
        return
    st.caption(f"Stage 2 · {state.phase} · saved automatically")
    if state.phase == "draft":
        if state.pending_question:
            st.caption(state.pending_question_model or "Architect model attribution unavailable")
            st.info(state.pending_question)
            if state.requires_requirement_change:
                st.warning(
                    "This decision may change the approved scope. Revise Stage 1 if necessary."
                )
            if answer := st.chat_input("Answer the architect"):

                def answer_architect():
                    submit_design_answer(state, path, answer)
                    generate_design(state, path, 300)

                action("Architect is updating the design…", answer_architect)
        elif st.button("Continue technical design", type="primary"):
            action("Architect is designing…", lambda: generate_design(state, path, 300))
        if state.validation_feedback:
            st.warning("The previous draft needs correction. Continue to send validation feedback.")
        return
    selected = selection_menu(
        key=f"selection_{path.name}_{len(state.revisions)}", on_selected_change=lambda: None
    )
    with st.expander("Explain part of this design"):
        st.caption("Highlight design text to reveal Explain with AI, or paste text here.")
        excerpt = st.text_area("Text to explain", key=f"excerpt_{path.name}")
        request = st.button("Explain text")
    text = selected.selected or (excerpt if request else None)
    if text:

        def explain():
            answer, model = explain_selection(state, text)
            st.session_state[f"explanation_{path.name}"] = (text, answer, model)

        action("Explaining with Codex…", explain)
    if explanation := st.session_state.get(f"explanation_{path.name}"):
        st.info(explanation[1])
        st.caption(f"Explanation model · {explanation[2]}")
        with st.expander("Explained selection"):
            st.write(explanation[0])
    workflow_view(state, path, action)
    blockers = context_blockers(state.revisions[-1].design, Path(state.project))
    for blocker in blockers:
        st.warning(blocker)
    if state.phase == "approved":
        st.success(f"Technical-design revision {state.approved_revision} approved.")
    elif st.button("Approve technical design", type="primary", disabled=bool(blockers)):
        action("Saving approval…", lambda: approve_design(state, path))
    with st.expander("Model allocations"):
        with st.form(f"design_models_{path.name}_{len(state.revisions)}"):
            architect = st.text_input("Architect model", value=state.architect_model or "default")
            allocations = {
                agent.id: st.text_input(
                    f"{agent.role} model",
                    value=(state.revisions[-1].agent_models[agent.id] or "default"),
                    key=f"agent_{path.name}_{agent.id}_{len(state.revisions)}",
                )
                for agent in state.revisions[-1].design.agents
            }
            if st.form_submit_button("Save model allocations"):
                action(
                    "Saving model choices…",
                    lambda: update_design_models(
                        state,
                        path,
                        normalize_model(architect),
                        {agent: normalize_model(model) for agent, model in allocations.items()},
                    ),
                )
    with st.expander("Request a design revision"):
        with st.form(f"revise_design_{path.name}"):
            feedback = st.text_area("What should change?")
            if st.form_submit_button("Revise technical design") and feedback.strip():

                def revise():
                    revise_design(state, path, feedback)
                    generate_design(state, path, 300)

                action("Architect is revising the workflow…", revise)
    st.download_button(
        "Download technical design", path.read_bytes(), file_name=path.name, mime="application/json"
    )


def main():
    st.set_page_config(page_title="Project planner", page_icon="◈", layout="wide")
    st.markdown(
        """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
    html, body, p, h1, h2, h3, label, input, textarea {font-family:'DM Sans',sans-serif}
    .block-container {max-width:1180px;padding-top:2.4rem}
    h1 {letter-spacing:-.045em;font-weight:650}
    [data-testid="stSidebar"] {background:#e8eee4}
    [data-testid="stSidebar"] .stButton button {border-radius:8px}
    [data-testid="stExpander"] {background:#fff;border-radius:12px}
    [data-testid="stChatMessage"] {border-radius:12px}
    .stButton button[kind="primary"], .stFormSubmitButton button[kind="primary"] {
      border-radius:8px;background:#285c46;border-color:#285c46}
    </style>""",
        unsafe_allow_html=True,
    )
    PLANS.mkdir(parents=True, exist_ok=True)
    st.sidebar.title("◈ Workbench")
    st.sidebar.caption("PROJECT PLANNER")
    if st.sidebar.button("Home", use_container_width=True):
        st.session_state.active = None
        st.session_state.home = True
        st.rerun()
    if st.sidebar.button("＋ New project", use_container_width=True):
        st.session_state.active = None
        st.session_state.home = False
        for key in ("new_name", "new_project", "new_idea"):
            st.session_state.pop(key, None)
        st.rerun()
    sessions = []
    for path in sorted(PLANS.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True):
        try:
            session = PlanningSession.load(path)
            sessions.append((path, session))
        except (OSError, ValueError):
            continue
    st.sidebar.divider()
    for path, session in sessions:
        if st.sidebar.button(
            f"{session.project_name} · {session.phase}",
            key=str(path),
            use_container_width=True,
        ):
            st.session_state.active = str(path)
            st.rerun()
    connection()
    if error := st.session_state.pop("last_error", None):
        st.error(error)
        st.caption("Your saved progress is available. Resolve the issue and retry.")
    active = st.session_state.get("active")
    if not active:
        if st.session_state.get("home", True) and sessions:
            st.title("Your projects")
            st.write("Open a project to see its agents, assignments, context, and code workspace.")
            for project_path, project_session in sessions:
                with st.container(border=True):
                    st.subheader(project_session.project_name)
                    st.caption(project_session.project)
                    st.write(f"Requirements · {project_session.phase}")
                    if st.button("Open dashboard", key=f"home_{project_path.name}"):
                        st.session_state.active = str(project_path)
                        st.session_state[f"view_{project_path.name}"] = "Dashboard"
                        st.rerun()
            return
        new_project()
        return
    path = Path(active)
    try:
        session = PlanningSession.load(path)
        st.title(session.project_name)
        st.caption(session.project)
        view = st.radio(
            "Stage",
            ["Dashboard", "Requirements", "Technical design", "Agent workspace"],
            horizontal=True,
            label_visibility="collapsed",
            key=f"view_{path.name}",
        )
        if view == "Dashboard":
            dashboard(session, path, action)
        elif view == "Agent workspace":
            dashboard(session, path, action, workspace_only=True)
        elif view == "Requirements":
            requirement_view(session, path)
        else:
            design_view(session, path)
    except (OSError, ValueError) as exc:
        st.error(str(exc))


if __name__ == "__main__":
    main()
