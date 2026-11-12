"""Human-readable worker reports and explicitly loaded execution evidence."""

import hashlib

import streamlit as st

from codex_orchestrator.worker_models import WorkerEvidence, WorkerSpec
from codex_orchestrator.worker_results import load_result


def worker_report_view(run, folder):
    if run.worker_contract_version is None:
        st.caption("Historical run: no structured worker report was required.")
        return
    st.markdown("**Worker report**")
    st.caption(f"Profile · {run.worker_profile} | Runner status · {run.status}")
    with st.expander("Worker assignment contract"):
        spec = WorkerSpec.load(folder / "worker_spec.json")
        st.write(spec.instructions)
        st.write("Owned files", ", ".join(spec.owned_paths) or "None · read-only verification")
        for check in spec.checks:
            st.write(f"{check.id} · {check.description}")
    file = folder / "worker_result.json"
    if file.exists():
        report = load_result(file)
        digest = hashlib.sha256(report.model_dump_json().encode("utf-8")).hexdigest()
        if digest != run.worker_result_digest:
            st.warning(
                "Worker report differs from the recorded hash; verification will be rejected."
            )
        st.write(report.summary)
        st.caption(
            f"Worker-reported outcome · {report.outcome}. Runner evidence checks determine "
            "whether verification can proceed."
        )
        if report.changes:
            st.markdown("**Reported changes**")
            for change in report.changes:
                st.write(f"{change.path} · {change.description}")
        if report.checks:
            st.markdown("**Reported checks**")
            for check in report.checks:
                st.write(f"{check.check_id} · {check.outcome} · {check.detail}")
                if check.command:
                    st.code(check.command, language="shell")
        for blocker in report.blockers:
            st.error(blocker)
        st.markdown("**Your manual checks**")
        for check in report.human_checks:
            st.markdown(f"- {check}")
        with st.expander("Handoff to the next agent"):
            st.write(report.handoff)
        st.download_button(
            "Download worker report",
            report.model_dump_json(indent=2),
            file_name=f"{run.id}-worker-result.json",
            mime="application/json",
        )
    elif run.status in {"starting", "running"}:
        st.caption("Structured results appear after the worker finishes.")
    else:
        st.warning(
            "No validated worker report. Inspect the run error and Codex output before retrying."
        )
    evidence_file = folder / "worker_evidence.json"
    if evidence_file.exists():
        key = f"worker_evidence_{run.id}"
        if st.button("Load worker evidence", key=f"load_evidence_{run.id}"):
            st.session_state[key] = WorkerEvidence.load(evidence_file).model_dump()
        if saved := st.session_state.get(key):
            evidence = WorkerEvidence.model_validate(saved)
            for violation in evidence.violations:
                st.error(violation)
            for warning in evidence.warnings:
                st.warning(warning)
            st.markdown("**Checks matched to command events**")
            for check, event in evidence.check_matches.items():
                st.write(f"{check} → {event}")
            for index, command in enumerate(evidence.commands):
                with st.expander(f"Observed command {index + 1} · exit {command.exit_code}"):
                    st.code(command.command, language="shell")
                    st.code(command.output, language=None)
            for change in evidence.changes:
                with st.expander("Observed change · " + change.path):
                    st.caption(f"Before · {change.before_sha256 or 'not captured / absent'}")
                    st.caption(f"After · {change.after_sha256 or 'not captured / absent'}")
                    if change.diff:
                        st.code(change.diff, language="diff")
            st.caption(
                "Command exit codes support the report but do not prove that a command "
                "adequately tests the requirement. Review the actual checks before acceptance."
            )
