"""Sequential, revision-bound stage branches and verified local merge ancestry."""

import hashlib
import re
from pathlib import Path

from pydantic import Field

from codex_orchestrator import git_service as git
from codex_orchestrator import review_store
from codex_orchestrator.coordination_store import design_fingerprint, stage_accepted
from codex_orchestrator.design import check_source
from codex_orchestrator.design_models import DesignSession
from codex_orchestrator.session import SessionData
from codex_orchestrator.stage_verification import check_stage_verification
from codex_orchestrator.worker_models import artifact_digest


class StageBranch(SessionData):
    branch: str
    start_commit: str
    merged_commit: str | None = None
    merged_base: str | None = None
    evidence_digest: str | None = None


class BranchRevision(SessionData):
    stages: dict[str, StageBranch] = Field(default_factory=dict)


class StageBranches(SessionData):
    base: str
    revisions: dict[str, BranchRevision] = Field(default_factory=dict)


def settings_path(path: Path) -> Path:
    return path.with_suffix(".stage-branches.json")


def load(path: Path) -> StageBranches | None:
    file = settings_path(path)
    return StageBranches.load(file) if file.exists() else None


def revision_key(state):
    return f"{len(state.revisions)}-{design_fingerprint(state)}"


def current(state, path):
    settings = load(path)
    if settings is None:
        return None, None
    revision = settings.revisions.get(revision_key(state))
    if revision is None:
        raise ValueError("Set up stage branches for this new design revision first.")
    return settings, revision


def valid_runs(state, path, latest):
    settings, revision = current(state, path)
    if settings is None:
        return latest
    tasks = {t.id: t for t in state.revisions[-1].design.tasks}
    return {
        key: run
        for key, run in latest.items()
        if key in tasks
        and tasks[key].stage_id in revision.stages
        and run.branch == revision.stages[tasks[key].stage_id].branch
    }


def branch_name(state, path, stage_id):
    stages = state.revisions[-1].design.stages
    index = next((i for i, s in enumerate(stages, 1) if s.id == stage_id), None)
    if index is None:
        raise ValueError("Unknown delivery stage.")
    identity = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:6]
    slug = re.sub(r"[^a-z0-9]+", "-", stage_id.lower()).strip("-")[:40] or "work"
    prefix = f"orchestrator/{identity}-r{len(state.revisions)}-{design_fingerprint(state)[:6]}"
    return f"{prefix}/stage-{index}-{slug}"


def state_for(path):
    state = DesignSession.load(path)
    from codex_orchestrator.cycle_manager import require_active

    require_active(Path(state.source_file))
    check_source(state)
    if state.phase != "approved":
        raise ValueError("Approve the technical design first.")
    return state, Path(state.project).resolve()


