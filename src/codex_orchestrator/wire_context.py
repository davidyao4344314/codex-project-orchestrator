"""Compact the model-facing payload without changing the full audited context snapshot."""

SOURCE_KINDS = {"project_file", "owned_file", "upstream_file"}
MIN_REFERENCE_BYTES = 1024
LARGE_FILE_BYTES = 8 * 1024


def worker_context(package, spec, previous=None):
    # model_dump returns an independent JSON-ready structure; never mutate the checkpoint.
    wire = package.model_dump()
    for field in ("limits", "blockers"):
        wire.pop(field, None)
    metadata = wire["metadata"]
    metadata.pop("owned_paths", None)  # Assignment and WorkerSpec already contain it.
    metadata.pop("allocation_reason", None)
    assignment = metadata["assignment"]
    for field in ("automated_checks", "human_checks", "acceptance_criteria"):
        assignment.pop(field, None)  # WorkerSpec is authoritative, including feedback checks.
    for field in ("model_capability", "model_reason"):
        metadata["agent"].pop(field, None)
    if not spec.read_only:
        stage = metadata["stage"]
        metadata["stage"] = {key: stage[key] for key in ("id", "title", "goal", "depends_on")}
    if feedback := metadata.get("user_feedback"):
        metadata["user_feedback"] = {
            key: feedback[key] for key in ("id", "note", "prior_run_id") if feedback.get(key)
        }

    prior_hashes = set()
    if (
        previous is not None
        and previous.agent_id == package.agent_id
        and previous.design_fingerprint == package.design_fingerprint
        and previous.requested_model == package.requested_model
    ):
        prior_hashes = {
            (r.reference, r.sha256)
            for r in previous.resources
            if r.kind in SOURCE_KINDS
            and r.status == "included"
            and r.sha256
            and r.content is not None
        }
    reused = 0
    for item in wire["resources"]:
        if (
            item["kind"] in SOURCE_KINDS
            and item["status"] == "included"
            and item["sha256"]
            and not any(alias.startswith(("instruction: ", "skill: ")) for alias in item["aliases"])
            and len((item["content"] or "").encode("utf-8")) >= LARGE_FILE_BYTES
        ):
            item["content"] = None
            item["content_mode"] = (
                "required_reference" if item["required"] else "optional_reference"
            )
        elif (
            item["kind"] in SOURCE_KINDS
            and item["status"] == "included"
            and (item["reference"], item["sha256"]) in prior_hashes
            and len((item["content"] or "").encode("utf-8")) >= MIN_REFERENCE_BYTES
        ):
            item["content"] = None
            item["content_mode"] = "unchanged_reference"
            reused += 1
        if item["status"] == "truncated":
            # Required truncated inputs are blocked before launch. Do not present an
            # optional partial file as if it were complete.
            item["content"] = None
            item["sha256"] = None
        if item["status"] == "included":
            item.pop("missing_action", None)
        for key in list(item):
            if item[key] is None or item[key] == []:
                item.pop(key)
    if not wire["warnings"]:
        wire.pop("warnings")
    return wire, reused
