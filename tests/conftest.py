import pytest

from codex_orchestrator.session import Brief, DraftPlan, PlanningSession, Revision, ScopedTask


@pytest.fixture
def approved_session(tmp_path):
    brief = Brief(
        goal="Build a recursion counter",
        users=["Terminal users"],
        requirements=["Count recursively from 0 to 5"],
        exclusions=["Website", "Database"],
        project_context=["An empty Python project"],
        constraints=["Python"],
        assumptions=[],
        success_criteria=["The sequence is 0 through 5 and stops"],
    )
    task = ScopedTask(
        id="counter",
        title="Implement the counter",
        workstream="general",
        description="Add recursive counting",
        depends_on=[],
        acceptance_criteria=["Stops at five"],
        requirement_ids=[1],
        scope=["counter.py"],
        difficulty="routine",
        automated_checks=["Check termination"],
        human_checks=["Try it in the terminal"],
    )
    plan = DraftPlan(
        assumptions=[], tasks=[task], agent_strategy="single", agent_rationale="Small task"
    )
    session = PlanningSession(
        project=str(tmp_path),
        project_name="sample",
        brief=brief,
        revisions=[Revision(brief=brief, plan=plan)],
        phase="approved",
        approved_revision=1,
    )
    path = tmp_path / "source.json"
    session.save(path, new=True)
    return path, session


@pytest.fixture
def technical_payload():
    return {
        "overview": "A small Python counter with focused verification",
        "repository_findings": ["The project is empty"],
        "components": [
            {
                "id": "counter",
                "name": "Counter",
                "responsibility": "Recursive counting",
                "paths": ["counter.py"],
            }
        ],
        "interfaces": [
            {
                "id": "count",
                "source_component": "counter",
                "target_component": None,
                "inputs": ["Start and limit"],
                "outputs": ["Integer sequence"],
                "errors": ["Invalid limits"],
            }
        ],
        "data_flow": ["Terminal input -> recursive function -> printed output"],
        "storage": [],
        "technologies": [{"name": "Python", "reason": "Approved constraint", "already_used": True}],
        "exclusions": ["Website", "Database"],
        "assumptions": [],
        "agents": [
            {
                "id": "developer",
                "role": "Python developer",
                "responsibility": "Implement and test counter",
                "exclusions": ["Web UI"],
                "skills": [{"name": "Python", "location": None}],
                "model_capability": "routine",
                "model_reason": "Small, precisely scoped change",
            }
        ],
        "stages": [
            {
                "id": "core",
                "title": "Counter",
                "goal": "A working recursion counter",
                "depends_on": [],
                "deliverables": ["counter.py", "Test results"],
                "gate": {
                    "agent_checks": [
                        {
                            "agent_id": "developer",
                            "check": "Run termination test",
                            "expected_result": "Stops at 5",
                        }
                    ],
                    "user_checks": ["Run it in the terminal"],
                    "acceptance_criteria": ["Correct sequence and termination"],
                    "review_owner": "user",
                    "failure_owner": "developer",
                    "required_evidence": ["Tested commit", "Commands and actual results"],
                },
            }
        ],
        "tasks": [
            {
                "id": "implement",
                "title": "Implement recursive counting",
                "workstream": "general",
                "description": "Count from zero to five",
                "depends_on": [],
                "acceptance_criteria": ["Correct sequence"],
                "requirement_ids": [1],
                "scope": ["counter.py and its checks"],
                "difficulty": "routine",
                "automated_checks": ["Check output and termination"],
                "human_checks": ["Read terminal output"],
                "stage_id": "core",
                "agent_id": "developer",
                "component_ids": ["counter"],
                "owned_paths": ["counter.py"],
                "exclusions": ["Website", "Database"],
                "context": [
                    {
                        "kind": "requirement",
                        "reference": "R1",
                        "reason": "Defines behavior",
                        "required": True,
                        "missing_action": "Ask the user",
                    }
                ],
                "deliverables": ["counter.py"],
                "handoff_to": ["user"],
                "handoff_summary": "Pass the code and test evidence for acceptance",
                "escalation": ["Ask before changing the counting behavior"],
            }
        ],
        "parallel_strategy": "One implementation task, followed by user verification",
    }
