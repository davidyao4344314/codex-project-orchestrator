"""An optional zero-inference Stage 1 outline; all technical decisions remain in Stage 2."""

from pathlib import Path

import typer

from codex_orchestrator.session import Brief, DraftPlan, PlanningSession, Revision, ScopedTask

RATIONALE = (
    "Local scope outline only; agent count, dependencies, difficulty and "
    "technical verification are determined by the architect."
)


def is_outline(plan: DraftPlan, brief: Brief | None = None) -> bool:
    if brief is not None:
        return bool(brief.requirements) and plan == outline_plan(brief)
    return plan.agent_rationale == RATIONALE and [task.id for task in plan.tasks] == [
        "scope-outline"
    ]


def outline_plan(brief: Brief) -> DraftPlan:
    # The Stage 1 task is a placeholder representing scope, not an executable assignment.
    # Keep every requirement and success criterion verbatim; invent no dependencies or checks.
    return DraftPlan(
        assumptions=[],
        agent_strategy="single",
        agent_rationale=RATIONALE,
        tasks=[
            ScopedTask(
                id="scope-outline",
                title="Define the approved delivery scope",
                workstream="general",
                description=brief.goal,
                depends_on=[],
                acceptance_criteria=brief.success_criteria or brief.requirements,
                requirement_ids=list(range(1, len(brief.requirements) + 1)),
                scope=brief.requirements,
                difficulty="moderate",
                automated_checks=[],
                human_checks=[
                    "Review the requirements, exclusions, constraints and success criteria."
                ],
            )
        ],
    )


def draft_outline(session: PlanningSession, path: Path) -> None:
    brief = session.brief
    if brief is None or not brief.requirements:
        raise ValueError("An outline requires a complete brief with at least one requirement.")
    plan = outline_plan(brief)
    session.revisions.append(Revision(brief=brief, plan=plan))
    session.pending_question = None
    session.pending_question_model = None
    session.phase = "review"
    session.save(path)
    typer.echo("Local delivery outline drafted; technical design will assign real tasks.")
