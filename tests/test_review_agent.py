import pytest
from test_git_service import accepted as accepted
from test_git_service import consent

from codex_orchestrator import git_service as git
from codex_orchestrator import review_agent as agent
from codex_orchestrator import review_store as store
from codex_orchestrator.worker_models import artifact_digest


def decision(outcome="approved"):
    return store.ReviewDecision(
        decision=outcome, summary="Independent assessment", findings=[], additional_human_checks=[]
    )


def fake_reviewer(monkeypatch, outcome="approved"):
    calls = []

    def respond(project, prompt, response_model, **kwargs):
        calls.append((prompt, kwargs))
        kwargs["on_response"]("reported-review-model")
        return decision(outcome)

    monkeypatch.setattr(agent, "codex_response", respond)
    return calls


def enable(project, path):
    agent.create_branch(project, "orchestrator/test-review")
    agent.save_policy(path, False, "review-model")


def mock_push(monkeypatch):
    real = git.git
    calls = []

    def invoke(project, *args, **kwargs):
        if "push" in args:
            calls.append(args)
            return ""
        return real(project, *args, **kwargs)

    monkeypatch.setattr(git, "git", invoke)
    return calls


def test_commit_requires_an_explicit_approval(accepted):
    _, path = accepted
    with pytest.raises(ValueError, match="need approval"):
        git.commit(path, git.preview(path, "core"), "No approval")


def test_separate_readonly_review_records_input_model_and_evidence(accepted, monkeypatch):
    project, path = accepted
    calls = fake_reviewer(monkeypatch)
    record = agent.run_review(path, git.preview(path, "core"))
    assert record.source == "reviewer" and record.result.decision == "approved"
    assert record.input_digest and record.reported_model == "reported-review-model"
    prompt, options = calls[0]
    assert "read-only" in prompt and '"commands"' in prompt and "print(5)" in prompt
    assert not options["model"]
    assert not git.publication_path(path).exists()
    assert not git.git(project, "rev-parse", "--verify", "HEAD", optional=True)


def test_review_cannot_publish_even_with_legacy_opt_in(accepted, monkeypatch):
    _, path = accepted
    fake_reviewer(monkeypatch)
    store.PublicationPolicy(allow_reviewer_push=True).save(store.policy_path(path))
    reviewed = git.preview(path, "core")
    with pytest.raises(ValueError, match="User permission|user permission"):
        agent.run_review(path, reviewed, publish=True)
    assert not git.publication_path(path).exists()


@pytest.mark.parametrize("outcome", ["changes_requested", "blocked"])
def test_negative_review_never_commits_or_pushes(accepted, monkeypatch, outcome):
    project, path = accepted
    enable(project, path)
    fake_reviewer(monkeypatch, outcome)
    pushes = mock_push(monkeypatch)
    reviewed = git.preview(path, "core")
    record = agent.run_review(path, reviewed)
    assert not pushes and not git.publication_path(path).exists()
    with pytest.raises(ValueError, match="need approval"):
        git.commit(path, reviewed, "Blocked", approval_id=record.id)


def test_ai_review_then_explicit_user_permission_pushes_exact_commit(accepted, monkeypatch):
    project, path = accepted
    enable(project, path)
    calls = fake_reviewer(monkeypatch)
    pushes = mock_push(monkeypatch)
    reviewed = git.preview(path, "core")
    record = agent.run_review(path, reviewed)
    assert not pushes and not git.publication_path(path).exists()
    receipt = git.commit(path, reviewed, "AI reviewed", approval_id=record.id)
    assert not receipt.pushed
    with pytest.raises(ValueError, match="User permission"):
        git.push(path)
    assert not pushes
    receipt = git.push(path, permission=consent(path))
    assert receipt.pushed and receipt.reviewer_id == record.id
    assert receipt.reviewer_digest == artifact_digest(record)
    assert pushes[0][-1] == f"{receipt.commit}:refs/heads/orchestrator/test-review"
    assert calls[0][1]["model"] == "review-model"
    assert git.git(project, "show", "--pretty=", "--name-only", "HEAD") == "counter.py"


