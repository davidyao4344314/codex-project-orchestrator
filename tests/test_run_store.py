from codex_orchestrator.run_store import Run, read_tail, run_directory, runs


def test_large_log_preview_is_bounded_and_keeps_recent_output(tmp_path):
    log = tmp_path / "events.jsonl"
    log.write_bytes(
        b"old-output" * 100_000
        + b"\n"
        + b"\n".join(f"event-{i}".encode() for i in range(100))
        + b"\n"
    )
    assert read_tail(log, max_bytes=2048, lines=3) == ["event-97", "event-98", "event-99"]
    # A huge individual record cannot cause an unbounded preview either.
    log.write_bytes(b"x" * 1_000_000)
    assert read_tail(log, max_bytes=1024) == []


def test_run_snapshots_refresh_after_updates_and_do_not_share_mutations(tmp_path):
    design = tmp_path / "design.json"
    folder = run_directory(design) / "job"
    folder.mkdir(parents=True)
    run = Run(id="job", task_id="task", agent_id="agent", revision=1, prompt="context")
    run.save(folder / "run.json")
    snapshot = runs(design)[0]
    snapshot.verified = True
    assert not runs(design)[0].verified
    run.status = "completed"
    run.save(folder / "run.json")
    assert runs(design)[0].status == "completed"
