"""Specialized worker instructions without creating a second execution harness."""

from codex_orchestrator.context_files import resolve_file
from codex_orchestrator.context_models import ContextResource
from codex_orchestrator.worker_models import RequiredCheck, WorkerSpec

PROFILE_INSTRUCTIONS = {
    "frontend": "Implement only the approved screen/user-flow scope. Reuse existing components and "
    "styling. Handle required empty/error states and accessibility. Separate automated "
    "checks from browser checks the user must perform; never invent visual verification.",
    "backend": "Implement the approved service/API contract, validation and error behavior. "
    "Use focused unit/contract tests, including boundary inputs. Preserve public interfaces "
    "unless this assignment explicitly changes them.",
    "database": "Implement the approved storage/schema contract. "
    "Prefer additive, reversible changes "
    "and parameterized queries. Test with isolated temporary databases. Do not drop, "
    "truncate, reset or migrate real user data without a separately approved task.",
    "integration": "Wire verified upstream interfaces within owned files. "
    "Run scoped integration "
    "or smoke checks using disposable data. Report contract mismatches rather than "
    "rewriting other agents' files or expanding the architecture.",
    "general": "Implement the approved task using the existing stack. "
    "Choose the simplest fitting design. "
    "Test its acceptance criteria and avoid unrelated cleanup or new dependencies.",
    "verification": "Inspect the supplied implementation and run planned checks without editing "
    "repository files. Use temporary output/cache locations for tests. Report findings "
    "and exact evidence; do not fix defects, approve merges or claim unrun tests passed.",
}


def worker_spec(package) -> WorkerSpec:
    task = package.metadata["assignment"]
    feedback = package.metadata.get("user_feedback")
    checks = [
        RequiredCheck(id=f"C{i}", description=check)
        for i, check in enumerate(task["automated_checks"], start=1)
    ]
    human_checks = list(task["human_checks"])
    if feedback:
        checks.append(
            RequiredCheck(
                id="FEEDBACK",
                description=(
                    "Run a focused automated check of the correction: " + feedback["note"]
                ),
            )
        )
        human_checks.append("Confirm the correction satisfies your feedback: " + feedback["note"])
    # Empty ownership is an explicit read-only assignment, regardless of its domain label.
    profile = "verification" if not task["owned_paths"] else task["workstream"]
    if profile not in PROFILE_INSTRUCTIONS:
        raise ValueError(f"Unsupported worker profile: {profile}")
    return WorkerSpec(
        task_id=package.task_id,
        agent_id=package.agent_id,
        role=package.metadata["agent"]["role"],
        profile=profile,
        model=package.requested_model,
        context_digest=package.digest(),
        owned_paths=task["owned_paths"],
        read_only=profile == "verification",
        instructions=PROFILE_INSTRUCTIONS[profile],
        checks=checks,
        acceptance_criteria=task["acceptance_criteria"],
        human_checks=human_checks,
    )


def validate_owned_paths(spec, project):
    normalized = []
    for path in spec.owned_paths:
        resolved = resolve_file(
            project,
            ContextResource(
                kind="owned_file", reference=path, reason="Worker ownership", status="pending"
            ),
        )
        normalized.append(str(resolved.relative_to(project.resolve())))
    spec.owned_paths = list(dict.fromkeys(normalized))