def test_main_cannot_enable_reviewer_publication(accepted):
    project, path = accepted
    git.git(project, "branch", "-M", "main")
    with pytest.raises(ValueError, match="Automatic pushing"):
        agent.save_policy(path, True, None)
    assert not store.load_policy(path).allow_reviewer_push


def test_changes_during_review_cannot_acquire_approval(accepted, monkeypatch):
    project, path = accepted

    def change(*args, **kwargs):
        (project / "counter.py").write_text("changed during review")
        return decision()

    monkeypatch.setattr(agent, "codex_response", change)
    with pytest.raises(ValueError, match="changed after execution"):
        agent.run_review(path, git.preview(path, "core"))
    assert store.load_reviews(path).records[-1].result.decision == "blocked"


def test_new_evidence_invalidates_an_approval(accepted, monkeypatch):
    _, path = accepted
    fake_reviewer(monkeypatch)
    reviewed = git.preview(path, "core")
    record = agent.run_review(path, reviewed)
    from codex_orchestrator.run_store import latest_runs, run_directory

    run = next(iter(latest_runs(path, 1).values()))
    evidence_path = run_directory(path) / run.id / "worker_evidence.json"
    evidence = git.review_evidence(evidence_path.parent)
    evidence.commands[0].output = "Changed evidence"
    evidence.save(evidence_path)
    with pytest.raises(ValueError, match="new preview"):
        git.commit(path, reviewed, "Old evidence", approval_id=record.id)


def test_superseded_reviewer_approval_cannot_be_used(accepted, monkeypatch):
    project, path = accepted
    enable(project, path)
    fake_reviewer(monkeypatch)
    reviewed = git.preview(path, "core")
    old = agent.run_review(path, reviewed)
    fake_reviewer(monkeypatch, "changes_requested")
    agent.run_review(path, reviewed)
    with pytest.raises(ValueError, match="superseded"):
        git.commit(path, reviewed, "Old review", approval_id=old.id)
    # Human confirmation cannot bypass a failed mandatory AI review.
    with pytest.raises(ValueError, match="need approval"):
        git.approve_user(path, reviewed)


def test_outstanding_human_checks_cannot_be_approved():
    with pytest.raises(ValueError, match="outstanding human"):
        store.ReviewDecision(
            decision="approved",
            summary="Done",
            findings=[],
            additional_human_checks=["Check in browser"],
        )


def test_review_budget_blocks_inference(accepted, monkeypatch):
    _, path = accepted
    monkeypatch.setattr(agent, "MAX_REVIEW_BYTES", 1)
    monkeypatch.setattr(agent, "codex_response", lambda *a, **kw: pytest.fail("Called model"))
    with pytest.raises(ValueError, match="256 KiB"):
        agent.run_review(path, git.preview(path, "core"))


def test_reviewer_error_releases_writer_lock_and_does_not_publish(accepted, monkeypatch):
    project, path = accepted

    def error(*a, **kw):
        raise ValueError("Invalid reviewer JSON")

    monkeypatch.setattr(agent, "codex_response", error)
    with pytest.raises(ValueError, match="Invalid reviewer"):
        agent.run_review(path, git.preview(path, "core"))
    assert not git.project_lock(project).exists()
    assert store.load_reviews(path).records[-1].result.decision == "blocked"


def test_changed_evidence_after_commit_blocks_push(accepted, monkeypatch):
    from codex_orchestrator.run_store import latest_runs, run_directory

    project, path = accepted
    reviewed = git.preview(path, "core")
    agent.run_review(path, reviewed)
    approval = git.approve_user(path, reviewed)
    git.commit(path, reviewed, "Reviewed", approval_id=approval.id)
    run = next(iter(latest_runs(path, 1).values()))
    folder = run_directory(path) / run.id
    evidence = git.review_evidence(folder)
    evidence.commands[0].output = "Changed evidence after commit"
    evidence.save(folder / "worker_evidence.json")
    pushes = mock_push(monkeypatch)
    with pytest.raises(ValueError, match="test evidence changed"):
        git.push(path, permission=consent(path))
    assert not pushes


