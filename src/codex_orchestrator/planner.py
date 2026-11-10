"""An offline starter plan; it does not infer requirements from the brief."""

from codex_orchestrator.models import PlanContent, Task, Workstream


def template_plan() -> PlanContent:
    """Return a reusable full-stack workflow with explicit prerequisites."""
    return PlanContent(
        assumptions=[
            "This is a generic template, not an AI-generated or repository-aware plan.",
            "The feature needs frontend, backend, and persistent data; confirm this scope.",
            "Refine each task against the project brief and existing code before implementation.",
        ],
        tasks=[
            Task(
                id="contract",
                title="Agree on feature scope and interface contracts",
                workstream=Workstream.INTEGRATION,
                description="Review the brief and existing code; define data and API boundaries.",
                depends_on=[],
                acceptance_criteria=[
                    "Expected user flows and exclusions are recorded.",
                    "Request, response, validation, and error formats are agreed.",
                ],
            ),
            Task(
                id="database",
                title="Design data storage and migrations",
                workstream=Workstream.DATABASE,
                description="Define entities and constraints that support the agreed contract.",
                depends_on=["contract"],
                acceptance_criteria=[
                    "Schema and migration approach match the feature requirements.",
                    "Migration and data integrity checks are defined.",
                ],
            ),
            Task(
                id="backend",
                title="Implement backend behavior",
                workstream=Workstream.BACKEND,
                description="Implement API endpoints, validation, and persistence behavior.",
                depends_on=["database"],
                acceptance_criteria=[
                    "Endpoints follow the agreed contract and handle invalid input.",
                    "Backend checks cover expected behavior and failure cases.",
                ],
            ),
            Task(
                id="frontend",
                title="Build the frontend user flow",
                workstream=Workstream.FRONTEND,
                description="Build the interface against the agreed contract using mock data.",
                depends_on=["contract"],
                acceptance_criteria=[
                    "The primary flow includes loading, empty, and error states.",
                    "User interactions are accessible and checked against the contract.",
                ],
            ),
            Task(
                id="integration",
                title="Connect frontend, backend, and database",
                workstream=Workstream.INTEGRATION,
                description="Replace mocks and verify the complete data flow.",
                depends_on=["backend", "frontend"],
                acceptance_criteria=[
                    "The main user flow works through the real API and persistence layer.",
                    "Configuration and error handling work across component boundaries.",
                ],
            ),
            Task(
                id="verification",
                title="Verify the feature and document setup",
                workstream=Workstream.INTEGRATION,
                description="Run end-to-end checks and review acceptance criteria from the brief.",
                depends_on=["integration"],
                acceptance_criteria=[
                    "The primary flow and a representative failure case pass.",
                    "Setup instructions and any remaining limitations are recorded.",
                ],
            ),
        ],
    )
