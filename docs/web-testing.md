# Browser interface checks

Start the local interface from the project environment:

```sh
python -m pip install -e '.[web,dev]'
orchestrator web
```

Open http://127.0.0.1:8501 on the same computer.

1. Select **Try sample project**. Expect the Reading list name, complete brief, and a
   new independent local sample folder to appear. This starter deliberately has no
   book-management implementation yet.
2. Select **Create planning session**. Expect a saved Stage 1 session. Switch to
   Technical design before approving it: the interface should ask you to approve Stage 1.
3. Return to Requirements and select **Continue with Codex**. Expect a question or a
   reviewable brief and plan, with the reported response model. Answer any questions.
   Check that the scope excludes accounts, cloud deployment, and external book APIs.
4. Approve the requirements, then open Technical design and create it. Expect a technical
   structure, model recommendations, agent responsibilities, declared context, staged
   tasks, and verification gates with automated and human checks.
5. Review a stage's **Context and handoff** and **Your checks before merge**. These are
   proposed future checks, not claims that the reading-list features were built or tested.
   Required missing context must block design approval.
6. Approve a valid design, then change an agent's model allocation. Expect a new revision
   needing approval. Request a scope-preserving revision and inspect its updated workflow.
7. Refresh the browser or restart the server. Reopen the saved session from the sidebar.
   Expect the saved approvals and revision history to remain, with no new Codex request
   until you explicitly continue or revise.

Use **Check connection** to confirm Codex sign-in. An unavailable CLI should direct you
back to `orchestrator setup`; a failed model request should preserve the saved session
and offer a retry. Credentials and generated sessions are excluded from Git.

Automated interface checks (mocked Codex calls, no model usage):

```sh
python -m pytest tests/test_web.py -q
```

The complete suite also verifies the CLI and shared planning, context, and approval rules:

```sh
python -m pytest -q
ruff check src examples tests
```