def clean(project):
    if git.git(project, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("Commit or preserve all current changes before switching stage branches.")


def enable(path, base="main"):
    state, project = state_for(path)
    with git.writer(project):
        if not git.repository(project):
            raise ValueError("Initialize Git at this project's root first.")
        git.git(project, "check-ref-format", "--branch", base)
        if base.startswith("orchestrator/"):
            raise ValueError(
                "Choose an integration base such as main, not an orchestrator/ branch."
            )
        clean(project)
        if not git.git(project, "rev-parse", "--verify", "HEAD", optional=True):
            raise ValueError(
                "Create an initial base commit and ignore generated files in your terminal first."
            )
        git.git(project, "rev-parse", "--verify", f"refs/heads/{base}^{{commit}}")
        if git.git(project, "symbolic-ref", "--short", "HEAD") != base:
            raise ValueError("Switch to the clean base branch before enabling stage branches.")
        settings = load(path) or StageBranches(base=base)
        if settings.base != base:
            raise ValueError("The configured integration base cannot be changed here.")
        settings.revisions.setdefault(revision_key(state), BranchRevision())
        settings.save(settings_path(path))


def ancestor(project, commit, ref):
    # --is-ancestor has no stdout, so use the existing optional wrapper with a marker.
    return (
        bool(git.git(project, "merge-base", commit, ref, optional=True))
        and git.git(project, "merge-base", commit, ref) == commit
    )


def prerequisites(state, path, stage_id):
    settings, revision = current(state, path)
    if settings is None:
        return
    stages = {s.id: s for s in state.revisions[-1].design.stages}
    if stage_id not in stages:
        raise ValueError("Unknown delivery stage.")
    pending, seen = list(stages[stage_id].depends_on), set()
    project = Path(state.project).resolve()
    while pending:
        parent = pending.pop()
        if parent in seen:
            continue
        seen.add(parent)
        item = revision.stages.get(parent)
        if (
            item is None
            or item.merged_commit is None
            or not stage_accepted(state, path, parent)
            or git.evidence_digest(state, path, parent) != item.evidence_digest
            or not ancestor(project, item.merged_commit, f"refs/heads/{settings.base}")
        ):
            raise ValueError(
                f"Merge prerequisite stage {parent} into {settings.base} "
                "and record its merge first."
            )
        pending.extend(stages[parent].depends_on)


def require_branch(state, path, stage_id):
    settings, revision = current(state, path)
    if settings is None:
        require_setup(state, path)
        return
    prerequisites(state, path, stage_id)
    item = revision.stages.get(stage_id)
    project = Path(state.project).resolve()
    if (
        item is None
        or git.git(project, "symbolic-ref", "--short", "HEAD", optional=True) != item.branch
    ):
        raise ValueError(
            "Create or switch to this delivery stage's branch before running or publishing."
        )
    if item.merged_commit:
        raise ValueError(
            "This stage is already merged. Create a new design revision for further work."
        )
    if not ancestor(project, item.start_commit, "HEAD"):
        raise ValueError("Stage branch history changed; inspect it in your terminal.")


def require_setup(state, path):
    """New managed cycles must not create work outside their stage branches."""
    from codex_orchestrator.cycle_store import find_history

    found = find_history(Path(state.source_file))
    if (
        found
        and (
            not found[1].cycles[-1].imported
            or any(r.origin == "correction" for r in state.revisions)
        )
        and load(path) is None
    ):
        raise ValueError("Enable stage branches before running this managed cycle.")


def prepare(path, stage_id):
    state, project = state_for(path)
    with git.writer(project):
        settings, revision = current(state, path)
        if settings is None:
            raise ValueError("Enable stage branches first.")
        prerequisites(state, path, stage_id)
        name = branch_name(state, path, stage_id)
        item = revision.stages.get(stage_id)
        active = git.git(project, "symbolic-ref", "--short", "HEAD", optional=True)
        if item and active == item.branch:
            require_branch(state, path, stage_id)
            return  # Retries keep partial edits on this same branch.
        clean(project)
        # Sequential stages may not abandon unmerged stage work, even if independent.
        for other_id, other in revision.stages.items():
            if other_id != stage_id and other.merged_commit is None:
                raise ValueError(
                    "Finish and record the active stage merge before switching stages."
                )
        if item:
            if item.merged_commit:
                raise ValueError("This stage is already merged.")
            git.git(project, "switch", item.branch)
            require_branch(state, path, stage_id)
            return
        base = git.git(project, "rev-parse", f"refs/heads/{settings.base}^{{commit}}")
        # Start explicitly at the local integration base; never reuse an unrelated branch.
        git.git(project, "switch", "-c", name, f"refs/heads/{settings.base}")
        revision.stages[stage_id] = StageBranch(branch=name, start_commit=base)
        settings.save(settings_path(path))


def refresh_base(path):
    state, project = state_for(path)
    with git.writer(project):
        settings, _ = current(state, path)
        if settings is None:
            raise ValueError("Enable stage branches first.")
        clean(project)
        remote = git.validate_remote(git.git(project, "remote", "get-url", "origin"))
        # Explicit network action, a normal fetch followed only by fast-forward synchronization.
        git.git(project, "fetch", "--", remote, f"refs/heads/{settings.base}")
        fetched = git.git(project, "rev-parse", "FETCH_HEAD^{commit}")
        local = git.git(project, "rev-parse", f"refs/heads/{settings.base}")
        if not ancestor(project, local, fetched):
            raise ValueError("Local base diverged from the remote. Resolve it in your terminal.")
        git.git(project, "switch", settings.base)
        git.git(project, "merge", "--ff-only", fetched)


def record_merge(path, stage_id):
    state, project = state_for(path)
    with git.writer(project):
        settings, revision = current(state, path)
        if settings is None or stage_id not in revision.stages:
            raise ValueError("Create this stage branch first.")
        clean(project)
        item = revision.stages[stage_id]
        receipt = git.GitPublication.load(git.publication_path(path))
        reviewer = review_store.require_reviewer(path, stage_id, receipt.reviewed_fingerprint or "")
        if (
            receipt.stage_id != stage_id
            or receipt.branch != item.branch
            or not receipt.pushed
            or receipt.design_fingerprint != design_fingerprint(state)
            or receipt.reviewer_id != reviewer.id
            or receipt.reviewer_digest != artifact_digest(reviewer)
            or receipt.evidence_digest != git.evidence_digest(state, path, stage_id)
            or not stage_accepted(state, path, stage_id)
        ):
            raise ValueError(
                "AI review, user-approved push and stage acceptance must match "
                "before recording merge."
            )
        if git.git(project, "rev-parse", f"refs/heads/{item.branch}") != receipt.commit:
            raise ValueError(
                "Stage branch has commits after the approved push. Review the new work first."
            )
        if git.git(project, "symbolic-ref", "--short", "HEAD") not in {settings.base, item.branch}:
            raise ValueError(
                "Switch to this stage branch or its integration base before recording merge."
            )
        check_stage_verification(state, path, stage_id)
        if not ancestor(project, receipt.commit, f"refs/heads/{settings.base}"):
            raise ValueError(
                "The approved commit is not merged into the local base. "
                "Merge it, or refresh the base first."
            )
        item.merged_commit = receipt.commit
        item.merged_base = git.git(project, "rev-parse", f"refs/heads/{settings.base}")
        item.evidence_digest = receipt.evidence_digest
        settings.save(settings_path(path))
