"""execute-code --background and poll-background."""


def test_background_job_runs_and_reports_result(run, kernel):
    proc, data = run.json(
        "execute-code", "-c", "1 + 1", "-k", kernel, "--background"
    )
    assert proc.returncode == 0, proc.stderr
    assert data["status"] == "running"
    job_id = data["job_id"]

    proc, data = run.json(
        "poll-background", "-k", kernel, "--job-id", job_id, "--wait"
    )
    assert proc.returncode == 0, proc.stderr
    job = next(j for j in data["jobs"] if j["job_id"] == job_id)
    assert job["status"] == "done"
    assert job["reaped"] is True
    assert job["results"][0]["status"] == "ok"
    assert any(
        o["type"] == "execute_result" and o["text"] == "2"
        for o in job["results"][0]["outputs"]
    )


def test_background_job_recorded_in_history(run, kernel):
    proc, data = run.json(
        "execute-code", "-c", "bg_history_marker = 99", "-k", kernel, "--background"
    )
    job_id = data["job_id"]
    block_id = data["block_ids"][0]
    run.json("poll-background", "-k", kernel, "--job-id", job_id, "--wait")

    proc, hist = run.json("show-history", "-k", kernel)
    assert proc.returncode == 0, proc.stderr
    block = next(b for b in hist["blocks"] if b["block_id"] == block_id)
    assert block["code"] == "bg_history_marker = 99"


def test_poll_background_keep_does_not_reap(run, kernel):
    proc, data = run.json("execute-code", "-c", "1", "-k", kernel, "--background")
    job_id = data["job_id"]

    proc, data = run.json(
        "poll-background", "-k", kernel, "--job-id", job_id, "--wait", "--keep"
    )
    job = next(j for j in data["jobs"] if j["job_id"] == job_id)
    assert job["status"] == "done"
    assert job["reaped"] is False

    # Still there on a second poll since it wasn't reaped.
    proc, data = run.json("poll-background", "-k", kernel, "--job-id", job_id)
    job = next(j for j in data["jobs"] if j["job_id"] == job_id)
    assert job["reaped"] is True  # reaped this time, --keep wasn't passed


def test_poll_background_unknown_job_id_errors(run, kernel):
    proc, data = run.json("poll-background", "-k", kernel, "--job-id", "deadbeef")
    assert proc.returncode != 0
    assert "deadbeef" in data["error"]


def test_concurrent_execute_refused_while_background_job_running(run, kernel):
    proc, data = run.json(
        "execute-code", "-c", "import time; time.sleep(2)",
        "-k", kernel, "--background",
    )
    assert proc.returncode == 0, proc.stderr
    job_id = data["job_id"]

    proc, data = run.json("execute-code", "-c", "1 + 1", "-k", kernel)
    assert proc.returncode != 0
    assert job_id in data["error"]
    assert "poll-background" in data["error"]

    # Foreground background-flag start is refused too, for the same reason.
    proc, data = run.json(
        "execute-code", "-c", "2 + 2", "-k", kernel, "--background"
    )
    assert proc.returncode != 0
    assert job_id in data["error"]

    proc, data = run.json(
        "poll-background", "-k", kernel, "--job-id", job_id, "--wait"
    )
    job = next(j for j in data["jobs"] if j["job_id"] == job_id)
    assert job["status"] == "done"

    # Kernel is usable again once the job is reaped.
    proc, data = run.json("execute-code", "-c", "3 + 3", "-k", kernel)
    assert proc.returncode == 0, proc.stderr
    assert data["status"] == "ok"


def test_poll_background_wait_timeout_returns_still_running(run, kernel):
    proc, data = run.json(
        "execute-code", "-c", "import time; time.sleep(5)",
        "-k", kernel, "--background",
    )
    job_id = data["job_id"]

    proc, data = run.json(
        "poll-background", "-k", kernel, "--job-id", job_id,
        "--wait", "--timeout", "0.5", "--keep",
    )
    assert proc.returncode == 0, proc.stderr
    job = next(j for j in data["jobs"] if j["job_id"] == job_id)
    assert job["status"] == "running"

    # Drain it so kernel teardown doesn't race the still-running worker.
    run.json("poll-background", "-k", kernel, "--job-id", job_id, "--wait")
