"""Read-only architect requests bound to an approved Stage 1 revision."""

from pathlib import Path
from time import monotonic

import typer

from codex_orchestrator.codex import CodexError, codex_response
from codex_orchestrator.design_display import show_design
from codex_orchestrator.design_models import (
    DesignReply,
    DesignRevision,
    DesignSession,
    source_digest,
)
from codex_orchestrator.design_validation import context_blockers, validate_design
from codex_orchestrator.interactive import READ_ONLY, ask, model_label
from codex_orchestrator.model_allocation import revision_models
from codex_orchestrator.planning_context import FOCUSED_EXPLORATION, planning_prompt, repository_map
from codex_orchestrator.session import Answer, PlanningSession
from codex_orchestrator.session_manager import digest, managed_turn
from codex_orchestrator.setup import ensure_codex

ARCHITECT_INSTRUCTIONS = (
    "Act as the architect for Stage 2. "
    "Use the approved brief as the scope. Explain existing code to reuse and new work needed. "
    "Return either a focused clarification question or a complete technical design. "
    "Set requires_requirement_change true only when answering the question would require "
    "changing approved requirements or exclusions. Never silently expand the scope. "
    "Scale the design to the request. A terminal program can use function interfaces "
    "and have no database. "
    "Define components, data flow, relevant interfaces, storage, and justified technology choices. "
    "Divide complex work into independently verifiable delivery stages. Each stage must include "
    "agent checks with expected outcomes, acceptance criteria, any required user tests, review and "
    "failure owners, and evidence needed before merging. All checks are proposed, not completed. "
    "The merge gate requires checks on the combined stage changes and the tested commit, "
    "including regressions, review, and required user acceptance. Dependent stages wait for it. "
    "Automatically assess the job before assigning agents: evaluate task difficulty, uncertainty, "
    "dependencies, required specialties, context size, and verification effort. "
    "Choose the smallest "
    "useful team (1-8 logical agents); do not create agents merely for frontend/backend/database "
    "labels when one agent can do the work. Encode task difficulty and match each agent's model "
    "capability to its hardest responsibility. Explain the team size and tradeoff between "
    "coordination overhead and specialization in parallel_strategy, and capability choices in "
    "model_reason. Raise a focused question when missing information materially affects scope. "
    "Use logical agents only where justified. Give each agent a responsibility, exclusions, "
    "required skills, model capability, and an allocation reason. Skill locations "
    "must identify actual SKILL.md files you inspected, or null if availability is unknown. "
    "Assign every task to an agent and stage. Number approved requirements R1, R2, etc. "
    "Use integer requirement_ids to link tasks to the approved brief and cover every requirement. "
    "Include component ownership and exact repository-relative owned file paths "
    "(no globs or directories), "
    "scope exclusions, context inputs, deliverables, acceptance checks, handoff recipients and "
    "payloads, and escalation conditions. Use empty owned_paths for read-only review tasks. "
    "Context by kind: requirement uses R1-style IDs, component and interface use IDs, "
    "project_file uses an existing repository-relative file. For prerequisite context use "
    "task_summary with the producer task ID, then upstream_file with producer-id:file-path "
    "for each exact producer-owned file needed. Use task_summary alone if no file is needed. "
    "Do not request the legacy task_output (which includes all owned files) unless every file "
    "is genuinely needed. Mark a handoff required when its file is required. "
    "archived_summary is reserved for the orchestrator's correction workflow. "
    "Explain why each context item is needed and what to do if it is missing. "
    "Set required true when a task cannot be completed correctly without that item. "
    "A verification task must require the implementation output it verifies. "
    "A required upstream output needs a dependency on its producer. Existing files needed for "
    "implementation belong in context, while future files belong in upstream_file references. "
    "Keep stage and task dependencies acyclic. Order tasks that write the same file. "
    "Split large assignments into independently testable tasks when practical. "
    "Prefer focused task checks and reserve full regression runs for stage verification. "
    "Assign combined stage regression checks to the final task's automated checks so the "
    "terminal feature workflow has observed results before presenting user tests. "
    "Do not require unrelated code changes merely to pass a task's focused checks. "
    "Handoff recipients and gate owners are agent IDs or user; failure owners must be agents. "
    "Designate owners for shared interfaces. Explain actual opportunities for parallelism. "
    "Record unverified feasibility as an assumption or a scoped investigation task before "
    "dependent implementation. Never claim that planned checks have already passed. "
    "You are proposing a workflow for user review. Return only JSON matching the schema."
)