def test_legacy_policy_never_replaces_push_permission(accepted, monkeypatch):
    project, path = accepted
    enable(project, path)
    fake_reviewer(monkeypatch)
    reviewed = git.preview(path, "core")
    approval = agent.run_review(path, reviewed)
    git.commit(path, reviewed, "Reviewed", approval_id=approval.id)
    policy = store.load_policy(path)
    policy.allow_reviewer_push = True
    policy.save(store.policy_path(path))
    with pytest.raises(ValueError, match="User permission"):
        git.push(path)


def test_failed_review_supersedes_previous_approval(accepted, monkeypatch):
    project, path = accepted
    enable(project, path)
    fake_reviewer(monkeypatch)
    reviewed = git.preview(path, "core")
    old = agent.run_review(path, reviewed)

    def error(*a, **kw):
        raise ValueError("Timed out")

    monkeypatch.setattr(agent, "codex_response", error)
    with pytest.raises(ValueError, match="Timed out"):
        agent.run_review(path, reviewed)
    with pytest.raises(ValueError, match="superseded"):
        git.commit(path, reviewed, "Old approval", approval_id=old.id)


def test_dashboard_review_is_explicit_and_refresh_does_not_call_model(accepted, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from codex_orchestrator import web

    _, path = accepted
    calls = fake_reviewer(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(path.parent))
    app = AppTest.from_file(web.__file__, default_timeout=10).run()
    next(b for b in app.button if b.label == "Open dashboard").click().run()
    next(b for b in app.button if b.label == "Preview accepted stage changes").click().run()
    assert not app.exception and not calls
    assert not any(b.label == "Review and publish if approved" for b in app.button)
    next(b for b in app.button if b.label == "Run independent review").click().run()
    assert not app.exception and len(calls) == 1
    assert any("Independent assessment" in m.value for m in app.markdown)
    app.run()
    assert not app.exception and len(calls) == 1
    assert not git.publication_path(path).exists()


def test_user_cannot_commit_before_ai_review(accepted):
    _, path = accepted
    reviewed = git.preview(path, "core")
    with pytest.raises(ValueError, match="independent AI review"):
        git.approve_user(path, reviewed)


def test_push_permission_is_bound_to_destination(accepted, monkeypatch):
    _, path = accepted
    fake_reviewer(monkeypatch)
    reviewed = git.preview(path, "core")
    approval = agent.run_review(path, reviewed)
    git.commit(path, reviewed, "Reviewed", approval_id=approval.id)
    pushes = mock_push(monkeypatch)
    for update in (
        {"commit": "different"},
        {"branch": "other"},
        {"remote": "https://other.invalid/repo"},
    ):
        wrong = consent(path).model_copy(update=update)
        with pytest.raises(ValueError, match="User permission"):
            git.push(path, permission=wrong)
    assert not pushes


def test_ui_requires_separate_permission_after_ai_review_and_commit(accepted, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from codex_orchestrator import web

    _, path = accepted
    fake_reviewer(monkeypatch)
    pushes = mock_push(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_PLANS", str(path.parent))
    app = AppTest.from_file(web.__file__, default_timeout=10).run()
    next(b for b in app.button if b.label == "Open dashboard").click().run()
    next(b for b in app.button if b.label == "Preview accepted stage changes").click().run()
    assert next(b for b in app.button if b.label == "Commit reviewed AI changes").disabled
    next(b for b in app.button if b.label == "Run independent review").click().run()
    next(
        c for c in app.checkbox if c.label == "I reviewed these files and want to commit them"
    ).check().run()
    next(b for b in app.button if b.label == "Commit reviewed AI changes").click().run()
    assert not app.exception and not pushes
    assert next(b for b in app.button if b.label == "Approve and push this commit").disabled
    app.run()
    assert not pushes
    next(c for c in app.checkbox if c.label.startswith("I give permission to push")).check().run()
    next(b for b in app.button if b.label == "Approve and push this commit").click().run()
    assert not app.exception and len(pushes) == 1
