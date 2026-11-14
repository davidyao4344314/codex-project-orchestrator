# Stage branch acceptance

Use a disposable project/remote and an approved design with at least two dependent stages.
Generated plans, logs and environments must be ignored. Real workers/reviews use account usage;
automated tests use fake Codex and temporary Git repositories.

1. On a clean committed main, enable Delivery stage branch for the current revision. Expect
   no branch switch or model request. Old runs no longer count as branch-bound verification.
2. Try Run next ready task or the individual runner before preparing a branch: expect blocked.
   Click Create or switch to stage branch. Expect an orchestrator/.../stage-1-... branch rooted
   at main. Refresh the dashboard: no further switch, fetch or model call.
3. Run stage tasks, inspect/test outputs, accept task and stage verification, obtain AI review,
   create the local commit and give explicit push permission. Expect the displayed development
   branch on your remote. An AI approval must never push by itself.
4. Attempt the dependent stage before merging/recording: expect blocked, even after all earlier
   tasks are verified and the stage accepted. Record stage merge must reject a commit absent
   from main and an unpushed local receipt.
5. Merge normally or fast-forward in your Git host/terminal. For remote merges, click Refresh
   base from origin. Expect only a fast-forward; divergence stops without discarding local work.
   Record stage merge, then prepare stage two. Its start commit must equal the integrated main.
6. Stop/retry a task on its stage branch: partial files remain. Try switching with dirty files:
   expect a blocker and no loss. Clean but unfinished independent stages also block switching.
7. For a read-only/no-change stage, expect review and permission as usual, with the existing SHA
   rather than an artificial empty commit. After pushing its stage ref, record its base ancestry.
8. Create a new design revision. Expect setup required again, unique branch names and retained
   earlier history. Do not expect old runs, AI approvals or merge evidence to approve new work.

Squash/rebase merges are not recognized in this increment: use ordinary merges or fast-forward.
Merge recording checks local Git ancestry, not remote PR metadata. Merging, resolving conflicts
and deployment remain user actions. Do not intentionally violate scope with a real worker just
to test gates; automated tests cover ancestry rejection, changed evidence, locks, dirty work,
revision isolation, direct-runner checks, read-only stages and read-only dashboard observation.