def approved_source(path: Path) -> PlanningSession:
    source = PlanningSession.load(path)
    if source.phase != "approved" or source.approved_revision != len(source.revisions):
        raise CodexError(
            f"Stage 2 needs an approved Stage 1 revision. Run 'orchestrator resume {path}'."
        )
    if not Path(source.project).is_dir():
        raise CodexError(f"The source project's directory is unavailable: {source.project}")
    return source


def _matching_source(state: DesignSession, source: PlanningSession) -> PlanningSession:
    if (
        len(source.revisions) != state.source_revision
        or source_digest(source.revisions[-1]) != state.source_digest
        or str(Path(source.project).resolve()) != state.project
    ):
        raise CodexError(
            "The approved Stage 1 source has changed. Start a new design with --output "
            "pointing to a new file so the previous design remains available."
        )
    return source


def check_source(state: DesignSession) -> PlanningSession:
    """Delivery always requires explicitly approved requirements."""
    return _matching_source(state, approved_source(Path(state.source_file)))


def draft_source(path: Path) -> PlanningSession:
    from codex_orchestrator.scope_outline import is_outline

    source = PlanningSession.load(path)
    if (
        source.phase != "review"
        or not source.revisions
        or not is_outline(source.revisions[-1].plan, source.revisions[-1].brief)
        or not Path(source.project).is_dir()
    ):
        raise CodexError("Combined planning requires a complete local scope outline.")
    return source


def planning_source(state: DesignSession) -> PlanningSession:
    """A pending combined approval permits read-only design work, never delivery."""
    if state.combined_approval and state.phase != "approved":
        source = PlanningSession.load(Path(state.source_file))
        if source.phase == "review":
            return _matching_source(state, draft_source(Path(state.source_file)))
    return check_source(state)


def prepare_design(
    source_file: Path,
    output: Path | None,
    *,
    architect_model: str | None,
    agent_model: str | None,
    combined_approval: bool = False,
) -> tuple[DesignSession, Path]:
    source_file = source_file.resolve()
    source = PlanningSession.load(source_file)
    source = (
        draft_source(source_file)
        if combined_approval and source.phase == "review"
        else approved_source(source_file)
    )
    output = (output or source_file.with_name(f"{source_file.stem}.design.json")).resolve()
    if output == source_file:
        raise CodexError("The design output must have a different path from the Stage 1 session.")
    if output.exists():
        state = DesignSession.load(output)
        if Path(state.source_file).resolve() != source_file:
            raise CodexError("This design belongs to a different Stage 1 session.")
        if combined_approval:
            planning_source(state)
        else:
            check_source(state)
    else:
        state = DesignSession(
            source_file=str(source_file),
            project=str(Path(source.project).resolve()),
            source_revision=len(source.revisions),
            combined_approval=combined_approval and source.phase == "review",
            source=source.revisions[-1],
            source_digest=source_digest(source.revisions[-1]),
            architect_model=(
                source.model_policy.strong_reasoning
                if source.model_policy
                else source.planning_model
            ),
            model_policy=source.model_policy,
            auto_model_allocation=source.model_policy is not None,
            models_configured=source.model_policy is not None,
        )
    new = not output.exists()
    if architect_model is not None:
        state.architect_model = normalize_model(architect_model)
    if agent_model is not None:
        state.default_agent_model = normalize_model(agent_model)
        state.auto_model_allocation = False
    if architect_model is not None or agent_model is not None:
        state.models_configured = architect_model is not None or state.models_configured
    if new:
        from codex_orchestrator.compact_planning import saved_compact_revision

        compact = saved_compact_revision(state)
        if compact is not None:
            state.revisions.append(compact)
            state.phase = "review"
            typer.echo("Using the saved compact design; no additional architect call is needed.")
        state.save(output, new=True)
    elif architect_model is not None or agent_model is not None:
        state.save(output)
    return state, output


def normalize_model(value: str) -> str | None:
    value = value.strip()
    if not value:
        raise typer.BadParameter("Model ID must not be empty.")
    return None if value.casefold() == "default" else value


def generate_design(state: DesignSession, output: Path, timeout: int) -> None:
    from codex_orchestrator.scope_outline import is_outline

    source = planning_source(state)
    local_outline = is_outline(state.source.plan, state.source.brief)
    data = {
        "project_name": source.project_name,
        "approved_brief": state.source.brief.model_dump(),
        # The exact local projection is redundant with the approved brief. Preserve
        # all other approved plan content, including older detailed Stage 1 revisions.
        "initial_tasks": (
            {"local_outline": True} if local_outline else state.source.plan.model_dump()
        ),
        "validation_feedback": state.validation_feedback,
        "clarifications": [answer.model_dump() for answer in state.answers],
        "previous_design": (state.revisions[-1].design.model_dump() if state.revisions else None),
        "repository_map": repository_map(
            Path(state.project),
            " ".join([state.source.brief.goal, *state.source.brief.requirements]),
        ),
    }
    clarifications = data["clarifications"]
    boundary = {
        "source_digest": state.source_digest,
        "previous_design": data["previous_design"],
        "repository_map": data["repository_map"],
        "planning_context_version": 1,
    }
    with managed_turn(
        output,
        Path(state.project),
        "architect",
        state.architect_model,
        boundary,
        resume_if=lambda binding: (
            binding.cursor <= len(clarifications)
            and binding.cursor_digest == digest(clarifications[: binding.cursor])
        ),
    ) as turn:
        _generate_design(state, output, timeout, data, turn)
        turn.cursor = len(clarifications)
        turn.cursor_digest = digest(clarifications)


def _generate_design(state, output, timeout, data, turn):
    reported = []
    started = monotonic()
    typer.echo("Architect is inspecting the project and designing the workflow...", err=True)
    if turn.previous:
        data = {
            "new_clarifications": data["clarifications"][turn.previous.cursor :],
            "validation_feedback": data["validation_feedback"],
        }
        instructions = (
            "Continue the existing technical design from the approved requirements and "
            "architect instructions. Apply these new clarifications and validation feedback. "
            "Keep exact agent ownership, dependency, context and verification boundaries. "
            "Return one focused question or a complete updated technical-design JSON."
        )
    else:
        from codex_orchestrator.scope_outline import is_outline

        instructions = ARCHITECT_INSTRUCTIONS
        if is_outline(state.source.plan, state.source.brief):
            instructions += (
                " The initial_tasks local_outline marker stands for the approved brief, "
                "not an agent assignment. Define the necessary real tasks, stages, "
                "dependencies and checks. "
                "Determine team size and difficulty independently."
            )
    if state.combined_approval:
        instructions += (
            " The supplied brief is the proposed scope for joint human approval. "
            "Do not treat either the requirements or this design as accepted yet."
        )
    reply = codex_response(
        Path(state.project),
        planning_prompt(
            READ_ONLY
            + " "
            + FOCUSED_EXPLORATION
            + instructions
            + " Keep descriptions and rationale concise; avoid repeating requirements and "
            "checks in multiple fields. Preserve all required verification and ownership detail.",
            data,
        ),
        DesignReply,
        model=state.architect_model,
        timeout=timeout,
        on_response=reported.append,
        turn=turn,
    )
    elapsed = monotonic() - started
    planning_source(state)
    actual_model = reported[-1] if reported else None
    if reply.design is None:
        state.pending_question = reply.question
        state.pending_question_model = model_label(state.architect_model, actual_model)
        state.requires_requirement_change = reply.requires_requirement_change
    else:
        try:
            validate_design(reply.design, state.source.brief, Path(state.project))
        except CodexError as exc:
            state.validation_feedback = [str(exc)]
            state.save(output)
            raise
        state.validation_feedback = []
        state.revisions.append(
            DesignRevision(
                design=reply.design,
                requested_model=state.architect_model,
                reported_model=actual_model,
                **revision_models(state, reply.design),
                elapsed_seconds=elapsed,
            )
        )
        state.phase = "review"
        state.requires_requirement_change = False
    state.save(output)


def choose_design_models(state: DesignSession, output: Path, *, allocations: bool) -> None:
    fields = [("architect", "Architect", state.architect_model)]
    if allocations:
        fields.extend(
            (f"agent:{agent}", f"Agent {agent}", model)
            for agent, model in state.revisions[-1].agent_models.items()
        )
    else:
        fields.append(("default agent", "Default agent", state.default_agent_model))
    selected = {}
    for name, label, current in fields:
        value = ""
        while not value:
            value = typer.prompt(
                f"{label} model ID (default = Codex configuration)", default=current or "default"
            ).strip()
        if value == ":save":
            return
        selected[name] = normalize_model(value)
    architect = selected.pop("architect")
    if allocations:
        allocations_by_agent = {
            agent: selected[f"agent:{agent}"] for agent in state.revisions[-1].agent_models
        }
        update_design_models(state, output, architect, allocations_by_agent)
    else:
        state.architect_model = architect
        state.models_configured = True
        state.default_agent_model = selected["default agent"]
        state.auto_model_allocation = False
        planning_source(state)
        state.save(output)


def update_design_models(
    state: DesignSession,
    output: Path,
    architect: str | None,
    allocations: dict[str, str | None],
) -> None:
    planning_source(state)
    current = state.revisions[-1]
    if set(allocations) != set(current.agent_models):
        raise ValueError("Choose a model for every assigned agent.")
    if current.agent_models != allocations or current.task_models is not None:
        state.revisions.append(
            DesignRevision(
                design=current.design,
                requested_model=current.requested_model,
                reported_model=current.reported_model,
                agent_models=allocations,
                elapsed_seconds=0,
                origin="model_allocation",
                allocation_rationale="Manual agent model choices override automatic allocation.",
            )
        )
        state.phase = "review"
        state.approved_revision = None
    state.architect_model = architect
    state.models_configured = True
    state.auto_model_allocation = False
    state.save(output)


def approve_design(state: DesignSession, output: Path) -> None:
    source = planning_source(state)
    if state.phase != "review" or not state.revisions:
        raise ValueError("A complete design must be in review before approval.")
    current = state.revisions[-1].design
    blockers = context_blockers(current, Path(state.project))
    if blockers:
        raise ValueError("Required context must be resolved before approval. " + " ".join(blockers))
    validate_design(current, state.source.brief, Path(state.project))
    if source.phase == "review":
        from codex_orchestrator.interactive import approve_plan

        source.require_current(Path(state.source_file))
        state.require_current(output)
        approve_plan(source, Path(state.source_file))
    check_source(state)
    state.phase = "approved"
    state.approved_revision = len(state.revisions)
    state.save(output)


def revise_design(state: DesignSession, output: Path, feedback: str) -> None:
    planning_source(state)
    if state.phase not in ("review", "approved"):
        raise ValueError("Finish a draft before requesting a revision.")
    state.answers.append(Answer(prompt="Technical-design revision request", response=feedback))
    state.phase = "draft"
    state.approved_revision = None
    state.save(output)


def submit_design_answer(state: DesignSession, output: Path, answer: str) -> None:
    planning_source(state)
    if not state.pending_question:
        raise ValueError("There is no architect question waiting for an answer.")
    state.answers.append(Answer(prompt=state.pending_question, response=answer))
    state.pending_question = None
    state.pending_question_model = None
    state.save(output)


def review_design(state: DesignSession, output: Path) -> bool:
    validate_design(state.revisions[-1].design, state.source.brief, Path(state.project))
    show_design(state)
    if state.approved_revision is not None:
        typer.echo(f"Technical-design revision {state.approved_revision} is approved.")
    choices = "revise / models / save"
    if state.phase == "review":
        choices = "approve / " + choices
    while True:
        label = (
            "Review requirements and technical design"
            if state.combined_approval
            else "Review technical design"
        )
        decision = ask(f"{label} [{choices}]")
        if decision is None or decision.casefold() == "save":
            return False
        planning_source(state)
        if decision.casefold() in ("models", ":models"):
            choose_design_models(state, output, allocations=True)
            return True
        if decision.casefold() == "approve" and state.phase == "review":
            blockers = context_blockers(state.revisions[-1].design, Path(state.project))
            if blockers:
                for blocker in blockers:
                    typer.echo(f"Approval blocked: {blocker}")
                typer.echo("Resolve required context or revise the design before approval.")
                continue
            approve_design(state, output)
            typer.echo(
                f"Approved technical-design revision {state.approved_revision}. "
                "Stage checks remain planned until implementation supplies actual evidence."
            )
            return False
        if decision.casefold() == "revise":
            feedback = ask("What should change in the technical design?")
            if feedback is None:
                return False
            state.answers.append(
                Answer(prompt="Technical-design revision request", response=feedback)
            )
            state.phase = "draft"
            state.approved_revision = None
            state.save(output)
            return True
        typer.echo(f"Choose {choices}.")


def run_design(state: DesignSession, output: Path, timeout: int) -> None:
    typer.echo(f"Technical-design session: {output}")
    typer.echo("Type :save to save and exit, or :models to change the architect model.")
    needs_setup = True
    try:
        while True:
            planning_source(state)
            if state.phase != "draft":
                if not review_design(state, output):
                    return
                continue
            if needs_setup:
                ensure_codex()
                needs_setup = False
            if not state.models_configured:
                choose_design_models(state, output, allocations=False)
                if not state.models_configured:
                    return
            if state.pending_question:
                typer.echo(f"Question model: {state.pending_question_model or 'unrecorded'}")
                if state.requires_requirement_change:
                    typer.echo(
                        "This decision may change the approved scope. Keep your answer within "
                        "the brief, or use :save and revise Stage 1 before continuing."
                    )
                answer = ask(state.pending_question)
                if answer is None:
                    return
                if answer == ":models":
                    choose_design_models(state, output, allocations=False)
                    continue
                submit_design_answer(state, output, answer)
            else:
                generate_design(state, output, timeout)
    except typer.Abort:
        typer.echo("Technical-design session saved.")
